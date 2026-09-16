#!/bin/sh
# Atajo para levantar Alejandria en el escritorio (no en el NAS).
cd "$(dirname "$0")/.." || exit 1
.venv/bin/python manage.py migrate --noinput
ALEJANDRIA_VIGILANTE=1 .venv/bin/python manage.py runserver "${1:-127.0.0.1:8000}"
