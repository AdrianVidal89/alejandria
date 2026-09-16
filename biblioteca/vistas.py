"""Vistas de Alejandria.

Todo se renderiza en el servidor con plantillas de Django. Los paneles se
refrescan pidiendo trozos de HTML (el parámetro `parcial`), así que no hay
framework de JavaScript, ni compilación, ni un solo megabyte de node_modules.
"""
import datetime as dt
import logging
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
from .vistas_filtros import ORDENES, consulta_actual, facetas, filtrar

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
# Cuenta, para cada etiqueta, los documentos de toda su rama SIN duplicar los que
# lleven a la vez la etiqueta padre y la hija. Una sola consulta recursiva de
# SQLite en lugar de traerse la tabla de relaciones entera a Python.
CONSULTA_RAMAS = """
WITH RECURSIVE rama(raiz, nodo) AS (
    SELECT id, id FROM biblioteca_etiqueta
    UNION
    SELECT r.raiz, e.id FROM biblioteca_etiqueta e JOIN rama r ON e.padre_id = r.nodo
)
SELECT r.raiz, COUNT(DISTINCT rel.documento_id)
FROM rama r
JOIN biblioteca_documento_etiquetas rel ON rel.etiqueta_id = r.nodo
JOIN biblioteca_documento d ON d.id = rel.documento_id AND d.papelera = 0
GROUP BY r.raiz
"""


def _arbol_etiquetas():
    """Etiquetas en árbol, cada una con el recuento real de su rama."""
    from django.db import connection

    with connection.cursor() as c:
        c.execute(CONSULTA_RAMAS)
        totales = dict(c.fetchall())

    etiquetas = list(
        Etiqueta.objects.annotate(
            n=Count("documentos", filter=Q(documentos__papelera=False), distinct=True)
        )
    )
    por_padre = {}
    for e in etiquetas:
        por_padre.setdefault(e.padre_id, []).append(e)

    def rama(padre_id, nivel=0):
        plano = []
        for e in sorted(por_padre.get(padre_id, []), key=lambda x: x.nombre.lower()):
            e.nivel = nivel
            e.total = totales.get(e.pk, 0)
            plano.append(e)
            plano.extend(rama(e.pk, nivel + 1))
        return plano

    return rama(None)


def contexto_lateral(peticion, filtros):
    base = Documento.objects.filter(papelera=False)
    return {
        "arbol": _arbol_etiquetas(),
        "corresponsales": Corresponsal.objects.annotate(
            n=Count("documentos", filter=Q(documentos__papelera=False))
        ).filter(n__gt=0),
        "tipos": TipoDocumento.objects.annotate(
            n=Count("documentos", filter=Q(documentos__papelera=False))
        ).filter(n__gt=0),
        "anios": facetas(base),
        "guardadas": BusquedaGuardada.objects.all(),
        "campos": CampoPersonalizado.objects.all(),
        "totales": {
            "todos": base.count(),
            "favoritos": base.filter(favorito=True).count(),
            "sin_clasificar": base.filter(
                etiquetas__isnull=True, corresponsal__isnull=True, tipo__isnull=True
            ).count(),
            "papelera": Documento.objects.filter(papelera=True).count(),
            "problemas": base.exclude(estado=Documento.OK).count(),
        },
    }


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
        **contexto_lateral(peticion, filtros),
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
    valores = {v.campo_id: v for v in doc.valores.select_related("campo")}
    campos = []
    for campo in CampoPersonalizado.objects.all():
        campos.append({"campo": campo, "valor": valores.get(campo.pk)})
    return {
        "doc": doc,
        "campos_documento": campos,
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

    for campo in CampoPersonalizado.objects.all():
        clave = f"campo_{campo.pk}"
        if clave not in datos:
            continue
        valor = datos.get(clave, "").strip()
        if valor:
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
    elif accion == "papelera":
        qs.update(papelera=True)
    elif accion == "restaurar":
        qs.update(papelera=False)
    elif accion == "favorito":
        qs.update(favorito=True)
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
    creados = []
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
            log.exception("No se pudo archivar %s", destino)
            Registro.anota(f"Error al subir {subido.name}: {e}", nivel="error")
    if peticion.headers.get("X-Parcial"):
        return JsonResponse({"ok": True, "creados": [d.pk for d in creados]})
    return redirect(reverse("biblioteca:biblioteca"))


# --- Organizar ----------------------------------------------------------------
@acceso
def organizar(peticion):
    return render(
        peticion,
        "biblioteca/organizar.html",
        {
            "arbol": _arbol_etiquetas(),
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
                "Escaneo: {nuevos} nuevos, {actualizados} actualizados, "
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
