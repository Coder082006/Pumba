"""Celery application.

Five queues with dedicated workers (SRS §8.8). Queue isolation is the point:
a burst of notifications must never delay payment verification.
"""

import os

from celery import Celery
from kombu import Queue

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("pumba")
app.config_from_object("django.conf:settings", namespace="CELERY")

app.conf.task_queues = (
    Queue("default"),
    Queue("realtime"),
    Queue("notify"),
    Queue("payments"),
    Queue("finance"),
)

app.autodiscover_tasks()
