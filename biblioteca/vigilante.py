"""Vigilante del buzón de entrada.

Sondeo por tiempo, no inotify: en un NAS con carpetas por SMB los eventos de
inotify no siempre llegan, y un `os.scandir` cada 30 segundos sobre una carpeta
casi vacía cuesta microsegundos. Cierra la conexión a la base entre pasadas para
no dejar memoria ni bloqueos colgando.
"""
import logging
import time

from django.conf import settings
from django.db import close_old_connections

from .escaner import procesar_entrada

log = logging.getLogger(__name__)


def una_pasada(memoria):
    close_old_connections()
    try:
        creados = procesar_entrada(memoria)
        if creados:
            log.info("Buzón: %s documento(s) archivado(s).", len(creados))
        return creados
    except Exception:
        log.exception("Fallo en la pasada del vigilante")
        return []
    finally:
        close_old_connections()


def bucle():
    memoria = {}
    time.sleep(3)  # deja que Django termine de arrancar
    while True:
        una_pasada(memoria)
        time.sleep(max(5, settings.VIGILANTE_INTERVALO))
