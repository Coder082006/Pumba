"""Base settings shared by every environment.

Twelve-factor: all values come from the environment (SRS §35.5). Only
*infrastructure* configuration belongs here. Business constants — thresholds,
rates, weights, TTLs — are `system_setting` rows read through
``apps.common.config.get_setting`` and are never Django settings (NFR-M07,
SRS Appendix B).
"""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-change-me")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# SRS §7.2: primary keys are BIGSERIAL internally.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# SRS §7.5.1. `identity` owns the user table; Django's own auth tables are
# not used for authorisation — see apps/common/authz and ADR 0005.
AUTH_USER_MODEL = "identity.User"

# auth.E003 insists USERNAME_FIELD carry an unconditional UNIQUE. SRS §7.5.1
# and §7.7 require the opposite: "UNIQUE(email) WHERE deleted_at IS NULL", so
# that "re-registration after account closure remains possible". An
# unconditional index would sit alongside the partial one and silently defeat
# it, so uniqueness is enforced by `user_email_unique_alive` and the check is
# silenced — which is what the Django documentation prescribes for a partial
# unique index on the username field.
SILENCED_SYSTEM_CHECKS = ["auth.E003"]

# ---------------------------------------------------------------------------
# Applications
#
# The 14 modules of SRS §6.4. Phase 1 ships them as skeletons: the package and
# layer structure exist so the import-linter contracts are real, but only
# `common` contains implementation.
#
# `django.contrib.gis` is enabled from Phase 3, when the first
# `geography(Point, 4326)` column lands (SRS §7.2, §13.1). It binds GDAL,
# GEOS and PROJ natively, so it also decides where the test suite can run —
# see docs/adr/0004 and docs/adr/0009.
# ---------------------------------------------------------------------------
DJANGO_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.gis",
    "django.contrib.auth",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "corsheaders",
    "django_celery_beat",
    "channels",
]

PLATFORM_APPS = [
    "apps.common",
    # L0 — no intra-platform dependencies
    "apps.identity",
    "apps.location",
    "apps.notify",
    # L1
    "apps.catalogue",
    "apps.provider",
    # L2
    "apps.inventory",
    "apps.transport",
    # L3
    "apps.trip",
    # L4
    "apps.booking",
    # L5
    "apps.payment",
    "apps.messaging",
    "apps.review",
    # L6
    "apps.finance",
    # L7
    "apps.administration",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + PLATFORM_APPS

# ---------------------------------------------------------------------------
# Middleware — order matters; mirrors the request lifecycle in SRS §8.3
# ---------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "apps.common.middleware.RequestIdMiddleware",
    "django.middleware.common.CommonMiddleware",
    # django.contrib.auth.middleware.AuthenticationMiddleware is deliberately
    # absent. It requires SessionMiddleware, and this API is stateless and
    # bearer-token only (SRS §30.4: "The mobile apps use bearer tokens and are
    # not CSRF-exposed"). Authentication is DRF's
    # apps.common.authentication.PrincipalJWTAuthentication, which attaches
    # `request.principal`; nothing reads `request.user`.
    "apps.common.middleware.LocaleMiddleware",
    "apps.common.middleware.AuditContextMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASES = {
    "default": {
        # PostGIS rather than plain postgresql: SRS §13.1 requires
        # ST_Distance on the geography type, "never planar
        # approximations", which the stock backend cannot express.
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("POSTGRES_DB", default="pumba"),
        "USER": env("POSTGRES_USER", default="pumba"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="pumba"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env.int("POSTGRES_PORT", default=5432),
        "CONN_MAX_AGE": 60,
    }
}

# ---------------------------------------------------------------------------
# Cache / Redis
# ---------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_URL,
        "OPTIONS": {"CLIENT_CLASS": "django_redis.client.DefaultClient"},
    }
}

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [env("CHANNELS_REDIS_URL", default="redis://localhost:6379/3")]},
    }
}

# ---------------------------------------------------------------------------
# Celery — five queues, isolated so a notification burst cannot delay payment
# verification (SRS §8.8)
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/1")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/2")
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_QUEUES_NAMES = ["default", "realtime", "notify", "payments", "finance"]
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_TIMEZONE = "UTC"
CELERY_ENABLE_UTC = True
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

# ---------------------------------------------------------------------------
# Authentication — Argon2id (SRS §30.2)
# ---------------------------------------------------------------------------
# SRS §30.2: "Argon2id (memory 64 MiB, time cost 3, parallelism 4)". Django's
# defaults are 100 MiB / t=2 / p=8, which is not what the SRS says.
PASSWORD_HASHERS = [
    "apps.common.hashers.PlatformArgon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
]

# SRS §9.4.2: access 15 min, refresh 30 days, rotating with reuse detection.
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=30),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "ALGORITHM": "HS256",
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# ---------------------------------------------------------------------------
# DRF
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.exception_handler.platform_exception_handler",
    # SRS §9.2's envelope, applied structurally rather than by each view
    # remembering to call `success_envelope`. It was written to be wired here
    # — "a view returns its resource; the renderer wraps it" — and never was,
    # so `/health` shipped unenveloped and every client that unwraps `.data`
    # got `undefined` from it.
    #
    # Views that already build the envelope themselves are unaffected: the
    # renderer passes through any body that already carries `data` or `error`.
    "DEFAULT_RENDERER_CLASSES": [
        "apps.common.envelope.EnvelopeJSONRenderer",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.CursorPagination",
    "PAGE_SIZE": 20,
    # Wraps SimpleJWT and attaches `request.principal` — SRS §30.3.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.common.authentication.PrincipalJWTAuthentication",
    ],
    # Secure by default: a view that declares no permission classes requires
    # a principal. Making a route public is then a deliberate, visible act
    # (`_PublicView`), which is what the URL-conf audit checks for.
    "DEFAULT_PERMISSION_CLASSES": [
        "apps.common.permissions.IsAuthenticatedPrincipal",
    ],
    "DEFAULT_VERSIONING_CLASS": "rest_framework.versioning.URLPathVersioning",
    "DEFAULT_VERSION": "v1",
    "ALLOWED_VERSIONS": ["v1"],
    "UNAUTHENTICATED_USER": None,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Tourism Journey Orchestration Platform API",
    "DESCRIPTION": (
        "Client-agnostic REST API. Consumed by the tourist website, the provider "
        "portal, the administration console and (later) the Flutter applications. "
        "No client-specific concern may leak into this contract."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "OAS_VERSION": "3.1.0",
    "SCHEMA_PATH_PREFIX": "/api/v[0-9]",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": True,
    "ENUM_NAME_OVERRIDES": {},
}

# ---------------------------------------------------------------------------
# Internationalisation — SRS §7.2: TIMESTAMPTZ everywhere, stored UTC,
# rendered in the destination's timezone (never here).
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-gb"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ---------------------------------------------------------------------------
# Logging — structured JSON carrying request_id (SRS §8.11, §32.6)
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "apps.common.logging.RequestIdFilter"},
    },
    "formatters": {
        "json": {
            "()": "apps.common.logging.JsonFormatter",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id"],
            "formatter": "json",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "INFO", "propagate": False},
    },
}
