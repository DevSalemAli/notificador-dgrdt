"""
Outlook web (OWA): envía el mail desde la casilla compartida y guarda el PDF del enviado.

Hace lo mismo que harías a mano en el Chrome del notificador:
  1) Redactar (Para / Asunto), elegir "De: relaciones.laborales@…".
  2) Pegar el cuerpo con negritas + tu firma de texto (reemplaza la firma automática con imagen).
  3) Adjuntar los PDF que dicen "Se adjunta…" (Adjuntar archivo → Examinar este equipo).
  4) Enviar.
  5) Ir a "Elementos enviados", abrir el mail y usar "Imprimir" de Outlook; la impresión se
     intercepta y se guarda como PDF (mismo formato que Ctrl+P → Guardar como PDF).
"""
import datetime as dt
import html as _html
import os
import re
import time
import urllib.parse

OWA = "https://outlook.cloud.microsoft"
T_LARGO = 60_000

RX_CUERPO = re.compile(r"^(Cuerpo del mensaje|Message body)", re.I)
RX_ENVIAR = re.compile(r"^(Enviar|Send)$", re.I)


# Intercepta window.print() de Outlook (que imprime desde un iframe) y guarda el HTML a imprimir.
# Solo actúa cuando el agente prende la bandera; si imprimís vos a mano, funciona normal.
INIT_PRINT = r"""(() => { try {
  const orig = window.print ? window.print.bind(window) : null;
  const fake = function(){
    let top = null; try { top = window.top; } catch(e) {}
    let activo = false; try { activo = !!(window.__notifInterceptPrint || (top && top.__notifInterceptPrint)); } catch(e) {}
    if (activo) {
      const dest = top || window;
      try { dest.__printHTML = document.documentElement.outerHTML; } catch(e) {}
      try { dest.__printRequested = (dest.__printRequested || 0) + 1; } catch(e) {}
      return;
    }
    return orig && orig();
  };
  Object.defineProperty(window, 'print', { value: fake, writable: true, configurable: true });
} catch(e) {} })();"""


def preparar(ctx):
    """Se llama una vez por conexión al Chrome del notificador."""
    ctx.add_init_script(INIT_PRINT)


# ----------------------------------------------------------------- utilidades
def cuerpo_html(texto):
    """Texto con **negritas** -> HTML con un <div> por renglón (como lo arma Outlook)."""
    out = []
    for ln in str(texto or "").replace("\r\n", "\n").split("\n"):
        if not ln.strip():
            out.append("<div><br></div>")
            continue
        h = _html.escape(ln)
        h = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", h)
        h = re.sub(r"(https?://[^\s<]+)", r'<a href="\1">\1</a>', h)
        out.append(f"<div>{h}</div>")
    return "".join(out)


def cuerpo_plano(texto):
    return re.sub(r"\*\*(.+?)\*\*", r"\1", str(texto or ""))


def _esperar_sesion(page, t, timeout_s=900):
    """Si Outlook pide iniciar sesión, espera a que el usuario lo haga en la ventana."""
    avisado = False
    fin = time.time() + timeout_s
    while time.time() < fin:
        u = page.url
        if re.search(r"outlook\.(office|office365|cloud\.microsoft)[^/]*/mail", u):
            return
        if not avisado and ("login." in u or "microsoftonline" in u):
            t.anotar("✋ Outlook pide iniciar sesión: hacelo en la ventana del notificador (espero).")
            avisado = True
        time.sleep(1)
    raise RuntimeError("Outlook no inició sesión a tiempo.")


def _base(page):
    m = re.match(r"(https://outlook\.[^/]+)", page.url or "")
    return m.group(1) if m else OWA


def _limpio(s):
    s = str(s or "").replace("/", "-")          # 24/11/2026 -> 24-11-2026 (no se pierde la fecha)
    return re.sub(r'[<>:"\\|?*\x00-\x1f]', "", s).strip()


