# Alejandria

Gestor documental propio: lo que se usa de Paperless (etiquetas, corresponsales,
tipos, campos personalizados, buzón de entrada) con el aire de trabajo de
DevonThink (tres paneles, teclado, vista previa al lado), **sin OCR y sin
modelos de IA** — que es justo lo que se come la RAM del NAS.

Presupuesto de memoria: **techo duro de 392 MB** repartido entre tres
contenedores, con un consumo real en reposo de 200-230 MB.

---

## Ideas de partida

1. **Los ficheros no son de la aplicación.** Alejandria indexa la carpeta *en su
   sitio*, guardando la ruta relativa y una huella SHA-256. Se pueden seguir
   tocando los papeles por SMB, moverlos o renombrarlos: el siguiente escaneo lo
   detecta y reengancha por huella. Nada de una carpeta oscura de la que no se
   puedan sacar los documentos.
2. **Sin procesos de fondo comiendo memoria.** Ni Celery, ni Redis, ni Postgres.
   Una base SQLite en modo WAL, un proceso de Django y un vigilante del buzón que
   mira la carpeta cada 30 segundos.
3. **Nada de OCR ni de IA.** La búsqueda es sobre metadatos (título, ruta,
   etiquetas, corresponsal, tipo, notas y campos), con el índice FTS5 que ya trae
   SQLite. Es lo que hace que quepa en un NAS.
4. **Las miniaturas se generan fuera de Python**, con `pdftoppm` o
   `vipsthumbnail`, bajo demanda, con límite de memoria y de tiempo, y se cachean.
5. **Los documentos se envían a trozos**, nunca se cargan enteros. Servir un PDF de
   300 MB sube el consumo de 70 a 72 MB y vuelve a bajar. Se puede delegar la entrega en
   nginx (`X-Accel-Redirect`, variable `ALEJANDRIA_ACCEL`) para ahorrar algo de CPU, pero
   exige que nginx pueda leer la carpeta de documentos — en un NAS con permisos de grupo
   cerrados no puede, y devuelve 403. Por eso viene desactivado.

## Qué hace

- Biblioteca en tres paneles: filtros a la izquierda, lista o miniaturas en el
  centro, ficha y vista previa a la derecha.
- Etiquetas **jerárquicas** con color, corresponsales, tipos de documento y
  campos personalizados (texto, número, fecha, sí/no, importe, enlace, lista).
  Los campos se eligen de una lista —varios de una vez— y aparecen en la ficha
  al momento, sin pasar por Guardar.
- Búsqueda instantánea con prefijos y sin tildes, filtros combinables por
  etiqueta (incluyendo su rama), corresponsal, tipo, año, formato y carpeta.
- **Varias etiquetas a la vez.** Por defecto suman (cualquiera de ellas), con un
  interruptor en la barra lateral para exigirlas todas. Una etiqueta nunca
  desaparece de la lista por haber elegido otra: como mucho se atenúa si no se
  cruza con ella, y se sigue pudiendo pulsar.
- Búsquedas guardadas fijadas en la barra lateral (las "carpetas inteligentes").
- Selección múltiple y acciones en bloque: etiquetar, asignar corresponsal,
  mandar a la papelera.
- Buzón de entrada: se sueltan ficheros (o se suben desde la aplicación) y se
  archivan solos con el patrón `{año}/{corresponsal}/{título}`, sacando la fecha
  del nombre del fichero.
- **Recién llegados.** Todo lo que entra por el buzón o por el botón de subir
  queda apartado en esa colección, con su número en la barra lateral, hasta que
  se cataloga. En cuanto un documento tiene etiqueta, corresponsal o tipo, sale
  solo de la lista (o se saca a mano con «Ya está»).
- Subir documentos abre una ventana con zona de arrastrar y botón de buscar:
  acepta varios a la vez, se pueden ir acumulando y al terminar deja abierta la
  lista de recién llegados con el primero listo para catalogar.
- Importación completa de una instalación de paperless-ngx.
- Teclado: `/` busca, `j`/`k` se mueven por la lista, `Intro` abre el documento.

## Probarlo en el escritorio

```sh
cd ~/Escritorio/Alejandria
.venv/bin/python manage.py migrate
.venv/bin/python manage.py escanear          # indexa datos/biblioteca
.venv/bin/python manage.py runserver
```

