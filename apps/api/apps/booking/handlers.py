"""booking module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Event handlers.

**The first subscribers in the codebase, and they are here for the reason the
tasks are.** §8.9's bus exists so a module can react to something it may not
reach into: `trip` publishes, `inventory` holds the rows, and §6.4 forbids
either from importing the other. `booking` may see both (ADR 0022), so the
composition lives where the dependencies are legal — exactly the argument that
puts `release_expired_holds` in `tasks.py`.

Nothing here decides anything. `publish` dispatches after commit and swallows a
handler's failure so one consumer cannot roll back its publisher, which is why
every handler below is safe to lose: the same work is done again by §17.5's
sweeper when the hold expires.
"""

from __future__ import annotations

import logging

from apps.common.events import subscribe
from apps.inventory import services as inventory
from apps.trip.services import TripDeleted

logger = logging.getLogger(__name__)

__all__ = ["on_trip_deleted", "register"]


def on_trip_deleted(event: TripDeleted) -> None:
    """Give a discarded plan's seats back at once, rather than at expiry.

    A PRICED trip holds real capacity. Waiting for the sweeper would leave
    those seats unsellable for the rest of the hold window for a trip that no
    longer exists — invisible to everyone except the next tourist who is told
    a departure is full.

    Idempotent, and safe for a DRAFT trip that never held anything:
    `inventory.release` answers zero rather than raising.
    """
    released = inventory.release(trip_id=event.trip_id)
    if released:
        logger.info(
            "trip_deleted_released_holds",
            extra={"trip_public_id": event.trip_public_id, "holds": released},
        )


def register() -> None:
    """Called from `BookingConfig.ready()`, which runs once per process."""
    subscribe(TripDeleted, on_trip_deleted)
