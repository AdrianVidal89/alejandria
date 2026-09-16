"""Filtrado por facetas.

La idea, que es la de cualquier buscador con filtros que funcione bien: los
filtros se combinan entre sí (Y), pero varios valores del MISMO filtro suman (O).
Y lo importante — los recuentos de la barra lateral se calculan sobre el conjunto
ya filtrado por todo lo demás, no sobre la biblioteca entera. Por eso existe el
parámetro `excepto`: para contar cuántos documentos tendría cada corresponsal hay
que aplicar todos los filtros menos el de corresponsales, o el propio filtro
escondería las demás opciones.
"""
from urllib.parse import urlencode

from django.db.models import Count, Q

from . import busqueda
from .models import Documento, Etiqueta

ORDENES = {
    "recientes": ("-anadido", "Añadidos primero"),
    "antiguos": ("anadido", "Más antiguos primero"),
    "fecha": ("-fecha", "Fecha del documento ↓"),
    "fecha_asc": ("fecha", "Fecha del documento ↑"),
    "titulo": ("titulo", "Título A→Z"),
    "tamano": ("-bytes", "Más pesados"),
    "abierto": ("-abierto", "Vistos hace poco"),
}

FACETAS = ("etiqueta", "corresponsal", "tipo", "anio")


def modo_etiquetas(peticion):
    """Cómo se combinan varias etiquetas elegidas.

    «y» (por defecto) = filtro doble: se exigen todas. Al elegir una, en la barra
    lateral solo quedan las que conviven con ella en algún documento, así que
    cada clic estrecha de verdad la búsqueda.
    «o» = suman. Necesario cuando las etiquetas se excluyen entre sí, como las
    que salen de las carpetas, donde exigir dos daría siempre cero.
    """
    return "o" if peticion.GET.get("modo_etiquetas") == "o" else "y"


def enteros(peticion, clave):
    valores = []
    for bruto in peticion.GET.getlist(clave):
        for trozo in str(bruto).split(","):
            if trozo.strip().isdigit():
                valores.append(int(trozo))
    return valores


def seleccion(peticion):
    return {clave: enteros(peticion, clave) for clave in FACETAS}


def rama_de(ids_etiquetas):
    """Amplía una lista de etiquetas con todas sus hijas."""
    ampliadas = set()
    for e in Etiqueta.objects.filter(pk__in=ids_etiquetas):
        ampliadas |= e.descendientes_ids()
    return ampliadas


# --- Construcción del conjunto ------------------------------------------------
def _ambito(qs, peticion):
    """Colección elegida: todos, favoritos, sin clasificar, papelera, problemas."""
    vista = peticion.GET.get("vista", "")
    qs = qs.filter(papelera=(vista == "papelera"))
    if vista == "favoritos":
        qs = qs.filter(favorito=True)
    elif vista == "sin_clasificar":
        qs = qs.filter(etiquetas__isnull=True, corresponsal__isnull=True, tipo__isnull=True)
    elif vista == "problemas":
        qs = qs.exclude(estado=Documento.OK)
    return qs


def _texto(qs, peticion):
    """Búsqueda libre. Devuelve (queryset, ids por relevancia o None)."""
    texto = peticion.GET.get("q", "").strip()
    if not texto:
        return qs, None
    ids = busqueda.ids_que_coinciden(texto)
    if ids is None:
        return qs, None
    return qs.filter(pk__in=ids), ids


def _otros(qs, peticion):
    """Filtros que no son facetas de la barra lateral."""
    get = peticion.GET

    extension = get.get("ext", "").strip().lower().lstrip(".")
    if extension:
        qs = qs.filter(ruta__iendswith=f".{extension}")

    carpeta = get.get("carpeta", "").strip("/")
    if carpeta:
        qs = qs.filter(Q(ruta__startswith=f"{carpeta}/") | Q(ruta=carpeta))

    if get.get("desde"):
        qs = qs.filter(fecha__gte=get["desde"])
    if get.get("hasta"):
        qs = qs.filter(fecha__lte=get["hasta"])

    campo, valor = get.get("campo", ""), get.get("valor", "").strip()
    if campo.isdigit() and valor:
        qs = qs.filter(valores__campo_id=int(campo), valores__valor__icontains=valor)
    return qs


def _faceta(qs, clave, valores, peticion):
    if not valores:
        return qs
    if clave == "etiqueta":
        if modo_etiquetas(peticion) == "y":
            # Una llamada a filter() por etiqueta: se exigen TODAS, cada una con
            # su rama. Útil con etiquetas transversales (Urgente + Fiscal).
            for uno in valores:
                qs = qs.filter(etiquetas__in=rama_de([uno]))
        else:
            # Por defecto suman. Es lo que hace falta cuando las etiquetas vienen
            # de carpetas y por tanto se excluyen entre sí: exigirlas todas daría
            # siempre cero y no se podría elegir una segunda.
            qs = qs.filter(etiquetas__in=rama_de(valores))
        return qs.distinct()
    if clave == "corresponsal":
        return qs.filter(corresponsal_id__in=valores)
    if clave == "tipo":
        return qs.filter(tipo_id__in=valores)
    if clave == "anio":
        return qs.filter(fecha__year__in=valores)
    return qs


