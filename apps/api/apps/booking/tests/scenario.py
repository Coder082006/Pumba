"""A quotable trip, built without importing `trip` or `inventory` internals.

`booking` may depend on both (§6.4), but `private-trip` and `private-inventory`
still limit it to their `services` and `dto` — and the ban is not relaxed for
tests. So the rows are built through `apps.get_model`, the way a migration
does, and everything a test asserts about behaviour goes through the two public
surfaces.

What this builds is the smallest thing §9.4.5 will quote: a trip with a
sequenced itinerary holding one ACTIVITY item whose `starts_at` is a real
departure's instant. That last detail is the whole mechanism — the tourist
picks a departure, the item stores its instant, and `UNIQUE(activity_id,
departs_at)` turns it back into a departure at quote time (ADR 0022).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.apps import apps as django_apps
from django.utils import timezone

__all__ = ["Scenario", "build", "add_stay"]

ZONE = "Pacific/Auckland"
BOUNDS = {
    "min_latitude": Decimal("-47.3000000"),
    "min_longitude": Decimal("166.4000000"),
    "max_latitude": Decimal("-34.3000000"),
    "max_longitude": Decimal("178.6000000"),
}

_counter = {"n": 0}


def _unique(prefix: str) -> str:
    _counter["n"] += 1
    return f"{prefix}-{_counter['n']}"


def _model(app: str, name: str) -> Any:
    return django_apps.get_model(app, name)


@dataclass(frozen=True, slots=True)
class Scenario:
    """The ids a test needs to drive and inspect one quote."""

    trip_public_id: Any
    trip_id: int
    tourist_id: int
    activity_id: int
    departure_id: int
    departs_at: datetime
    item_public_id: Any


def _destination() -> Any:
    country = _model("catalogue", "Country").objects.filter(iso_code="NZ").first() or _model(
        "catalogue", "Country"
    ).objects.create(
        iso_code="NZ",
        name="New Zealand",
        default_currency="NZD",
        default_timezone=ZONE,
        **BOUNDS,
    )
    market = _model("catalogue", "Market").objects.filter(slug="far-north").first() or _model(
        "catalogue", "Market"
    ).objects.create(country=country, name="Far North", slug="far-north", is_active=True)
    region = _model("catalogue", "Region").objects.filter(slug="northland").first() or _model(
        "catalogue", "Region"
    ).objects.create(country=country, market=market, name="Northland", slug="northland")
    from django.contrib.gis.geos import Point

    return _model("catalogue", "Destination").objects.create(
        region=region,
        name="Bay of Islands",
        slug=_unique("bay-of-islands"),
        centroid=Point(174.05, -35.28, srid=4326),
        timezone=ZONE,
        default_currency="NZD",
        is_active=True,
    )


def build(
    *,
    capacity: int = 12,
    capacity_sold: int = 0,
    adults: int = 2,
    children: int = 0,
    days_ahead: int = 30,
    price_per_person: str = "95.00",
    validation_state: str = "VALID",
    generated: bool = True,
    departure_status: str = "OPEN",
    with_activity: bool = True,
    tourist_id: int | None = None,
) -> Scenario:
    """One trip, one activity, one departure, wired together.

    `tourist_id` lets a caller that has already signed somebody in own the
    trip — the HTTP tests in `tests/test_quote_api.py` need a principal that
    can actually authenticate, and a user built here cannot: it has no
    password and no verified address, which is right for a service test and
    useless for a request.
    """
    from django.contrib.gis.geos import Point

    destination = _destination()
    if tourist_id is None:
        user = _model("identity", "User").objects.create(
            email=f"{_unique('tourist')}@example.test", password="!unusable"
        )
        tourist_id = int(
            _model("identity", "TouristProfile")
            .objects.create(user=user, first_name="Ada", last_name="Lovelace")
            .id
        )

    activity = _model("catalogue", "Activity").objects.create(
        destination=destination,
        name="Harbour Kayak Tour",
        slug=_unique("harbour-kayak-tour"),
        coordinates=Point(174.08, -35.26, srid=4326),
        duration_minutes=180,
        price_per_person=Decimal(price_per_person),
        currency="NZD",
        min_pax=1,
        max_pax=12,
        booking_cutoff_hours=24,
    )

    departs_at = (timezone.now() + timedelta(days=days_ahead)).replace(
        hour=8, minute=30, second=0, microsecond=0
    )
    departure = _model("inventory", "ActivityDeparture").objects.create(
        activity_id=activity.id,
        departs_at=departs_at,
        capacity_total=capacity,
        capacity_sold=capacity_sold,
        status=departure_status,
    )

    start = departs_at.date() - timedelta(days=1)
    trip = _model("trip", "Trip").objects.create(
        reference=_unique("TRP-2027")[:20],
        tourist_id=tourist_id,
        destination_id=destination.id,
        start_date=start,
        end_date=start + timedelta(days=4),
        adults=adults,
        children=children,
        currency="NZD",
    )
    itinerary = _model("trip", "Itinerary").objects.create(
        trip=trip,
        validation_state=validation_state,
        generated_at=timezone.now() if generated else None,
    )

    item = None
    if with_activity:
        item = _model("trip", "ItineraryItem").objects.create(
            itinerary=itinerary,
            item_type="ACTIVITY",
            day_number=2,
            sequence_no=1,
            title="Harbour Kayak Tour",
            # The instant the tourist chose, which is the departure's own.
            starts_at=departs_at,
            ends_at=departs_at + timedelta(minutes=180),
            activity_id=activity.id,
        )

    return Scenario(
        trip_public_id=trip.public_id,
        trip_id=int(trip.id),
        tourist_id=int(tourist_id),
        activity_id=int(activity.id),
        departure_id=int(departure.id),
        departs_at=departs_at,
        item_public_id=item.public_id if item is not None else None,
    )


def add_stay(scenario: Scenario) -> None:
    """A stay anchor on the same trip — ADR 0013: no price, no hold."""
    from django.contrib.gis.geos import Point

    accommodation = _model("catalogue", "Accommodation").objects.create(
        destination_id=_model("trip", "Trip").objects.get(id=scenario.trip_id).destination_id,
        name="Harbour Lodge",
        slug=_unique("harbour-lodge"),
        property_type="LODGE",
        coordinates=Point(174.07, -35.27, srid=4326),
        address_line="1 Marsden Road",
        check_in_time=time(14, 0),
        check_out_time=time(10, 0),
    )
    trip = _model("trip", "Trip").objects.get(id=scenario.trip_id)
    _model("trip", "ItineraryItem").objects.create(
        itinerary=trip.itinerary,
        item_type="STAY",
        day_number=1,
        sequence_no=1,
        title="Harbour Lodge",
        starts_at=datetime.combine(trip.start_date, time(14, 0), tzinfo=UTC),
        ends_at=datetime.combine(trip.end_date, time(10, 0), tzinfo=UTC),
        accommodation_id=accommodation.id,
    )
