#!/bin/sh
# Arranque del contenedor web: migraciones, estáticos y gunicorn.
set -e
cd /app

python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear >/dev/null || {
    echo "AVISO: no se pudieron copiar los estáticos. Suele ser permisos de la"
    echo "carpeta datos/: comprueba que sea del mismo usuario que 'user:' en"
    echo "docker-compose.yml (1000:100 en el NAS)."
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
    --max-requests 800 \
    --max-requests-jitter 100 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
