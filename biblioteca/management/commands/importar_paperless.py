"""Importa etiquetas, corresponsales, tipos, campos y documentos de Paperless.

Lee la base de datos SQLite de paperless-ngx **en solo lectura** (no la toca) y
vuelca sus metadatos en Alejandria, emparejándolos con los ficheros que ya están
en la carpeta de originales. Es la vía para no perder el trabajo de clasificación
hecho en Paperless al cambiar de gestor.
"""
import datetime as dt
import sqlite3
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from ... import busqueda
from ...escaner import alta
from ...models import (
    CampoPersonalizado, Corresponsal, Documento, Etiqueta, TipoDocumento, ValorCampo,
)

# data_type de paperless -> tipo de campo de Alejandria
TIPOS_CAMPO = {
    1: CampoPersonalizado.TEXTO, "string": CampoPersonalizado.TEXTO,
    2: CampoPersonalizado.URL, "url": CampoPersonalizado.URL,
    3: CampoPersonalizado.FECHA, "date": CampoPersonalizado.FECHA,
    4: CampoPersonalizado.BOOLEANO, "boolean": CampoPersonalizado.BOOLEANO,
    5: CampoPersonalizado.NUMERO, "integer": CampoPersonalizado.NUMERO,
    6: CampoPersonalizado.NUMERO, "float": CampoPersonalizado.NUMERO,
    7: CampoPersonalizado.MONEDA, "monetary": CampoPersonalizado.MONEDA,
    9: CampoPersonalizado.SELECCION, "select": CampoPersonalizado.SELECCION,
}
COLUMNAS_VALOR = (
    "value_text", "value_bool", "value_url", "value_date", "value_int",
    "value_float", "value_monetary", "value_select",
)


def localizar_base(raiz: Path):
    """Busca db.sqlite3 en las rutas donde suele dejarlo paperless."""
    candidatas = [
        raiz / "db.sqlite3",
        raiz / "data" / "db.sqlite3",
        raiz / "config" / "db.sqlite3",
        raiz / "data" / "data" / "db.sqlite3",
    ]
    for c in candidatas:
        if c.is_file():
            return c
    encontradas = sorted(raiz.rglob("db.sqlite3"))
    return encontradas[0] if encontradas else None


def emparejar(Modelo, paperless_id, nombre, extra=None, clave_nombre="nombre"):
    """Reutiliza el objeto que ya exista (el escaneo crea corresponsales por carpeta).

    Prioridad: el que ya tenga este paperless_id > el que se llame igual > uno nuevo.
    Así importar dos veces no duplica nada ni choca con los nombres únicos.
    """
    valores = {clave_nombre: nombre, **(extra or {})}
    obj = Modelo.objects.filter(paperless_id=paperless_id).first()
    if obj is None:
        obj = Modelo.objects.filter(**{clave_nombre: nombre}).first()
    if obj is None:
        return Modelo.objects.create(paperless_id=paperless_id, **valores)
    for campo, valor in valores.items():
        setattr(obj, campo, valor)
    obj.paperless_id = paperless_id
    obj.save()
    return obj


