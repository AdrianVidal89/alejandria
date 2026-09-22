"""Vistas de Alejandria.

Todo se renderiza en el servidor con plantillas de Django. Los paneles se
refrescan pidiendo trozos de HTML (el parámetro `parcial`), así que no hay
framework de JavaScript, ni compilación, ni un solo megabyte de node_modules.
"""
import datetime as dt
import logging
import os
import tempfile
import zipfile
import mimetypes
import threading
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponseBadRequest, JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import busqueda, escaner, miniaturas
from .models import (
    BusquedaGuardada, CampoPersonalizado, Corresponsal, Documento, Etiqueta,
    Registro, TipoDocumento, ValorCampo,
)
from .vistas_filtros import (
    ORDENES, conjunto, consulta_actual, cuenta_por_rama, enteros, filtrar,
    modo_etiquetas, recuento_etiquetas, recuento_simple,
)

log = logging.getLogger(__name__)


def acceso(vista):
    """Login obligatorio solo si ALEJANDRIA_LOGIN=1 (en Tailscale suele sobrar)."""
    protegida = login_required(vista)

    def envoltorio(peticion, *args, **kwargs):
        destino = protegida if settings.EXIGIR_LOGIN else vista
        return destino(peticion, *args, **kwargs)

    envoltorio.__name__ = vista.__name__
    return envoltorio


# --- Barra lateral ------------------------------------------------------------
def _arbol_etiquetas(peticion):
    """Etiquetas en árbol con el recuento de documentos que quedan bajo cada una.

    Se esconde una etiqueta si no alcanza a ningún documento del conjunto —sin
    contar el propio filtro de etiquetas— y también si no se cruza con las ya
    elegidas (`sin_cruce`). Esta segunda antes se pintaba atenuada en vez de
    desaparecer, para poder cruzarla igualmente; la lista se ensuciaba con
    etiquetas que no llevaban a ninguna parte, así que ahora se va. Con el modo
    «cualquiera» no se esconde nada por este motivo: ahí se suman, no se cruzan.

    Una etiqueta con descendencia visible se queda aunque ella misma no cruce:
    si se fuera, sus hijas colgarían de la nada.
    """
    resultado, alcance, propios = recuento_etiquetas(peticion)
    elegidas = set(enteros(peticion, "etiqueta"))
    exigir_todas = modo_etiquetas(peticion) == "y"

    etiquetas = list(Etiqueta.objects.all())
    hijas = {}
    for e in etiquetas:
        hijas.setdefault(e.padre_id, []).append(e)

    def rama(padre_id, nivel=0):
        salida = []
        for e in sorted(hijas.get(padre_id, []), key=lambda x: x.nombre.lower()):
            debajo = rama(e.pk, nivel + 1)
            e.nivel = nivel
            e.total = resultado.get(e.pk, 0)
            e.alcance = alcance.get(e.pk, 0)
            e.propios = propios.get(e.pk, 0)
            e.elegida = e.pk in elegidas
            e.sin_cruce = exigir_todas and not e.elegida and not e.total
            e.tiene_hijas = bool(hijas.get(e.pk))
            if e.elegida or debajo or (e.alcance and not e.sin_cruce):
                salida.append(e)
                salida.extend(debajo)
        return salida

    return rama(None)


def _arbol_completo():
    """Todas las etiquetas en árbol, también las que no usa ningún documento.

    El árbol de la barra lateral esconde las etiquetas vacías, que es lo que se
    quiere mientras filtras. En Organizar sobran justo las contrarias: se viene
    aquí a ver qué hay de más, y una etiqueta que no usa nadie es la primera
    candidata a borrar. Si no se lista, no hay forma de quitarla.

    `propios` son los documentos que llevan puesta esa etiqueta; `alcance`
    cuenta además los de sus hijas, sin repetir los que llevan las dos.
    """
    alcance, propios = cuenta_por_rama(Documento.objects.visibles())

    hijas = {}
    for e in Etiqueta.objects.all():
        hijas.setdefault(e.padre_id, []).append(e)

    def rama(padre_id, nivel=0):
        salida = []
        for e in sorted(hijas.get(padre_id, []), key=lambda x: x.nombre.lower()):
            e.nivel = nivel
            e.propios = propios.get(e.pk, 0)
            e.alcance = alcance.get(e.pk, 0)
            e.tiene_hijas = bool(hijas.get(e.pk))
            salida.append(e)
            salida.extend(rama(e.pk, nivel + 1))
        return salida

    return rama(None)


