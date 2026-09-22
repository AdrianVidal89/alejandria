"""
Configuración de Alejandria.

Criterio general: todo lo que consuma memoria o procesos va apagado o al mínimo.
Una sola base SQLite en modo WAL, sin cachés externas, sin colas, sin Celery.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _ruta(var, por_defecto):
    return Path(os.environ.get(var, por_defecto)).expanduser().resolve()


def _bool(var, por_defecto=False):
    return os.environ.get(var, "1" if por_defecto else "0").lower() in ("1", "true", "yes", "si", "sí")


# --- Rutas de datos -----------------------------------------------------------
# BIBLIOTECA: carpeta raíz de documentos ya existente (la de Paperless, en el NAS).
#             Alejandria la indexa EN SU SITIO: no mueve ni copia nada de aquí.
# ENTRADA:    buzón donde se sueltan documentos nuevos; el vigilante los archiva
#             dentro de BIBLIOTECA y los da de alta.
# DATOS:      base de datos y miniaturas generadas.
BIBLIOTECA_DIR = _ruta("ALEJANDRIA_BIBLIOTECA", BASE_DIR / "datos" / "biblioteca")
ENTRADA_DIR = _ruta("ALEJANDRIA_ENTRADA", BASE_DIR / "datos" / "entrada")
DATOS_DIR = _ruta("ALEJANDRIA_DATOS", BASE_DIR / "datos")
MINIATURAS_DIR = DATOS_DIR / "miniaturas"

for _d in (BIBLIOTECA_DIR, ENTRADA_DIR, DATOS_DIR, MINIATURAS_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Núcleo Django ------------------------------------------------------------
SECRET_KEY = os.environ.get("ALEJANDRIA_SECRET_KEY", "dev-inseguro-cambiar-en-el-nas")
DEBUG = _bool("ALEJANDRIA_DEBUG", True)
ALLOWED_HOSTS = os.environ.get("ALEJANDRIA_HOSTS", "*").split(",")
CSRF_TRUSTED_ORIGINS = [
    o for o in os.environ.get("ALEJANDRIA_CSRF_ORIGINS", "").split(",") if o
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "biblioteca",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "alejandria.urls"
WSGI_APPLICATION = "alejandria.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DATOS_DIR / "alejandria.sqlite3",
        "OPTIONS": {
            # WAL + timeout: lecturas de la web no se bloquean mientras el
            # vigilante escribe. init_command se aplica a cada conexión nueva.
            "timeout": 20,
            "init_command": (
                "PRAGMA journal_mode=WAL;"
                "PRAGMA synchronous=NORMAL;"
                "PRAGMA temp_store=MEMORY;"
                "PRAGMA mmap_size=0;"
                "PRAGMA cache_size=-8000;"  # 8 MB de caché de páginas, no más
            ),
            "transaction_mode": "IMMEDIATE",
        },
        "CONN_MAX_AGE": 0,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "es-es"
TIME_ZONE = os.environ.get("TZ", "Europe/Madrid")
USE_I18N = True
USE_TZ = True

STATIC_URL = "estaticos/"
# Dentro de la imagen, no en los datos: los estáticos son parte del programa, no
# información del usuario. Se generan durante la construcción y los sirve
# whitenoise, así que el contenedor no necesita escribir en ninguna carpeta del
# NAS para arrancar (era una fuente segura de fallos de permisos).
STATIC_ROOT = Path(os.environ.get("ALEJANDRIA_ESTATICOS", BASE_DIR / "estaticos"))
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/acceder/"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "/acceder/"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 90

# Subidas: nada de buffer gigante en memoria, al disco a partir de 2 MB.
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

# --- Ajustes propios de Alejandria -------------------------------------------
# Extensiones que se indexan. Sin OCR ni IA: solo se cataloga el fichero.
EXTENSIONES = tuple(
    e.strip().lower()
    for e in os.environ.get(
        "ALEJANDRIA_EXTENSIONES",
        ".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.txt,.md,.doc,.docx,.xls,.xlsx,.odt,.ods,.ppt,.pptx,.eml,.zip,.csv",
    ).split(",")
    if e.strip()
)

# Generar miniaturas (subproceso pdftoppm/convert, bajo demanda y cacheadas).
MINIATURAS_ACTIVAS = _bool("ALEJANDRIA_MINIATURAS", True)
MINIATURA_ANCHO = int(os.environ.get("ALEJANDRIA_MINIATURA_ANCHO", "420"))
# Techo de memoria del subproceso de miniaturas (MB). Protege al NAS de un
# escaneo a 600 ppp que intente reventar la RAM.
MINIATURA_LIMITE_MB = int(os.environ.get("ALEJANDRIA_MINIATURA_LIMITE_MB", "220"))
MINIATURA_TIMEOUT = int(os.environ.get("ALEJANDRIA_MINIATURA_TIMEOUT", "25"))

# Vigilante de la carpeta de entrada: hilo interno, sondeo por tiempo.
# En Docker corre como proceso aparte (ALEJANDRIA_VIGILANTE=0 en la web).
VIGILANTE_ACTIVO = _bool("ALEJANDRIA_VIGILANTE", False)
VIGILANTE_INTERVALO = int(os.environ.get("ALEJANDRIA_VIGILANTE_INTERVALO", "30"))

# Patrón de archivado de lo que entra por el buzón, relativo a BIBLIOTECA_DIR.
PATRON_ARCHIVADO = os.environ.get("ALEJANDRIA_PATRON", "{anio}/{corresponsal}/{titulo}{ext}")

# Si el patrón empieza por una carpeta fija —«Bandeja de entrada/{titulo}{ext}»—,
# todo lo que llega por el buzón se acumula ahí hasta que alguien lo coloca. Ese
# nombre es el que usa el atajo «Bandeja de entrada» de la barra lateral. Con el
# patrón clásico por años no hay carpeta fija y el atajo no se enseña.
_cabeza_patron = PATRON_ARCHIVADO.split("{")[0]
CARPETA_BUZON = _cabeza_patron.strip("/") if "/" in _cabeza_patron else ""

# Servir ficheros delegando en nginx (X-Accel-Redirect): Django no lee el PDF.
ACCEL_PREFIJO = os.environ.get("ALEJANDRIA_ACCEL", "")

PAGINADO = int(os.environ.get("ALEJANDRIA_PAGINADO", "60"))

# Acceso: con un solo usuario en una red Tailscale, exigir login es opcional.
EXIGIR_LOGIN = _bool("ALEJANDRIA_LOGIN", False)

X_FRAME_OPTIONS = "SAMEORIGIN"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"consola": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["consola"], "level": os.environ.get("ALEJANDRIA_LOG", "INFO")},
}
