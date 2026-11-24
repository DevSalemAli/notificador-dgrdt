"""
Cola de trabajos del agente.

Un "trabajo" = una notificación completa (uno o más mails + GEDO + agenda).
Se procesan de a uno, en un hilo aparte, porque el navegador se maneja de a una cosa por vez.
"""
import base64
import datetime as dt
import json
import os
import queue
import re
import threading
import time
import traceback
import uuid

import config
from rutas import CASOS

_TRABAJOS = {}
_COLA = queue.Queue()
_LOCK = threading.Lock()


def _ahora():
    return dt.datetime.now().strftime("%H:%M:%S")


def limpiar_nombre(s, maxlen=90):
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(s or "")).strip()
    s = re.sub(r"\s+", " ", s)
    return s[:maxlen].rstrip(" .") or "caso"


class Cancelado(Exception):
    pass


class Trabajo:
    def __init__(self, datos):
        self.id = uuid.uuid4().hex[:10]
        self.creado = dt.datetime.now()
        self.datos = datos
        self.estado = "en_cola"
        self.pasos = []
        self.log = []
        self.resultado = {}
        self.error = ""
        self.cancelar = False
        self.pregunta = None            # lo que el agente le pregunta al usuario (se ve en la web)
        self.respuesta = None
        self._evento = threading.Event()
        caso = datos.get("caso") or {}
        nombre = "{} {} {} - {}".format(
            self.creado.strftime("%Y-%m-%d %H%M"),
            caso.get("expediente") or "SIN-EX",
            caso.get("sigla") or caso.get("sindicato") or "",
            caso.get("empresa") or "",
        )
        self.carpeta = os.path.join(CASOS, limpiar_nombre(nombre, 120))

    # ---------- pasos ----------
    def paso(self, clave, titulo):
        p = {"clave": clave, "titulo": titulo, "estado": "pendiente", "detalle": "", "archivos": []}
        self.pasos.append(p)
        return p

    def set_paso(self, p, estado=None, detalle=None, archivo=None):
        with _LOCK:
            if estado:
                p["estado"] = estado
            if detalle is not None:
                p["detalle"] = detalle
            if archivo:
                p["archivos"].append(archivo)
        if detalle:
            self.anotar(f"[{p['titulo']}] {detalle}")

    def anotar(self, msg):
        with _LOCK:
            self.log.append(f"{_ahora()}  {msg}")
        try:
            print(f"[{self.id}] {msg}", flush=True)
        except Exception:
            pass

    def esperando(self, msg):
        """El paso en curso espera algo del usuario (ej. iniciar sesión): se ve con ✋ en la web."""
        self.anotar(msg)
        with _LOCK:
            for p in self.pasos:
                if p["estado"] == "corriendo":
                    p["estado"] = "esperando"
                    p["detalle"] = msg.replace("✋", "").strip()

    def seguir(self):
        with _LOCK:
            for p in self.pasos:
                if p["estado"] == "esperando":
                    p["estado"] = "corriendo"
                    p["detalle"] = ""

    def preguntar(self, p, texto, campo=None, valor="", botones=None, timeout_s=3600):
        """Frena el paso y le pregunta algo al usuario en la web. Devuelve su respuesta (dict con
        'accion' y, si hay campo, su valor). Mientras espera, el paso se ve con ✋."""
        with _LOCK:
            self.respuesta = None
            self._evento.clear()
            self.pregunta = {"paso": p["clave"], "texto": texto, "campo": campo, "valor": valor,
                             "botones": botones or [{"accion": "seguir", "texto": "Seguir"}]}
            p["estado"] = "esperando"
            p["detalle"] = texto
        self.anotar("✋ " + texto)
        fin = time.time() + timeout_s
        while not self._evento.wait(1):
            if self.cancelar or time.time() > fin:
                break
        with _LOCK:
            r = dict(self.respuesta or {})
            self.pregunta = None
            p["estado"] = "corriendo"
            p["detalle"] = ""
        self.revisar_cancelacion()
        if not r:
            raise RuntimeError("Nadie respondió la pregunta a tiempo.")
        return r

    def responder(self, datos):
        with _LOCK:
            if not self.pregunta:
                return False
            self.respuesta = dict(datos or {})
        self._evento.set()
        return True

    def revisar_cancelacion(self):
        if self.cancelar:
            raise Cancelado("Cancelado por el usuario")

    def to_dict(self):
        with _LOCK:
            return {
                "id": self.id,
                "estado": self.estado,
                "pasos": json.loads(json.dumps(self.pasos)),
                "log": list(self.log[-200:]),
                "resultado": dict(self.resultado),
                "error": self.error,
                "carpeta": self.carpeta,
                "creado": self.creado.isoformat(timespec="seconds"),
                "pregunta": json.loads(json.dumps(self.pregunta)) if self.pregunta else None,
            }


