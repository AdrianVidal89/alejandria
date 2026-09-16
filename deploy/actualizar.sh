#!/bin/sh
# ==============================================================================
# actualizar.sh — Actualiza Alejandria en el NAS a la última versión publicada
# ==============================================================================
# Uso:
#   sh /share/homes/adrian/scripts/actualizar.sh          (rama main)
#   sh /share/homes/adrian/scripts/actualizar.sh otra-rama
#
# Por qué existe: en el shell del QNAP no hay git, así que la actualización es
# bajar el paquete del repositorio y cambiar la carpeta de sitio.
#
# Qué respeta, pase lo que pase:
#   - el fichero .env (tu configuración y tu clave)
#   - la carpeta datos/ (las fichas, el índice y las miniaturas)
#
# La versión anterior no se borra: queda en alejandria.anterior por si hay que
# volver atrás. Se reemplaza en la siguiente actualización.
#
# Este script vive FUERA de la carpeta que reemplaza (en ~/scripts), a propósito:
# si viviera dentro, se estaría borrando a sí mismo a media ejecución.
# ==============================================================================

set -e

REPO=https://github.com/AdrianVidal89/alejandria
RAMA="${1:-main}"
APPS="${ALEJANDRIA_APPS:-/share/homes/adrian/apps}"
DESTINO="$APPS/alejandria"
NUEVO="$APPS/.alejandria-nuevo"
ANTERIOR="$APPS/alejandria.anterior"
PAQUETE="$APPS/.alejandria.tgz"

export DOCKER_CONFIG=/tmp/dockercfg

aviso() { echo "[$(date '+%H:%M:%S')] $1"; }

# --- Comprobaciones antes de tocar nada ---------------------------------------
[ -d "$DESTINO" ] || { echo "ERROR: no existe $DESTINO"; exit 1; }
[ -f "$DESTINO/.env" ] || { echo "ERROR: falta $DESTINO/.env"; exit 1; }

aviso "Descargando la rama $RAMA…"
rm -rf "$NUEVO" "$PAQUETE"
curl -fsSL "$REPO/archive/refs/heads/$RAMA.tar.gz" -o "$PAQUETE"
tar xzf "$PAQUETE" -C "$APPS"
rm -f "$PAQUETE"
mv "$APPS/alejandria-$RAMA" "$NUEVO"

# Si la descarga viniera a medias, mejor enterarse ahora que con todo parado.
[ -f "$NUEVO/manage.py" ] || { echo "ERROR: la descarga no es válida"; rm -rf "$NUEVO"; exit 1; }
[ -f "$NUEVO/docker-compose.yml" ] || { echo "ERROR: la descarga no es válida"; rm -rf "$NUEVO"; exit 1; }

aviso "Parando los contenedores…"
cd "$DESTINO"
docker compose down

aviso "Conservando configuración y fichas…"
cp -p "$DESTINO/.env" "$NUEVO/.env"
if [ -d "$DESTINO/datos" ]; then
    mv "$DESTINO/datos" "$NUEVO/datos"
else
    mkdir -p "$NUEVO/datos"
fi

aviso "Cambiando de versión…"
rm -rf "$ANTERIOR"
cd "$APPS"
mv "$DESTINO" "$ANTERIOR"
mv "$NUEVO" "$DESTINO"

aviso "Reconstruyendo y levantando…"
cd "$DESTINO"
docker compose up -d --build

PUERTO=$(grep '^PUERTO=' "$DESTINO/.env" | cut -d= -f2)
PUERTO=${PUERTO:-8890}

aviso "Esperando a que responda en el puerto $PUERTO…"
INTENTO=0
while [ "$INTENTO" -lt 40 ]; do
    if curl -fsS -o /dev/null "http://localhost:$PUERTO/estado/" 2>/dev/null; then
        aviso "Listo. Alejandria responde."
        docker compose ps
        echo ""
        echo "La versión anterior sigue guardada en:"
        echo "  $ANTERIOR"
        exit 0
    fi
    INTENTO=$((INTENTO + 1))
    sleep 3
done

echo ""
echo "AVISO: han pasado dos minutos y no responde. Mira qué dice:"
echo "  cd $DESTINO && docker compose logs --tail 40 web"
echo ""
echo "Para volver a la versión anterior (tus fichas se mueven con ella):"
echo "  cd $DESTINO && docker compose down"
echo "  mv $DESTINO/datos $ANTERIOR/datos"
echo "  cd $APPS && rm -rf $DESTINO && mv $ANTERIOR $DESTINO"
echo "  cd $DESTINO && docker compose up -d"
exit 1
