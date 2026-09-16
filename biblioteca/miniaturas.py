"""Miniaturas: se generan bajo demanda, en un subproceso, y se cachean en disco.

Nunca se carga una imagen en el proceso de Django (nada de Pillow): tira de
`pdftoppm` (poppler) y `convert` (ImageMagick), con límite de memoria y de
tiempo, de forma que un escaneo de 600 ppp no pueda tumbar el NAS.
"""
import logging
import resource
import shutil
import subprocess
import tempfile
from pathlib import Path

from django.conf import settings

log = logging.getLogger(__name__)

PDFTOPPM = shutil.which("pdftoppm")
# vipsthumbnail es la opción preferida para imágenes: hace el trabajo en streaming
# y con una fracción de la memoria de ImageMagick (importa en un NAS de 4 GB).
VIPS = shutil.which("vipsthumbnail")
CONVERT = shutil.which("convert") or shutil.which("magick")


def _limitar_memoria():
    tope = settings.MINIATURA_LIMITE_MB * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (tope, tope))


def _correr(orden):
    try:
        subprocess.run(
            orden,
            check=True,
            timeout=settings.MINIATURA_TIMEOUT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=_limitar_memoria,
        )
        return True
    except Exception as e:
        log.info("Miniatura fallida (%s): %s", orden[0], e)
        return False


def ruta_cache(documento):
    clave = documento.hash or f"id{documento.pk}"
    return Path(settings.MINIATURAS_DIR) / clave[:2] / f"{clave}.jpg"


def obtener(documento):
    """Devuelve la ruta de la miniatura, generándola si hace falta. None si no hay."""
    if not settings.MINIATURAS_ACTIVAS:
        return None
    destino = ruta_cache(documento)
    if destino.is_file() and destino.stat().st_size > 0:
        return destino
    origen = documento.ruta_absoluta
    if not origen.is_file():
        return None
    destino.parent.mkdir(parents=True, exist_ok=True)
    ancho = settings.MINIATURA_ANCHO
    ok = False
    if documento.es_pdf and PDFTOPPM:
        with tempfile.TemporaryDirectory(dir=settings.MINIATURAS_DIR) as tmp:
            base = Path(tmp) / "m"
            ok = _correr([
                PDFTOPPM, "-jpeg", "-r", "72", "-f", "1", "-l", "1",
                "-scale-to-x", str(ancho), "-scale-to-y", "-1",
                "-singlefile", str(origen), str(base),
            ])
            generado = base.with_suffix(".jpg")
            if ok and generado.is_file():
                shutil.move(str(generado), destino)
            else:
                ok = False
    elif documento.es_imagen and VIPS:
        ok = _correr([
            VIPS, str(origen), "--size", f"{ancho}x{ancho}", "--output",
            f"{destino}[Q=78,strip]",
        ])
    elif documento.es_imagen and CONVERT:
        ok = _correr([
            CONVERT, "-limit", "memory", f"{settings.MINIATURA_LIMITE_MB // 2}MiB",
            "-limit", "map", f"{settings.MINIATURA_LIMITE_MB}MiB",
            f"{origen}[0]", "-auto-orient", "-thumbnail", f"{ancho}x{ancho}>",
            "-quality", "78", str(destino),
        ])
    return destino if ok and destino.is_file() else None


def olvidar(documento):
    ruta = ruta_cache(documento)
    if ruta.is_file():
        try:
            ruta.unlink()
        except OSError:
            pass