class Command(BaseCommand):
    help = "Importa los metadatos de una instalación de paperless-ngx (SQLite)."

    def add_arguments(self, parser):
        parser.add_argument(
            "raiz", nargs="?", default=None,
            help="Carpeta de paperless (la que tiene data/, config/, consume/).",
        )
        parser.add_argument("--db", help="Ruta directa al db.sqlite3 de paperless.")
        parser.add_argument(
            "--originales",
            help="Carpeta de originales de paperless. Por defecto, la biblioteca de Alejandria.",
        )
        parser.add_argument(
            "--sustituir-etiquetas", action="store_true",
            help="Deja en cada documento SOLO las etiquetas de paperless. Por defecto se "
                 "añaden a las que ya tuviera (por ejemplo, las sacadas de las carpetas).",
        )
        parser.add_argument(
            "--simular", action="store_true", help="Enseña lo que haría sin escribir nada."
        )

    def handle(self, *args, **o):
        if o["db"]:
            base = Path(o["db"]).expanduser()
        elif o["raiz"]:
            base = localizar_base(Path(o["raiz"]).expanduser())
        else:
            raise CommandError("Indica la carpeta de paperless o --db con la ruta del db.sqlite3.")
        if not base or not base.is_file():
            raise CommandError("No encuentro el db.sqlite3 de paperless.")

        originales = Path(o["originales"]).expanduser() if o["originales"] else Path(settings.BIBLIOTECA_DIR)
        self.stdout.write(f"Base de paperless: {base}")
        self.stdout.write(f"Originales:        {originales}")
        if o["simular"]:
            self.stdout.write(self.style.WARNING("Modo simulación: no se escribe nada."))

        con = sqlite3.connect(f"file:{base}?mode=ro&immutable=1", uri=True)
        con.row_factory = sqlite3.Row
        self.sustituir = o["sustituir_etiquetas"]
        try:
            self.importar(con, originales, o["simular"])
        finally:
            con.close()

    # --- Importación ---------------------------------------------------------
    def importar(self, con, originales, simular):
        cuenta = {"etiquetas": 0, "corresponsales": 0, "tipos": 0, "campos": 0,
                  "documentos": 0, "emparejados": 0, "sin_fichero": 0, "valores": 0}

        etiquetas, corresponsales, tipos, campos = {}, {}, {}, {}

        for fila in self.tabla(con, "documents_tag"):
            cuenta["etiquetas"] += 1
            if simular:
                continue
            obj = emparejar(
                Etiqueta, fila["id"], fila["name"][:128],
                {
                    "color": (fila["color"] or "#8a8f98")[:7],
                    "inbox": bool(fila["is_inbox_tag"]) if "is_inbox_tag" in fila.keys() else False,
                },
            )
            etiquetas[fila["id"]] = obj

        for fila in self.tabla(con, "documents_correspondent"):
            cuenta["corresponsales"] += 1
            if simular:
                continue
            obj = emparejar(Corresponsal, fila["id"], fila["name"][:190])
            corresponsales[fila["id"]] = obj

        for fila in self.tabla(con, "documents_documenttype"):
            cuenta["tipos"] += 1
            if simular:
                continue
            obj = emparejar(TipoDocumento, fila["id"], fila["name"][:190])
            tipos[fila["id"]] = obj

        for fila in self.tabla(con, "documents_customfield"):
            cuenta["campos"] += 1
            if simular:
                continue
            extra = fila["extra_data"] if "extra_data" in fila.keys() else None
            opciones = self.opciones_seleccion(extra)
            obj = emparejar(
                CampoPersonalizado, fila["id"], fila["name"][:190],
                {
                    "tipo": TIPOS_CAMPO.get(fila["data_type"], CampoPersonalizado.TEXTO),
                    "opciones": opciones,
                },
            )
            campos[fila["id"]] = obj

        # Etiquetas por documento
        por_documento = {}
        for fila in self.tabla(con, "documents_document_tags"):
            por_documento.setdefault(fila["document_id"], []).append(fila["tag_id"])

        documentos = self.tabla(con, "documents_document")
        for fila in documentos:
            cuenta["documentos"] += 1
            if simular:
                continue
            doc = self.importar_documento(
                fila, originales, corresponsales, tipos, etiquetas,
                por_documento.get(fila["id"], []), cuenta,
            )
            if doc is None:
                continue

        # Valores de campos personalizados
        if not simular and campos:
            for fila in self.tabla(con, "documents_customfieldinstance"):
                doc = Documento.objects.filter(paperless_id=fila["document_id"]).first()
                campo = campos.get(fila["field_id"])
                if not doc or not campo:
                    continue
                valor = self.valor_de(fila)
                if valor in (None, ""):
                    continue
                ValorCampo.objects.update_or_create(
                    documento=doc, campo=campo, defaults={"valor": str(valor)}
                )
                cuenta["valores"] += 1
                busqueda.indexar(doc)

        self.stdout.write(
            self.style.SUCCESS(
                "Importado: {etiquetas} etiquetas, {corresponsales} corresponsales, "
                "{tipos} tipos, {campos} campos, {documentos} documentos "
                "({emparejados} con fichero localizado, {sin_fichero} sin fichero), "
                "{valores} valores de campo.".format(**cuenta)
            )
        )
        if cuenta["sin_fichero"]:
            self.stdout.write(
                self.style.WARNING(
                    "Los documentos sin fichero se han quedado fuera. Revisa que --originales "
                    "apunte a la carpeta de originales de paperless."
                )
            )

    @transaction.atomic
    def importar_documento(self, fila, originales, corresponsales, tipos, etiquetas, ids_etiquetas, cuenta):
        claves = fila.keys()
        nombre = fila["filename"] if "filename" in claves else None
        ruta_rel = None
        if nombre:
            candidata = originales / nombre
            if candidata.is_file():
                ruta_rel = Path(nombre).as_posix()
            else:
                # Fallback: mismo nombre de fichero en cualquier subcarpeta.
                base = Path(nombre).name
                existente = Documento.objects.filter(ruta__endswith=f"/{base}").first()
                if existente:
                    ruta_rel = existente.ruta
                else:
                    encontrada = next(originales.rglob(base), None)
                    if encontrada:
                        ruta_rel = encontrada.relative_to(originales).as_posix()
        if ruta_rel is None:
            cuenta["sin_fichero"] += 1
            return None

        doc = Documento.objects.filter(ruta=ruta_rel).first()
        if doc is None:
            doc = alta(ruta_rel, adivinar=False)
        cuenta["emparejados"] += 1

        doc.paperless_id = fila["id"]
        doc.titulo = (fila["title"] or doc.titulo)[:300]
        doc.fecha = self.a_fecha(fila["created"] if "created" in claves else None)
        doc.notas = doc.notas or ""
        if "correspondent_id" in claves and fila["correspondent_id"]:
            doc.corresponsal = corresponsales.get(fila["correspondent_id"])
        if "document_type_id" in claves and fila["document_type_id"]:
            doc.tipo = tipos.get(fila["document_type_id"])
        doc.save()
        marcadas = [etiquetas[t] for t in ids_etiquetas if t in etiquetas]
        if self.sustituir:
            doc.etiquetas.set(marcadas)
        elif marcadas:
            # Se añaden, no se reemplazan: las etiquetas que vengan de las
            # carpetas del disco son información igual de válida y se perderían.
            doc.etiquetas.add(*marcadas)
        busqueda.indexar(doc)
        return doc

    # --- Utilidades ----------------------------------------------------------
    def tabla(self, con, nombre):
        try:
            return con.execute(f"SELECT * FROM {nombre}").fetchall()
        except sqlite3.Error:
            self.stdout.write(self.style.WARNING(f"Tabla {nombre} no encontrada, se salta."))
            return []

    @staticmethod
    def opciones_seleccion(extra):
        if not extra:
            return []
        import json

        try:
            datos = json.loads(extra)
        except (TypeError, ValueError):
            return []
        opciones = datos.get("select_options", []) if isinstance(datos, dict) else []
        salida = []
        for o in opciones:
            salida.append(o.get("label") if isinstance(o, dict) else str(o))
        return [s for s in salida if s]

    @staticmethod
    def valor_de(fila):
        for columna in COLUMNAS_VALOR:
            if columna in fila.keys() and fila[columna] not in (None, ""):
                valor = fila[columna]
                if columna == "value_bool":
                    return "1" if valor else "0"
                return valor
        return None

    @staticmethod
    def a_fecha(valor):
        if not valor:
            return None
        texto = str(valor)[:10]
        try:
            return dt.date.fromisoformat(texto)
        except ValueError:
            return None
