import logging
import os
import threading

from django.apps import AppConfig
from django.conf import settings

log = logging.getLogger(__name__)


class BibliotecaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "biblioteca"
    verbose_name = "Biblioteca"

    def ready(self):
        from . import senales  # noqa: F401

        # El vigilante puede ir como hilo dentro del proceso web (escritorio,
        # cuesta ~0 MB) o como proceso aparte (Docker). Nunca los dos: en Docker
        # la web arranca con ALEJANDRIA_VIGILANTE=0.
        if settings.VIGILANTE_ACTIVO and os.environ.get("RUN_MAIN") != "false":
            from .vigilante import bucle

            if not any(h.name == "alejandria-vigilante" for h in threading.enumerate()):
                hilo = threading.Thread(
                    target=bucle, name="alejandria-vigilante", daemon=True
                )
                hilo.start()
                log.info("Vigilante del buzón arrancado dentro del proceso web.")
