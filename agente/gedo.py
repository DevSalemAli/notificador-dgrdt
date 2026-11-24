"""
GEDO (SADE GCABA): genera la "Notificación fehaciente" (acrónimo NOTIF) importando el PDF del
mail enviado, la firma y descarga el documento final.

Pasos (los mismos que a mano):
  Inicio de Documento → Documento Electrónico: NOTIF → Producirlo yo mismo →
  Referencia + Seleccionar Archivo (PDF del mail) → Firmar Yo Mismo el Documento → firmar →
  número PV-AAAA-…-GCABA-DGRDT → descargar el PDF.
"""
import os
import re
import time

URL_GEDO = "https://eut-pr.apps.buenosaires.gob.ar/gedo-web/"
RX_NUMERO = re.compile(r"\b[A-Z]{2,6}-\d{4}-\d{5,9}-+GCABA-[A-Z0-9]+\b")


def _esperar_sesion(page, t, timeout_s=900):
    avisado = False
    fin = time.time() + timeout_s
    while time.time() < fin:
        u = page.url or ""
        if "gedo-web" in u and "login" not in u and "oidc_callback" not in u:
            try:
                if page.get_by_text("Inicio de Documento").count():
                    return
            except Exception:
                pass
        if "oidc_callback" in u:   # "Bad Request" del inicio de sesión viejo: se reintenta solo
            page.goto(URL_GEDO, wait_until="domcontentloaded")
        elif ("identidad-gcaba" in u or "cas-pr" in u or "cas.buenosaires" in u) and not avisado:
            t.anotar("✋ GEDO pide iniciar sesión: hacelo en la ventana del notificador (espero).")
            avisado = True
        time.sleep(1.5)
    raise RuntimeError("GEDO no inició sesión a tiempo.")


def _ventana(page, titulo):
    return page.locator(".z-window-modal, .z-window-highlighted").filter(has_text=titulo).last


def _click_texto(scope, texto, exacto=True, timeout=15000):
    el = scope.get_by_text(texto, exact=exacto).last
    el.wait_for(state="visible", timeout=timeout)
    el.click()
    return el


def _cerrar_mensajes(page, t):
    """Cierra cartelitos de ZK (Error / Información) y devuelve su texto."""
    textos = []
    for _ in range(3):
        box = page.locator(".z-messagebox-window").filter(has=page.locator("button"))
        if not box.count() or not box.last.is_visible():
            break
        txt = re.sub(r"\s+", " ", box.last.inner_text())
        textos.append(txt)
        t.anotar("GEDO dice: " + txt[:200])
        btn = box.last.get_by_role("button").first
        btn.click()
        time.sleep(1)
    return textos


def _elegir_tipo(page, win, acronimo, t):
    inp = win.locator("input.z-bandbox-input").first
    inp.click()
    inp.fill("")
    inp.type(acronimo, delay=60)
    inp.press("Enter")
    fin = time.time() + 12
    while time.time() < fin:
        if re.search(r"Notificaci[oó]n fehaciente", win.inner_text(), re.I):
            return
        time.sleep(0.5)
    # plan B: abrir el buscador de tipos y elegir la fila con ese acrónimo
    win.locator("a[id$='-btn']").first.click()
    time.sleep(1.5)
    pop = page.locator(".z-bandbox-popup.z-bandbox-open").first
    buscar = pop.locator("input.z-textbox").first
    buscar.fill(acronimo)
    time.sleep(2.5)
    filas = pop.locator("tr.z-treerow")
    for i in range(filas.count()):
        celdas = [c.strip() for c in filas.nth(i).locator("td").all_inner_texts()]
        if len(celdas) > 1 and celdas[1].upper() == acronimo.upper():
            filas.nth(i).locator("td").first.click()
            break
    time.sleep(2)
    if not re.search(r"Notificaci[oó]n fehaciente", win.inner_text(), re.I):
        raise RuntimeError(f"GEDO no reconoció el tipo de documento {acronimo}.")


