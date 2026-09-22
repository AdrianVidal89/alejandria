"""Escáner de la biblioteca y buzón de entrada.

Filosofía: la carpeta manda. Alejandria recorre la biblioteca, da de alta lo que
no conoce, detecta lo que se ha movido (por hash) y marca lo que ha desaparecido.
Nunca borra ni reescribe un fichero del usuario; lo único que mueve son los
documentos que se sueltan en el buzón de entrada.
"""
import datetime as dt
import logging
import mimetypes
import os
import re
import unicodedata
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from . import busqueda
from .models import Corresponsal, Documento, Etiqueta, Registro, sha256

log = logging.getLogger(__name__)

# Carpetas que nunca se indexan: basura del NAS, cachés y los derivados que
# genera Paperless (miniaturas y la copia con OCR, que duplicaría cada papel).
IGNORAR = {
    "thumbnails", "archive", "originals_backup", "index", "log", "logs",
    "@eaDir", "@Recycle", "#recycle", ".stfolder", ".stversions", ".Trash-1000",
    "__pycache__", ".git", ".cache", "lost+found",
}
IGNORAR |= {
    c.strip() for c in os.environ.get("ALEJANDRIA_IGNORAR", "").split(",") if c.strip()
}

ANIO = re.compile(r"^(19|20)\d{2}$")
FECHA_EN_NOMBRE = re.compile(r"(19|20)\d{2}[-_.]?(0[1-9]|1[0-2])[-_.]?(0[1-9]|[12]\d|3[01])")


def _saltar(nombre):
    return nombre in IGNORAR or nombre.startswith(".")


def recorrer(raiz=None):
    """Genera rutas relativas de los ficheros indexables de la biblioteca."""
    raiz = Path(raiz or settings.BIBLIOTECA_DIR)
    for carpeta, subcarpetas, ficheros in os.walk(raiz, followlinks=False):
        subcarpetas[:] = sorted(s for s in subcarpetas if not _saltar(s))
        for fichero in sorted(ficheros):
            if fichero.startswith("."):
                continue
            if not fichero.lower().endswith(settings.EXTENSIONES):
                continue
            yield Path(carpeta, fichero).relative_to(raiz).as_posix()


def deducir(ruta_rel):
    """Saca título, fecha y corresponsal de la propia ruta.

    Encaja con el patrón por defecto de Paperless ({año}/{corresponsal}/{título}),
    pero degrada con dignidad en cualquier carpeta hecha a mano.
    """
    partes = Path(ruta_rel).parts
    titulo = Path(ruta_rel).stem.replace("_", " ").strip()
    anio = next((int(p) for p in partes[:-1] if ANIO.match(p)), None)
    corresponsal = next(
        (p for p in partes[:-1] if not ANIO.match(p) and len(p) < 120), None
    )
    fecha = None
    m = FECHA_EN_NOMBRE.search(Path(ruta_rel).name)
    if m:
        digitos = re.sub(r"[^0-9]", "", m.group(0))[:8]
        try:
            fecha = dt.date(int(digitos[:4]), int(digitos[4:6]), int(digitos[6:8]))
            titulo = titulo.replace(m.group(0), "").strip(" -_·")
        except ValueError:
            fecha = None
    if fecha is None and anio:
        fecha = dt.date(anio, 1, 1)
    return titulo or Path(ruta_rel).name, fecha, corresponsal


def _corresponsal(nombre):
    if not nombre:
        return None
    obj, _ = Corresponsal.objects.get_or_create(nombre=nombre.strip()[:190])
    return obj


def etiquetas_de_ruta(ruta_rel):
    """Convierte las carpetas de la ruta en un árbol de etiquetas.

    Pensado para bibliotecas organizadas a mano (Compras/, Vehiculos/,
    Documentacion Personal/…), donde la carpeta es una categoría, no el nombre de
    quien manda el papel. Las carpetas de año se saltan: para eso está la fecha.
    Devuelve la etiqueta más profunda, que ya cuelga de sus padres.
    """
    padre = None
    for trozo in Path(ruta_rel).parts[:-1]:
        if ANIO.match(trozo) or not trozo.strip():
            continue
        padre, _ = Etiqueta.objects.get_or_create(nombre=trozo.strip()[:128], padre=padre)
    return padre


