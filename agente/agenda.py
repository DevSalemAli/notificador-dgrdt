"""
Agenda en Excel y en Word (usa el Office instalado, así no se rompe ningún formato).

- Excel: agrega una fila nueva debajo de la última usada, llenando cada columna según su título
  (Fecha, Hora, Expediente, Sindicato, Empresa, Modalidad, Link, Documento, Estado…).
- Word: si el documento tiene una tabla, agrega una fila a la última tabla con el mismo criterio;
  si no tiene tablas, agrega al final un párrafo con el texto interno (respetando la negrita).

Si el archivo ya está abierto en Excel/Word, escribe ahí mismo (no hace falta cerrarlo).
"""
import datetime as dt
import os
import re
import unicodedata


def _n(s):
    s = unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


# título de columna (normalizado) -> campo
_REGLAS = [
    (("link", "meet", "enlace"), "link"),
    (("hora",), "hora"),
    (("fecha de notif", "fecha notif", "notificado el", "fecha de envio", "envio"), "hoy"),
    (("fecha", "dia", "audiencia"), "fecha"),
    (("expediente", "ex ", "n ex", "nro ex", "exp"), "expediente"),
    (("partes", "caratula", "autos"), "partes"),
    (("sindicato", "gremio", "entidad sindical", "sigla"), "sindicato"),
    (("empresa", "razon social", "empleador", "parte empleadora"), "empresa"),
    (("modalidad", "presencial", "virtual"), "modalidad"),
    (("gedo", "notif fehac", "fehaciente", "nro notif"), "gedo"),
    (("providencia", "documento", "pv", "acto", "doc"), "documento"),
    (("estado", "observ", "detalle", "texto", "movimiento", "accion", "tramite", "motivo", "escenario"), "estado"),
    (("asesor", "abogad", "responsable", "a cargo"), "nada"),
]


def _campo_de_titulo(titulo):
    t = " " + _n(titulo) + " "
    if not t.strip():
        return None
    for claves, campo in _REGLAS:
        for c in claves:
            if (" " + c) in t or t.strip() == c.strip():
                return campo
    return None


def _valores(datos, estado_mail):
    caso = datos.get("caso") or {}
    gedos = [v.get("numero") for v in (estado_mail or {}).values() if v.get("numero")]
    fecha = caso.get("audiencia_fecha") or ""
    fecha_dt = None
    m = re.match(r"^\s*(\d{1,2})/(\d{1,2})/(\d{2,4})\s*$", fecha)
    if m:
        d, mo, a = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a < 100:
            a += 2000
        try:
            fecha_dt = dt.datetime(a, mo, d)
        except ValueError:
            fecha_dt = None
    sind = caso.get("sigla") or caso.get("sindicato") or ""
    estado = (caso.get("texto_interno") or "").replace("**", "")
    return {
        "fecha": fecha_dt or fecha,
        "hora": caso.get("audiencia_hora") or "",
        "hoy": dt.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
        "expediente": caso.get("expediente") or "",
        "sindicato": sind,
        "empresa": caso.get("empresa") or "",
        "partes": " - ".join(x for x in (sind, caso.get("empresa") or "") if x),
        "modalidad": (caso.get("modalidad") or "").upper(),
        "link": caso.get("link_meet") or "",
        "gedo": " / ".join(gedos),
        "documento": caso.get("documento_notificado") or "",
        "estado": estado,
        "nada": None,
    }


