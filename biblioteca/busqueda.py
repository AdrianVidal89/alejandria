"""Búsqueda por texto con FTS5 de SQLite.

Se indexan METADATOS (título, ruta, corresponsal, tipo, etiquetas, notas y
campos personalizados), no el contenido de los ficheros: sin OCR y sin extraer
texto no hay picos de memoria ni procesos de fondo comiendo CPU.

La tabla virtual `documento_fts` usa el rowid = id del documento, así que no
duplica nada: ocupa unos pocos KB por cada mil documentos.
"""
import re

from django.db import connection

CREAR_TABLA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS documento_fts "
    "USING fts5(texto, tokenize=\"unicode61 remove_diacritics 2\")"
)
BORRAR_TABLA = "DROP TABLE IF EXISTS documento_fts"

_LIMPIA = re.compile(r"[^\w\sáéíóúüñç-]", re.UNICODE)


def indexar(documento):
    texto = documento.texto_indexable()
    with connection.cursor() as c:
        c.execute("DELETE FROM documento_fts WHERE rowid = %s", [documento.pk])
        c.execute(
            "INSERT INTO documento_fts(rowid, texto) VALUES (%s, %s)", [documento.pk, texto]
        )


def borrar(documento_id):
    with connection.cursor() as c:
        c.execute("DELETE FROM documento_fts WHERE rowid = %s", [documento_id])


def reconstruir(documentos):
    with connection.cursor() as c:
        c.execute("DELETE FROM documento_fts")
        c.executemany(
            "INSERT INTO documento_fts(rowid, texto) VALUES (?, ?)",
            [(d.pk, d.texto_indexable()) for d in documentos],
        )


def _consulta_fts(texto):
    """Pasa lo que escribe el usuario a sintaxis FTS5 segura, con prefijos."""
    from .models import normaliza

    palabras = [p for p in _LIMPIA.sub(" ", normaliza(texto)).split() if len(p) > 1]
    if not palabras:
        return None
    return " AND ".join(f'"{p}"*' for p in palabras)


def ids_que_coinciden(texto, limite=2000):
    """IDs de documentos que casan, ordenados por relevancia. None si no aplica."""
    consulta = _consulta_fts(texto)
    if not consulta:
        return None
    with connection.cursor() as c:
        try:
            c.execute(
                "SELECT rowid FROM documento_fts WHERE documento_fts MATCH %s "
                "ORDER BY rank LIMIT %s",
                [consulta, limite],
            )
        except Exception:  # consulta rara -> no rompemos la vista
            return []
        return [fila[0] for fila in c.fetchall()]