def contexto_lateral(peticion):
    cuenta_corr = recuento_simple(peticion, "corresponsal", "corresponsal")
    cuenta_tipo = recuento_simple(peticion, "tipo", "tipo")
    cuenta_anio = recuento_simple(peticion, "anio", "fecha__year")

    elegidos_corr = set(enteros(peticion, "corresponsal"))
    elegidos_tipo = set(enteros(peticion, "tipo"))
    elegidos_anio = set(enteros(peticion, "anio"))

    corresponsales = []
    for c in Corresponsal.objects.all():
        c.total = cuenta_corr.get(c.pk, 0)
        c.elegido = c.pk in elegidos_corr
        if c.total or c.elegido:
            corresponsales.append(c)

    tipos = []
    for t in TipoDocumento.objects.all():
        t.total = cuenta_tipo.get(t.pk, 0)
        t.elegido = t.pk in elegidos_tipo
        if t.total or t.elegido:
            tipos.append(t)

    anios = [
        {"anio": a, "n": cuenta_anio.get(a, 0), "elegido": a in elegidos_anio}
        for a in sorted(set(cuenta_anio) | elegidos_anio, reverse=True)
    ]

    # Las colecciones se cuentan con todos los filtros puestos menos el ámbito,
    # para que digan cuántos de los documentos que estás viendo son favoritos.
    ambito = conjunto(peticion, excepto={"vista"})

    return {
        "arbol": _arbol_etiquetas(peticion),
        "corresponsales": corresponsales,
        "tipos": tipos,
        "anios": anios,
        "guardadas": BusquedaGuardada.objects.all(),
        "campos": CampoPersonalizado.objects.all(),
        "carpetas": escaner.carpetas(),
        "totales": {
            "todos": ambito.count(),
            # Este se cuenta sobre toda la biblioteca, no sobre lo filtrado: es
            # un aviso de trabajo pendiente y tiene que decir la verdad aunque
            # estés mirando otra cosa.
            "por_revisar": Documento.objects.filter(papelera=False, por_revisar=True).count(),
            "favoritos": ambito.filter(favorito=True).count(),
            "sin_clasificar": ambito.filter(
                etiquetas__isnull=True, corresponsal__isnull=True, tipo__isnull=True
            ).count(),
            "problemas": ambito.exclude(estado=Documento.OK).count(),
            "papelera": Documento.objects.filter(papelera=True).count(),
            # Lo que sigue físicamente en la carpeta del buzón. No es lo mismo
            # que «Recién llegados»: eso se apaga en cuanto catalogas, pero el
            # fichero se queda ahí hasta que lo mueves a su carpeta.
            "en_buzon": (
                Documento.objects.filter(
                    papelera=False, ruta__startswith=f"{settings.CARPETA_BUZON}/"
                ).count()
                if settings.CARPETA_BUZON else 0
            ),
        },
        "carpeta_buzon": settings.CARPETA_BUZON,
        "activos": _filtros_activos(peticion),
    }


def _filtros_activos(peticion):
    """Lista de los filtros puestos, para pintarlos como pastillas quitables."""
    activos = []
    get = peticion.GET
    if get.get("q"):
        activos.append({"clase": "busqueda", "clave": "q", "valor": None,
                        "texto": f"«{get['q']}»"})
    for e in Etiqueta.objects.filter(pk__in=enteros(peticion, "etiqueta")):
        activos.append({"clase": "etiqueta", "clave": "etiqueta", "valor": e.pk,
                        "texto": e.ruta_nombre, "color": e.color})
    for c in Corresponsal.objects.filter(pk__in=enteros(peticion, "corresponsal")):
        activos.append({"clase": "corresponsal", "clave": "corresponsal", "valor": c.pk,
                        "texto": c.nombre})
    for t in TipoDocumento.objects.filter(pk__in=enteros(peticion, "tipo")):
        activos.append({"clase": "tipo", "clave": "tipo", "valor": t.pk, "texto": t.nombre})
    for a in enteros(peticion, "anio"):
        activos.append({"clase": "anio", "clave": "anio", "valor": a, "texto": str(a)})
    if get.get("carpeta"):
        activos.append({"clase": "carpeta", "clave": "carpeta", "valor": None,
                        "texto": get["carpeta"]})
    if get.get("ext"):
        activos.append({"clase": "ext", "clave": "ext", "valor": None,
                        "texto": get["ext"].upper()})
    return activos