Y abrir <http://127.0.0.1:8000>. La biblioteca de pruebas está en
`datos/biblioteca` y el buzón en `datos/entrada`.

Para apuntar a otra carpeta sin tocar nada de código:

```sh
ALEJANDRIA_BIBLIOTECA=~/Documentos/Papeles .venv/bin/python manage.py runserver
```

## Comandos

| Comando | Para qué |
|---|---|
| `manage.py escanear` | Recorre la biblioteca: da de alta lo nuevo, reengancha lo movido, marca lo que falta. |
| `manage.py escanear --rehashear` | Recalcula la huella de todo (verificación de integridad). |
| `manage.py importar_paperless <carpeta>` | Vuelca los metadatos de paperless-ngx. |
| `manage.py vigilar` | Vigila el buzón y archiva lo que aparezca. |
| `manage.py reindexar` | Reconstruye el índice de búsqueda. |

## Traerse lo de Paperless

El importador lee el `db.sqlite3` de paperless **en solo lectura** (no lo
modifica) y trae etiquetas con sus colores, corresponsales, tipos, campos
personalizados con sus valores, y el emparejado de cada documento con su
fichero. Se puede ejecutar las veces que haga falta: no duplica nada.

```sh
# 1. Apuntar la biblioteca a los originales de paperless y escanear
ALEJANDRIA_BIBLIOTECA=$PAPERLESS/data/documents/originals \
  manage.py escanear

# 2. Traer los metadatos
ALEJANDRIA_BIBLIOTECA=$PAPERLESS/data/documents/originals \
  manage.py importar_paperless $PAPERLESS
```

Si un documento de paperless no tiene su fichero en la carpeta, se salta y se
avisa al final; no se inventa nada.

> Paperless con base de datos PostgreSQL no está contemplado: la instalación del
> NAS es la imagen todo-en-uno de LinuxServer, que usa SQLite.

## Despliegue en el NAS

```sh
cp .env.example .env    # y ajustar rutas, puerto y clave
docker compose up -d --build
```

Un `docker-compose.yml` propio con red aislada. Si usas un script central de
despliegue, la línea del registro de proyectos sería:

```
alejandria|/ruta/al/repo/alejandria|main|alejandria-nginx
```

### Reparto de memoria

| Contenedor | Techo | Qué hace |
|---|---|---|
| `alejandria-web` | 288 MB | Django + gunicorn, **1 worker** con 4 hilos |
| `alejandria-vigilante` | 80 MB | Archiva lo que cae en el buzón |
| `alejandria-nginx` | 24 MB | Estáticos y entrega de ficheros |

Los techos son límites duros de Docker: si algo se desmadra, se reinicia
Alejandria y no se lleva por delante a Treasure ni a PhotoPrism.

### Convivencia con Paperless

Se puede tener Paperless y Alejandria a la vez sobre la **misma carpeta** durante
la transición, siempre que solo uno de los dos escriba: Paperless entrega los
documentos a su manera y Alejandria los ve aparecer en el siguiente escaneo.
Para dejar de depender de Paperless basta con apagar su contenedor; los ficheros
siguen donde están y Alejandria sigue funcionando igual.

## Ajustes

Todo va por variables de entorno, ver `.env.example`. Los que más se tocan:

| Variable | Por defecto | Qué hace |
|---|---|---|
| `ALEJANDRIA_BIBLIOTECA` | `datos/biblioteca` | Carpeta de documentos a indexar. |
| `ALEJANDRIA_ENTRADA` | `datos/entrada` | Buzón. |
| `ALEJANDRIA_PATRON` | `{anio}/{corresponsal}/{titulo}{ext}` | Cómo se archiva lo del buzón. |
| `ALEJANDRIA_MINIATURAS` | `1` | Miniaturas sí o no. |
| `ALEJANDRIA_LOGIN` | `0` | Exigir usuario y contraseña. |
| `ALEJANDRIA_EXTENSIONES` | pdf, imágenes, ofimática… | Qué se indexa. |
| `ALEJANDRIA_IGNORAR` | — | Carpetas extra a saltar, separadas por comas. |

## Lo que Alejandria no hace (a propósito)

- No hace OCR ni busca dentro del texto de los PDFs escaneados.
- No clasifica sola con modelos de IA.
- No borra ni reorganiza ficheros que ya estén en la biblioteca. «Quitar ficha»
  quita el documento de Alejandria; **el fichero del disco no se toca.**
