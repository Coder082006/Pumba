"""booking module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Celery tasks.

**Both jobs live here because both touch two modules' rows**, and `booking` is
the only one that may see them (ADR 0022). `inventory` owns the counters and
the hold rows; `trip` owns the status a released hold has to move. §6.4 forbids
either from reaching the other, and §8.8 registers the sweeper without naming a
module, so nothing is contradicted by putting the composition where the
dependencies are legal.

Neither decides anything. §17.5's rules are in `inventory.services`, §20.5's in
`trip.services`, and what is here is the order they run in and the reporting an
operator reads.
"""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

from apps.inventory import services as inventory
from apps.trip import services as trip_services

logger = logging.getLogger(__name__)

__all__ = ["release_expired_holds", "reconcile_inventory"]


@shared_task(name="booking.release_expired_holds", queue="default")
def release_expired_holds() -> dict[str, int]:
    """§17.5's sweeper, and the second half of TC-052.

    §8.8: *"Beat, every 60 s … Release inventory_hold past expires_at.
    Idempotent."*

    Two steps, in this order:

        1. `inventory.release_expired` — mark each dead hold EXPIRED and give
           its capacity back, one transaction per hold, under lock
        2. `trip.expire_quote` — return each affected trip to DRAFT

    **Capacity first.** A tourist whose trip returned to DRAFT while its seats
    were still held would find the planner editable and the departure
    apparently full, which is the confusing half of the two orders. The other
    way round, the seats are free for a moment before the trip stops claiming
    them, which nobody can observe as anything but a slightly early release.

    **A trip is de-duplicated before it is touched.** Three expired holds on
    one trip are one trip to move, and `expire_quote` is idempotent anyway —
    but calling it three times would log three transitions where one happened.

    Returns counts because §8.8 gives this job no other output, and "the
    sweeper ran" is not the same statement as "the sweeper found nothing".
    """
    now = timezone.now()
    trip_ids = inventory.release_expired(now=now)

    moved = 0
    for trip_id in dict.fromkeys(trip_ids):
        if trip_services.expire_quote(trip_id):
            moved += 1

    if trip_ids:
        logger.info(
            "release_expired_holds: %s holds expired across %s trips, %s returned to DRAFT",
            len(trip_ids),
            len(set(trip_ids)),
            moved,
        )
    return {"holds": len(trip_ids), "trips": moved}


@shared_task(name="booking.reconcile_inventory", queue="default")
def reconcile_inventory() -> dict[str, int]:
    """§17.4's reconciliation, over the half of the system that exists.

    §17.4 defines it as *"Nightly job compares Σ confirmed bookings against
    `*_sold` and alerts on drift"*, and ADR 0022 records why v1 cannot: there
    are no bookings until Phase 7 and nothing moves `capacity_sold` until
    §20.8's confirmation routine exists, so a job written to that letter would
    compare zero against zero and pass every night whether or not it worked.

    What it checks instead is the same class of drift over what Phase 5 owns:
    `capacity_held` against the live holds that justify it. A departure with
    capacity spoken for by no hold is capacity no tourist can buy and no
    sweeper will ever return — a slow leak that ends as a boat with empty
    seats and a screen saying it is full.

    **It alerts rather than repairs.** §38.5 targets zero oversell incidents
    and zero reconciliation exceptions unresolved beyond 48 hours; a job that
    quietly corrected the counter would meet the second target by destroying
    the evidence for the first. Drift here means a defect above it, and the
    number is worth less than knowing it happened.
    """
    drifts = inventory.reconcile()
    for drift in drifts:
        logger.error(
            "inventory drift: departure %s holds %s but live holds account for %s",
            drift.departure_public_id,
            drift.capacity_held,
            drift.held_by_live_holds,
        )
    return {"drifted": len(drifts)}
