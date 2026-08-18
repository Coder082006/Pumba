"""Local development, run via Docker Compose."""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["*"]

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",  # web-tourist (Next.js)
    "http://localhost:5173",  # web-console (Vite)
]

# Run Celery tasks eagerly only when explicitly asked; the Compose stack runs
# real workers, so the default is False.
CELERY_TASK_ALWAYS_EAGER = False
