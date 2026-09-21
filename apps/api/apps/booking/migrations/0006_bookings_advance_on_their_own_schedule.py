"""The sweeper that starts and finishes a booking — ADR 0028.

§8.8 registers no such job, because the SRS assumes a driver reports a transfer
and never says who reports an activity. Nobody does: a dive centre will not open
a console to say the boat came back.

Scheduled with the module that owns the transition, for the reason
`0001_periodic_jobs` gives. Fifteen minutes because the precision that matters
is the day, and BR-071's accrual has a two-day settlement hold behind it.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

TASK = "booking.advance_due_bookings"


def _add(apps: Any, schema_editor: Any) -> None:
    interval_model = apps.get_model("django_celery_beat", "IntervalSchedule")
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")
    quarter_hour, _ = interval_model.objects.get_or_create(every=900, period="seconds")
    periodic.objects.get_or_create(
        name="Start and complete bookings whose times have passed",
        defaults={"task": TASK, "interval": quarter_hour, "queue": "default"},
    )


def _remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(task=TASK).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0005_booking_voucher"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(_add, _remove)]