# --- Listado ------------------------------------------------------------------
@acceso
def biblioteca(peticion):
    qs, filtros = filtrar(peticion)
    paginador = Paginator(qs, settings.PAGINADO)
    pagina = paginador.get_page(peticion.GET.get("pagina"))

    seleccion = peticion.GET.get("doc")
    actual = None
    if seleccion and str(seleccion).isdigit():
        actual = Documento.objects.filter(pk=seleccion).first()
    if actual is None and pagina.object_list:
        actual = pagina.object_list[0]

    contexto = {
        "pagina": pagina,
        "filtros": filtros,
        "ordenes": ORDENES,
        "actual": actual,
        "presentacion": peticion.GET.get("modo", peticion.COOKIES.get("modo", "lista")),
        "consulta": consulta_actual(peticion),
        **contexto_lateral(peticion),
    }
    if actual is not None:
        contexto.update(_contexto_documento(actual))

    parcial = peticion.GET.get("parcial")
    if parcial == "lista":
        return render(peticion, "biblioteca/partes/lista.html", contexto)
    if parcial == "lateral":
        return render(peticion, "biblioteca/partes/lateral.html", contexto)
    respuesta = render(peticion, "biblioteca/biblioteca.html", contexto)
    if peticion.GET.get("modo"):
        respuesta.set_cookie("modo", contexto["presentacion"], max_age=60 * 60 * 24 * 365)
    return respuesta


def _contexto_documento(doc):
    """Solo se muestran los campos que este documento usa.

    Enseñar los quince campos de la biblioteca en cada ficha es ruido: la mayoría
    están vacíos. Los que no usa quedan detrás del botón de añadir.
    """
    usados = list(doc.valores.select_related("campo").order_by("campo__nombre"))
    ids_usados = {v.campo_id for v in usados}
    return {
        "doc": doc,
        "campos_usados": usados,
        "campos_disponibles": CampoPersonalizado.objects.exclude(pk__in=ids_usados),
        "tipos_campo": CampoPersonalizado.TIPOS,
        "carpetas": escaner.carpetas(),
        "etiquetas_todas": Etiqueta.objects.all(),
        "corresponsales_todos": Corresponsal.objects.all(),
        "tipos_todos": TipoDocumento.objects.all(),
    }


@acceso
def panel(peticion, pk):
    doc = get_object_or_404(Documento, pk=pk)
    Documento.objects.filter(pk=pk).update(abierto=timezone.now())
    return render(peticion, "biblioteca/partes/panel.html", _contexto_documento(doc))


@acceso
def documento(peticion, pk):
    doc = get_object_or_404(Documento, pk=pk)
    return redirect(f"{reverse('biblioteca:biblioteca')}?doc={doc.pk}")


