"""Impresión física de los documentos finales (opcional)."""
import os
import time


def impresora_predeterminada():
    try:
        import win32print
        return win32print.GetDefaultPrinter() or ""
    except Exception:
        return ""


VIRTUALES = ("microsoft print to pdf", "onenote", "xps", "fax", "pdf")


def imprimir(rutas, pw_estado, t):
    imp = impresora_predeterminada()
    if not imp or any(v in imp.lower() for v in VIRTUALES):
        raise RuntimeError(
            f"No hay una impresora física predeterminada (la actual es «{imp or 'ninguna'}»). "
            "Los PDF quedaron guardados en la carpeta del caso."
        )
    import win32api
    for r in rutas:
        t.anotar(f"Imprimiendo {os.path.basename(r)} en «{imp}»")
        # usa el programa de PDF predeterminado de Windows
        win32api.ShellExecute(0, "print", r, None, os.path.dirname(r), 0)
        time.sleep(4)
