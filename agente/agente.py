"""
AGENTE NOTIFICADOR DGRDT
========================
Servidor local (solo en esta PC, http://127.0.0.1:8765) que:
  1) muestra la misma web del Notificador, y
  2) recibe la orden "Notificar automáticamente" y la ejecuta en el Chrome del notificador:
     Outlook web (envía el mail) -> PDF del enviado -> GEDO (Notificación fehaciente) -> agenda.

Se inicia con INICIAR-NOTIFICADOR.bat (o el acceso directo del escritorio).
"""
import json
import mimetypes
import os
import re
import secrets
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

import config      # noqa: E402
import navegador   # noqa: E402
import trabajos    # noqa: E402
from rutas import BASE, CASOS  # noqa: E402

PUERTO = 8765
VERSION = "1.3"


def _token_persistente():
    """Token fijo de esta PC (así la pestaña abierta sigue funcionando si se reinicia el agente)."""
    ruta = os.path.join(BASE, "token.txt")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            tok = f.read().strip()
            if len(tok) >= 20:
                return tok
    except Exception:
        pass
    tok = secrets.token_urlsafe(24)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


TOKEN = _token_persistente()
INDEX = os.path.join(os.path.dirname(AQUI), "index.html")
# En un servidor en la nube el agente se usa por HTTPS con su nombre público (NOTIFICADOR_HOSTS).
HOSTS_REMOTOS = {h.strip().lower() for h in os.environ.get("NOTIFICADOR_HOSTS", "").split(",") if h.strip()}
HOSTS_OK = {f"127.0.0.1:{PUERTO}", f"localhost:{PUERTO}"} | HOSTS_REMOTOS
WEB_OFICIAL = "https://notificador-dgrdt.vercel.app"


