from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("gestion-django/", admin.site.urls),
    path("", include("biblioteca.urls")),
]