# ---------------------------------------------------------------- EXCEL
def agendar_excel(ruta, datos, estado_mail, t):
    if not ruta or not (os.path.exists(ruta) or ruta.lower().startswith("http")):
        raise RuntimeError(f"No encuentro el Excel de agenda: {ruta}")
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        xl, wb, abierto = _abrir_excel(ruta, win32com.client)
        ws = wb.ActiveSheet if abierto else wb.Worksheets(1)
        used = ws.UsedRange
        fila0, col0 = used.Row, used.Column
        nfil, ncol = used.Rows.Count, used.Columns.Count
        # buscar la fila de títulos: la primera (de las 10 primeras) con 3+ celdas de texto
        fila_tit = None
        for r in range(fila0, fila0 + min(nfil, 10)):
            textos = [ws.Cells(r, c).Value for c in range(col0, col0 + ncol)]
            if sum(1 for x in textos if isinstance(x, str) and x.strip()) >= 3:
                fila_tit = r
                break
        if fila_tit is None:
            raise RuntimeError("No encontré la fila de títulos en el Excel de agenda.")
        mapa = {}
        for c in range(col0, col0 + ncol):
            campo = _campo_de_titulo(ws.Cells(fila_tit, c).Value)
            if campo and campo not in mapa.values():
                mapa[c] = campo
        if not mapa:
            raise RuntimeError("No reconocí ninguna columna (Fecha, Hora, Expediente, Sindicato, Empresa…).")
        # última fila con datos en las columnas mapeadas
        ultima = fila_tit
        for c in mapa:
            celda = ws.Cells(ws.Rows.Count, c).End(-4162)  # xlUp
            ultima = max(ultima, celda.Row)
        nueva = ultima + 1
        vals = _valores(datos, estado_mail)
        # copiar formato de la fila anterior (si es una fila de datos)
        if ultima > fila_tit:
            ws.Rows(ultima).Copy()
            ws.Rows(nueva).PasteSpecial(-4122)  # xlPasteFormats
            xl.CutCopyMode = False
        escritos = []
        for c, campo in mapa.items():
            v = vals.get(campo)
            if v in (None, ""):
                continue
            celda = ws.Cells(nueva, c)
            if isinstance(v, dt.datetime):
                # fecha como número de serie de Excel (evita corrimientos por zona horaria)
                celda.Value = (v - dt.datetime(1899, 12, 30)).days
                if str(celda.NumberFormat) in ("General", "@"):
                    celda.NumberFormat = "dd/mm/yyyy"
            else:
                celda.Value = v
            escritos.append(str(ws.Cells(fila_tit, c).Value))
        wb.Save()
        if not abierto:
            wb.Close(SaveChanges=False)
        return f"Fila {nueva} agregada ({', '.join(escritos)})"
    finally:
        pythoncom.CoUninitialize()


def _abrir_excel(ruta, client):
    nombre = os.path.basename(ruta).lower()
    try:
        xl = client.GetActiveObject("Excel.Application")
        for wb in xl.Workbooks:
            if wb.FullName.lower() == ruta.lower() or wb.Name.lower() == nombre:
                return xl, wb, True
    except Exception:
        xl = None
    if xl is None:
        xl = client.DispatchEx("Excel.Application")
        xl.Visible = False
        xl.DisplayAlerts = False
    wb = xl.Workbooks.Open(ruta)
    return xl, wb, False


# ---------------------------------------------------------------- WORD
def agendar_word(ruta, datos, estado_mail, t):
    if not ruta or not (os.path.exists(ruta) or ruta.lower().startswith("http")):
        raise RuntimeError(f"No encuentro el Word de agenda: {ruta}")
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    try:
        wd, doc, abierto = _abrir_word(ruta, win32com.client)
        vals = _valores(datos, estado_mail)
        caso = datos.get("caso") or {}
        if doc.Tables.Count > 0:
            tabla = doc.Tables(doc.Tables.Count)
            titulos = [_texto_celda(tabla.Cell(1, c)) for c in range(1, tabla.Columns.Count + 1)]
            fila = tabla.Rows.Add()
            escritos = []
            for i, tit in enumerate(titulos, start=1):
                campo = _campo_de_titulo(tit)
                v = vals.get(campo) if campo else None
                if isinstance(v, dt.datetime):
                    v = v.strftime("%d/%m/%Y")
                if v:
                    fila.Cells(i).Range.Text = str(v)
                    escritos.append(tit)
            det = f"Fila agregada a la tabla ({', '.join(escritos) or 'sin columnas reconocidas'})"
        else:
            texto = caso.get("texto_interno") or ""
            if not texto:
                raise RuntimeError("El caso no tiene texto interno para agendar.")
            _agregar_parrafo(doc, texto)
            det = "Texto interno agregado al final del documento"
        doc.Save()
        if not abierto:
            doc.Close(SaveChanges=False)
        return det
    finally:
        pythoncom.CoUninitialize()


def _texto_celda(celda):
    return celda.Range.Text.replace("\r\x07", "").strip()


def _agregar_parrafo(doc, texto):
    """Agrega un párrafo al final, con negrita en lo que está entre ** **."""
    pos = doc.Content.End - 1                      # justo antes de la marca final del documento
    if doc.Range(0, pos).Text.strip():             # si el documento no está vacío, párrafo nuevo
        doc.Range(pos, pos).InsertAfter("\r")
        pos += 1
    for parte in re.split(r"(\*\*.+?\*\*)", texto):
        if not parte:
            continue
        negrita = parte.startswith("**") and parte.endswith("**")
        contenido = parte[2:-2] if negrita else parte
        r = doc.Range(pos, pos)
        r.InsertAfter(contenido)
        r.Font.Bold = negrita
        pos = r.End


def _abrir_word(ruta, client):
    try:
        wd = client.GetActiveObject("Word.Application")
        for d in wd.Documents:
            if d.FullName.lower() == ruta.lower():
                return wd, d, True
    except Exception:
        wd = None
    if wd is None:
        wd = client.DispatchEx("Word.Application")
        wd.Visible = False
    doc = wd.Documents.Open(ruta)
    return wd, doc, False