# ---------------------------------------------------------------------------
def crear(datos):
    t = Trabajo(datos)
    with _LOCK:
        _TRABAJOS[t.id] = t
    _COLA.put(t.id)
    return t


def obtener(tid):
    return _TRABAJOS.get(tid)


def listar():
    with _LOCK:
        ts = sorted(_TRABAJOS.values(), key=lambda x: x.creado, reverse=True)
    return [{"id": t.id, "estado": t.estado, "carpeta": t.carpeta, "creado": t.creado.isoformat(timespec="seconds")} for t in ts[:30]]


# ---------------------------------------------------------------------------
def _guardar_archivos(t):
    os.makedirs(t.carpeta, exist_ok=True)
    rutas = {}
    for a in t.datos.get("archivos") or []:
        nombre = limpiar_nombre(a.get("nombre") or "documento.pdf", 150)
        if not nombre.lower().endswith(".pdf"):
            nombre += ".pdf"
        ruta = os.path.join(t.carpeta, nombre)
        with open(ruta, "wb") as f:
            f.write(base64.b64decode(a.get("base64") or ""))
        rutas[a.get("nombre")] = ruta
        rutas[nombre] = ruta
    return rutas


def _resolver_adjuntos(notif, rutas):
    """Devuelve las rutas locales de los PDF que van adjuntos a esta notificación."""
    out = []
    for n in notif.get("adjuntos") or []:
        r = rutas.get(n) or rutas.get(limpiar_nombre(n, 150))
        if not r:
            # por si viene sin .pdf o con otro casing
            for k, v in rutas.items():
                if k and k.lower().replace(".pdf", "") == str(n).lower().replace(".pdf", ""):
                    r = v
                    break
        if r and r not in out:
            out.append(r)
    return out


def _pedir_expediente(t, p, caso, motivo):
    """Le pide al usuario el número de EX correcto. Devuelve el número normalizado,
    o None si elige seguir sin SADE (vincular y pase a mano)."""
    import expediente
    viejo = caso.get("expediente") or ""
    try:
        rep = expediente.partes(viejo)[3]
    except Exception:
        rep = "DGRDT"
    texto = motivo
    while True:
        r = t.preguntar(p, texto, campo="expediente", valor=viejo, botones=[
            {"accion": "seguir", "texto": "Seguir con este número"},
            {"accion": "saltear", "texto": "Seguir sin SADE (lo hago a mano)"}])
        if r.get("accion") == "saltear":
            return None
        try:
            return expediente.normalizar(r.get("expediente"), rep)
        except ValueError as e:
            texto = f"{e} Escribilo de nuevo:"


def _cambiar_expediente(t, caso, mails, nuevo):
    """Reemplaza el EX equivocado por el correcto en el caso (agenda, SADE) y en los mails que faltan enviar."""
    import expediente
    viejo = caso.get("expediente") or ""
    rx = None
    try:
        rx = expediente.patron(viejo)
    except Exception:
        pass
    caso["expediente"] = nuevo
    if rx:
        for k in ("texto_interno", "ref_gedo"):
            if caso.get(k):
                caso[k] = rx.sub(nuevo, caso[k])
        for n in mails:
            for k in ("asunto", "cuerpo"):
                if n.get(k):
                    n[k] = rx.sub(nuevo, n[k])
    t.resultado["expediente_corregido"] = {"antes": viejo, "ahora": nuevo}
    t.anotar(f"Expediente corregido a mano: {viejo} → {nuevo}")


