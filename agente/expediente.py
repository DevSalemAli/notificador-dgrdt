"""
Expediente Electrónico (SADE): vincula documentos al expediente y realiza el pase.

Pasos (los mismos que a mano):
  1) En la barra de búsqueda de arriba se busca el EX → fila del expediente → Acciones: Tramitar
     (si no está adquirido: Adquirir → Sí).
  2) Pestaña Documentos → Número SADE (Actuación / Año / Número / Repartición) → «Vincular Documentos»
     (uno por uno: el IF/PV notificado y la/s notificación/es fehaciente/s).
  3) «Realizar Pase» → Motivo, Estado: Tramitación, Repartición: DGRDT, Sector: COLECTIVAS → «Realizar Pase».
"""
import re
import time

URL_EE = "https://eue-pr.apps.buenosaires.gob.ar/expedientes-web/"
URL_GEDO = "https://eut-pr.apps.buenosaires.gob.ar/gedo-web/"
RX_NUM = re.compile(r"^\s*([A-Z]{2,6})\s*-\s*(\d{4})\s*-\s*(\d{2,9})(?:\s*-\s*)+GCABA\s*-\s*([A-Z0-9]+)", re.I)


def partes(num):
    m = RX_NUM.match(num or "")
    if not m:
        raise RuntimeError(f"Número SADE inválido: {num}")
    return m.group(1).upper(), m.group(2), m.group(3), m.group(4).upper()


def _ventanas(page):
    import gedo
    return gedo._ventanas(page)


def _click_js(page, titulo, rx):
    import gedo
    return gedo._click_js(page, titulo, rx)


def _cerrar_avisos(page, t):
    """Cierra cartelitos (Error / Información / Confirmación con OK) y devuelve sus textos."""
    textos = []
    for _ in range(4):
        vs = [v for v in _ventanas(page) if v["botones"] and set(v["botones"]) <= {"OK", "Aceptar", "Cerrar"}]
        if not vs:
            break
        v = vs[-1]
        textos.append(v["texto"])
        t.anotar("SADE dice: " + v["texto"][:220])
        _click_js(page, v["titulo"], r"^(OK|Aceptar|Cerrar)$")
        time.sleep(1)
    return textos


def _esperar_sesion(page, t, timeout_s=900):
    avisado = False
    fin = time.time() + timeout_s
    while time.time() < fin:
        u = page.url or ""
        if ("expedientes-web" in u or "gedo-web" in u) and "login" not in u:
            return
        if ("identidad-gcaba" in u or "cas-pr" in u) and not avisado:
            t.anotar("✋ SADE pide iniciar sesión: hacelo en la ventana del notificador (espero).")
            avisado = True
        time.sleep(1.5)
    raise RuntimeError("SADE no inició sesión a tiempo.")


def _tramitacion(page, ex):
    return page.locator(".z-window").filter(has_text=re.compile(r"Expediente:\s*" + re.escape(ex[:16]))).last


def abrir_expediente(page, expediente, t):
    """Busca el EX y abre su ventana de Tramitación (Tramitar, o Adquirir → Sí → Tramitar)."""
    tipo, anio, numero, rep = partes(expediente)
    ex_txt = f"EX-{anio}-{numero}"
    page.goto(URL_EE, wait_until="domcontentloaded")
    _esperar_sesion(page, t)
    time.sleep(2)
    caja = page.locator("input[placeholder*='número SADE']").first
    caja.wait_for(state="visible", timeout=30000)
    caja.fill(f"EX-{anio}-{numero}- -GCABA-{rep}")
    page.locator("button[title='Buscar']").first.click()
    def fila_visible():
        fin = time.time() + 30
        while time.time() < fin:
            filas = page.locator("tr").filter(has_text=ex_txt)
            for i in range(filas.count()):
                if filas.nth(i).is_visible():
                    return filas.nth(i)
            time.sleep(1)
        raise RuntimeError(f"No encontré {ex_txt} en SADE.")

    fila_visible()
    time.sleep(3)   # la lista de resultados se vuelve a dibujar un instante después

    def elegir(opcion):
        for _ in range(4):
            fila = fila_visible()
            fila.locator("a.z-combobox-button").first.click()
            time.sleep(1.2)
            pop = page.locator(".z-combobox-popup.z-combobox-open")
            if pop.count():
                item = pop.last.locator(".z-comboitem").filter(has_text=re.compile(rf"^\s*{opcion}\s*$")).first
                if item.count():
                    item.click()
                    time.sleep(3)
                    return
            time.sleep(1.5)
        raise RuntimeError(f"No pude elegir «{opcion}» en el expediente.")

    elegir("Tramitar")
    for _ in range(3):
        if _tramitacion(page, ex_txt).count() and _tramitacion(page, ex_txt).is_visible():
            t.anotar(f"SADE: expediente {ex_txt} abierto en Tramitación.")
            return _tramitacion(page, ex_txt)
        avisos = _cerrar_avisos(page, t)
        # confirmaciones "¿Desea…?" → Sí
        for v in _ventanas(page):
            if any(b in ("Sí", "Si") for b in v["botones"]):
                _click_js(page, v["titulo"], r"^(Sí|Si)$")
                time.sleep(3)
        if any(re.search(r"(adquir|no se encuentra en su buz|asignad)", a, re.I) for a in avisos):
            elegir("Adquirir")
            for v in _ventanas(page):
                if any(b in ("Sí", "Si") for b in v["botones"]):
                    _click_js(page, v["titulo"], r"^(Sí|Si)$")
                    time.sleep(3)
            _cerrar_avisos(page, t)
            if not _tramitacion(page, ex_txt).count():
                elegir("Tramitar")
        time.sleep(2)
    raise RuntimeError(f"No pude abrir {ex_txt} en Tramitación.")


