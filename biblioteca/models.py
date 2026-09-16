"""Modelo de datos de Alejandria.

Equivalencias con Paperless para que la importación sea directa:
    Tag -> Etiqueta, Correspondent -> Corresponsal, DocumentType -> TipoDocumento,
    CustomField -> CampoPersonalizado, Document -> Documento.

Diferencia de fondo: el fichero NO pertenece a la aplicación. Alejandria guarda
una ruta relativa a la biblioteca y un hash; los ficheros siguen viviendo donde
los dejó Paperless y se pueden seguir tocando por SMB sin romper nada.
"""
import hashlib
import mimetypes
import unicodedata
from pathlib import Path

from django.conf import settings
from django.db import models
from django.urls import reverse
from django.utils import timezone


def normaliza(texto):
    """Minúsculas y sin tildes, para búsquedas y comparaciones."""
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).lower().strip()


def sha256(ruta, bloque=1024 * 256):
    """Hash leyendo a trozos de 256 KB: un PDF de 200 MB no entra en RAM."""
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for trozo in iter(lambda: f.read(bloque), b""):
            h.update(trozo)
    return h.hexdigest()


class Etiqueta(models.Model):
    """Etiqueta jerárquica (Paperless es plano; el árbol es lo de DevonThink)."""

    nombre = models.CharField(max_length=128)
    color = models.CharField(max_length=7, default="#8a8f98")
    padre = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="hijas"
    )
    inbox = models.BooleanField(
        default=False, verbose_name="Es bandeja de entrada",
        help_text="Se asigna automáticamente a lo que llega por el buzón.",
    )
    paperless_id = models.IntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "Etiquetas"
        constraints = [
            models.UniqueConstraint(fields=["nombre", "padre"], name="etiqueta_unica_por_padre")
        ]

    def __str__(self):
        return self.nombre

    @property
    def ruta_nombre(self):
        partes, nodo, tope = [], self, 0
        while nodo is not None and tope < 10:
            partes.append(nodo.nombre)
            nodo, tope = nodo.padre, tope + 1
        return " / ".join(reversed(partes))

    def descendientes_ids(self):
        """IDs de esta etiqueta y todas sus hijas (filtrar por la rama entera)."""
        ids, pendientes = {self.pk}, [self.pk]
        while pendientes:
            hijas = list(
                Etiqueta.objects.filter(padre_id__in=pendientes)
                .exclude(pk__in=ids)
                .values_list("pk", flat=True)
            )
            if not hijas:
                break
            ids.update(hijas)
            pendientes = hijas
        return ids


class Corresponsal(models.Model):
    nombre = models.CharField(max_length=190, unique=True)
    paperless_id = models.IntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name_plural = "Corresponsales"

    def __str__(self):
        return self.nombre


class TipoDocumento(models.Model):
    nombre = models.CharField(max_length=190, unique=True)
    paperless_id = models.IntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Tipo de documento"
        verbose_name_plural = "Tipos de documento"

    def __str__(self):
        return self.nombre