# ----------------------------------------------------------------- envío
def enviar(page_outlook, notif, adjuntos, cfg, prueba, t):
    ctx = page_outlook.context
    _esperar_sesion(page_outlook, t)
    base = _base(page_outlook)

    dest = [cfg.get("correo_prueba")] if prueba else list(notif.get("destinatarios") or [])
    dest = [d.strip() for d in dest if d and "@" in d]
    if not dest:
        raise RuntimeError("No hay destinatarios con email válido.")
    asunto = ("[PRUEBA] " if prueba else "") + (notif.get("asunto") or "").strip()

    url = (f"{base}/mail/deeplink/compose?to=" + urllib.parse.quote(";".join(dest)) +
           "&subject=" + urllib.parse.quote(asunto))
    page = ctx.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded")
        _esperar_sesion(page, t)
        cuerpo = page.get_by_role("textbox", name=RX_CUERPO).first
        cuerpo.wait_for(state="visible", timeout=T_LARGO)
        time.sleep(2.5)  # que Outlook termine de cargar la firma automática

        remitente = (cfg.get("remitente") or "").strip()
        if remitente:
            _elegir_remitente(page, remitente, t)

        texto = (notif.get("cuerpo") or "").rstrip()
        firma = (cfg.get("firma") or "").strip()
        if firma:
            texto = texto + "\n\n" + firma          # firma de texto en lugar de la firma automática
        else:
            texto = texto + "\n"                    # sin firma propia: queda la de Outlook abajo
        _pegar_cuerpo(page, cuerpo, texto, t, reemplazar_todo=bool(firma))

        if adjuntos:
            _adjuntar(page, adjuntos, t)

        _verificar_antes_de_enviar(page, dest, asunto, remitente, adjuntos, texto, t)

        hora_envio = dt.datetime.now()
        page.get_by_role("button", name=RX_ENVIAR).first.click()
        _esperar_envio(page, t)
        t.anotar("Mail enviado: " + asunto)
        return {"para": dest, "asunto": asunto, "hora": hora_envio.isoformat(timespec="seconds"),
                "adjuntos": [os.path.basename(a) for a in adjuntos]}
    finally:
        try:
            if not page.is_closed():
                page.close()
        except Exception:
            pass


def _elegir_remitente(page, remitente, t):
    de = page.locator("button[id$='_FROM']").first
    try:
        de.wait_for(state="visible", timeout=10000)
    except Exception:
        raise RuntimeError("No veo el campo «De» en la redacción (activá 'Mostrar siempre De' en Outlook).")
    if remitente.lower() in (de.get_attribute("aria-label") or "").lower():
        return
    de.click()
    time.sleep(0.8)
    op = page.get_by_role("menuitemradio", name=re.compile(re.escape(remitente), re.I)).first
    if op.count():
        op.click()
    else:
        otra = page.get_by_role("menuitem", name=re.compile(r"(Otra dirección|Other email)", re.I)).first
        otra.click()
        time.sleep(0.8)
        campo = page.locator("input[aria-label^='De'], input[aria-label^='From']").first
        campo.fill(remitente)
        campo.press("Enter")
    time.sleep(1.2)
    if remitente.lower() not in (de.get_attribute("aria-label") or "").lower():
        raise RuntimeError("No pude poner «De: " + remitente + "».")
    t.anotar("De: " + remitente)


