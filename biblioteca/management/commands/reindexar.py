"""Reconstruye el índice de búsqueda desde cero."""
from django.core.management.base import BaseCommand
from django.db import connection

from ... import busqueda
from ...models import Documento


class Command(BaseCommand):
    help = "Reconstruye el índice FTS5 de búsqueda."

    def handle(self, *args, **opciones):
        with connection.cursor() as c:
            c.execute(busqueda.CREAR_TABLA)
        documentos = Documento.objects.con_relaciones().prefetch_related("valores__campo")
        busqueda.reconstruir(documentos.iterator(chunk_size=200))
        self.stdout.write(self.style.SUCCESS(f"Índice reconstruido: {documentos.count()} documentos."))
