"""
Agenda en Google: planilla "COLECTIVAS 2026" (Google Sheets) y Word de seguimiento (.docx en Drive).

Se hace en el Chrome del notificador (con tu sesión de Google), igual que a mano:
  - Planilla: lee los datos (exportación CSV de la sesión) para ubicar la fila, y escribe con el
    teclado usando el "Cuadro de nombre" para ir a cada celda. Respeta las validaciones
    (sindicato de la lista, casillas, fórmulas de la columna C).
  - Word de seguimiento: descarga el .docx para ubicar el día de hoy y escribe el renglón con el
    teclado en Google Docs (debajo del encabezado del día; si el día o la semana no existen, los crea).
"""
import csv
import datetime as dt
import io
import re
import time
import unicodedata

DIAS = ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"]
MOTIVOS = ["SALARIALES", "ART 223 BIS", "CONDICIONES LABORALES", "PREMIOS", "OTROS", "SIN ESPECIFICAR",
           "SOSTENIMIENTO DE TRABAJO", "APLICACION DE CCT", "CERTIF LIBRE CONFLICT COLEC"]
APERTURAS = {"audiencia_presencial", "audiencia_virtual", "audiencia_sin_presentacion_presencial",
             "audiencia_sin_presentacion_virtual"}
NUEVA_FECHA = {"cambio_fecha_con_presentacion", "cambio_fecha_sin_presentacion", "nueva_audiencia_acta",
               "acta_firma_nueva_fecha"}

COL = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4, "F": 5, "G": 6, "H": 7, "I": 8, "J": 9}
BLOQUES_AUD = [10 + 4 * k for k in range(16)]   # K, O, S, … : Fecha Aud. | Virtual | PV | Fecha Notif.


