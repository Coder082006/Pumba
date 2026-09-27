"""Application layer (SRS §8.2 layer 2).

    The ONLY module boundary. Other modules call this and nothing else
    (SRS §6.5 rule 1). Orchestrates a use case in one transaction and
    emits domain events.

    Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

    Public interface: route(), distance_matrix(), geocode(), last_known()

Phase 9a implements `last_known()`, which is the one §11.6 needs. The other
three go through `RoutingPort` where they are called and have not needed a home
here; `route_cache` is a performance decision and there is no traffic to make it
from yet (see `models`).

**Freshness is the caller's number, not a default here.** §11.6 says "fresh
(< 15 min)" and this module takes the window as an argument. A dispatcher
scoring a job three days out and a tracking screen following a car are asking
two different questions of the same table, and a constant here would answer
both with whichever one was written first.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from apps.location.dto import DriverPositionDTO
from apps.location.models import DriverLocation

__all__ = ["last_known", "record_position", "DriverPositionDTO"]


def record_position(
    *,
    driver_id: int,
    lat: float,
    lng: float,
    recorded_at: datetime,
    accuracy_m: int | None = None,
    speed_kph: Decimal | None = None,
    heading: int | None = None,
) -> DriverPositionDTO:
    """One reading, stored as given.

    Nothing is corrected or discarded here. §33's plausibility rules — an
    implausible speed, an accuracy radius too wide to be useful — belong to
    Phase 10's ingest, and a filter applied at write time would throw away the
    evidence those rules are checked against. `driver_location` rows are also
    what §20.10 reads as no-show evidence, so a discarded reading is a dispute
    that cannot be answered.
    """
    from django.contrib.gis.geos import Point

    row = DriverLocation.objects.create(
        driver_id=driver_id,
        point=Point(lng, lat, srid=4326),
        accuracy_m=accuracy_m,
        speed_kph=speed_kph,
        heading=heading,
        recorded_at=recorded_at,
    )
    return _dto(row)


def last_known(
    driver_ids: Sequence[int], *, now: datetime, within: timedelta
) -> dict[int, DriverPositionDTO]:
    """`last_known()` from the §6.4 interface — §11.6's dispatch anchor.

    A mapping of only the drivers whose latest reading is inside `within`.
    Absence is the answer for the rest: §11.6 falls back to the home
    destination's centroid, and a stale position returned as though it were
    current would be worse than none — it would score a driver as nearby
    because that is where they were before their phone lost signal an hour ago.

    One query for the whole candidate list. A dispatch run scores every
    eligible driver, and a per-driver lookup would make the query count a
    function of how healthy the supply is.
    """
    if not driver_ids:
        return {}

    cutoff = now - within
    rows = (
        DriverLocation.objects.filter(driver_id__in=set(driver_ids), recorded_at__gte=cutoff)
        .order_by("driver_id", "-recorded_at")
        .distinct("driver_id")
    )
    return {int(row.driver_id): _dto(row) for row in rows}


def _dto(row: DriverLocation) -> DriverPositionDTO:
    return DriverPositionDTO(
        driver_id=int(row.driver_id),
        lat=float(row.point.y),
        lng=float(row.point.x),
        accuracy_m=row.accuracy_m,
        recorded_at=row.recorded_at,
    )
