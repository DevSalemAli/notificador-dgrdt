"""
Navegador propio del Notificador (Chrome con un perfil separado).

- Se abre un Chrome "normal" con un perfil solo para el notificador
  (ahí iniciás sesión UNA vez en Outlook web y en GEDO, y queda guardada).
- El agente se conecta a ese Chrome por el puerto de depuración (CDP)
  usando Playwright, y desde ahí maneja las pestañas como si fueras vos.
"""
import glob
import os
import subprocess
import time
import urllib.request

from rutas import BASE, PERFIL_CHROME

PUERTO_CDP = int(os.environ.get("NOTIFICADOR_CDP") or 9333)

URL_OUTLOOK = "https://outlook.office.com/mail/"
URL_GEDO = "https://eut-pr.apps.buenosaires.gob.ar/gedo-web/"

_CHROME = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
_EDGE = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


WINDOWS = os.name == "nt"
if not WINDOWS:   # servidor Linux (nube)
    _CHROME = ["/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/opt/google/chrome/chrome"]
    _EDGE = []


def _chromium_propio():
    """Chromium de Playwright: el del servidor en la nube, o el que se usa cuando la empresa
    bloquea el control de Chrome y Edge (a ese navegador no le aplican sus políticas)."""
    carpetas = [os.path.join(BASE, "navegador"), os.path.expandvars(r"%LOCALAPPDATA%\ms-playwright"),
                os.path.expanduser("~/.cache/ms-playwright")]
    for d in carpetas:
        exes = glob.glob(os.path.join(d, "chromium-*", "chrome-win*", "chrome.exe")) + \
               glob.glob(os.path.join(d, "chromium-*", "chrome-linux*", "chrome"))
        for exe in sorted(exes, reverse=True):
            return exe
    return None


def candidatos():
    """[(nombre, ejecutable, perfil)] en orden de preferencia: el que anduvo la última vez,
    o el elegido en Ajustes ("navegador"), y después Chrome → Edge → Chromium propio."""
    import config
    cfg = config.cargar()
    grupos = {
        "chrome": [c for c in _CHROME if os.path.exists(c)][:1],
        "edge": [c for c in _EDGE if os.path.exists(c)][:1],
        "chromium": [c for c in [_chromium_propio()] if c],
    }
    pref = (cfg.get("navegador") or "auto").lower()
    orden = [pref] if pref in grupos else ([cfg.get("navegador_usado")] if cfg.get("navegador_usado") in grupos else [])
    orden += [g for g in ("chrome", "edge", "chromium") if g not in orden]
    # cada navegador con su propio perfil (el de Chrome es el de siempre)
    perfiles = {"chrome": PERFIL_CHROME, "edge": PERFIL_CHROME + "-edge", "chromium": PERFIL_CHROME + "-chromium"}
    return [(g, exe, perfiles[g]) for g in orden for exe in grupos[g]]


def ejecutable_chrome():
    c = candidatos()
    if not c:
        raise RuntimeError("No encontré Google Chrome, Microsoft Edge ni el navegador propio del notificador.")
    return c[0][1]


def pausa(page, segundos):
    """Espera dejando que Playwright procese lo que pasa en el navegador.

    OJO: con time.sleep, Playwright no se entera de nada: page.url y la lista de pestañas
    quedan congeladas (por eso el agente no veía que ya habías iniciado sesión en SADE)."""
    try:
        if page is not None and not page.is_closed():
            page.wait_for_timeout(int(segundos * 1000))
            return
    except Exception:
        pass
    time.sleep(segundos)


def avisar_espera(t, msg):
    """Marca el paso como «esperando al usuario» (✋ en la web); si no se puede, solo lo anota."""
    (getattr(t, "esperando", None) or t.anotar)(msg)


def fin_espera(t):
    f = getattr(t, "seguir", None)
    if f:
        f()


