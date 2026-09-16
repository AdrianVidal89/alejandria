from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from . import busqueda, miniaturas
from .models import Documento, ValorCampo


@receiver(post_save, sender=Documento)
def reindexar_documento(sender, instance, **kwargs):
    busqueda.indexar(instance)


@receiver(m2m_changed, sender=Documento.etiquetas.through)
def reindexar_etiquetas(sender, instance, action, **kwargs):
    if action in ("post_add", "post_remove", "post_clear") and isinstance(instance, Documento):
        busqueda.indexar(instance)


@receiver(post_save, sender=ValorCampo)
@receiver(post_delete, sender=ValorCampo)
def reindexar_valores(sender, instance, **kwargs):
    busqueda.indexar(instance.documento)


@receiver(post_delete, sender=Documento)
def limpiar_documento(sender, instance, **kwargs):
    busqueda.borrar(instance.pk)
    miniaturas.olvidar(instance)
