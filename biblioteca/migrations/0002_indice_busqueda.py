"""Tabla virtual FTS5 para la búsqueda por texto."""
from django.db import migrations

from biblioteca import busqueda


class Migration(migrations.Migration):
    dependencies = [("biblioteca", "0001_initial")]

    operations = [
        migrations.RunSQL(sql=busqueda.CREAR_TABLA, reverse_sql=busqueda.BORRAR_TABLA),
    ]
