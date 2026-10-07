import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise RuntimeError("DJANGO_SECRET_KEY must be set")
DEBUG = os.environ.get("DJANGO_DEBUG", "false").lower() == "true"
ALLOWED_HOSTS = os.environ.get("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
ATOMIC_REQUESTS = False

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "rest_framework",
    "store",
    "fake_payments",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.request_id.RequestIdMiddleware",
]

db_common = {
    "ENGINE": "django.db.backends.postgresql",
    "USER": os.environ.get("DB_USER", "uniblox"),
    "PASSWORD": os.environ.get("DB_PASSWORD", ""),
    "HOST": os.environ.get("DB_HOST", "db"),
    "PORT": os.environ.get("DB_PORT", "5432"),
    "CONN_MAX_AGE": 0,
    "ATOMIC_REQUESTS": False,
}
DATABASES = {
    "default": {**db_common, "NAME": os.environ.get("DB_NAME", "uniblox")},
    "payments": {**db_common, "NAME": os.environ.get("PAYMENTS_DB_NAME", "uniblox_payments")},
}
DATABASE_ROUTERS = ["config.db_router.DatabaseRouter"]

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "config.errors.json_exception_handler",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "UNAUTHENTICATED_USER": None,
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "config.logging.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
