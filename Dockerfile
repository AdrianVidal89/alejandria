# Imagen deliberadamente mínima: Python slim + dos utilidades de línea de comandos
# para las miniaturas. Ni OCR, ni tesseract, ni modelos: eso es lo que hincha
# paperless hasta los 1,3 GB en el NAS.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils \
        libvips-tools \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Los estáticos se preparan aquí, en la construcción y como root: el contenedor
# arranca después sin necesidad de escribir nada fuera de sus datos.
RUN ALEJANDRIA_DATOS=/tmp/construccion python manage.py collectstatic --noinput --clear \
    && rm -rf /tmp/construccion

# UID 1000 / GID 100 = adrian:everyone en QTS (ver nota de infraestructura del NAS)
RUN useradd -u 1000 -g 100 -M -d /app alejandria || true
EXPOSE 8000

CMD ["sh", "/app/deploy/arrancar.sh"]