def _documentos_en_ventana(win):
    txt = re.sub(r"\s+", " ", win.inner_text())
    return {re.sub(r"-+", "-", m.group(0)) for m in re.finditer(r"\b[A-Z]{2,6}-\d{4}-\d{2,9}-+GCABA-[A-Z0-9]+\b", txt)}


def vincular(page, win, numeros, t):
    """Vincula cada número SADE (si no estaba ya vinculado)."""
    hechos = []
    for num in numeros:
        tipo, anio, numero, rep = partes(num)
        normal = f"{tipo}-{anio}-{numero}-GCABA-{rep}"
        if normal in _documentos_en_ventana(win):
            t.anotar(f"SADE: {normal} ya estaba vinculado.")
            hechos.append(normal)
            continue
        fila = win.locator("tr, .z-row, .z-hbox").filter(has_text="Número SADE").first
        campos = win.locator("input.z-bandbox-input, input.z-intbox")
        # en la fila "Número SADE": [Actuación (bandbox)] [Año] [Número] [Ecosistema] [Repartición (bandbox)]
        act = win.locator("input.z-bandbox-input").nth(0)
        anio_in = win.locator("input.z-intbox").nth(0)
        num_in = win.locator("input.z-intbox").nth(1)
        rep_in = win.locator("input.z-bandbox-input").nth(1)
        for campo, valor in ((act, tipo), (anio_in, anio), (num_in, numero), (rep_in, rep)):
            campo.click()
            campo.fill(valor)
            campo.press("Tab")
            time.sleep(0.4)
        if not _click_js(page, "Expediente:", r"^Vincular Documentos$"):
            win.get_by_text("Vincular Documentos", exact=True).first.click()
        time.sleep(3)
        for v in _ventanas(page):
            if any(b in ("Sí", "Si") for b in v["botones"]):
                t.anotar("SADE pregunta: " + v["texto"][:160])
                _click_js(page, v["titulo"], r"^(Sí|Si)$")
                time.sleep(3)
        avisos = _cerrar_avisos(page, t)
        fin = time.time() + 20
        while time.time() < fin and normal not in _documentos_en_ventana(win):
            time.sleep(1)
        if normal not in _documentos_en_ventana(win):
            raise RuntimeError(f"No se vinculó {normal}" + (f": {avisos[-1][:160]}" if avisos else ""))
        t.anotar(f"SADE: vinculado {normal}")
        hechos.append(normal)
    return hechos


def _ventana_pase(page):
    return page.locator(".z-window-modal, .z-window-highlighted").filter(has_text="Realizar Pase y Comunicar").last


def realizar_pase(page, win, cfg, t, ejecutar=True):
    env = completar_pase(page, win, cfg, t)
    if ejecutar:
        confirmar_pase(page, env, t)
    return env


