"""§8.8's finance jobs, as Beat rows.

Three of the four §8.8 lists for this module. `accrue_commission` is not among
them: it is an event handler rather than a schedule ("Trigger: Event: booking
completed"), and it lives in `handlers.py` where the event reaches it.

Cadences are the SRS's where it gives one — weekly batches (§8.8), a daily
reconciliation (§8.8) — and daily for the settlement hold, which §22.4 measures
in days and which an hourly job would re-check twenty-three times for nothing.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

JOBS = {
    "finance.mature_provider_balances": ("Move held provider balances to available", 86400),
    "finance.build_payout_batches": ("Assemble weekly provider payouts", 604800),
    "finance.check_the_ledger": ("Assert the ledger invariant (BR-064)", 86400),
}


def _add(apps: Any, schema_editor: Any) -> None:
    interval_model = apps.get_model("django_celery_beat", "IntervalSchedule")
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")
    for task, (name, seconds) in JOBS.items():
        schedule, _ = interval_model.objects.get_or_create(every=seconds, period="seconds")
        periodic.objects.get_or_create(
            name=name,
            defaults={"task": task, "interval": schedule, "queue": "finance"},
        )


def _remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(
        task__in=list(JOBS)
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("finance", "0001_initial"),
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(_add, _remove)]
