"""Django settings. Everything user-facing comes from mii/config.py (the root .env).

The whole backend is one Django app (`mii`): models, views, the WebSocket
consumer, and the management commands all live in this package.
"""

from mii.config import BACKEND_DIR, config

BASE_DIR = BACKEND_DIR

# Local-only hackathon app: no public deployment, no user accounts.
SECRET_KEY = "mii-nagement-local-dev-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": config.database_path,
        "CONN_MAX_AGE": 0,  # Django recommends no persistent connections in async mode
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
