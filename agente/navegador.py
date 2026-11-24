"""
Navegador propio del Notificador (Chrome con un perfil separado).

- Se abre un Chrome "normal" con un perfil solo para el notificador
  (ahí iniciás sesión UNA vez en Outlook web y en GEDO, y queda guardada).
- El agente se conecta a ese Chrome por el puerto de depuración (CDP)
  usando Playwright, y desde ahí maneja las pestañas como si fueras vos.
"""
import os
import subprocess
import time
import urllib.request

from rutas import PERFIL_CHROME

PUERTO_CDP = 9333

URL_OUTLOOK = "https://outlook.office.com/mail/"
URL_GEDO = "https://eut-pr.apps.buenosaires.gob.ar/gedo-web/"

_CANDIDATOS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]


def ejecutable_chrome():
    for c in _CANDIDATOS:
        if os.path.exists(c):
            return c
    raise RuntimeError("No encontré Google Chrome ni Microsoft Edge instalados.")


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
    """Abre el Chrome del notificador (si no está abierto) y espera a que responda."""
    if chrome_activo():
        return
    os.makedirs(PERFIL_CHROME, exist_ok=True)
    args = [
        ejecutable_chrome(),
        f"--remote-debugging-port={PUERTO_CDP}",
        f"--user-data-dir={PERFIL_CHROME}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--hide-crash-restore-bubble",
        "--disable-features=ChromeWhatsNewUI,PrivacySandboxSettings4",
        "--start-maximized",
    ] + list(urls or [])
    # DETACHED: el Chrome sigue abierto aunque se cierre el agente
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(args, creationflags=flags, close_fds=True)
    for _ in range(60):
        if chrome_activo():
            return
        time.sleep(0.5)
    raise RuntimeError("Chrome no respondió en el puerto de depuración.")


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