def chrome_activo():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO_CDP}/json/version", timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def abrir_chrome(urls=None):
    """Abre la ventana del notificador (si no está abierta) y espera a que responda.
    Si un navegador no deja que el agente lo maneje (política de la empresa), prueba el siguiente."""
    if chrome_activo():
        return
    cands = candidatos()
    if not cands:
        raise RuntimeError("No encontré Google Chrome, Microsoft Edge ni el navegador propio del notificador.")
    probados = []
    for nombre, exe, perfil in cands:
        os.makedirs(perfil, exist_ok=True)
        args = [
            exe,
            f"--remote-debugging-port={PUERTO_CDP}",
            f"--user-data-dir={perfil}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-session-crashed-bubble",
            "--hide-crash-restore-bubble",
            "--disable-features=ChromeWhatsNewUI,PrivacySandboxSettings4",
            "--start-maximized",
        ]
        if not WINDOWS:   # servidor: pantalla virtual, en castellano, sin llavero del sistema
            args += ["--lang=es-419", "--password-store=basic", "--window-position=0,0", "--window-size=1600,1000"]
        args += list(urls or [])
        if WINDOWS:
            # DETACHED: el navegador sigue abierto aunque se cierre el agente
            flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            proc = subprocess.Popen(args, creationflags=flags, close_fds=True)
        else:
            proc = subprocess.Popen(args, start_new_session=True, close_fds=True,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(40):
            if chrome_activo():
                _recordar(nombre)
                return
            time.sleep(0.5)
        probados.append(nombre)
        try:
            proc.kill()      # no respondió: probablemente la empresa bloquea que lo maneje un programa
        except Exception:
            pass
    raise RuntimeError("Ningún navegador dejó que el agente lo maneje (" + ", ".join(probados) +
                       "). Si es la PC del trabajo, volvé a correr el instalador: instala un navegador propio.")


def _recordar(nombre):
    try:
        import config
        if config.cargar().get("navegador_usado") != nombre:
            config.guardar({"navegador_usado": nombre})
    except Exception:
        pass


def abrir_url(url):
    """Abre (o enfoca) una pestaña con esa URL en el Chrome del notificador."""
    if not chrome_activo():
        abrir_chrome([url])
        return
    import json
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PUERTO_CDP}/json/list", timeout=2) as r:
            for t in json.loads(r.read().decode("utf-8")):
                if t.get("type") == "page" and t.get("url", "").startswith(url):
                    urllib.request.urlopen(f"http://127.0.0.1:{PUERTO_CDP}/json/activate/{t['id']}", timeout=2)
                    return
    except Exception:
        pass
    req = urllib.request.Request(f"http://127.0.0.1:{PUERTO_CDP}/json/new?{url}", method="PUT")
    urllib.request.urlopen(req, timeout=3)


def conectar(pw):
    """Devuelve (browser, context) del Chrome del notificador, abriéndolo si hace falta."""
    abrir_chrome()
    browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{PUERTO_CDP}")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return browser, ctx


_DOMINIOS = {
    "outlook": ("outlook.office.com", "outlook.cloud.microsoft", "outlook.office365.com", "login.microsoftonline.com"),
    "gedo": ("eut-pr.apps.buenosaires.gob.ar/gedo-web", "identidad-gcaba.apps.buenosaires.gob.ar",
             "cas-pr.apps.buenosaires.gob.ar", "cas.buenosaires.gob.ar"),
    "expediente": ("eue-pr.apps.buenosaires.gob.ar",),
}
URL_EE = "https://eue-pr.apps.buenosaires.gob.ar/expedientes-web/"
_INICIO = {"outlook": URL_OUTLOOK, "gedo": URL_GEDO, "expediente": URL_EE}


def pestania(ctx, cual):
    """Busca la pestaña de Outlook o de GEDO; si no existe la abre."""
    for pg in ctx.pages:
        try:
            if any(d in pg.url for d in _DOMINIOS[cual]):
                pg.bring_to_front()
                return pg
        except Exception:
            pass
    pg = ctx.new_page()
    pg.goto(_INICIO[cual], wait_until="domcontentloaded")
    return pg


if __name__ == "__main__":
    abrir_chrome([URL_OUTLOOK, URL_GEDO])
    print("Chrome del notificador abierto en el puerto", PUERTO_CDP)