def conjunto(peticion, excepto=()):
    """Documentos que cumplen los filtros, saltándose las facetas de `excepto`."""
    qs = Documento.objects.all()
    if "vista" in excepto:
        qs = qs.filter(papelera=False)
    else:
        qs = _ambito(qs, peticion)
    qs, _ = _texto(qs, peticion)
    qs = _otros(qs, peticion)
    elegido = seleccion(peticion)
    for clave in FACETAS:
        if clave in excepto:
            continue
        qs = _faceta(qs, clave, elegido[clave], peticion)
    return qs


def filtrar(peticion):
    """Conjunto final ya ordenado, más el resumen de filtros aplicados."""
    get = peticion.GET
    qs = conjunto(peticion).con_relaciones()

    texto = get.get("q", "").strip()
    ids_relevancia = busqueda.ids_que_coinciden(texto) if texto else None

    orden = get.get("orden", "recientes")
    if ids_relevancia and orden == "recientes":
        from django.db.models import Case, IntegerField, Value, When

        posicion = Case(
            *[When(pk=pk, then=Value(i)) for i, pk in enumerate(ids_relevancia)],
            default=Value(len(ids_relevancia)),
            output_field=IntegerField(),
        )
        qs = qs.annotate(_relevancia=posicion).order_by("_relevancia", "-id")
    else:
        qs = qs.order_by(ORDENES.get(orden, ORDENES["recientes"])[0], "-id")

    elegido = seleccion(peticion)
    return qs, {
        "q": texto,
        "vista": get.get("vista", ""),
        "orden": orden,
        "etiquetas_sel": elegido["etiqueta"],
        "corresponsales_sel": elegido["corresponsal"],
        "tipos_sel": elegido["tipo"],
        "anios_sel": elegido["anio"],
        "ext": get.get("ext", ""),
        "carpeta": get.get("carpeta", "").strip("/"),
        "desde": get.get("desde", ""),
        "hasta": get.get("hasta", ""),
        "campo": get.get("campo", ""),
        "valor": get.get("valor", ""),
        "modo_etiquetas": modo_etiquetas(peticion),
        "hay_filtros": bool(
            texto or get.get("vista") or get.get("ext") or get.get("carpeta")
            or any(elegido.values())
        ),
    }


# --- Recuentos de la barra lateral --------------------------------------------
def recuento_etiquetas(peticion):
    """Documentos por etiqueta contando toda su rama y sin duplicar.

    Se calcula sobre el conjunto ya filtrado —etiquetas puestas incluidas—, así
    que cada número responde a «cuántos documentos me quedarían si además pulso
    esta». Las que se quedan a cero desaparecen de la lista: es lo que mantiene
    la barra lateral corta cuando hay muchas etiquetas.

    El recorrido se hace en Python sobre los pares (documento, etiqueta) porque
    un documento etiquetado a la vez con «Vehiculos» y «Vehiculos/Volvo» tiene
    que contar una sola vez en la rama de «Vehiculos».
    """
    # Sumando (O), el recuento de cada etiqueta se calcula sin aplicar el filtro
    # de etiquetas, o al elegir una las demás se irían a cero y no se podrían
    # añadir. Exigiéndolas todas (Y) sí se aplica: cada número dice cuántos
    # quedarían al añadir esa.
    excepto = {"etiqueta"} if modo_etiquetas(peticion) == "o" else set()  # ver modo_etiquetas()
    base = conjunto(peticion, excepto=excepto)
    pares = (
        Documento.etiquetas.through.objects
        .filter(documento_id__in=base.values("pk"))
        .values_list("etiqueta_id", "documento_id")
    )
    directos = {}
    for etiqueta_id, documento_id in pares:
        directos.setdefault(etiqueta_id, set()).add(documento_id)

    etiquetas = list(Etiqueta.objects.all())
    hijas = {}
    for e in etiquetas:
        hijas.setdefault(e.padre_id, []).append(e)

    totales, propios = {}, {}

    def recorrer(etiqueta):
        acumulado = set(directos.get(etiqueta.pk, ()))
        for hija in hijas.get(etiqueta.pk, []):
            acumulado |= recorrer(hija)
        totales[etiqueta.pk] = len(acumulado)
        propios[etiqueta.pk] = len(directos.get(etiqueta.pk, ()))
        return acumulado

    for raiz in hijas.get(None, []):
        recorrer(raiz)
    return totales, propios


def recuento_simple(peticion, faceta, campo):
    # .order_by() sin argumentos es imprescindible: el orden por defecto del
    # modelo se colaría en el GROUP BY y partiría cada recuento en varias filas.
    base = conjunto(peticion, excepto={faceta}).order_by()
    filas = base.values(campo).annotate(n=Count("id", distinct=True))
    return {fila[campo]: fila["n"] for fila in filas if fila[campo] is not None}


# --- Enlaces ------------------------------------------------------------------
def consulta_actual(peticion, **cambios):
    datos = peticion.GET.copy()
    datos.pop("doc", None)
    datos.pop("pagina", None)
    for clave, valor in cambios.items():
        datos.pop(clave, None)
        if valor is None:
            continue
        if isinstance(valor, (list, tuple, set)):
            for v in valor:
                datos.appendlist(clave, str(v))
        else:
            datos[clave] = str(valor)
    return urlencode(datos, doseq=True)


def alternar(lista, valor):
    lista = list(lista)
    if valor in lista:
        lista.remove(valor)
    else:
        lista.append(valor)
    return lista
