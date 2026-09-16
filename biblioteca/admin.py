from django.contrib import admin

from .models import (
    BusquedaGuardada, CampoPersonalizado, Corresponsal, Documento, Etiqueta,
    Registro, TipoDocumento, ValorCampo,
)


class ValorCampoInline(admin.TabularInline):
    model = ValorCampo
    extra = 0


@admin.register(Documento)
class DocumentoAdmin(admin.ModelAdmin):
    list_display = ("titulo", "corresponsal", "tipo", "fecha", "estado", "papelera")
    list_filter = ("estado", "papelera", "favorito", "tipo", "corresponsal", "etiquetas")
    search_fields = ("titulo", "ruta", "notas")
    filter_horizontal = ("etiquetas",)
    date_hierarchy = "anadido"
    inlines = [ValorCampoInline]
    readonly_fields = ("hash", "bytes", "mtime", "anadido", "modificado")


@admin.register(Etiqueta)
class EtiquetaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "padre", "color", "inbox")
    list_filter = ("inbox",)
    search_fields = ("nombre",)


admin.site.register([Corresponsal, TipoDocumento, CampoPersonalizado, BusquedaGuardada])


@admin.register(Registro)
class RegistroAdmin(admin.ModelAdmin):
    list_display = ("momento", "nivel", "mensaje")
    list_filter = ("nivel",)


admin.site.site_header = "Alejandria — administración"
admin.site.site_title = "Alejandria"