# ----------------------------------------------------------------- utilidades
def _norm(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", s)


def letra(idx):
    s, n = "", idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def clave_ex(s):
    m = re.search(r"EX\s*-?\s*(\d{4})\s*-\s*(\d{5,9})", str(s or ""), re.I)
    return (m.group(1), m.group(2).lstrip("0")) if m else None


def ex_formato_planilla(ex):
    k = clave_ex(ex)
    if not k:
        return ex
    m = re.search(r"GCABA\s*-\s*([A-Z]+)", ex or "", re.I)
    rep = (m.group(1) if m else "DGRDT").upper()
    num = re.search(r"EX\s*-?\s*\d{4}\s*-\s*(\d{5,9})", ex, re.I).group(1)
    return f"EX-{k[0]}-{num}- -GCABA-{rep}"


def fecha_ddmmaaaa(s):
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$", str(s or ""))
    if not m:
        return ""
    a = int(m.group(3))
    a = a + 2000 if a < 100 else a
    return f"{int(m.group(1))}/{int(m.group(2))}/{a}"


def ids_planilla(url):
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    g = re.search(r"gid=(\d+)", url or "")
    return (m.group(1) if m else None), (g.group(1) if g else "0")


def id_documento(url):
    m = re.search(r"/(?:document|file)/d/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else None


def _pagina(ctx, url_parcial, url, t):
    for p in ctx.pages:
        if url_parcial in (p.url or ""):
            p.bring_to_front()
            return p
    p = ctx.new_page()
    p.goto(url, wait_until="domcontentloaded")
    return p


def _esperar_google(page, t, timeout_s=900):
    import navegador
    avisado = False
    fin = time.time() + timeout_s
    while time.time() < fin:
        u = page.url or ""
        if "docs.google.com" in u and "accounts.google" not in u:
            if avisado:
                navegador.fin_espera(t)
                t.anotar("Google: sesión iniciada, sigo.")
            return
        if not avisado and "accounts.google" in u:
            navegador.avisar_espera(t, "✋ Google pide iniciar sesión: hacelo en la ventana del notificador (espero).")
            avisado = True
        navegador.pausa(page, 1.5)   # no time.sleep: así page.url se actualiza
    raise RuntimeError("Google no inició sesión a tiempo.")


# ----------------------------------------------------------------- PLANILLA
def leer_csv(ctx, sheet_id, gid):
    r = ctx.request.get(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}")
    if r.status != 200 or "text/csv" not in (r.headers.get("content-type") or ""):
        raise RuntimeError("No pude leer la planilla (¿sesión de Google iniciada en la ventana del notificador?).")
    return list(csv.reader(io.StringIO(r.text())))


def _sindicato_de_lista(ctx, sheet_id, sigla):
    try:
        filas = leer_csv(ctx, sheet_id, "131388646")   # pestaña "Lista Sindicatos"
        lista = [f[0].strip() for f in filas if f and f[0].strip()]
    except Exception:
        return sigla
    n = _norm(sigla)
    for v in lista:
        if _norm(v) == n:
            return v
    cand = [v for v in lista if _norm(v).startswith(n) or n.startswith(_norm(v))]
    return cand[0] if len(cand) == 1 else None


def _ir_a(page, ref):
    nb = page.locator("#t-name-box")
    nb.click()
    nb.fill(ref)
    nb.press("Enter")
    time.sleep(0.7)


def _tipear_fila(page, valores):
    """valores: lista de str | None (saltar) | True (tildar casilla) | False (dejar casilla)."""
    for v in valores:
        if v is True:
            page.keyboard.press("Space")
            time.sleep(0.2)
        elif isinstance(v, str) and v != "":
            page.keyboard.type(v, delay=8)
        page.keyboard.press("Tab")
        time.sleep(0.25)
    time.sleep(0.5)


def agendar_planilla(ctx, url, datos, estado_mail, t):
    caso = datos.get("caso") or {}
    esc = caso.get("escenario") or ""
    sheet_id, gid = ids_planilla(url)
    if not sheet_id:
        raise RuntimeError("El link de la planilla no es válido.")
    if esc not in APERTURAS and esc not in NUEVA_FECHA:
        return "No corresponde agendar en la planilla para este escenario."
    fecha_aud = fecha_ddmmaaaa(caso.get("audiencia_fecha"))
    if not fecha_aud:
        raise RuntimeError("Falta la fecha de audiencia para agendar.")
    virtual = (caso.get("modalidad") or "").lower().startswith("virt")
    hoy = dt.date.today()
    hoy_s = f"{hoy.day}/{hoy.month}/{hoy.year}"
    doc = caso.get("documento_notificado") or ""

    filas = leer_csv(ctx, sheet_id, gid)
    k_ex = clave_ex(caso.get("expediente"))
    fila_ex = None
    if k_ex:
        for i, f in enumerate(filas):
            if len(f) > 6 and clave_ex(f[6]) == k_ex:
                fila_ex = i      # nos quedamos con la última aparición

    page = _pagina(ctx, f"/spreadsheets/d/{sheet_id}", f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}", t)
    _esperar_google(page, t)
    page.locator("#t-name-box").wait_for(state="visible", timeout=60000)
    time.sleep(1.5)

    if esc in APERTURAS and fila_ex is None:
        ultima = max((i for i, f in enumerate(filas) if len(f) > 6 and f[6].strip()), default=3)
        fila = ultima + 1                     # índice 0-based; en la planilla es fila+1
        sind = _sindicato_de_lista(ctx, sheet_id, caso.get("sigla") or "")
        if not sind:
            t.anotar(f"Aviso: el sindicato «{caso.get('sigla')}» no está en la lista de la planilla; quedó vacío.")
        motivo = (caso.get("motivo") or "SIN ESPECIFICAR").upper()
        if motivo not in MOTIVOS:
            motivo = "SIN ESPECIFICAR"
        valores = [
            fecha_ddmmaaaa(caso.get("fecha_presentacion")) or "",   # A fecha presentación
            hoy_s,                                                  # B fecha apertura
            None,                                                   # C días hábiles (fórmula)
            True,                                                   # D APERTURA (casilla)
            motivo,                                                 # E motivo
            caso.get("presentacion") or "",                         # F GEDO presentación
            ex_formato_planilla(caso.get("expediente") or ""),      # G expediente
            sind or "",                                             # H sindicato
            (caso.get("empresa") or "").upper(),                    # I razón social
            None,                                                   # J trabajadores afectados
            fecha_aud,                                              # K fecha aud.
            True if virtual else False,                             # L virtual
            doc,                                                    # M PV
            hoy_s,                                                  # N fecha notif.
        ]
        _ir_a(page, f"A{fila + 1}")
        _tipear_fila(page, valores)
        res = f"Fila {fila + 1} nueva (apertura)"
        verif_fila, verif_col = fila, 10
    else:
        if fila_ex is None:
            raise RuntimeError("No encontré el expediente en la planilla para agregar la nueva audiencia.")
        f = filas[fila_ex]
        libre = next((c for c in BLOQUES_AUD if len(f) <= c or not f[c].strip()), None)
        if libre is None:
            raise RuntimeError("El expediente ya tiene las 16 audiencias cargadas en la planilla.")
        _ir_a(page, f"{letra(libre)}{fila_ex + 1}")
        _tipear_fila(page, [fecha_aud, True if virtual else False, doc, hoy_s])
        res = f"Fila {fila_ex + 1}: audiencia en columna {letra(libre)}"
        verif_fila, verif_col = fila_ex, libre

    # verificación: volver a leer
    time.sleep(3)
    for _ in range(5):
        filas2 = leer_csv(ctx, sheet_id, gid)
        if len(filas2) > verif_fila and len(filas2[verif_fila]) > verif_col:
            valor = filas2[verif_fila][verif_col].strip()
            if valor and fecha_ddmmaaaa(valor) == fecha_aud:
                return res + f" · {fecha_aud}" + (" virtual" if virtual else "")
        time.sleep(2)
    raise RuntimeError("Escribí en la planilla pero no pude verificar la fecha cargada (revisala).")


# ----------------------------------------------------------------- WORD DE SEGUIMIENTO
def _descargar_docx(ctx, doc_id):
    r = ctx.request.get(f"https://drive.google.com/uc?export=download&id={doc_id}")
    if r.status != 200 or len(r.body()) < 1000:
        raise RuntimeError("No pude descargar el Word de seguimiento.")
    return r.body()


def _parrafos(docx_bytes, n=80):
    import docx
    d = docx.Document(io.BytesIO(docx_bytes))
    return [p.text.strip() for p in d.paragraphs[:n]]


def encabezado_dia(f):
    return f"{DIAS[f.weekday()]} {f.day}-{f.month}-{f.strftime('%y')}"


def encabezado_semana(f):
    lunes = f - dt.timedelta(days=f.weekday())
    viernes = lunes + dt.timedelta(days=4)
    return f"LUNES {lunes.day}-{lunes.month}-{lunes.strftime('%y')}  al VIERNES {viernes.day}-{viernes.month}-{viernes.strftime('%y')}"


RX_DIA = re.compile(r"^(LUNES|MARTES|MI[EÉ]RCOLES|JUEVES|VIERNES|S[AÁ]BADO|DOMINGO)\s+(\d{1,2})-(\d{1,2})-(\d{2,4})\b", re.I)


def _fecha_de(txt):
    m = RX_DIA.match(txt or "")
    if not m:
        return None
    a = int(m.group(4))
    a = a + 2000 if a < 100 else a
    try:
        return dt.date(a, int(m.group(3)), int(m.group(2)))
    except ValueError:
        return None


def _buscar_en_docs(page, texto):
    """Ctrl+F en Google Docs, deja seleccionado el texto encontrado."""
    page.keyboard.press("Control+f")
    time.sleep(0.8)
    caja = page.locator("#docs-findbar-input input, input.docs-findinput-input, #docs-findbar-input").first
    try:
        caja.fill(texto)
    except Exception:
        page.keyboard.type(texto)
    time.sleep(0.5)
    page.keyboard.press("Enter")
    time.sleep(0.8)
    page.keyboard.press("Escape")
    time.sleep(0.6)


def _negrita_activa(page):
    try:
        return page.locator("#boldButton").first.get_attribute("aria-pressed") == "true"
    except Exception:
        return None


def _tipear_marcado(page, texto):
    """Escribe en Google Docs respetando **negritas**: lo marcado en negrita, el resto normal
    (como el resto de los renglones del Word de seguimiento)."""
    for parte in re.split(r"(\*\*.+?\*\*)", texto):
        if not parte:
            continue
        neg = parte.startswith("**") and parte.endswith("**")
        contenido = parte[2:-2] if neg else parte
        actual = _negrita_activa(page)
        if actual is not None and actual != neg:
            page.keyboard.press("Control+b")
            time.sleep(0.2)
        page.keyboard.type(contenido, delay=4)


def _linea_vacia_sin_vineta(page):
    """Con el cursor al inicio de un renglón con viñeta: agrega arriba un renglón vacío SIN viñeta."""
    page.keyboard.press("Enter")
    time.sleep(0.2)
    page.keyboard.press("ArrowUp")
    time.sleep(0.2)
    page.keyboard.press("Backspace")     # en un renglón vacío con viñeta, quita la viñeta
    time.sleep(0.2)
    page.keyboard.press("ArrowDown")
    time.sleep(0.2)


def agendar_seguimiento(ctx, url, datos, estado_mail, t, fecha=None):
    caso = datos.get("caso") or {}
    marcado = (caso.get("texto_interno") or "").strip()
    marcado = re.sub(r"\.\*\*\.", ".**", marcado)                       # "S.A.**." -> "S.A.**"
    if "**" not in marcado:                                             # sin marca: la identificación va en negrita
        m = re.match(r"^(APERTURA\s+)?(EX-[^.]*?-\s*-?\s*GCABA-[A-Z]+[^.]*?)(\.|\s)", marcado)
        if m:
            marcado = "**" + marcado[:m.end(2)] + "**" + marcado[m.end(2):]
    texto = re.sub(r"\*\*(.+?)\*\*", r"\1", marcado)
    texto = re.sub(r"(?<=[A-Za-zÁÉÍÓÚÑáéíóúñ])\.\.(?!\.)", ".", texto)   # "S.A.." -> "S.A."
    if not texto:
        return "El caso no tiene texto interno: no se agendó en el Word."
    doc_id = id_documento(url)
    if not doc_id:
        raise RuntimeError("El link del Word de seguimiento no es válido.")
    hoy = fecha or dt.date.today()
    dia = encabezado_dia(hoy)
    semana = encabezado_semana(hoy)

    pars = _parrafos(_descargar_docx(ctx, doc_id))
    i_dia = next((i for i, p in enumerate(pars) if _fecha_de(p) == hoy and " al " not in p), None)
    semana_actual = bool(pars) and _fecha_de(pars[0]) is not None and " al " in pars[0] and \
        (_fecha_de(pars[0]) == hoy - dt.timedelta(days=hoy.weekday()))

    page = _pagina(ctx, f"/document/d/{doc_id}", f"https://docs.google.com/document/d/{doc_id}/edit", t)
    _esperar_google(page, t)
    page.locator(".kix-appview-editor").first.wait_for(state="visible", timeout=60000)
    time.sleep(2.5)
    page.locator(".kix-appview-editor").first.click(position={"x": 200, "y": 150})
    time.sleep(0.4)

    if i_dia is not None:
        # Hay encabezado de hoy: el renglón va primero en el bloque del día.
        # Se escribe al principio del primer renglón del día (hereda la viñeta y el formato).
        blancos = 0
        for p in pars[i_dia + 1:]:
            if p:
                break
            blancos += 1
        _buscar_en_docs(page, pars[i_dia])
        page.keyboard.press("End")
        for _ in range(blancos + 1):
            page.keyboard.press("ArrowRight")
        _tipear_marcado(page, marcado)
        page.keyboard.press("Enter")
        det = f"Agregado debajo de «{dia}»"
    elif semana_actual:
        # La semana existe pero falta el día: el día nuevo va arriba de todo en la semana,
        # copiando el formato del encabezado del último día (viñeta + negrita).
        blancos = 0
        for p in pars[1:]:
            if p:
                break
            blancos += 1
        page.keyboard.press("Control+Home")
        page.keyboard.press("End")
        for _ in range(blancos + 1):
            page.keyboard.press("ArrowRight")          # inicio del encabezado del último día
        page.keyboard.type(dia, delay=4)
        page.keyboard.press("Enter")                    # [DÍA NUEVO][DÍA ANTERIOR]
        _linea_vacia_sin_vineta(page)                   # renglón vacío debajo del día nuevo
        _tipear_marcado(page, marcado)
        page.keyboard.press("Enter")                    # [RENGLÓN][DÍA ANTERIOR]
        _linea_vacia_sin_vineta(page)
        _linea_vacia_sin_vineta(page)
        det = f"Creado el día «{dia}» y agregado el renglón"
    else:
        # Semana nueva: se abren renglones vacíos arriba de todo y se completan uno por uno:
        # [SEMANA][vacío][• DÍA][vacío][• RENGLÓN][vacío ×4][semana anterior…]
        page.keyboard.press("Control+Home")
        for _ in range(9):
            page.keyboard.press("Enter")
            time.sleep(0.1)
        page.keyboard.press("Control+Home")              # renglón vacío nº 1

        def bajar():
            page.keyboard.press("ArrowDown")
            time.sleep(0.15)
            page.keyboard.press("Home")
            time.sleep(0.15)

        page.keyboard.type(semana, delay=4)              # 1: semana
        bajar()                                          # 2: vacío
        bajar()                                          # 3: día (con viñeta)
        page.keyboard.type(dia, delay=4)
        page.keyboard.press("Control+Shift+8")
        time.sleep(0.3)
        bajar()                                          # 4: vacío
        bajar()                                          # 5: renglón (con viñeta)
        page.keyboard.press("Control+Shift+8")
        time.sleep(0.3)
        _tipear_marcado(page, marcado)
        det = f"Creada la semana «{semana}», el día y el renglón"

    # verificación (Drive tarda unos segundos en guardar el .docx)
    clave = texto[:60]
    for _ in range(12):
        time.sleep(5)
        try:
            pars2 = _parrafos(_descargar_docx(ctx, doc_id), 120)
            if any(clave in p for p in pars2):
                return det
        except Exception:
            pass
    raise RuntimeError("Escribí en el Word de seguimiento pero todavía no veo el renglón guardado (revisalo).")
