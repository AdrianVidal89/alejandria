from django.urls import path

from . import vistas

app_name = "biblioteca"

urlpatterns = [
    path("", vistas.biblioteca, name="biblioteca"),
    path("doc/<int:pk>/", vistas.documento, name="documento"),
    path("doc/<int:pk>/panel/", vistas.panel, name="panel"),
    path("doc/<int:pk>/guardar/", vistas.guardar, name="guardar"),
    path("doc/<int:pk>/fichero/", vistas.fichero, name="fichero"),
    path("doc/<int:pk>/descargar/", vistas.fichero, {"adjunto": True}, name="descargar"),
    path("doc/<int:pk>/miniatura/", vistas.miniatura, name="miniatura"),
    path("acciones/", vistas.acciones, name="acciones"),
    path("subir/", vistas.subir, name="subir"),
    path("organizar/", vistas.organizar, name="organizar"),
    path("organizar/guardar/", vistas.organizar_guardar, name="organizar_guardar"),
    path("estado/", vistas.estado, name="estado"),
    path("estado/escanear/", vistas.escanear_ahora, name="escanear"),
    path("acceder/", vistas.acceder, name="acceder"),
    path("salir/", vistas.salir, name="salir"),
]
