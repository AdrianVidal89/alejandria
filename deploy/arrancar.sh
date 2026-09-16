#!/bin/sh
# Arranque del contenedor web: migraciones, estáticos y gunicorn.
set -e
cd /app

# Los estáticos ya vienen hechos dentro de la imagen (ver Dockerfile).
python manage.py migrate --noinput || {
    echo "ERROR: no se pudieron aplicar las migraciones."
    echo "Casi siempre son permisos de la carpeta 'datos': tiene que pertenecer al"
    echo "mismo usuario que la línea 'user:' del docker-compose.yml (1000:100 en el"
    echo "NAS). Se arregla con:"
    echo "  docker compose run --rm --user root web sh -c 'chown -R 1000:100 /datos'"
    exit 1
}

# 1 worker + 4 hilos: un solo intérprete de Python en memoria (~130 MB) y
# suficiente concurrencia para servir la interfaz de una persona. Subir workers
# multiplica la memoria por cada uno; es el error que comete paperless por defecto.
exec gunicorn alejandria.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${ALEJANDRIA_WORKERS:-1}" \
    --threads "${ALEJANDRIA_HILOS:-4}" \
    --worker-class gthread \
    --no-control-socket \
    --max-requests 800 \
    --max-requests-jitter 100 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