def _mismo_fichero_movido(ruta_rel, digest, nombre, tamano):
    """La ficha de un fichero que se ha movido a mano, si es que la hay.

    Primero por huella, que es lo fiable. Y si no aparece, por nombre y tamaño
    exactos entre los que se han quedado sin fichero: un documento dado de alta
    sin huella —pasaba con lo que entraba por el buzón— no se reconocía al
    moverlo, y el escaneo creaba una ficha nueva vacía al lado de la buena.
    """
    if digest:
        gemelo = (
            Documento.objects.filter(hash=digest)
            .exclude(ruta=ruta_rel)
            .order_by("estado")
            .first()
        )
        if gemelo is not None and not gemelo.ruta_absoluta.is_file():
            return gemelo

    candidatos = [
        d for d in Documento.objects.filter(bytes=tamano).exclude(ruta=ruta_rel)
        if Path(d.ruta).name == nombre and not d.ruta_absoluta.is_file()
    ]
    # Solo si no hay duda: con dos ficheros iguales en sitios distintos no se
    # puede saber cuál es, y equivocarse aquí mezcla dos documentos.
    return candidatos[0] if len(candidatos) == 1 else None


def alta(ruta_rel, con_hash=True, adivinar=True, carpetas_como_etiquetas=False):
    """Da de alta un fichero ya presente en la biblioteca. Devuelve el Documento."""
    absoluta = Path(settings.BIBLIOTECA_DIR) / ruta_rel
    st = absoluta.stat()
    titulo, fecha, corresponsal = deducir(ruta_rel) if adivinar else (absoluta.stem, None, None)
    digest = sha256(absoluta) if con_hash else ""

    gemelo = _mismo_fichero_movido(ruta_rel, digest, absoluta.name, st.st_size)
    if gemelo is not None:
        gemelo.ruta = ruta_rel
        gemelo.estado = Documento.OK
        gemelo.bytes, gemelo.mtime = st.st_size, st.st_mtime
        if digest and not gemelo.hash:
            gemelo.hash = digest      # se quedó sin huella; se repara de paso
        gemelo.save()
        busqueda.indexar(gemelo)
        gemelo.reenganchado = True   # para que el resumen no lo cuente como nuevo
        return gemelo

    if carpetas_como_etiquetas:
        corresponsal = None  # la carpeta es una categoría, no un remitente

    doc = Documento.objects.create(
        titulo=titulo[:300],
        ruta=ruta_rel,
        hash=digest,
        bytes=st.st_size,
        mtime=st.st_mtime,
        mime=mimetypes.guess_type(absoluta.name)[0] or "application/octet-stream",
        fecha=fecha,
        corresponsal=_corresponsal(corresponsal) if adivinar else None,
    )
    if carpetas_como_etiquetas:
        etiqueta = etiquetas_de_ruta(ruta_rel)
        if etiqueta is not None:
            doc.etiquetas.add(etiqueta)
    busqueda.indexar(doc)
    return doc