# --- Edición ------------------------------------------------------------------
@acceso
@require_POST
def guardar(peticion, pk):
    doc = get_object_or_404(Documento, pk=pk)
    datos = peticion.POST

    doc.titulo = datos.get("titulo", doc.titulo).strip()[:300] or doc.titulo
    doc.notas = datos.get("notas", doc.notas)
    doc.favorito = datos.get("favorito") in ("1", "on", "true")

    fecha = datos.get("fecha", "").strip()
    doc.fecha = dt.date.fromisoformat(fecha) if fecha else None

    nombre_corresponsal = datos.get("corresponsal_nuevo", "").strip()
    if nombre_corresponsal:
        doc.corresponsal, _ = Corresponsal.objects.get_or_create(nombre=nombre_corresponsal[:190])
    else:
        valor = datos.get("corresponsal", "")
        doc.corresponsal_id = int(valor) if valor.isdigit() else None

    nombre_tipo = datos.get("tipo_nuevo", "").strip()
    if nombre_tipo:
        doc.tipo, _ = TipoDocumento.objects.get_or_create(nombre=nombre_tipo[:190])
    else:
        valor = datos.get("tipo", "")
        doc.tipo_id = int(valor) if valor.isdigit() else None

    doc.save()

    etiquetas = [int(v) for v in datos.getlist("etiquetas") if v.isdigit()]
    nuevas = [n.strip() for n in datos.get("etiquetas_nuevas", "").split(",") if n.strip()]
    for nombre in nuevas:
        etiqueta, _ = Etiqueta.objects.get_or_create(nombre=nombre[:128], padre=None)
        etiquetas.append(etiqueta.pk)
    doc.etiquetas.set(etiquetas)

    # Catalogar es lo que saca un documento de «Recién llegados»: en cuanto
    # tiene etiqueta, corresponsal o tipo deja de estar pendiente. El botón de
    # la ficha manda por encima de eso, en los dos sentidos.
    marca = datos.get("revisado", "")
    if marca == "0":
        pendiente = True
    elif marca in ("1", "on", "true"):
        pendiente = False
    else:
        pendiente = doc.por_revisar and not (
            doc.corresponsal_id or doc.tipo_id or doc.etiquetas.exists()
        )
    if pendiente != doc.por_revisar:
        doc.por_revisar = pendiente
        doc.save(update_fields=["por_revisar"])

    # Crear un campo que no existía en toda la biblioteca, desde la propia ficha.
    nombre_campo = datos.get("campo_nuevo_nombre", "").strip()
    if nombre_campo:
        campo, _ = CampoPersonalizado.objects.get_or_create(
            nombre=nombre_campo[:190],
            defaults={"tipo": datos.get("campo_nuevo_tipo", CampoPersonalizado.TEXTO)},
        )
        ValorCampo.objects.update_or_create(
            documento=doc, campo=campo,
            defaults={"valor": datos.get("campo_nuevo_valor", "").strip()},
        )

    # Los campos que no venían en el formulario (los que no usa) no se tocan.
    # `campo_mantener` lo mandan las filas que están puestas en la ficha: esas
    # se conservan aunque queden vacías, o añadir un campo hoy y rellenarlo
    # mañana sería imposible. La × de la fila quita esa marca, y eso es lo que
    # borra el campo de este documento.
    mantener = {int(v) for v in datos.getlist("campo_mantener") if v.isdigit()}
    for campo in CampoPersonalizado.objects.all():
        clave = f"campo_{campo.pk}"
        if clave not in datos:
            continue
        valor = datos.get(clave, "").strip()
        if valor or campo.pk in mantener:
            ValorCampo.objects.update_or_create(
                documento=doc, campo=campo, defaults={"valor": valor}
            )
        else:
            ValorCampo.objects.filter(documento=doc, campo=campo).delete()

    busqueda.indexar(doc)
    if peticion.headers.get("X-Parcial"):
        return render(peticion, "biblioteca/partes/panel.html", _contexto_documento(doc))
    return redirect(f"{reverse('biblioteca:biblioteca')}?doc={doc.pk}")


@acceso
@require_POST
def mover(peticion, pk):
    """Cambia un documento de carpeta: la «carpeta contenedora» de paperless.

    Mueve el fichero de verdad dentro de la biblioteca. El hash no cambia, así
    que la ficha se conserva entera: etiquetas, campos, notas y fecha.
    """
    doc = get_object_or_404(Documento, pk=pk)
    destino = peticion.POST.get("carpeta", "")
    try:
        escaner.mover(doc, destino)
    except FileNotFoundError:
        return HttpResponseBadRequest("El fichero no está en la carpeta")
    except (ValueError, OSError) as e:
        return HttpResponseBadRequest(f"No se ha podido mover: {e}")
    if peticion.headers.get("X-Parcial"):
        return JsonResponse({"ok": True, "ruta": doc.ruta})
    return redirect(peticion.POST.get("volver") or doc.get_absolute_url())