def notificacion_fehaciente(page, referencia, pdf_mail, carpeta, cfg, prueba, t):
    if not referencia:
        raise RuntimeError("Falta la referencia para GEDO.")
    if not os.path.exists(pdf_mail):
        raise RuntimeError("No encuentro el PDF del mail para importar.")
    _esperar_sesion(page, t)
    page.goto(URL_GEDO, wait_until="domcontentloaded")   # arrancar siempre desde el buzón limpio
    _esperar_sesion(page, t)
    time.sleep(1.5)

    # 1) Inicio de Documento → NOTIF → Producirlo yo mismo
    _click_texto(page, "Inicio de Documento")
    win = _ventana(page, "Iniciar Producción de Documento")
    win.wait_for(state="visible", timeout=20000)
    _elegir_tipo(page, win, cfg.get("gedo_tipo_documento") or "NOTIF", t)
    _click_texto(win, "Producirlo yo mismo")

    # 2) Producir documento: referencia + PDF importado
    prod = _ventana(page, "Producir documento")
    prod.wait_for(state="visible", timeout=30000)
    ref = prod.locator("input.z-textbox").first
    ref.fill(referencia)
    ref.press("Tab")
    time.sleep(0.5)
    with page.expect_file_chooser(timeout=20000) as fc:
        _click_texto(prod, "Seleccionar Archivo")
    fc.value.set_files(pdf_mail)
    nombre = os.path.basename(pdf_mail)
    fin = time.time() + 60
    while time.time() < fin and nombre[:30] not in prod.inner_text():
        time.sleep(0.5)
    if nombre[:30] not in prod.inner_text():
        raise RuntimeError("GEDO no tomó el PDF del mail.")
    _cerrar_mensajes(page, t)
    t.anotar(f"GEDO: referencia «{referencia}» y PDF importado.")

    if prueba or not cfg.get("gedo_firmar", True):
        _click_texto(prod, "Guardar")
        time.sleep(2)
        _cerrar_mensajes(page, t)
        t.anotar("GEDO: documento guardado SIN firmar (modo prueba / firma desactivada).")
        return {"numero": "", "pdf": "", "referencia": referencia, "firmado": False}

    # 3) Firmar yo mismo (antes se anotan los números que ya están en pantalla, para no confundirlos)
    antes = _numeros(page)
    if not _click_js(page, "Producir documento", r"^Firmar Yo Mismo"):
        _click_texto(prod, "Firmar Yo Mismo el Documento")
    numero = _firmar(page, referencia, antes, t)

    # 4) Descargar
    pdf_final = ""
    try:
        pdf_final = _descargar(page, numero, carpeta, t)
    except Exception as e:
        t.anotar("No pude descargar el documento firmado: " + str(e)[:200])
    return {"numero": numero, "pdf": pdf_final, "referencia": referencia, "firmado": True}


def _ventanas(page):
    """Ventanas emergentes visibles de GEDO con su título y sus botones (para decidir qué apretar)."""
    return page.evaluate(r"""() => [...document.querySelectorAll('.z-window-modal, .z-window-highlighted, .z-messagebox-window')]
      .filter(w => w.getBoundingClientRect().width > 0)
      .map(w => ({
        titulo: ((w.querySelector('[class*="-header"]') || {}).innerText || '').trim(),
        texto: (w.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 400),
        botones: [...w.querySelectorAll('button, .z-button')].filter(b => b.getBoundingClientRect().width > 0)
                   .map(b => (b.innerText || b.title || '').trim().replace(/\s+/g, ' ')).filter(Boolean)
      }))""")


def _numeros(page):
    try:
        txt = page.locator("body").inner_text()
    except Exception:
        return set()
    return {re.sub(r"-+", "-", m.group(0)) for m in RX_NUMERO.finditer(txt)}


def _click_js(page, titulo_ventana, rx_boton):
    """Aprieta un botón de una ventana de GEDO por código (los clics comunes a veces no llegan,
    por la vista previa del PDF). Devuelve el texto del botón o None."""
    return page.evaluate(
        r"""([tit, src]) => {
          const rx = new RegExp(src);
          const wins = [...document.querySelectorAll('.z-window-modal, .z-window-highlighted, .z-messagebox-window')]
            .filter(w => w.getBoundingClientRect().width > 0 && (!tit || (w.innerText || '').includes(tit)));
          for (const w of wins.reverse()) {
            const b = [...w.querySelectorAll('button, .z-button')]
              .find(b => b.getBoundingClientRect().width > 0 && rx.test((b.innerText || b.title || '').trim().replace(/\s+/g, ' ')));
            if (b) { b.click(); return (b.innerText || b.title || '').trim(); }
          }
          return null;
        }""",
        [titulo_ventana, rx_boton],
    )


