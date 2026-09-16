"""Traduce los parámetros de la URL a un queryset. Lo comparten la lista,
las acciones masivas y las búsquedas guardadas."""
from urllib.parse import urlencode

from django.db.models import Case, Count, IntegerField, Q, Value, When

from . import busqueda
from .models import Documento

ORDENES = {
    "recientes": ("-anadido", "Añadidos primero"),
    "antiguos": ("anadido", "Más antiguos primero"),
    "fecha": ("-fecha", "Fecha del documento ↓"),
    "fecha_asc": ("fecha", "Fecha del documento ↑"),
    "titulo": ("titulo", "Título A→Z"),
    "tamano": ("-bytes", "Más pesados"),
    "abierto": ("-abierto", "Vistos hace poco"),
}


def enteros(peticion, clave):
    valores = []
    for bruto in peticion.GET.getlist(clave):
        for trozo in str(bruto).split(","):
            if trozo.strip().isdigit():
                valores.append(int(trozo))
    return valores


def filtrar(peticion):
    """Devuelve (queryset, contexto de filtros aplicados)."""
    get = peticion.GET
    qs = Documento.objects.con_relaciones()

    vista = get.get("vista", "")
    if vista == "papelera":
        qs = qs.filter(papelera=True)
    else:
        qs = qs.filter(papelera=False)
    if vista == "favoritos":
        qs = qs.filter(favorito=True)
    elif vista == "sin_clasificar":
        qs = qs.filter(etiquetas__isnull=True, corresponsal__isnull=True, tipo__isnull=True)
    elif vista == "problemas":
        qs = qs.exclude(estado=Documento.OK)

    texto = get.get("q", "").strip()
    ids_relevancia = None
    if texto:
        ids_relevancia = busqueda.ids_que_coinciden(texto)
        if ids_relevancia is not None:
            qs = qs.filter(pk__in=ids_relevancia)

    etiquetas = enteros(peticion, "etiqueta")
    if etiquetas:
        from .models import Etiqueta

        # Filtrar por una etiqueta incluye su rama de hijas (árbol DevonThink).
        ampliadas = set()
        for e in Etiqueta.objects.filter(pk__in=etiquetas):
            ampliadas |= e.descendientes_ids()
        modo = get.get("modo_etiquetas", "o")
        if modo == "y" and len(etiquetas) > 1:
            for uno in etiquetas:
                qs = qs.filter(etiquetas__in=list(Etiqueta.objects.get(pk=uno).descendientes_ids()))
        else:
            qs = qs.filter(etiquetas__in=ampliadas)
        qs = qs.distinct()

    corresponsales = enteros(peticion, "corresponsal")
    if corresponsales:
        qs = qs.filter(corresponsal_id__in=corresponsales)
    tipos = enteros(peticion, "tipo")
    if tipos:
        qs = qs.filter(tipo_id__in=tipos)
    anios = enteros(peticion, "anio")
    if anios:
        qs = qs.filter(fecha__year__in=anios)

    extension = get.get("ext", "").strip().lower().lstrip(".")
    if extension:
        qs = qs.filter(ruta__iendswith=f".{extension}")

    carpeta = get.get("carpeta", "").strip("/")
    if carpeta:
        qs = qs.filter(Q(ruta__startswith=f"{carpeta}/") | Q(ruta=carpeta))

    desde, hasta = get.get("desde", ""), get.get("hasta", "")
    if desde:
        qs = qs.filter(fecha__gte=desde)
    if hasta:
        qs = qs.filter(fecha__lte=hasta)

    campo = get.get("campo", "")
    valor = get.get("valor", "").strip()
    if campo.isdigit() and valor:
        qs = qs.filter(valores__campo_id=int(campo), valores__valor__icontains=valor)

    orden = get.get("orden", "recientes")
    if ids_relevancia and orden == "recientes":
        # Buscando y sin orden explícito: manda la relevancia que da FTS5.
        posicion = Case(
            *[When(pk=pk, then=Value(i)) for i, pk in enumerate(ids_relevancia)],
            default=Value(len(ids_relevancia)),
            output_field=IntegerField(),
        )
        qs = qs.annotate(_relevancia=posicion).order_by("_relevancia", "-id")
    else:
        qs = qs.order_by(ORDENES.get(orden, ORDENES["recientes"])[0], "-id")

    return qs, {
        "q": texto,
        "vista": vista,
        "orden": orden,
        "etiquetas_sel": etiquetas,
        "corresponsales_sel": corresponsales,
        "tipos_sel": tipos,
        "anios_sel": anios,
        "ext": extension,
        "carpeta": carpeta,
        "desde": desde,
        "hasta": hasta,
        "campo": campo,
        "valor": valor,
        "modo_etiquetas": get.get("modo_etiquetas", "o"),
    }


def consulta_actual(peticion, **cambios):
    """Reconstruye la query string cambiando o quitando parámetros (valor None)."""
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


def facetas(qs_base):
    """Contadores para la barra lateral, calculados sobre lo visible."""
    return list(
        qs_base.exclude(fecha=None)
        .values("fecha__year")
        .annotate(n=Count("id"))
        .order_by("-fecha__year")
    )
