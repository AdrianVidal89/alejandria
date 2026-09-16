"""Vigilante del buzón como proceso independiente (el de Docker)."""
from django.conf import settings
from django.core.management.base import BaseCommand

from ...vigilante import bucle


class Command(BaseCommand):
    help = "Vigila la carpeta de entrada y archiva lo que aparezca."

    def handle(self, *args, **opciones):
        self.stdout.write(
            f"Vigilando {settings.ENTRADA_DIR} cada {settings.VIGILANTE_INTERVALO}s. Ctrl+C para parar."
        )
        try:
            bucle()
        except KeyboardInterrupt:
            self.stdout.write("Vigilante detenido.")