def _clave_remota():
    """Clave para entrar al agente desde internet (solo existe en el servidor)."""
    try:
        with open(os.path.join(BASE, "clave_remota.txt"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


_FALLAS = []            # horarios de claves incorrectas (para frenar a quien pruebe claves)
_FALLAS_LOCK = threading.Lock()


def _bloqueado():
    with _FALLAS_LOCK:
        ahora = time.time()
        _FALLAS[:] = [x for x in _FALLAS if ahora - x < 600]
        return len(_FALLAS) >= 5
# Páginas web que pueden usar este agente desde el navegador (además de la que sirve él mismo).
# El navegador manda el encabezado Origin y una página no lo puede falsificar.
ORIGENES_WEB = {"https://notificador-dgrdt.vercel.app"} | {
    o.strip().rstrip("/") for o in os.environ.get("NOTIFICADOR_ORIGENES", "").split(",") if o.strip()}


def _dentro_de_casos(ruta):
    try:
        r = os.path.realpath(ruta)
        return os.path.commonpath([r, os.path.realpath(CASOS)]) == os.path.realpath(CASOS)
    except Exception:
        return False


def estado_sesiones():
    """Mira las pestañas del Chrome del notificador para saber si hay sesión iniciada."""
    res = {"chrome": False, "outlook": False, "gedo": False}
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{navegador.PUERTO_CDP}/json/list", timeout=1.5) as r:
            tabs = json.loads(r.read().decode("utf-8"))
        res["chrome"] = True
        for t in tabs:
            if t.get("type") != "page":
                continue
            u = t.get("url", "")
            if ("outlook.office.com/mail" in u or "outlook.cloud.microsoft/mail" in u or "outlook.office365.com/mail" in u):
                res["outlook"] = True
            if re.search(r"(eut|eue)-pr\.apps\.buenosaires\.gob\.ar/(gedo-web|expedientes-web)", u):
                res["gedo"] = True
    except Exception:
        pass
    return res


class Handler(BaseHTTPRequestHandler):
    server_version = "AgenteDGRDT/" + VERSION

    def log_message(self, fmt, *args):  # silencio en consola
        pass

    # ---------- helpers ----------
    def _origen(self):
        """Origin de la página que llama, si es una de las permitidas (si no, "")."""
        o = (self.headers.get("Origin") or "").rstrip("/")
        return o if o in ORIGENES_WEB else ""

    def _cors(self):
        o = self._origen()
        if o:
            self.send_header("Access-Control-Allow-Origin", o)
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Vary", "Origin")

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _host_ok(self):
        return (self.headers.get("Host", "") or "").lower() in HOSTS_OK

    def _es_remoto(self):
        """Pedido que llega desde internet (por el proxy HTTPS del servidor), no desde esta máquina."""
        return bool(self.headers.get("X-Forwarded-For")) or (self.headers.get("Host", "") or "").lower() in HOSTS_REMOTOS

    def _token_ok(self, query=None):
        tok = self.headers.get("X-Agente-Token", "")
        if not tok and query:
            tok = (urllib.parse.parse_qs(query).get("k") or [""])[0]
        return secrets.compare_digest(tok, TOKEN)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    # ---------- OPTIONS (consulta previa del navegador para la web publicada) ----------
    def do_OPTIONS(self):
        if not self._host_ok() or not self._origen():
            self.send_response(403)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Agente-Token")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---------- GET ----------
    def do_GET(self):
        if not self._host_ok():
            return self._json({"error": "host no permitido"}, 403)
        url = urllib.parse.urlparse(self.path)
        ruta = url.path

        if ruta == "/api/emparejar":
            # la web publicada pide el token: solo se lo damos a las páginas permitidas.
            # Desde internet NO (fuera del navegador el Origin se puede falsificar): ahí se entra con la clave.
            if self._es_remoto() or not self._origen():
                return self._json({"error": "origen no permitido"}, 403)
            return self._json({"ok": True, "token": TOKEN, "version": VERSION})

        if ruta in ("/", "/index.html") and self._es_remoto():
            # en el servidor la página no lleva el token: se usa desde la web oficial con la clave
            data = (f'<!doctype html><meta charset="utf-8"><title>Agente Notificador DGRDT</title>'
                    f'<p style="font-family:sans-serif">Este es el agente del Notificador DGRDT en la nube. '
                    f'Usalo desde <a href="{WEB_OFICIAL}">{WEB_OFICIAL}</a> → «Conectar con el agente en la nube».</p>').encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        if ruta in ("/", "/index.html"):
            with open(INDEX, "r", encoding="utf-8") as f:
                html = f.read()
            meta = f'<meta name="agente-token" content="{TOKEN}"><meta name="agente-version" content="{VERSION}">'
            html = html.replace("<head>", "<head>\n" + meta, 1)
            data = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        if not ruta.startswith("/api/"):
            return self._json({"error": "no encontrado"}, 404)
        if not self._token_ok(url.query if ruta == "/api/archivo" else None):
            return self._json({"error": "token inválido"}, 401)

        if ruta == "/api/estado":
            return self._json({"ok": True, "version": VERSION, "base": BASE, "remoto": self._es_remoto(),
                               "windows": os.name == "nt", **estado_sesiones(), "config": config.cargar()})
        if ruta == "/api/config":
            return self._json(config.cargar())
        if ruta == "/api/trabajos":
            return self._json(trabajos.listar())
        if ruta.startswith("/api/trabajos/"):
            t = trabajos.obtener(ruta.split("/")[3])
            return self._json(t.to_dict() if t else {"error": "no existe"}, 200 if t else 404)
        if ruta == "/api/archivo":
            q = urllib.parse.parse_qs(url.query)
            p = (q.get("ruta") or [""])[0]
            if not p or not _dentro_de_casos(p) or not os.path.isfile(p):
                return self._json({"error": "archivo no permitido"}, 403)
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            with open(p, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Disposition", "inline; filename*=UTF-8''" + urllib.parse.quote(os.path.basename(p)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
            return
        return self._json({"error": "no encontrado"}, 404)

    # ---------- POST ----------
    def do_POST(self):
        if not self._host_ok():
            return self._json({"error": "host no permitido"}, 403)
        ruta = urllib.parse.urlparse(self.path).path
        if ruta == "/api/remoto/entrar":
            return self._entrar_remoto()
        if not self._token_ok():
            return self._json({"error": "token inválido"}, 401)
        try:
            body = self._body()
        except Exception:
            return self._json({"error": "JSON inválido"}, 400)

        if ruta == "/api/config":
            return self._json(config.guardar(body))
        if ruta == "/api/trabajos":
            if not (body.get("notificaciones") or []):
                return self._json({"error": "no hay notificaciones"}, 400)
            t = trabajos.crear(body)
            return self._json({"id": t.id})
        if ruta.startswith("/api/trabajos/") and ruta.endswith("/responder"):
            # respuesta a una pregunta del agente (ej. el número de EX correcto)
            t = trabajos.obtener(ruta.split("/")[3])
            ok = bool(t) and t.responder(body)
            return self._json({"ok": ok} if ok else {"error": "No hay ninguna pregunta pendiente"}, 200 if ok else 409)
        if ruta.startswith("/api/trabajos/") and ruta.endswith("/cancelar"):
            t = trabajos.obtener(ruta.split("/")[3])
            if t:
                t.cancelar = True
            return self._json({"ok": bool(t)})
        if ruta == "/api/abrir":
            if os.name != "nt" or self._es_remoto():
                return self._json({"error": "Abrir carpetas solo funciona en la PC donde corre el agente"}, 501)
            p = body.get("ruta") or ""
            if p and _dentro_de_casos(p) and os.path.exists(p):
                os.startfile(p)
                return self._json({"ok": True})
            return self._json({"error": "ruta no permitida"}, 403)
        if ruta == "/api/navegador":
            # abre la ventana del notificador y las pestañas que falten (Outlook / GEDO)
            if not navegador.chrome_activo():
                navegador.abrir_chrome([navegador.URL_OUTLOOK, navegador.URL_GEDO])
            else:
                ses = estado_sesiones()
                if not ses["outlook"]:
                    navegador.abrir_url(navegador.URL_OUTLOOK)
                if not ses["gedo"]:
                    navegador.abrir_url(navegador.URL_GEDO)
            return self._json({"ok": True})
        return self._json({"error": "no encontrado"}, 404)

    def _entrar_remoto(self):
        """La web oficial entra al agente de la nube con la clave y recibe el token."""
        if not self._origen():
            return self._json({"error": "origen no permitido"}, 403)
        clave = _clave_remota()
        if not clave:
            return self._json({"error": "Este agente no tiene acceso desde internet"}, 404)
        if _bloqueado():
            return self._json({"error": "Demasiados intentos con clave incorrecta: esperá 10 minutos"}, 429)
        try:
            dada = str(self._body().get("clave") or "").strip()
        except Exception:
            dada = ""
        if dada and secrets.compare_digest(dada.encode("utf-8"), clave.encode("utf-8")):
            return self._json({"ok": True, "token": TOKEN, "version": VERSION})
        with _FALLAS_LOCK:
            _FALLAS.append(time.time())
        time.sleep(1)
        return self._json({"error": "Clave incorrecta"}, 401)


def _ya_corriendo():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO}/", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    abrir_web = "--sin-web" not in sys.argv
    # sin consola (pythonw): todo lo que se imprime va a un log
    if sys.stdout is None or sys.stderr is None or "--log" in sys.argv:
        from rutas import LOGS
        f = open(os.path.join(LOGS, "agente.log"), "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = f
    else:
        for s in (sys.stdout, sys.stderr):
            try:
                s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    if _ya_corriendo():
        # ya hay un agente abierto: solo mostrar la web
        navegador.abrir_url(f"http://127.0.0.1:{PUERTO}/")
        return
    trabajos.iniciar_trabajador()
    srv = ThreadingHTTPServer(("127.0.0.1", PUERTO), Handler)
    print(f"Agente Notificador DGRDT v{VERSION} escuchando en http://127.0.0.1:{PUERTO}", flush=True)
    if abrir_web:
        def _abrir():
            try:
                navegador.abrir_url(f"http://127.0.0.1:{PUERTO}/")
            except Exception:
                webbrowser.open(f"http://127.0.0.1:{PUERTO}/")
        threading.Timer(0.8, _abrir).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