def escanear(rehashear=False, adivinar=True, informar=None, carpetas_como_etiquetas=False):
    """Sincroniza la base con el contenido real de la carpeta.

    rehashear=False (lo normal) solo calcula el hash de ficheros nuevos o cuyo
    tamaño/fecha haya cambiado: recorrer 20.000 papeles cuesta segundos, no minutos.
    """
    resumen = {"nuevos": 0, "movidos": 0, "actualizados": 0, "ausentes": 0, "vistos": 0}
    conocidos = dict(Documento.objects.values_list("ruta", "id"))
    vistos = set()

    for ruta_rel in recorrer():
        resumen["vistos"] += 1
        if informar and resumen["vistos"] % 500 == 0:
            informar(f"  {resumen['vistos']} ficheros recorridos…")
        doc_id = conocidos.get(ruta_rel)
        if doc_id is None:
            doc = alta(ruta_rel, adivinar=adivinar,
                       carpetas_como_etiquetas=carpetas_como_etiquetas)
            # Un fichero que solo ha cambiado de carpeta no es un documento nuevo:
            # es el mismo de siempre, y decir «nuevo» asusta sin motivo.
            if getattr(doc, "reenganchado", False):
                resumen["movidos"] += 1
                vistos.add(doc.pk)
            else:
                resumen["nuevos"] += 1
            continue
        vistos.add(doc_id)
        doc = Documento.objects.get(pk=doc_id)
        st = doc.ruta_absoluta.stat()
        if rehashear or doc.bytes != st.st_size or doc.mtime != st.st_mtime:
            doc.refrescar_desde_disco(rehashear=True)
            doc.save()
            busqueda.indexar(doc)
            resumen["actualizados"] += 1

    perdidos = (
        Documento.objects.exclude(pk__in=vistos).filter(estado=Documento.OK)
    )
    for doc in perdidos:
        if not doc.ruta_absoluta.is_file():
            doc.estado = Documento.AUSENTE
            doc.save(update_fields=["estado"])
            resumen["ausentes"] += 1
    return resumen


# --- Buzón de entrada ---------------------------------------------------------
def _limpio(texto, por_defecto="Sin nombre"):
    texto = unicodedata.normalize("NFKC", (texto or "").strip())
    texto = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", texto)
    texto = re.sub(r"\s+", " ", texto).strip(" .")
    return texto[:120] or por_defecto


def destino_archivado(titulo, fecha, corresponsal, extension):
    patron = settings.PATRON_ARCHIVADO
    relativa = patron.format(
        anio=(fecha or timezone.localdate()).year,
        mes=f"{(fecha or timezone.localdate()).month:02d}",
        corresponsal=_limpio(corresponsal or "Sin corresponsal"),
        titulo=_limpio(titulo),
        ext=extension,
    )
    destino = Path(settings.BIBLIOTECA_DIR) / relativa
    contador = 1
    while destino.exists():
        contador += 1
        destino = destino.with_name(f"{destino.stem} ({contador}){destino.suffix}")
    return destino


def archivar(origen: Path, titulo=None, fecha=None, corresponsal=None, etiquetas=()):
    """Mueve un fichero suelto a su sitio dentro de la biblioteca y lo da de alta.

    Lo que no se indique se deduce del nombre del fichero: una fecha al principio
    (2026-09-10 Recibo del gimnasio.pdf) se convierte en la fecha del documento y
    desaparece del título, que es como archiva Paperless.
    """
    deducido_titulo, deducida_fecha, _ = deducir(origen.name)
    titulo = titulo or deducido_titulo
    fecha = fecha or deducida_fecha
    destino = destino_archivado(titulo, fecha, corresponsal, origen.suffix.lower())
    destino.parent.mkdir(parents=True, exist_ok=True)
    try:
        origen.replace(destino)  # mismo volumen: instantáneo
    except OSError:
        import shutil

        shutil.move(str(origen), str(destino))
    ruta_rel = destino.relative_to(settings.BIBLIOTECA_DIR).as_posix()
    with transaction.atomic():
        doc = alta(ruta_rel, adivinar=False)
        doc.titulo = _limpio(titulo)[:300]
        doc.fecha = fecha
        doc.corresponsal = _corresponsal(corresponsal)
        # Todo lo que entra por el buzón o por el botón de subir queda en
        # cuarentena hasta que alguien lo cataloga: es la lista de «Recién
        # llegados» de la barra lateral.
        doc.por_revisar = True
        doc.save()
        etiquetas = list(etiquetas) or list(Etiqueta.objects.filter(inbox=True))
        if etiquetas:
            doc.etiquetas.set(etiquetas)
        busqueda.indexar(doc)
    return doc


def _estable(ruta: Path, memoria):
    """True si el fichero no ha cambiado de tamaño desde la última pasada.

    Evita indexar a medias algo que todavía se está copiando por SMB.
    """
    try:
        tamano = ruta.stat().st_size
    except OSError:
        return False
    previo = memoria.get(ruta)
    memoria[ruta] = tamano
    return previo is not None and previo == tamano and tamano > 0


