"""Recorre la biblioteca y sincroniza la base de datos con los ficheros."""
from django.core.management.base import BaseCommand

from ...escaner import escanear


class Command(BaseCommand):
    help = "Da de alta ficheros nuevos, detecta movidos y marca los que faltan."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rehashear", action="store_true",
            help="Recalcula la huella de TODOS los ficheros (lento, para verificar integridad).",
        )
        parser.add_argument(
            "--carpetas-como-etiquetas", action="store_true",
            help="Convierte cada carpeta en una etiqueta (jerárquica) en vez de "
                 "tomarla por el corresponsal. Para bibliotecas organizadas a mano.",
        )
        parser.add_argument(
            "--sin-deducir", action="store_true",
            help="No intenta sacar título, fecha ni corresponsal de la ruta.",
        )

    def handle(self, *args, **opciones):
        self.stdout.write("Escaneando la biblioteca…")
        resumen = escanear(
            rehashear=opciones["rehashear"],
            adivinar=not opciones["sin_deducir"],
            carpetas_como_etiquetas=opciones["carpetas_como_etiquetas"],
            informar=lambda t: self.stdout.write(t),
        )
        self.stdout.write(
            self.style.SUCCESS(
                "Listo: {nuevos} nuevos, {movidos} movidos, {actualizados} actualizados, "
                "{ausentes} ausentes, {vistos} ficheros recorridos.".format(**resumen)
            )
        )
