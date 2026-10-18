"""Django settings. Everything user-facing comes from mii/config.py (the root .env).

The whole backend is one Django app (`mii`): models, views, the WebSocket
consumer, and the management commands all live in this package.
"""

from urllib.parse import parse_qs, unquote, urlsplit

from mii.config import BACKEND_DIR, config

BASE_DIR = BACKEND_DIR

# Local by default; the hosted demo sets DJANGO_DEBUG=0 and its own DJANGO_SECRET_KEY.
SECRET_KEY = config.secret_key
DEBUG = config.debug
ALLOWED_HOSTS = ["*"]
# Room for a task's attachments (8 MB of files, base64 in JSON: tasks.MAX_ATTACHMENTS_TOTAL).
DATA_UPLOAD_MAX_MEMORY_SIZE = 12 * 1024 * 1024

INSTALLED_APPS = [
    "channels",
    "mii",
]

# JSON API only: no sessions, auth, CSRF (views are @csrf_exempt), or templates.
MIDDLEWARE = [
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "mii.urls"
ASGI_APPLICATION = "mii.asgi.application"

# SQLite by default. MII_DATABASE_URL=postgres://user:pass@host:5432/db?sslmode=require
# switches to Postgres (TimescaleDB / Tiger Cloud also gets the metrics hypertable: metrics.py).
_db_url = urlsplit(config.database_url or "")
if _db_url.scheme in ("postgres", "postgresql"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": _db_url.path.lstrip("/"),
            "USER": unquote(_db_url.username or ""),
            "PASSWORD": unquote(_db_url.password or ""),
            "HOST": _db_url.hostname or "",
            "PORT": str(_db_url.port or ""),
            "OPTIONS": {key: values[0] for key, values in parse_qs(_db_url.query).items() if key == "sslmode"},
            "CONN_MAX_AGE": 0,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": config.database_path,
            "CONN_MAX_AGE": 0,  # Django recommends no persistent connections in async mode
            "OPTIONS": {"timeout": 20},  # parallel packages write at once: wait for the lock, don't fail
        }
    }

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "%(levelname)s %(name)s: %(message)s"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "plain"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
