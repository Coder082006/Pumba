"""§8.8's sweeper and §17.4's reconciler, as rows rather than as code.

`booking`'s first migration, and it creates no table — the booking models are
Phase 7. What it seeds is the cadence of the two jobs `booking.tasks` defines,
for the reason `inventory/0004` gives: `CELERY_BEAT_SCHEDULER` is the database
scheduler, so a `beat_schedule` dict in code would never run.

The two jobs are scheduled here rather than alongside the materialiser because
a `PeriodicTask` naming a task no worker can resolve fires on schedule and is
discarded every time — a log full of `NotRegistered` and a job an operator
believes is running. They are scheduled with the module that defines them.

    release_expired_holds | Beat, every 60 s | default | Idempotent

§17.4 gives the reconciler no cadence beyond "nightly", and no name in §8.8's
registry at all. 03:45 is after `materialise_activity_departures` at 02:15, so
a night's new departures are already in place when their counters are checked.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

SWEEPER = "booking.release_expired_holds"
RECONCILER = "booking.reconcile_inventory"


def _add(apps: Any, schema_editor: Any) -> None:
    interval_model = apps.get_model("django_celery_beat", "IntervalSchedule")
    crontab_model = apps.get_model("django_celery_beat", "CrontabSchedule")
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")

    every_minute, _ = interval_model.objects.get_or_create(every=60, period="seconds")
    nightly, _ = crontab_model.objects.get_or_create(
        minute="45", hour="3", day_of_week="*", day_of_month="*", month_of_year="*"
    )

    periodic.objects.get_or_create(
        name="Release expired inventory holds",
        defaults={"task": SWEEPER, "interval": every_minute, "queue": "default"},
    )
    periodic.objects.get_or_create(
        name="Reconcile inventory counters",
        defaults={"task": RECONCILER, "crontab": nightly, "queue": "default"},
    )


def _remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        task__in=[SWEEPER, RECONCILER]
    ).delete()


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("django_celery_beat", "0019_alter_periodictasks_options"),
        # The sweeper composes both modules' services, and its schedule should
        # not exist before the tables they read. By name, not by import.
        ("inventory", "0004_periodic_jobs"),
        ("trip", "0001_initial"),
    ]

    operations = [migrations.RunPython(_add, _remove)]