def _ejecutar(t, pw_estado):
    """Corre todos los pasos de un trabajo. pw_estado = dict con la conexión de Playwright."""
    import outlook
    import gedo
    import agenda
    import impresion

    cfg = config.cargar()
    prueba = bool(t.datos.get("prueba")) or bool(cfg.get("modo_prueba"))
    pasos_ok = t.datos.get("pasos") or {}
    caso = t.datos.get("caso") or {}
    notifs = [n for n in (t.datos.get("notificaciones") or []) if n]
    mails = [n for n in notifs if "TAD" not in str(n.get("via", "")).upper()]
    tads = [n for n in notifs if "TAD" in str(n.get("via", "")).upper()]

    if prueba and not cfg.get("correo_prueba"):
        raise RuntimeError("Modo prueba activo pero falta el 'correo de prueba' en la configuración.")

    # armar la lista de pasos (para que la web los muestre desde el principio)
    p_arch = t.paso("archivos", "Preparar carpeta del caso")
    plan = []
    # Antes de enviar nada: ¿el EX existe en SADE? Si el acta lo trae mal, se corrige acá
    # (en el mail, la agenda y SADE) y la notificación sigue sola.
    hace_ee = bool(mails) and pasos_ok.get("gedo", True) and pasos_ok.get("vincular", cfg.get("vincular", True))
    if caso.get("expediente") and not prueba and pasos_ok.get("verificar_ex", hace_ee):
        plan.append(("verificar_ex", 0, t.paso("verificar_ex", "Verificar el expediente en SADE")))
    for i, n in enumerate(mails):
        sufijo = f" ({n.get('para')})" if len(mails) > 1 and n.get("para") else ""
        if pasos_ok.get("mail", True):
            plan.append(("mail", i, t.paso(f"mail{i}", "Enviar mail" + sufijo)))
            if pasos_ok.get("pdf_mail", True):
                plan.append(("pdf", i, t.paso(f"pdf{i}", "PDF del mail enviado" + sufijo)))
                if pasos_ok.get("gedo", True):
                    plan.append(("gedo", i, t.paso(f"gedo{i}", "GEDO: Notificación fehaciente" + sufijo)))
    for i, n in enumerate(tads):
        plan.append(("tad", i, t.paso(f"tad{i}", "TAD (empresa sin email)")))
    if mails and pasos_ok.get("gedo", True) and pasos_ok.get("vincular", cfg.get("vincular", True)):
        plan.append(("vincular", 0, t.paso("vincular", "Expediente: vincular y hacer el pase")))
    if pasos_ok.get("imprimir", cfg.get("imprimir")):
        plan.append(("imprimir", 0, t.paso("imprimir", "Imprimir")))
    url_planilla = cfg.get("agenda_excel_prueba") if prueba else cfg.get("agenda_excel")
    url_word = cfg.get("agenda_word_prueba") if prueba else cfg.get("agenda_word")
    if pasos_ok.get("agenda_excel", True) and url_planilla:
        plan.append(("excel", 0, t.paso("excel", "Agendar en la planilla" + (" (copia de prueba)" if prueba else ""))))
    if pasos_ok.get("agenda_word", True) and url_word:
        plan.append(("word", 0, t.paso("word", "Agendar en el Word de seguimiento" + (" (copia de prueba)" if prueba else ""))))

    # 1) carpeta + PDFs
    t.set_paso(p_arch, "corriendo")
    rutas = _guardar_archivos(t)
    t.set_paso(p_arch, "ok", f"{len(set(rutas.values()))} PDF guardados en la carpeta del caso")
    t.resultado["carpeta"] = t.carpeta
    if prueba:
        t.anotar(f"⚠️ MODO PRUEBA: los mails van solo a {cfg.get('correo_prueba')}, GEDO se saltea y la agenda va a las copias de prueba.")

    estado_mail = {}   # i -> dict(enviado, pdf, gedo_numero, gedo_pdf)
    finales = []       # PDFs finales para imprimir
    sin_sade = False   # el usuario eligió seguir sin SADE (vincular y pase a mano)

    for tipo, i, p in plan:
        t.revisar_cancelacion()
        t.set_paso(p, "corriendo")
        try:
            if tipo == "verificar_ex":
                import expediente
                while True:
                    page = pw_estado["pagina"]("expediente")
                    try:
                        expediente.verificar(page, caso["expediente"], t)
                        t.set_paso(p, "ok", f"{caso['expediente']} existe en SADE")
                        break
                    except expediente.NoExiste:
                        nuevo = _pedir_expediente(t, p, caso, f"{caso['expediente']} no existe en SADE "
                                                  "(¿está mal escrito en el acta?). Escribí el número correcto:")
                        if not nuevo:
                            sin_sade = True
                            t.set_paso(p, "omitido", "Seguís sin SADE: la vinculación y el pase quedan para hacer a mano.")
                            break
                        _cambiar_expediente(t, caso, mails, nuevo)
            elif tipo == "mail":
                n = mails[i]
                adj = _resolver_adjuntos(n, rutas)
                faltan = [a for a in (n.get("adjuntos") or []) if not any(os.path.basename(x).lower() == limpiar_nombre(a, 150).lower() for x in adj)]
                if faltan:
                    t.anotar("Aviso: no encontré estos adjuntos entre los PDF subidos: " + ", ".join(faltan))
                page = pw_estado["pagina"]("outlook")
                info = outlook.enviar(page, n, adj, cfg, prueba, t)
                estado_mail[i] = {"enviado": True, **info}
                t.set_paso(p, "ok", "Enviado a " + ", ".join(info.get("para") or []) + (f" · {len(adj)} adjunto/s" if adj else ""))
            elif tipo == "pdf":
                if not estado_mail.get(i, {}).get("enviado"):
                    t.set_paso(p, "omitido", "No se envió el mail")
                    continue
                n = mails[i]
                page = pw_estado["pagina"]("outlook")
                ruta_pdf = outlook.pdf_del_enviado(page, n, estado_mail[i], t.carpeta, prueba, t)
                estado_mail[i]["pdf"] = ruta_pdf
                t.set_paso(p, "ok", os.path.basename(ruta_pdf), archivo=ruta_pdf)
            elif tipo == "gedo":
                pdf_mail = estado_mail.get(i, {}).get("pdf")
                if not pdf_mail:
                    t.set_paso(p, "omitido", "No hay PDF del mail")
                    continue
                if prueba:
                    t.set_paso(p, "omitido", "Modo prueba: en GEDO no se genera nada (referencia: " + (caso.get("ref_gedo") or "-") + ")")
                    continue
                page = pw_estado["pagina"]("gedo")
                ref = caso.get("ref_gedo") or ""
                res = gedo.notificacion_fehaciente(page, ref, pdf_mail, t.carpeta, cfg, prueba, t)
                estado_mail[i].update(res)
                if res.get("pdf"):
                    finales.append(res["pdf"])
                t.resultado.setdefault("gedo", []).append(res)
                t.set_paso(p, "ok", (res.get("numero") or "Documento preparado (sin firmar)"), archivo=res.get("pdf"))
            elif tipo == "tad":
                t.set_paso(p, "omitido", "La notificación por TAD todavía se hace a mano (el bloque está listo para copiar).")
            elif tipo == "vincular":
                notifs_gedo = [x.get("numero") for x in estado_mail.values() if x.get("numero")]
                numeros = ([caso["documento_notificado"]] if caso.get("documento_notificado") else []) + notifs_gedo
                if prueba:
                    t.set_paso(p, "omitido", "Modo prueba: no se vincula ni se hace el pase (" + (", ".join(numeros) or "-") + ")")
                    continue
                if not notifs_gedo:
                    t.set_paso(p, "omitido", "No hay notificación firmada en GEDO para vincular")
                    continue
                if sin_sade:
                    t.set_paso(p, "omitido", "A mano (elegiste seguir sin SADE): vincular " + ", ".join(numeros))
                    continue
                if not caso.get("expediente"):
                    raise RuntimeError("Falta el número de expediente para vincular.")
                import expediente
                hechos = None
                while True:
                    page = pw_estado["pagina"]("expediente")
                    try:
                        hechos = expediente.vincular_y_pase(page, caso["expediente"], numeros, cfg, t)
                        break
                    except expediente.NoExiste:
                        nuevo = _pedir_expediente(t, p, caso, f"{caso['expediente']} no existe en SADE. "
                                                  "Escribí el número correcto para vincular y hacer el pase:")
                        if not nuevo:
                            break
                        _cambiar_expediente(t, caso, [], nuevo)
                if hechos is None:
                    t.set_paso(p, "omitido", "A mano: vincular " + ", ".join(numeros))
                    continue
                t.resultado["vinculados"] = hechos
                t.set_paso(p, "ok", "Vinculados: " + ", ".join(hechos) +
                           f" · pase a {cfg.get('pase_reparticion')} / {cfg.get('pase_sector')}")
            elif tipo == "imprimir":
                docs = finales or [x.get("pdf") for x in estado_mail.values() if x.get("pdf")]
                if not docs:
                    t.set_paso(p, "omitido", "No hay documentos para imprimir")
                    continue
                impresion.imprimir(docs, pw_estado, t)
                t.set_paso(p, "ok", f"{len(docs)} documento/s enviados a la impresora")
            elif tipo == "excel":
                if "docs.google.com" in url_planilla:
                    import agenda_google
                    det = agenda_google.agendar_planilla(pw_estado["contexto"](), url_planilla, t.datos, estado_mail, t)
                else:
                    det = agenda.agendar_excel(url_planilla, t.datos, estado_mail, t)
                t.set_paso(p, "omitido" if det.startswith("No corresponde") else "ok", det)
            elif tipo == "word":
                if "google.com" in url_word:
                    import agenda_google
                    det = agenda_google.agendar_seguimiento(pw_estado["contexto"](), url_word, t.datos, estado_mail, t)
                else:
                    det = agenda.agendar_word(url_word, t.datos, estado_mail, t)
                t.set_paso(p, "omitido" if det.startswith("El caso no tiene") else "ok", det)
        except Cancelado:
            t.set_paso(p, "error", "Cancelado")
            raise
        except Exception as e:
            t.set_paso(p, "error", str(e)[:400])
            t.anotar(traceback.format_exc()[-1500:])
            # captura de pantalla para diagnosticar
            try:
                pg = pw_estado.get("ultima")
                if pg:
                    shot = os.path.join(t.carpeta, f"error_{p['clave']}.png")
                    pg.screenshot(path=shot, full_page=False)
                    p["archivos"].append(shot)
            except Exception:
                pass
            # si falla el mail, no seguimos con lo que depende de él
            if tipo in ("mail", "pdf"):
                continue
    resumen = os.path.join(t.carpeta, "resumen.json")
    with open(resumen, "w", encoding="utf-8") as f:
        json.dump({"caso": caso, "pasos": t.pasos, "resultado": t.resultado, "log": t.log}, f, ensure_ascii=False, indent=2)