def completar_pase(page, win, cfg, t):
    motivo = cfg.get("pase_motivo") or "SE REMITEN LAS PRESENTES ACTUACIONES A FIN DE LA PROSECUCIÓN DEL TRÁMITE."
    if not _click_js(page, "Expediente:", r"^Realizar Pase$"):
        win.get_by_text("Realizar Pase", exact=True).first.click()
    env = _ventana_pase(page)
    env.wait_for(state="visible", timeout=20000)
    time.sleep(1.5)
    # Motivo: editor de texto enriquecido (CKEditor dentro de un iframe)
    escrito = page.evaluate(
        """(txt) => {
            if (window.CKEDITOR && Object.keys(CKEDITOR.instances).length) {
                const inst = Object.values(CKEDITOR.instances).pop(); inst.setData('<p>' + txt + '</p>'); inst.fire('change'); return 'ck';
            }
            return null;
        }""",
        motivo,
    )
    if not escrito:
        fr = env.frame_locator("iframe").first
        body = fr.locator("body")
        body.click()
        body.press("Control+a")
        body.type(motivo, delay=5)
    time.sleep(0.6)
    # Estado / Repartición / Sector
    combos = env.locator("input.z-combobox-input")
    if combos.count():
        est = combos.first
        if (est.input_value() or "").strip().lower() != (cfg.get("pase_estado") or "Tramitación").lower():
            est.fill(cfg.get("pase_estado") or "Tramitación")
            est.press("Tab")
    bandboxes = env.locator(".z-bandbox")
    _elegir_en_bandbox(page, bandboxes.nth(0), cfg.get("pase_reparticion") or "DGRDT", t)
    sector_radio = env.locator("label").filter(has_text=re.compile(r"^\s*Sector\s*$")).first
    if sector_radio.count():
        sector_radio.click()
        time.sleep(0.6)
    _elegir_en_bandbox(page, bandboxes.nth(1), cfg.get("pase_sector") or "COLECTIVA", t)
    _cerrar_avisos(page, t)
    vals = leer_pase(page, env) or {}
    t.anotar("SADE: pase completado (sin confirmar) → " + ", ".join(v for v in vals.get("vals", []) if v))
    return env


def _elegir_en_bandbox(page, bandbox, codigo, t):
    """Escribe el código; si SADE no lo acepta (campo en rojo), lo elige de la lista de la lupa."""
    inp = bandbox.locator("input").first
    inp.click()
    inp.fill(codigo)
    inp.press("Tab")
    time.sleep(1.5)
    if "z-bandbox-invalid" not in (inp.get_attribute("class") or ""):
        return
    bandbox.locator("a").first.click()                        # lupa
    time.sleep(2)
    ok = page.evaluate(
        r"""(cod) => {
          const pop = [...document.querySelectorAll('.z-bandbox-popup')].filter(p => p.getBoundingClientRect().width > 0).pop();
          if (!pop) return false;
          const fila = [...pop.querySelectorAll('tr.z-listitem')].find(tr => {
            const c = tr.querySelector('td'); return c && (c.innerText || '').trim().toUpperCase() === cod.toUpperCase();
          });
          if (!fila) return false;
          fila.click(); return true;
        }""",
        codigo,
    )
    time.sleep(1.5)
    if not ok or "z-bandbox-invalid" in (inp.get_attribute("class") or ""):
        raise RuntimeError(f"SADE no aceptó «{codigo}» (revisá el código en Ajustes del agente).")
    t.anotar(f"SADE: elegido «{codigo}» de la lista.")


def leer_pase(page, env):
    """Valores cargados en la ventana del pase (para controlar antes de confirmar)."""
    return page.evaluate(
        r"""() => {
          const w = [...document.querySelectorAll('.z-window-modal, .z-window-highlighted')].filter(x => (x.innerText||'').includes('Realizar Pase y Comunicar')).pop();
          if (!w) return null;
          const vals = [...w.querySelectorAll('input.z-combobox-input, input.z-bandbox-input')].map(i => i.value);
          const radios = [...w.querySelectorAll('input[type=radio]')].map(r => ({checked: r.checked, label: (r.parentElement && r.parentElement.innerText || '').trim()}));
          let motivo = null;
          if (window.CKEDITOR && Object.keys(CKEDITOR.instances).length) motivo = Object.values(CKEDITOR.instances).pop().getData();
          return {vals, radios, motivo};
        }"""
    )


def confirmar_pase(page, env, t):
    if not _click_js(page, "Realizar Pase y Comunicar", r"^Realizar Pase$"):
        env.get_by_text("Realizar Pase", exact=True).first.click()
    time.sleep(3)
    for v in _ventanas(page):
        if any(b in ("Sí", "Si") for b in v["botones"]):
            t.anotar("SADE pregunta: " + v["texto"][:160])
            _click_js(page, v["titulo"], r"^(Sí|Si)$")
            time.sleep(3)
    avisos = _cerrar_avisos(page, t)
    if any(re.search(r"(error|no se pudo|debe|obligatori)", a, re.I) for a in avisos):
        raise RuntimeError("SADE no hizo el pase: " + avisos[-1][:200])
    if env.count() and env.is_visible():
        raise RuntimeError("La ventana del pase sigue abierta (revisá en SADE).")
    t.anotar("SADE: pase realizado.")


def vincular_y_pase(page, expediente, numeros, cfg, t, hacer_pase=True):
    win = abrir_expediente(page, expediente, t)
    hechos = vincular(page, win, numeros, t)
    if hacer_pase:
        realizar_pase(page, win, cfg, t)
    return hechos
