"""Data-access layer (SRS §8.2 layer 4). All ORM writes.

§20.1: "No other module may write booking.status." Every write to that column
in the system is in this file, and every one of them is preceded, in `services`,
by a call to the booking machine.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from django.contrib.gis.geos import Point
from django.db import IntegrityError, transaction

from apps.booking.models import (
    Booking,
    BookingActivity,
    BookingStatusHistory,
    BookingTransfer,
)
from apps.common.reference import new_reference

__all__ = [
    "REFERENCE_ATTEMPTS",
    "create_booking",
    "create_activity",
    "create_transfer",
    "record_transition",
    "of_trip",
]

#: How many references to try before giving up. ADR 0025 via
#: `common.reference`: seven random digits per year, so a collision is rare and
#: five in a row would mean something other than bad luck.
REFERENCE_ATTEMPTS = 5


def create_booking(**fields: Any) -> Booking:
    """Insert a booking under a fresh `BKG-YYYY-NNNNNNN`, retrying on collision.

    Each attempt runs in a savepoint, so a unique violation on `reference`
    rolls back only that insert and not the basket around it.
    """
    for attempt in range(REFERENCE_ATTEMPTS):
        try:
            with transaction.atomic():
                return Booking.objects.create(reference=new_reference("BKG"), **fields)
        except IntegrityError as exc:
            if "reference" not in str(exc) or attempt == REFERENCE_ATTEMPTS - 1:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def create_activity(booking: Booking, **fields: Any) -> BookingActivity:
    return BookingActivity.objects.create(booking=booking, **fields)


def create_transfer(
    booking: Booking,
    *,
    pickup_lonlat: tuple[float, float],
    dropoff_lonlat: tuple[float, float],
    **fields: Any,
) -> BookingTransfer:
    return BookingTransfer.objects.create(
        booking=booking,
        pickup_point=Point(*pickup_lonlat, srid=4326),
        dropoff_point=Point(*dropoff_lonlat, srid=4326),
        **fields,
    )


def record_transition(
    booking: Booking,
    *,
    from_status: str | None,
    to_status: str,
    actor_role: str,
    actor_user_id: int | None,
    reason: str,
    occurred_at: datetime,
) -> BookingStatusHistory:
    """R26 and BR-032: one history row per change, naming actor and reason."""
    return BookingStatusHistory.objects.create(
        booking=booking,
        from_status=from_status,
        to_status=to_status,
        actor_role=actor_role,
        actor_user_id=actor_user_id,
        reason=reason,
        occurred_at=occurred_at,
    )


def of_trip(trip_id: int, *, statuses: Iterable[str] | None = None) -> list[Booking]:
    rows = Booking.objects.filter(trip_id=trip_id)
    if statuses is not None:
        rows = rows.filter(status__in=list(statuses))
    return list(rows.order_by("id"))
