import json

from django import template
from django.utils.safestring import mark_safe

from ..vistas_filtros import alternar, consulta_actual, enteros

registrar = template.Library()
register = registrar  # Django busca este nombre


@registrar.simple_tag(takes_context=True)
def consulta(context, **cambios):
    """Query string actual con los cambios indicados (None borra el parámetro)."""
    return mark_safe(consulta_actual(context["request"], **cambios))


@registrar.simple_tag(takes_context=True)
def alterna_consulta(context, clave, valor):
    """Añade o quita un valor de un filtro múltiple (etiquetas, años…)."""
    peticion = context["request"]
    actuales = enteros(peticion, clave)
    valor = int(valor)
    return mark_safe(consulta_actual(peticion, **{clave: alternar(actuales, valor) or None}))


@registrar.simple_tag(takes_context=True)
def quitar_filtro(context, clave, valor=None):
    """Enlace que quita un filtro concreto (o todos los de esa clave)."""
    peticion = context["request"]
    if valor in (None, ""):
        return mark_safe(consulta_actual(peticion, **{clave: None}))
    restantes = [v for v in enteros(peticion, clave) if v != int(valor)]
    return mark_safe(consulta_actual(peticion, **{clave: restantes or None}))


@registrar.filter
def como_json(valor):
    """JSON para meterlo en un atributo data-. Sin mark_safe: lo escapa Django."""
    return json.dumps(valor, ensure_ascii=False)


@registrar.filter
def sangria(nivel):
    return 12 + int(nivel) * 14


@registrar.filter
def megas(valor):
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return "0"
    for unidad in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unidad == "TB":
            return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
        n /= 1024


@registrar.filter
def porcentaje(parte, total):
    try:
        return round(float(parte) / float(total) * 100)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0