class CampoPersonalizado(models.Model):
    TEXTO, NUMERO, FECHA, BOOLEANO, MONEDA, URL, SELECCION = (
        "texto", "numero", "fecha", "booleano", "moneda", "url", "seleccion",
    )
    TIPOS = [
        (TEXTO, "Texto"), (NUMERO, "Número"), (FECHA, "Fecha"),
        (BOOLEANO, "Sí / No"), (MONEDA, "Importe"), (URL, "Enlace"),
        (SELECCION, "Lista de opciones"),
    ]
    nombre = models.CharField(max_length=190, unique=True)
    tipo = models.CharField(max_length=16, choices=TIPOS, default=TEXTO)
    opciones = models.JSONField(default=list, blank=True, help_text="Solo para listas de opciones.")
    paperless_id = models.IntegerField(null=True, blank=True, unique=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "Campo personalizado"
        verbose_name_plural = "Campos personalizados"

    def __str__(self):
        return self.nombre


class DocumentoQuerySet(models.QuerySet):
    def visibles(self):
        return self.filter(papelera=False)

    def con_relaciones(self):
        return self.select_related("corresponsal", "tipo").prefetch_related("etiquetas")


class Documento(models.Model):
    OK, AUSENTE, CAMBIADO = "ok", "ausente", "cambiado"
    ESTADOS = [(OK, "Correcto"), (AUSENTE, "Fichero ausente"), (CAMBIADO, "Cambiado en disco")]

    titulo = models.CharField(max_length=300)
    ruta = models.CharField(
        max_length=1000, unique=True,
        help_text="Ruta relativa a la carpeta de biblioteca.",
    )
    hash = models.CharField(max_length=64, db_index=True, blank=True)
    bytes = models.BigIntegerField(default=0)
    mime = models.CharField(max_length=120, blank=True)
    paginas = models.IntegerField(null=True, blank=True)

    corresponsal = models.ForeignKey(
        Corresponsal, null=True, blank=True, on_delete=models.SET_NULL, related_name="documentos"
    )
    tipo = models.ForeignKey(
        TipoDocumento, null=True, blank=True, on_delete=models.SET_NULL, related_name="documentos"
    )
    etiquetas = models.ManyToManyField(Etiqueta, blank=True, related_name="documentos")

    fecha = models.DateField(
        null=True, blank=True, db_index=True, verbose_name="Fecha del documento"
    )
    anadido = models.DateTimeField(default=timezone.now, db_index=True)
    modificado = models.DateTimeField(auto_now=True)
    mtime = models.FloatField(default=0.0, help_text="Marca de tiempo del fichero en disco.")
    abierto = models.DateTimeField(null=True, blank=True, verbose_name="Visto por última vez")

    notas = models.TextField(blank=True)
    favorito = models.BooleanField(default=False)
    papelera = models.BooleanField(default=False)
    estado = models.CharField(max_length=10, choices=ESTADOS, default=OK)
    paperless_id = models.IntegerField(null=True, blank=True, unique=True)

    objects = DocumentoQuerySet.as_manager()

    class Meta:
        ordering = ["-anadido"]
        indexes = [
            models.Index(fields=["-fecha"]),
            models.Index(fields=["papelera", "-anadido"]),
        ]

    def __str__(self):
        return self.titulo

    # --- Fichero -------------------------------------------------------------
    @property
    def ruta_absoluta(self) -> Path:
        return Path(settings.BIBLIOTECA_DIR) / self.ruta

    @property
    def nombre_fichero(self):
        return Path(self.ruta).name

    @property
    def carpeta(self):
        return str(Path(self.ruta).parent) if "/" in self.ruta else ""

    @property
    def extension(self):
        return Path(self.ruta).suffix.lower().lstrip(".")

    @property
    def existe(self):
        return self.ruta_absoluta.is_file()

    @property
    def tamano_legible(self):
        n = float(self.bytes)
        for unidad in ("B", "KB", "MB", "GB"):
            if n < 1024 or unidad == "GB":
                return f"{n:.0f} {unidad}" if unidad == "B" else f"{n:.1f} {unidad}"
            n /= 1024

    @property
    def es_pdf(self):
        return self.extension == "pdf"

    @property
    def es_imagen(self):
        return self.extension in ("png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "bmp")

    @property
    def es_texto(self):
        return self.extension in ("txt", "md", "csv", "log", "json", "xml", "yml", "yaml")

    @property
    def previsualizable(self):
        return self.es_pdf or self.es_imagen or self.es_texto

    @property
    def icono(self):
        return {
            "pdf": "📕", "doc": "📘", "docx": "📘", "odt": "📘",
            "xls": "📗", "xlsx": "📗", "ods": "📗", "csv": "📗",
            "ppt": "📙", "pptx": "📙", "zip": "🗜️", "eml": "✉️",
            "txt": "📄", "md": "📄",
        }.get(self.extension, "🖼️" if self.es_imagen else "📎")

    def get_absolute_url(self):
        return reverse("biblioteca:documento", args=[self.pk])

    def refrescar_desde_disco(self, rehashear=True):
        """Sincroniza tamaño/mtime/hash con el fichero. Devuelve True si cambió."""
        ruta = self.ruta_absoluta
        if not ruta.is_file():
            cambio = self.estado != self.AUSENTE
            self.estado = self.AUSENTE
            return cambio
        st = ruta.stat()
        cambio = self.estado != self.OK or self.bytes != st.st_size or self.mtime != st.st_mtime
        self.bytes, self.mtime, self.estado = st.st_size, st.st_mtime, self.OK
        if not self.mime:
            self.mime = mimetypes.guess_type(ruta.name)[0] or "application/octet-stream"
        if rehashear and cambio:
            self.hash = sha256(ruta)
        return cambio

    def texto_indexable(self):
        """Lo que entra en el índice de búsqueda. Metadatos, no contenido."""
        campos = " ".join(
            f"{v.campo.nombre} {v.mostrar()}" for v in self.valores.select_related("campo")
        )
        return normaliza(
            " ".join(
                filter(
                    None,
                    [
                        self.titulo,
                        self.ruta.replace("/", " ").replace("_", " ").replace("-", " "),
                        self.corresponsal.nombre if self.corresponsal else "",
                        self.tipo.nombre if self.tipo else "",
                        " ".join(e.nombre for e in self.etiquetas.all()),
                        self.notas,
                        campos,
                    ],
                )
            )
        )


class ValorCampo(models.Model):
    documento = models.ForeignKey(Documento, on_delete=models.CASCADE, related_name="valores")
    campo = models.ForeignKey(CampoPersonalizado, on_delete=models.CASCADE, related_name="valores")
    valor = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["documento", "campo"], name="valor_unico_por_campo")
        ]
        verbose_name = "Valor de campo"
        verbose_name_plural = "Valores de campo"

    def __str__(self):
        return f"{self.campo.nombre}: {self.valor}"

    def mostrar(self):
        if self.campo.tipo == CampoPersonalizado.BOOLEANO:
            return "Sí" if self.valor in ("1", "true", "on", "Sí", "si") else "No"
        if self.campo.tipo == CampoPersonalizado.MONEDA and self.valor:
            try:
                return f"{float(self.valor):,.2f} €".replace(",", "@").replace(".", ",").replace("@", ".")
            except ValueError:
                return self.valor
        return self.valor


