"""Row builders for trip's own tables.

Plain functions rather than a factory library, matching
`apps.catalogue.tests.factories`: the interesting cases here are about *which*
nullable columns are set, and a builder that fills every field with plausible
noise hides the one column a test is about.

`make_item` deliberately supplies **no** type-specific columns of its own. Each
of the five item shapes is spelled out at its call site, because the whole
point of the §7.5.11 constraints is that the shapes differ, and a helper that
quietly filled in the right subset would make a test pass without the
constraint ever being exercised.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from apps.trip.models import (
    ItemType,
    Itinerary,
    ItineraryItem,
    Trip,
    TripFlight,
)
from apps.trip.tests import external_rows

__all__ = ["make_trip", "make_itinerary", "make_item", "make_flight", "AT"]

#: A fixed instant, so a test never depends on when it ran. Inside the trip
#: window the factories default to.
AT = datetime(2027, 6, 1, 9, 0, tzinfo=UTC)

_counter = {"n": 0}


def _reference() -> str:
    _counter["n"] += 1
    return f"TRP-2027-{_counter['n']:07d}"


def make_trip(**overrides: Any) -> Trip:
    values: dict[str, Any] = {
        "reference": _reference(),
        "tourist_id": None,
        "destination_id": None,
        "start_date": date(2027, 6, 1),
        "end_date": date(2027, 6, 6),
        "adults": 2,
        "currency": "NZD",
    }
    values.update(overrides)
    if values["tourist_id"] is None:
        values["tourist_id"] = external_rows.make_tourist_id()
    if values["destination_id"] is None:
        values["destination_id"] = external_rows.make_destination().id
    return Trip.objects.create(**values)


def make_itinerary(trip: Trip | None = None, **overrides: Any) -> Itinerary:
    return Itinerary.objects.create(trip=trip or make_trip(), **overrides)


def make_item(
    itinerary: Itinerary | None = None,
    *,
    item_type: str = ItemType.FREE_TIME,
    day_number: int = 1,
    sequence_no: int = 1,
    minutes: int = 60,
    **overrides: Any,
) -> ItineraryItem:
    values: dict[str, Any] = {
        "itinerary": itinerary or make_itinerary(),
        "item_type": item_type,
        "day_number": day_number,
        "sequence_no": sequence_no,
        "title": "An item",
        "starts_at": AT,
        "ends_at": AT + timedelta(minutes=minutes),
    }
    values.update(overrides)
    return ItineraryItem.objects.create(**values)


def make_flight(trip: Trip | None = None, **overrides: Any) -> TripFlight:
    values: dict[str, Any] = {
        "trip": trip or make_trip(),
        "direction": "INBOUND",
        "flight_number": "451",
        "airline_iata": "NZ",
        "gateway_destination_id": None,
        "scheduled_at": AT,
        "pax_count": 2,
    }
    values.update(overrides)
    if values["gateway_destination_id"] is None:
        values["gateway_destination_id"] = external_rows.make_destination(is_gateway=True).id
    return TripFlight.objects.create(**values)