def procesar_entrada(memoria=None):
    """Archiva lo que haya en el buzón. Devuelve la lista de documentos creados."""
    memoria = memoria if memoria is not None else {}
    entrada = Path(settings.ENTRADA_DIR)
    creados = []
    for hijo in sorted(entrada.rglob("*")):
        if not hijo.is_file() or hijo.name.startswith("."):
            continue
        if not hijo.name.lower().endswith(settings.EXTENSIONES):
            continue
        if not _estable(hijo, memoria):
            continue
        try:
            doc = archivar(hijo)
            creados.append(doc)
            Registro.anota(f"Archivado desde el buzón: {doc.titulo}")
            memoria.pop(hijo, None)
        except Exception as e:  # un fichero roto no puede parar el vigilante
            log.exception("Error archivando %s", hijo)
            Registro.anota(f"Error archivando {hijo.name}: {e}", nivel="error")
    return creados


# --- Cambiar un documento de carpeta ------------------------------------------
def carpeta_segura(carpeta_rel):
    """Ruta absoluta de una carpeta de la biblioteca, a prueba de `../`.

    Se limpia segmento a segmento con las mismas reglas que el archivado, así que
    lo que escriba el usuario en el cuadro de mover no puede salir de la
    biblioteca ni colar caracteres que rompan el sistema de ficheros.
    """
    base = Path(settings.BIBLIOTECA_DIR).resolve()
    partes = [
        _limpio(p, "")
        for p in str(carpeta_rel or "").replace("\\", "/").split("/")
    ]
    partes = [p for p in partes if p and p not in (".", "..")]
    destino = base.joinpath(*partes).resolve() if partes else base
    if destino != base and base not in destino.parents:
        raise ValueError("La carpeta queda fuera de la biblioteca")
    return destino


def mover(doc, carpeta_rel):
    """Mueve el fichero de un documento a otra carpeta de la biblioteca.

    Es lo que en paperless hacía la «carpeta contenedora». Mueve el fichero de
    verdad y actualiza la ficha; el hash no cambia, así que ni se recalcula ni se
    pierde nada de lo que tenga puesto.
    """
    origen = doc.ruta_absoluta
    if not origen.is_file():
        raise FileNotFoundError(doc.ruta)

    carpeta = carpeta_segura(carpeta_rel)
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / origen.name
    if destino == origen:
        return doc.ruta

    # Si ya hay uno con ese nombre en el destino, se numera en vez de pisarlo.
    contador = 1
    while destino.exists():
        contador += 1
        destino = carpeta / f"{origen.stem} ({contador}){origen.suffix}"

    origen.replace(destino) if origen.parent == destino.parent else _mueve(origen, destino)
    base = Path(settings.BIBLIOTECA_DIR).resolve()
    doc.ruta = str(destino.resolve().relative_to(base))
    doc.refrescar_desde_disco(rehashear=False)
    doc.save(update_fields=["ruta", "bytes", "mtime", "estado", "mime"])
    busqueda.indexar(doc)   # la ruta entra en el índice: hay que reindexar

    # La carpeta de origen se queda vacía muy a menudo al vaciar el buzón.
    try:
        if origen.parent != base and not any(origen.parent.iterdir()):
            origen.parent.rmdir()
    except OSError:
        pass
    return doc.ruta


def _mueve(origen: Path, destino: Path):
    """Mover entre carpetas, aguantando que sean sistemas de ficheros distintos."""
    import shutil
    shutil.move(str(origen), str(destino))


def carpetas():
    """Todas las carpetas de la biblioteca, en relativo y ordenadas."""
    base = Path(settings.BIBLIOTECA_DIR)
    salida = set()
    for raiz, subcarpetas, _ in os.walk(base):
        subcarpetas[:] = [s for s in subcarpetas if not s.startswith((".", "@"))]
        for s in subcarpetas:
            salida.add(str(Path(raiz, s).relative_to(base)))
    return sorted(salida, key=str.lower)