def _pegar_cuerpo(page, cuerpo, texto, t, reemplazar_todo=True):
    """Pega el cuerpo con negritas. Con reemplazar_todo reemplaza también la firma automática;
    si no, lo pega al principio y deja la firma de Outlook debajo."""
    h = cuerpo_html(texto)
    plano = cuerpo_plano(texto)
    cuerpo.click()
    time.sleep(0.3)
    page.evaluate(
        """([el, html, plano, todo]) => {
            el.focus();
            const s = window.getSelection(); const r = document.createRange();
            if (todo) { r.selectNodeContents(el); } else { r.setStart(el, 0); r.collapse(true); }
            s.removeAllRanges(); s.addRange(r);
            const dt = new DataTransfer(); dt.setData('text/html', html); dt.setData('text/plain', plano);
            el.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
        }""",
        [cuerpo.element_handle(), h, plano, reemplazar_todo],
    )
    time.sleep(1.5)
    actual = cuerpo.inner_text()
    primera = next((l for l in plano.split("\n") if l.strip()), "")[:50]
    ultima = next((l for l in reversed(plano.split("\n")) if l.strip()), "")[:40]
    if primera and primera not in actual:
        raise RuntimeError("No pude escribir el cuerpo del mail en Outlook.")
    if ultima and ultima not in actual:
        raise RuntimeError("El cuerpo quedó incompleto en Outlook.")
    if reemplazar_todo and cuerpo.evaluate("e => !!e.querySelector('[data-signature-name] img, #Signature img')"):
        t.anotar("Aviso: quedó la firma automática de Outlook además de la de texto.")


def _adjuntar(page, rutas, t):
    """Adjuntar archivo → Examinar este equipo; si el botón está escondido, usa el selector oculto."""
    nombres = [os.path.basename(r) for r in rutas]
    hecho = False
    boton = page.get_by_role("button", name="Adjuntar archivo", exact=True).first
    if boton.count() and boton.is_visible():
        boton.click()
        time.sleep(0.8)
        examinar = page.get_by_role("menuitem", name=re.compile(r"Examinar este equipo|Browse this computer", re.I)).first
        if examinar.count():
            with page.expect_file_chooser(timeout=15000) as fc:
                examinar.click()
            fc.value.set_files(rutas)
            hecho = True
    if not hecho:
        inp = page.locator("input[type=file][data-testid='local-computer-filein']:not([accept='image/*'])").first
        if not inp.count():
            raise RuntimeError("No encontré cómo adjuntar archivos en Outlook.")
        inp.set_input_files(rutas)
    # esperar que cada adjunto aparezca ya subido (con su tamaño en KB/MB)
    fin = time.time() + 120
    while time.time() < fin:
        listos = 0
        for n in nombres:
            chip = page.get_by_role("option", name=re.compile(re.escape(n[:40]), re.I)).first
            if chip.count():
                lab = chip.get_attribute("aria-label") or ""
                if re.search(r"\d+\s*(KB|MB|bytes)", lab, re.I):
                    listos += 1
        if listos == len(nombres):
            break
        time.sleep(1)
    else:
        raise RuntimeError("Los adjuntos no terminaron de subir a Outlook.")
    time.sleep(1)
    t.anotar("Adjuntos: " + ", ".join(nombres))


def _verificar_antes_de_enviar(page, dest, asunto, remitente, adjuntos, texto, t):
    para = page.locator("div[aria-label='Para']").first.inner_text().lower()
    faltan = [d for d in dest if d.lower() not in para]
    if faltan:
        raise RuntimeError("Outlook no cargó estos destinatarios: " + ", ".join(faltan))
    subj = page.locator("input[aria-label='Asunto']").first
    if subj.input_value().strip() != asunto.strip():
        subj.fill(asunto)
    if remitente:
        de = page.locator("button[id$='_FROM']").first.get_attribute("aria-label") or ""
        if remitente.lower() not in de.lower():
            raise RuntimeError("El remitente no quedó en " + remitente)


