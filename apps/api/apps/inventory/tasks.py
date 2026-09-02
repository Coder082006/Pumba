"""inventory module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Celery tasks.

**The first real task in this codebase**, so it sets conventions the eleven
still-empty `tasks.py` files will follow:

*The task is a thin shell.* It reads the settings a use case needs, calls
`services`, and logs the outcome. Nothing decides anything here — a rule that
lived in a task would be a rule only reachable through a worker, untestable
without one and invisible to the module's own suite.

*It is named for what it does to the data*, matching §8.8's registry exactly
(`materialise_activity_departures`), because that table is what an operator
reads when a queue backs up.

*Its schedule is data.* `CELERY_BEAT_SCHEDULER` is the database scheduler, so
the cadence lives in a `django_celery_beat.PeriodicTask` row created by a data
migration rather than in a `beat_schedule` dict. That means an operator can
retime a job without a deploy, which is the reason the database scheduler was
chosen, and it means the seed of that row is a migration somebody can read.

*Idempotence is a property of the routine, not of the queue.* §8.8 marks both
of this module's jobs idempotent. `acks_late` is on, so a worker lost
mid-execution redelivers, and a job that only worked once would silently
double-count on exactly the day the machine died.
"""

from __future__ import annotations

import logging
from datetime import datetime

from celery import shared_task
from django.utils import timezone

from apps.common.config import get_setting
from apps.inventory import services

logger = logging.getLogger(__name__)

__all__ = ["materialise_activity_departures"]


@shared_task(name="inventory.materialise_activity_departures", queue="default")
def materialise_activity_departures(*, today: str | None = None) -> dict[str, int]:
    """§8.8: *"Beat, nightly … expand schedules into departures for the horizon.
    Idempotent upsert."*

    `today` is a date string for the benefit of a manual run and of tests;
    Celery serialises arguments as JSON, so a `date` object could not survive
    the queue and passing one would work locally and fail in a worker.

    Returns counts rather than nothing, so the result backend carries something
    an operator can read when asked whether last night's run did anything.
    """
    when = datetime.fromisoformat(today).date() if today else timezone.localdate()
    horizon = int(get_setting("departures.horizon_days"))
    created = services.materialise_departures(start=when, horizon_days=horizon)
    logger.info(
        "materialise_activity_departures: %s departures created over %s days from %s",
        created,
        horizon,
        when,
    )
    return {"created": created, "horizon_days": horizon}
