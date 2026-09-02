"""§8.8's nightly materialisation, as a row rather than as code.

`CELERY_BEAT_SCHEDULER` is `django_celery_beat`'s database scheduler
(`config/settings/base.py`), so a schedule declared in a `beat_schedule` dict
would never run: the scheduler reads `django_celery_beat_periodictask` and
nothing else. That choice was made so an operator can retime a job without a
deploy, and its cost is that the *initial* cadence has to be seeded — here,
where it is reviewable, reversible, and applied on every fresh database.

§8.8's registry, verbatim, for the job this migration schedules:

    materialise_activity_departures | Beat, nightly | default |
                                      Expand schedules into departures for the
                                      horizon | Idempotent upsert

**Only jobs that exist are scheduled.** `release_expired_holds` is §8.8's other
Phase 5 entry and it is scheduled by `booking`'s own migration, alongside the
task itself — a `PeriodicTask` row naming a task no worker can resolve fires
every sixty seconds and is discarded every time, which is a log full of
`NotRegistered` and a job an operator believes is running.

**`get_or_create` on the name, not `create`.** An operator who has retimed a
job in the admin did so deliberately, and re-applying this migration must not
put it back. That is also what makes it safe against a database that has
already seen it.

The nightly run is at 02:15 rather than at midnight. Nothing else runs then,
and a job extending a 180-day horizon has no reason to compete with the
end-of-day traffic a midnight schedule guarantees it will meet.
"""

from __future__ import annotations

from typing import Any

from django.db import migrations

#: Matches `@shared_task(name=…)` exactly. A mismatch here is a job that is
#: scheduled, fires, and is discarded by every worker as unknown.
MATERIALISER = "inventory.materialise_activity_departures"


def _add(apps: Any, schema_editor: Any) -> None:
    crontab_model = apps.get_model("django_celery_beat", "CrontabSchedule")
    periodic = apps.get_model("django_celery_beat", "PeriodicTask")

    nightly, _ = crontab_model.objects.get_or_create(
        minute="15", hour="2", day_of_week="*", day_of_month="*", month_of_year="*"
    )
    periodic.objects.get_or_create(
        name="Materialise activity departures",
        defaults={"task": MATERIALISER, "crontab": nightly, "queue": "default"},
    )


def _remove(apps: Any, schema_editor: Any) -> None:
    apps.get_model("django_celery_beat", "PeriodicTask").objects.filter(task=MATERIALISER).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0003_inventory_hold"),
        # By name. `django_celery_beat` owns these tables and its migrations
        # must have run before rows can be written into them.
        ("django_celery_beat", "0019_alter_periodictasks_options"),
    ]

    operations = [migrations.RunPython(_add, _remove)]