def _esperar_envio(page, t):
    fin = time.time() + 90
    while time.time() < fin:
        if page.is_closed():
            return
        try:
            # cartelito de Outlook antes de enviar (ej. "¿olvidó adjuntar?")
            dlg = page.get_by_role("dialog").filter(has_text=re.compile(r"adjunt|attach", re.I))
            if dlg.count() and dlg.first.is_visible():
                t.anotar("Outlook preguntó: " + dlg.first.inner_text()[:200].replace("\n", " "))
                b = dlg.first.get_by_role("button", name=re.compile(r"^(Enviar|Send)", re.I)).first
                if b.count():
                    b.click()
            if not page.get_by_role("textbox", name=RX_CUERPO).count():
                return  # la redacción se cerró: enviado
            txt = page.locator("body").inner_text(timeout=3000)
            if re.search(r"(no se pudo enviar|couldn't be sent|could not be sent)", txt, re.I):
                raise RuntimeError("Outlook dice que no se pudo enviar el mensaje.")
        except RuntimeError:
            raise
        except Exception:
            return
        time.sleep(1)
    raise RuntimeError("Outlook no confirmó el envío (revisá la pestaña de redacción).")


# ----------------------------------------------------------------- PDF del enviado
_HEADER = ('<div style="font-size:8px;font-family:Arial,Helvetica,sans-serif;width:100%;padding:0 0.45in;'
           'display:flex;justify-content:space-between"><span class="date"></span><span>{titulo}</span><span></span></div>')
_FOOTER = ('<div style="font-size:8px;font-family:Arial,Helvetica,sans-serif;width:100%;padding:0 0.45in;'
           'display:flex;justify-content:space-between"><span>{url}</span>'
           '<span><span class="pageNumber"></span>/<span class="totalPages"></span></span></div>')


def pdf_del_enviado(page, notif, info_envio, carpeta, prueba, t):
    """Busca el mail en Elementos enviados, lo imprime con Outlook y lo guarda como PDF."""
    _esperar_sesion(page, t)
    base = _base(page)
    asunto = info_envio.get("asunto") or notif.get("asunto") or ""
    clave = re.sub(r"\s+", " ", asunto).strip()[:70]
    item = None
    fin = time.time() + 150
    while time.time() < fin and item is None:
        page.goto(f"{base}/mail/sentitems", wait_until="domcontentloaded")
        _esperar_sesion(page, t)
        try:
            page.get_by_role("option").first.wait_for(timeout=30000)
        except Exception:
            pass
        for _ in range(10):
            cand = page.get_by_role("option").filter(has_text=clave)
            if cand.count():
                item = cand.first
                break
            time.sleep(2)
    if item is None:
        raise RuntimeError("No encontré el mail en Elementos enviados.")
    item.click()
    time.sleep(2.5)
    titulo_pag = page.title() or ""
    m = re.search(r":\s*(.+?)\s*-\s*Outlook", titulo_pag)
    titulo = f"Elementos enviados: {m.group(1)} - Outlook" if m else "Elementos enviados - Outlook"
    url = page.url

    page.evaluate("() => { window.__notifInterceptPrint = true; window.__printRequested = 0; window.__printHTML = null; }")
    try:
        item.focus()
        page.keyboard.press("Control+p")
        fin = time.time() + 60
        while time.time() < fin:
            if page.evaluate("() => window.__printRequested || 0"):
                break
            time.sleep(0.5)
        html_print = page.evaluate("() => window.__printHTML")
    finally:
        page.evaluate("() => { window.__notifInterceptPrint = false; }")
    if not html_print:
        raise RuntimeError("Outlook no generó la vista de impresión del mail.")

    nombre = "Mail enviado - " + _limpio(asunto)[:100] + ".pdf"
    ruta = os.path.join(carpeta, nombre)
    p = page.context.new_page()
    try:
        p.set_content(html_print, wait_until="load")
        p.emulate_media(media="print")
        p.pdf(path=ruta, format="A4", print_background=True, display_header_footer=True,
              header_template=_HEADER.format(titulo=_html.escape(titulo)),
              footer_template=_FOOTER.format(url=_html.escape(url[:110] + ("…" if len(url) > 110 else ""))),
              margin={"top": "0.5in", "bottom": "0.5in", "left": "0.45in", "right": "0.45in"})
    finally:
        p.close()
    t.anotar("PDF del enviado: " + nombre)
    return ruta
