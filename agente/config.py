"""Configuración del agente (se guarda en C:\\Users\\<usuario>\\NotificadorDGRDT\\config.json)."""
import json
import os

from rutas import CONFIG

DEFAULTS = {
    # IA (Gemini, gratis): la clave se guarda acá para no tener que volver a cargarla
    "gemini_key": "",
    "gemini_model": "auto",
    # Outlook web
    "remitente": "relaciones.laborales@buenosaires.gob.ar",
    # Firma de texto del mail (se carga en ⚙️ Ajustes del agente). Vacía = se deja la firma de Outlook.
    "firma": "",
    # GEDO
    "gedo_tipo_documento": "NOTIF",
    "gedo_firmar": True,            # firmar yo mismo el documento al terminar
    # Expediente (SADE): vincular la notificación + el documento notificado y hacer el pase
    "vincular": True,
    "pase_motivo": "SE REMITEN LAS PRESENTES ACTUACIONES A FIN DE LA PROSECUCIÓN DEL TRÁMITE.",
    "pase_estado": "Tramitación",
    "pase_reparticion": "DGRDT",
    "pase_sector": "COLECTIVA",     # código del sector (se elige de la lista de la lupa)
    # Impresión física (además del PDF)
    "imprimir": False,
    # Agenda: link de Google (o ruta de un archivo de Office en la PC). Vacío = no agendar.
    # Los links reales se cargan en ⚙️ Ajustes del agente (quedan solo en esta PC, no en el código).
    "agenda_excel": "",
    "agenda_word": "",
    # Modo prueba: el mail va SOLO a esta dirección, GEDO y el expediente se saltean
    # y la agenda va a las copias de prueba
    "modo_prueba": False,
    "correo_prueba": "",
    "agenda_excel_prueba": "",
    "agenda_word_prueba": "",
}


def cargar():
    datos = dict(DEFAULTS)
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, "r", encoding="utf-8") as f:
                datos.update(json.load(f))
        except Exception:
            pass
    return datos


def guardar(nuevos):
    datos = cargar()
    for k, v in (nuevos or {}).items():
        if k in DEFAULTS:
            datos[k] = v
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    return datos
