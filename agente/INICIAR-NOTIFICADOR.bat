@echo off
rem Abre el Notificador DGRDT (agente local + ventana de Chrome).
set "PYW=%USERPROFILE%\NotificadorDGRDT\venv\Scripts\pythonw.exe"
if not exist "%PYW%" (
  echo Primera vez: instalando el agente...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0instalar.ps1"
)
start "" "%PYW%" "%~dp0agente.py"
