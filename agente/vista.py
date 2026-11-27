"""
Ventana remota por HTTP: ver y manejar el navegador del agente desde la web, con capturas
de pantalla y clics (sin websockets, así pasa por cualquier proxy: Vercel, redes de oficina).

Playwright (modo sync) no se puede usar desde varios hilos, así que un hilo propio de la
"vista" tiene su conexión al navegador y atiende los pedidos en orden.
"""
import queue
import threading

_COLA = queue.Queue()
_HILO = None
_LOCK = threading.Lock()

# teclas permitidas desde la web
TECLAS = {"Enter": "Enter", "Tab": "Tab", "ShiftTab": "Shift+Tab", "Backspace": "Backspace",
          "Escape": "Escape", "Delete": "Delete", "ArrowUp": "ArrowUp", "ArrowDown": "ArrowDown",
          "ArrowLeft": "ArrowLeft", "ArrowRight": "ArrowRight", "SelectAll": "Control+a"}


def _trabajador():
    from playwright.sync_api import sync_playwright
    import navegador
    pw = sync_playwright().start()
    browser = None
    while True:
        t = _COLA.get()
        try:
            if browser is None or not browser.is_connected():
                navegador.abrir_chrome()
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{navegador.PUERTO_CDP}")
            ctx = browser.contexts[0] if browser.contexts else browser.new_context()
            t["res"]["valor"] = t["fn"](ctx)
        except Exception as e:
            t["res"]["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            try:
                browser.close()      # conectado por CDP: solo se desconecta, el navegador sigue abierto
            except Exception:
                pass
            browser = None
        finally:
            t["res"]["listo"].set()


def _ejecutar(fn, timeout=30):
    global _HILO
    with _LOCK:
        if _HILO is None or not _HILO.is_alive():
            _HILO = threading.Thread(target=_trabajador, name="vista", daemon=True)
            _HILO.start()
    res = {"listo": threading.Event()}
    _COLA.put({"fn": fn, "res": res})
    if not res["listo"].wait(timeout):
        raise RuntimeError("La ventana remota no respondió a tiempo")
    if "error" in res:
        raise RuntimeError(res["error"])
    return res.get("valor")


def _paginas(ctx):
    """[(id estable de la pestaña, página)] en el orden del navegador."""
    out = []
    for pg in ctx.pages:
        try:
            s = ctx.new_cdp_session(pg)
            tid = s.send("Target.getTargetInfo")["targetInfo"]["targetId"]
            s.detach()
        except Exception:
            tid = f"p{id(pg)}"
        out.append((tid, pg))
    return out


def _pagina(ctx, tid):
    ps = _paginas(ctx)
    for t, pg in ps:
        if t == tid:
            return pg
    if ps:
        return ps[-1][1]
    return ctx.new_page()


def pestanias():
    def f(ctx):
        res = []
        for t, pg in _paginas(ctx):
            try:
                titulo = pg.title()
            except Exception:
                titulo = ""
            res.append({"id": t, "titulo": (titulo or pg.url)[:90], "url": pg.url[:300]})
        return res
    return _ejecutar(f)


def _a_jpeg(png, calidad, ancho_max=1400):
    """PNG → JPEG liviano (con Pillow). OJO: no se le pide JPEG al navegador porque el Chromium
    para ARM del servidor arma esas imágenes rotas; en PNG salen bien."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(png)).convert("RGB")
        if im.width > ancho_max:
            im = im.resize((ancho_max, round(im.height * ancho_max / im.width)), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=max(20, min(int(calidad), 85)), optimize=True)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return png, "image/png"


def captura(tid, calidad=55):
    def f(ctx):
        pg = _pagina(ctx, tid)
        ancho, alto = pg.evaluate("() => [window.innerWidth, window.innerHeight]")
        img, tipo = _a_jpeg(pg.screenshot(type="png", timeout=20000), calidad)
        return {"img": img, "tipo": tipo, "ancho": ancho, "alto": alto}
    return _ejecutar(f)


def accion(tid, d):
    tipo = str(d.get("tipo") or "")

    def f(ctx):
        if tipo == "nueva":
            pg = ctx.new_page()
            url = str(d.get("url") or "").strip()
            if url:
                pg.goto(url if "://" in url else "https://" + url, wait_until="domcontentloaded")
            pg.bring_to_front()
            return True
        pg = _pagina(ctx, tid)
        if tipo == "click":
            pg.bring_to_front()
            pg.mouse.click(float(d["x"]), float(d["y"]))
        elif tipo == "texto":
            pg.keyboard.type(str(d.get("texto") or ""), delay=12)
        elif tipo == "tecla":
            pg.keyboard.press(TECLAS.get(str(d.get("tecla")), "Enter"))
        elif tipo == "scroll":
            pg.mouse.move(float(d.get("x", 400)), float(d.get("y", 300)))
            pg.mouse.wheel(0, float(d.get("dy", 400)))
        elif tipo == "ir":
            url = str(d.get("url") or "").strip()
            pg.goto(url if "://" in url else "https://" + url, wait_until="domcontentloaded")
        elif tipo == "atras":
            pg.go_back(wait_until="domcontentloaded")
        elif tipo == "recargar":
            pg.reload(wait_until="domcontentloaded")
        elif tipo == "cerrar":
            if len(ctx.pages) > 1:
                pg.close()
        elif tipo == "activar":
            pg.bring_to_front()
        else:
            raise ValueError("acción desconocida")
        return True
    return _ejecutar(f, timeout=45)