def iniciar_trabajador():
    hilo = threading.Thread(target=_bucle, name="trabajador", daemon=True)
    hilo.start()
    return hilo


def _bucle():
    from playwright.sync_api import sync_playwright
    import navegador

    estado = {"pw": None, "browser": None, "ctx": None, "ultima": None}

    def asegurar():
        import outlook
        if estado["pw"] is None:
            estado["pw"] = sync_playwright().start()
        b = estado.get("browser")
        if b is None or not b.is_connected():
            estado["browser"], estado["ctx"] = navegador.conectar(estado["pw"])
            outlook.preparar(estado["ctx"])   # intercepta la impresión de Outlook (para el PDF)
        return estado["ctx"]

    def pagina(cual):
        ctx = asegurar()
        pg = navegador.pestania(ctx, cual)
        estado["ultima"] = pg
        return pg

    estado["pagina"] = pagina
    estado["contexto"] = asegurar

    def soltar():
        """Se desconecta del Chrome (sin cerrarlo) para no quedar 'enganchado' mientras inicias sesión."""
        try:
            if estado.get("browser"):
                estado["browser"].close()
        except Exception:
            pass
        estado["browser"] = None

    def login_en_curso():
        import urllib.request
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{navegador.PUERTO_CDP}/json/list", timeout=2) as r:
                tabs = json.loads(r.read().decode("utf-8"))
            return any(re.search(r"identidad-gcaba|cas-pr|cas\.buenosaires|accounts\.google|login\.microsoftonline|login\.live",
                                 x.get("url", "")) for x in tabs if x.get("type") == "page")
        except Exception:
            return True

    while True:
        try:
            tid = _COLA.get(timeout=300)
        except queue.Empty:
            # cada 5 minutos sin trabajo: mantener viva la sesión de GEDO
            try:
                import gedo
                if navegador.chrome_activo() and not login_en_curso():
                    gedo.mantener_sesion(asegurar())
            except Exception:
                pass
            soltar()
            continue
        t = _TRABAJOS.get(tid)
        if not t:
            continue
        if t.cancelar:
            t.estado = "cancelado"
            continue
        t.estado = "corriendo"
        try:
            _ejecutar(t, estado)
            errores = [p for p in t.pasos if p["estado"] == "error"]
            t.estado = "con_errores" if errores else "ok"
        except Cancelado:
            t.estado = "cancelado"
        except Exception as e:
            t.estado = "error"
            t.error = str(e)
            t.anotar("ERROR: " + traceback.format_exc()[-2000:])
        finally:
            soltar()
