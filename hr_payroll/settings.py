import os
from pathlib import Path
from datetime import timedelta
from celery.schedules import crontab


BASE_DIR = Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────
# ENVIRONMENT HELPERS
# ─────────────────────────────────────────────────────────────

def env_bool(name, default=False):
    value = os.environ.get(name)

    if value is None:
        return default

    return value.strip().lower() in (
        "true",
        "1",
        "yes",
        "y",
        "on",
    )


def env_int(name, default=0):
    value = os.environ.get(name)

    if value is None or value == "":
        return default

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def env_list(name, default=""):
    value = os.environ.get(name, default)

    if not value:
        return []

    return [
        item.strip()
        for item in value.split(",")
        if item.strip()
    ]


# ─────────────────────────────────────────────────────────────
# SECURITY
# ─────────────────────────────────────────────────────────────

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "change-me-in-production",
)

DEBUG = env_bool(
    "DEBUG",
    False,
)

ALLOWED_HOSTS = env_list(
    "ALLOWED_HOSTS",
    "hr.dimeapp.co.ke,localhost,127.0.0.1",
)

AUTH_USER_MODEL = "users.User"


# ─────────────────────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────────────────────

if DEBUG:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",

            "NAME": os.environ.get(
                "DB_NAME",
                "hr_payroll",
            ),

            "USER": os.environ.get(
                "DB_USER",
                "hr_payroll_user",
            ),

            "PASSWORD": os.environ.get(
                "DB_PASSWORD",
                "",
            ),

            "HOST": os.environ.get(
                "DB_HOST",
                "localhost",
            ),

            "PORT": os.environ.get(
                "DB_PORT",
                "5432",
            ),

            "CONN_MAX_AGE": 60,

            "OPTIONS": {
                "connect_timeout": 10,
            },
        }
    }


# ─────────────────────────────────────────────────────────────
# REDIS
# ─────────────────────────────────────────────────────────────

REDIS_URL = os.environ.get(
    "REDIS_URL",
    "redis://redis:6379/0",
)


# ─────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────

if DEBUG:
    CACHES = {
        "default": {
            "BACKEND": (
                "django.core.cache.backends.locmem.LocMemCache"
            ),
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": (
                    "django_redis.client.DefaultClient"
                ),
            },
        }
    }


# ─────────────────────────────────────────────────────────────
# SESSION
# ─────────────────────────────────────────────────────────────

if DEBUG:
    SESSION_ENGINE = (
        "django.contrib.sessions.backends.db"
    )
else:
    SESSION_ENGINE = (
        "django.contrib.sessions.backends.cache"
    )
    SESSION_CACHE_ALIAS = "default"
