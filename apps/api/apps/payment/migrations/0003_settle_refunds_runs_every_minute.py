"""The refund settler, as a Beat row — ADR 0027 decision 3.

§8.8 registers `verify_payment_status` (a countdown, scheduled per payment) and
names no job for refunds, because the SRS assumes a refund is issued inline
with the cancellation. It cannot be: `publish` swallows a handler's failure, so
an inline refund is one gateway timeout away from being lost, and there is no
sweeper behind refunds the way §17.5's stands behind holds.

Every minute, because the row already exists and the tourist is waiting: the
work is a small query against an indexed status, and a refund that settles a
minute after the cancellation is indistinguishable from one that settled
instantly to everyone except a log.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

TASK = "payment.settle_refunds"


def _add(apps: Any, schema_editor: Any) -> None:
    interval_model = apps.get_model("django_celery_beat", "IntervalSchedule")
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")
    every_minute, _ = interval_model.objects.get_or_create(every=60, period="seconds")
    periodic.objects.get_or_create(
        name="Settle requested refunds at the payment provider",
        defaults={"task": TASK, "interval": every_minute, "queue": "payments"},
    )


def _remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(task=TASK).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("payment", "0002_a_webhook_outcome_is_ours_to_write"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(_add, _remove)]