def _firmar(page, referencia, antes, t):
    """Ventana «Firma De Documento» → «Firmar con Certificado». Si no la reconoce, espera a que
    firmes vos. El número se toma comparando con los que ya estaban en pantalla (`antes`)."""
    hecho = None
    for _ in range(40):
        time.sleep(1)
        vs = _ventanas(page)
        if any("Firma De Documento" in v["titulo"] or "Firmar con Certificado" in " ".join(v["botones"]) for v in vs):
            hecho = _click_js(page, "", r"^Firmar con [Cc]ertificado")
            if hecho:
                break
    if hecho:
        t.anotar("GEDO: «Firmar con Certificado»…")
    else:
        t.anotar("✋ No encontré «Firmar con Certificado»: firmá vos en la pestaña de GEDO (yo espero el número).")
    # confirmaciones y espera del número nuevo
    fin = time.time() + 300
    while time.time() < fin:
        time.sleep(2)
        vs = _ventanas(page)
        for v in vs:
            if re.search(r"(seguro|confirma|desea firmar|desea continuar)", v["texto"], re.I):
                t.anotar("GEDO pregunta: " + v["texto"][:150])
                _click_js(page, v["titulo"] or "", r"^(Sí|Si|Aceptar|Firmar|Confirmar)$")
        nuevos = sorted(n for n in _numeros(page) - antes if n.startswith(("PV-", "IF-")))
        if nuevos:
            num = next((n for n in nuevos if n.startswith("PV-")), nuevos[0])
            t.anotar("GEDO: documento firmado " + num)
            time.sleep(1.5)
            _cerrar_mensajes(page, t)
            return num
        errores = [v["texto"] for v in vs if re.search(r"(error|no se pudo|fall[oó])", v["texto"], re.I)]
        if errores:
            raise RuntimeError("GEDO informó un error al firmar: " + errores[-1][:200])
    raise RuntimeError("No apareció el número del documento firmado (revisá la pestaña de GEDO).")


def _descargar(page, numero, carpeta, t):
    """Descarga el PDF firmado desde 'Avisos' del buzón (fila del documento → Descargar)."""
    page.goto(URL_GEDO, wait_until="domcontentloaded")
    time.sleep(3)
    fila = page.locator("tr").filter(has_text=numero).first
    fila.wait_for(state="visible", timeout=30000)
    combo = fila.locator("input.z-combobox-input").first
    if combo.count() and "Descargar" not in (combo.input_value() or ""):
        combo.fill("Descargar")
    destino = os.path.join(carpeta, numero + ".pdf")
    ctx = page.context
    paginas_antes = set(ctx.pages)
    try:
        with page.expect_download(timeout=45000) as dl:
            fila.locator("button").last.click()
        # se vuelve a pedir el archivo con la sesión (más confiable que la carpeta temporal de descargas)
        r = ctx.request.get(dl.value.url)
        if r.status == 200 and r.body()[:4] == b"%PDF":
            with open(destino, "wb") as f:
                f.write(r.body())
        else:
            dl.value.save_as(destino)
    except Exception:
        # a veces GEDO abre el PDF en una pestaña en vez de descargarlo
        nuevas = [p for p in ctx.pages if p not in paginas_antes]
        if not nuevas:
            raise
        url = nuevas[-1].url
        r = ctx.request.get(url)
        if r.status != 200 or not r.body()[:4] == b"%PDF":
            raise RuntimeError("GEDO no entregó el PDF del documento.")
        with open(destino, "wb") as f:
            f.write(r.body())
        nuevas[-1].close()
    t.anotar("GEDO: descargado " + os.path.basename(destino))
    return destino


def mantener_sesion(ctx):
    """Pedido liviano a GEDO para que no se cierre la sesión por inactividad (no toca la pestaña)."""
    try:
        r = ctx.request.get(URL_GEDO, timeout=20000)
        return "gedo-web" in r.url and "identidad" not in r.url and "cas" not in r.url
    except Exception:
        return False