class BusquedaGuardada(models.Model):
    """Carpeta inteligente: una búsqueda con nombre fijada en la barra lateral."""

    nombre = models.CharField(max_length=120, unique=True)
    consulta = models.CharField(max_length=500, help_text="Parámetros de la URL de búsqueda.")
    icono = models.CharField(max_length=8, default="🔎")
    orden = models.IntegerField(default=0)

    class Meta:
        ordering = ["orden", "nombre"]
        verbose_name = "Búsqueda guardada"
        verbose_name_plural = "Búsquedas guardadas"

    def __str__(self):
        return self.nombre


class Registro(models.Model):
    """Traza corta de lo que hace el vigilante. Se poda sola."""

    momento = models.DateTimeField(default=timezone.now, db_index=True)
    nivel = models.CharField(max_length=10, default="info")
    mensaje = models.CharField(max_length=500)

    class Meta:
        ordering = ["-momento"]
        verbose_name = "Registro"
        verbose_name_plural = "Registro"

    def __str__(self):
        return f"[{self.nivel}] {self.mensaje}"

    @classmethod
    def anota(cls, mensaje, nivel="info"):
        cls.objects.create(mensaje=mensaje[:500], nivel=nivel)
        if cls.objects.count() > 600:
            viejos = cls.objects.order_by("-momento").values_list("pk", flat=True)[400:]
            cls.objects.filter(pk__in=list(viejos)).delete()
