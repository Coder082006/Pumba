"""CI and test settings.

Kept deliberately close to `dev`. Integration tests that need a real database
are marked `@pytest.mark.integration` and provision Postgres and Redis via
testcontainers; pure domain-layer unit tests never touch either.
"""

from .base import *  # noqa: F403

DEBUG = False
SECRET_KEY = "ci-insecure-not-a-secret"
ALLOWED_HOSTS = ["*"]

# Fast hashing — CI is not testing Argon2's work factor.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

LOGGING["root"]["level"] = "WARNING"  # noqa: F405
