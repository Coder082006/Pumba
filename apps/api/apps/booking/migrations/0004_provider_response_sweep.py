"""§14.4's on-request timeout, as a Beat row — ADR 0025 decision 7.

The SRS gives the rule ("auto-cancels with full refund" after
`provider_response_hours`) and no job. Scheduled with the module that defines
the task, for the reason `0001_periodic_jobs` gives.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

TASK = "booking.expire_provider_responses"


def _add(apps: Any, schema_editor: Any) -> None:
    interval_model = apps.get_model("django_celery_beat", "IntervalSchedule")
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")
    every_five, _ = interval_model.objects.get_or_create(every=300, period="seconds")
    periodic.objects.get_or_create(
        name="Cancel unanswered on-request bookings",
        defaults={"task": TASK, "interval": every_five, "queue": "default"},
    )


def _remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(task=TASK).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0003_booking_activity_confirmation_mode"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(_add, _remove)]