@acceso
@require_POST
def descargar_varios(peticion):
    """Un zip con los documentos seleccionados, nombrados por su título.

    Se arma en un fichero temporal, no en memoria: el NAS tiene 3,7 GB y aquí
    puede caer una selección de cientos de PDF. Compresión al mínimo, porque un
    PDF ya viene comprimido y lo único que se ganaría es calentar la CPU.
    """
    ids = [int(v) for v in peticion.POST.getlist("ids") if v.isdigit()]
    docs = [d for d in Documento.objects.filter(pk__in=ids) if d.existe]
    if not docs:
        return HttpResponseBadRequest("No hay documentos que descargar")

    temporal = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    usados = set()
    try:
        with zipfile.ZipFile(temporal, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
            for doc in docs:
                ruta = doc.ruta_absoluta
                nombre = f"{escaner._limpio(doc.titulo)}{ruta.suffix}"
                raiz, extension = os.path.splitext(nombre)
                contador = 1
                while nombre.lower() in usados:
                    contador += 1
                    nombre = f"{raiz} ({contador}){extension}"
                usados.add(nombre.lower())
                z.write(ruta, arcname=nombre)
        temporal.close()
    except Exception:
        temporal.close()
        os.unlink(temporal.name)
        raise

    marca = timezone.localdate().isoformat()
    respuesta = FileResponse(
        _TemporalQueSeBorra(temporal.name),
        as_attachment=True,
        filename=f"alejandria-{marca}.zip",
        content_type="application/zip",
    )
    respuesta["Content-Length"] = os.path.getsize(temporal.name)
    return respuesta


class _TemporalQueSeBorra:
    """Fichero que se borra solo cuando la respuesta termina de enviarse."""

    def __init__(self, ruta):
        self.ruta = ruta
        self._f = open(ruta, "rb")

    def read(self, *a):
        return self._f.read(*a)

    def __iter__(self):
        return iter(lambda: self._f.read(65536), b"")

    def close(self):
        self._f.close()
        try:
            os.unlink(self.ruta)
        except OSError:
            pass


@acceso
@require_POST
def acciones(peticion):
    """Acciones sobre varios documentos a la vez (la selección de la lista)."""
    ids = [int(v) for v in peticion.POST.getlist("ids") if v.isdigit()]
    accion = peticion.POST.get("accion", "")
    qs = Documento.objects.filter(pk__in=ids)
    afectados = qs.count()

    if accion == "etiquetar" and peticion.POST.get("etiqueta", "").isdigit():
        etiqueta = Etiqueta.objects.get(pk=int(peticion.POST["etiqueta"]))
        for doc in qs:
            doc.etiquetas.add(etiqueta)
    elif accion == "desetiquetar" and peticion.POST.get("etiqueta", "").isdigit():
        etiqueta = Etiqueta.objects.get(pk=int(peticion.POST["etiqueta"]))
        for doc in qs:
            doc.etiquetas.remove(etiqueta)
    elif accion == "corresponsal":
        valor = peticion.POST.get("corresponsal", "")
        qs.update(corresponsal_id=int(valor) if valor.isdigit() else None)
    elif accion == "tipo":
        valor = peticion.POST.get("tipo", "")
        qs.update(tipo_id=int(valor) if valor.isdigit() else None)
    elif accion == "mover":
        destino = peticion.POST.get("carpeta", "")
        movidos = 0
        for doc in qs:
            try:
                escaner.mover(doc, destino)
                movidos += 1
            except (FileNotFoundError, ValueError, OSError) as e:
                log.warning("No se pudo mover %s: %s", doc.pk, e)
        afectados = movidos
    elif accion == "papelera":
        qs.update(papelera=True)
    elif accion == "restaurar":
        qs.update(papelera=False)
    elif accion == "favorito":
        qs.update(favorito=True)
    elif accion == "revisado":
        qs.update(por_revisar=False)
    elif accion == "borrar_definitivo":
        # Borra la ficha, NUNCA el fichero del disco.
        for doc in qs:
            busqueda.borrar(doc.pk)
            miniaturas.olvidar(doc)
        qs.delete()
    else:
        return HttpResponseBadRequest("Acción desconocida")

    for doc in Documento.objects.filter(pk__in=ids):
        busqueda.indexar(doc)
    if peticion.headers.get("X-Parcial"):
        return JsonResponse({"ok": True, "afectados": afectados})
    return redirect(peticion.POST.get("volver") or reverse("biblioteca:biblioteca"))


# --- Ficheros -----------------------------------------------------------------
@acceso
def fichero(peticion, pk, adjunto=False):
    doc = get_object_or_404(Documento, pk=pk)
    ruta = doc.ruta_absoluta
    if not ruta.is_file():
        Documento.objects.filter(pk=pk).update(estado=Documento.AUSENTE)
        raise Http404("El fichero ya no está en la carpeta.")
    Documento.objects.filter(pk=pk).update(abierto=timezone.now())
    tipo = doc.mime or mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
    disposicion = "attachment" if adjunto else "inline"
    cabecera = f"{disposicion}; filename*=UTF-8''{quote(doc.nombre_fichero)}"

    if settings.ACCEL_PREFIJO:
        # nginx sirve el fichero; Django ni lo abre. Memoria constante aunque
        # el PDF pese 300 MB.
        respuesta = HttpResponse(content_type=tipo)
        respuesta["X-Accel-Redirect"] = settings.ACCEL_PREFIJO + quote(doc.ruta)
        respuesta["Content-Disposition"] = cabecera
        return respuesta

    respuesta = FileResponse(open(ruta, "rb"), content_type=tipo)
    respuesta["Content-Disposition"] = cabecera
    return respuesta


@acceso
def miniatura(peticion, pk):
    doc = get_object_or_404(Documento, pk=pk)
    ruta = miniaturas.obtener(doc)
    if ruta is None:
        return redirect("/estaticos/biblioteca/sin-miniatura.svg")
    respuesta = FileResponse(open(ruta, "rb"), content_type="image/jpeg")
    respuesta["Cache-Control"] = "private, max-age=604800"
    return respuesta


@acceso
@require_POST
def subir(peticion):
    """Sube ficheros al buzón y los archiva en el momento."""
    creados, fallidos = [], 0
    entrada = Path(settings.ENTRADA_DIR)
    for subido in peticion.FILES.getlist("ficheros"):
        destino = entrada / escaner._limpio(subido.name, "documento")
        contador = 1
        while destino.exists():
            contador += 1
            destino = destino.with_name(f"{destino.stem} ({contador}){destino.suffix}")
        with open(destino, "wb") as salida:
            for trozo in subido.chunks():  # a disco a trozos, sin cargarlo entero
                salida.write(trozo)
        try:
            creados.append(escaner.archivar(destino))
        except Exception as e:
            fallidos += 1
            log.exception("No se pudo archivar %s", destino)
            Registro.anota(f"Error al subir {subido.name}: {e}", nivel="error")
    if peticion.headers.get("X-Parcial"):
        return JsonResponse(
            {"ok": True, "creados": [d.pk for d in creados], "fallidos": fallidos}
        )
    return redirect(reverse("biblioteca:biblioteca"))


# --- Organizar ----------------------------------------------------------------
@acceso
def organizar(peticion):
    arbol = _arbol_completo()
    return render(
        peticion,
        "biblioteca/organizar.html",
        {
            "arbol": arbol,
            # Sin usar de verdad: ni en documentos propios ni en los de sus hijas.
            # Una que solo agrupa no cuenta aquí; borrarla dejaría sueltas a las hijas.
            "etiquetas_sin_usar": sum(1 for e in arbol if not e.alcance),
            "corresponsales": Corresponsal.objects.annotate(n=Count("documentos")),
            "tipos": TipoDocumento.objects.annotate(n=Count("documentos")),
            "campos": CampoPersonalizado.objects.all(),
            "guardadas": BusquedaGuardada.objects.all(),
            "tipos_campo": CampoPersonalizado.TIPOS,
        },
    )


@acceso
@require_POST
def organizar_guardar(peticion):
    datos = peticion.POST
    que, accion = datos.get("que"), datos.get("accion", "crear")
    pk = datos.get("id", "")

    modelos = {
        "etiqueta": Etiqueta,
        "corresponsal": Corresponsal,
        "tipo": TipoDocumento,
        "campo": CampoPersonalizado,
        "guardada": BusquedaGuardada,
    }
    Modelo = modelos.get(que)
    if Modelo is None:
        return HttpResponseBadRequest("Elemento desconocido")

    if accion == "borrar" and pk.isdigit():
        Modelo.objects.filter(pk=int(pk)).delete()
        return redirect(reverse("biblioteca:organizar"))

    campos = {"nombre": datos.get("nombre", "").strip()[:190]}
    if que == "etiqueta":
        padre = datos.get("padre", "")
        campos.update(
            color=datos.get("color", "#8a8f98")[:7],
            padre_id=int(padre) if padre.isdigit() else None,
            inbox=datos.get("inbox") in ("1", "on"),
        )
    elif que == "campo":
        opciones = [o.strip() for o in datos.get("opciones", "").split(",") if o.strip()]
        campos.update(tipo=datos.get("tipo", "texto"), opciones=opciones)
    elif que == "guardada":
        campos.update(
            consulta=datos.get("consulta", "").lstrip("?")[:500],
            icono=datos.get("icono", "🔎")[:8],
        )

    if not campos["nombre"]:
        return HttpResponseBadRequest("Hace falta un nombre")

    if pk.isdigit():
        Modelo.objects.filter(pk=int(pk)).update(**campos)
    else:
        Modelo.objects.create(**campos)
    return redirect(reverse("biblioteca:organizar"))


# --- Estado -------------------------------------------------------------------
_escaneo = {"corriendo": False, "resumen": None}


@acceso
def estado(peticion):
    import shutil

    total = Documento.objects.filter(papelera=False).count()
    bytes_totales = sum(
        Documento.objects.filter(papelera=False).values_list("bytes", flat=True)
    )
    uso = shutil.disk_usage(settings.BIBLIOTECA_DIR)
    return render(
        peticion,
        "biblioteca/estado.html",
        {
            "total": total,
            "bytes_totales": bytes_totales,
            "por_extension": _por_extension(),
            "registro": Registro.objects.all()[:40],
            "escaneo": _escaneo,
            "rutas": {
                "biblioteca": settings.BIBLIOTECA_DIR,
                "entrada": settings.ENTRADA_DIR,
                "datos": settings.DATOS_DIR,
            },
            "disco": {
                "total": uso.total,
                "libre": uso.free,
                "usado_pct": round((uso.total - uso.free) / uso.total * 100),
            },
            "problemas": Documento.objects.exclude(estado=Documento.OK).count(),
            "memoria": _memoria(),
        },
    )


def _por_extension():
    cuenta = {}
    for ruta in Documento.objects.filter(papelera=False).values_list("ruta", flat=True):
        ext = Path(ruta).suffix.lower().lstrip(".") or "sin extensión"
        cuenta[ext] = cuenta.get(ext, 0) + 1
    return sorted(cuenta.items(), key=lambda x: -x[1])[:12]


def _memoria():
    """Memoria del propio proceso, para vigilar el presupuesto del NAS."""
    try:
        with open("/proc/self/status") as f:
            for linea in f:
                if linea.startswith("VmRSS:"):
                    return f"{int(linea.split()[1]) / 1024:.0f} MB"
    except OSError:
        pass
    return "n/d"


@acceso
@require_POST
def escanear_ahora(peticion):
    if _escaneo["corriendo"]:
        return redirect(reverse("biblioteca:estado"))

    rehashear = peticion.POST.get("rehashear") == "1"

    def tarea():
        from django.db import close_old_connections

        _escaneo["corriendo"] = True
        try:
            close_old_connections()
            resumen = escaner.escanear(rehashear=rehashear)
            _escaneo["resumen"] = resumen
            Registro.anota(
                "Escaneo: {nuevos} nuevos, {movidos} movidos, {actualizados} actualizados, "
                "{ausentes} ausentes, {vistos} ficheros".format(**resumen)
            )
        except Exception as e:
            log.exception("Escaneo fallido")
            Registro.anota(f"Escaneo fallido: {e}", nivel="error")
        finally:
            _escaneo["corriendo"] = False
            close_old_connections()

    threading.Thread(target=tarea, name="alejandria-escaneo", daemon=True).start()
    return redirect(reverse("biblioteca:estado"))


# --- Acceso -------------------------------------------------------------------
def acceder(peticion):
    error = ""
    if peticion.method == "POST":
        usuario = authenticate(
            peticion,
            username=peticion.POST.get("usuario"),
            password=peticion.POST.get("clave"),
        )
        if usuario is not None:
            login(peticion, usuario)
            return redirect(peticion.GET.get("next") or reverse("biblioteca:biblioteca"))
        error = "Usuario o contraseña incorrectos."
    return render(peticion, "biblioteca/acceder.html", {"error": error})


def salir(peticion):
    logout(peticion)
    return redirect(reverse("biblioteca:acceder"))
