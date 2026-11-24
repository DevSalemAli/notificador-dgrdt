"""Carpetas que usa el agente (fuera de OneDrive y fuera de AppData)."""
import os

# Carpeta de trabajo del agente: C:\Users\<usuario>\NotificadorDGRDT
BASE = os.path.join(os.path.expanduser("~"), "NotificadorDGRDT")

PERFIL_CHROME = os.path.join(BASE, "chrome-perfil")   # sesión de Outlook web y GEDO
CASOS = os.path.join(BASE, "casos")                   # un subcarpeta por notificación
LOGS = os.path.join(BASE, "logs")
CONFIG = os.path.join(BASE, "config.json")

for _d in (BASE, CASOS, LOGS):
    os.makedirs(_d, exist_ok=True)
