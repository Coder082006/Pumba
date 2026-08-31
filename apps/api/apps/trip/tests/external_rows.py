"""Rows owned by other modules, built without importing them.

`trip` may depend on `catalogue` (§6.4) but not on `identity`, and contract
`private-catalogue` forbids `apps.catalogue.models` to every module in either
direction. Neither ban is relaxed for tests: a test that reaches through a
boundary stops the linter noticing when production code does the same, because
the chain is already permitted.

`apps.get_model` is Django's own answer to "I need a model I must not import" —
it is what every migration uses, for the same reason. Nothing here exercises
another module's *behaviour*; it inserts the parent rows the foreign keys in
`trip/0001_initial` require and hands back the ids `trip` stores.

The market is a made-up one in a zone unlike the seed market's, matching
`apps.catalogue.tests.factories` and `apps.inventory.tests.catalogue_rows`, so
nothing here passes by being in East Africa.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.contrib.gis.geos import Point

__all__ = [
    "ZONE",
    "make_tourist_id",
    "make_destination",
    "make_accommodation",
    "make_accommodation_id",
    "make_attraction",
    "make_attraction_id",
    "make_activity",
    "make_activity_departure_id",
]

ZONE = "Pacific/Auckland"

#: New Zealand's bounding box. NOT NULL on `country` since `catalogue/0007`,
#: because a country with no bounds silently disables the transposed-coordinate
#: guard for every row beneath it. Restated rather than imported, for the same
#: reason as everything else in this file.
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


def _catalogue(name: str) -> Any:
    return apps.get_model("catalogue", name)


def _identity(name: str) -> Any:
    return apps.get_model("identity", name)


def _inventory(name: str) -> Any:
    return apps.get_model("inventory", name)


def make_tourist_id() -> int:
    """A `tourist_profile.id`, which is what `trip.tourist_id` stores."""
    user = _identity("User").objects.create(
        email=f"{_unique('tourist')}@example.test",
        password="!unusable",
    )
    profile = _identity("TouristProfile").objects.create(
        user=user, first_name="Ada", last_name="Lovelace"
    )
    return int(profile.id)


def make_destination(*, is_gateway: bool = False, longitude: float = 174.05) -> Any:
    country = _catalogue("Country").objects.filter(iso_code="NZ").first() or _catalogue(
        "Country"
    ).objects.create(
        iso_code="NZ",
        name="New Zealand",
        default_currency="NZD",
        default_timezone=ZONE,
        **BOUNDS,
    )
    market = _catalogue("Market").objects.filter(country=country).first() or _catalogue(
        "Market"
    ).objects.create(country=country, name="Far North", slug="far-north", is_active=True)
    region = _catalogue("Region").objects.filter(market=market).first() or _catalogue(
        "Region"
    ).objects.create(country=country, market=market, name="Northland", slug="northland")

    gateway = (
        {"is_gateway": True, "gateway_type": "AIRPORT", "gateway_code": _unique("AP")[:10]}
        if is_gateway
        else {}
    )
    return _catalogue("Destination").objects.create(
        region=region,
        name="Bay of Islands",
        slug=_unique("bay-of-islands"),
        centroid=Point(longitude, -35.28, srid=4326),
        timezone=ZONE,
        default_currency="NZD",
        is_active=True,
        **gateway,
    )


def make_accommodation(destination: Any | None = None) -> Any:
    return _catalogue("Accommodation").objects.create(
        destination=destination or make_destination(),
        name="Harbourside Lodge",
        slug=_unique("harbourside-lodge"),
        property_type="HOTEL",
        coordinates=Point(174.06, -35.27, srid=4326),
        address_line="1 Marsden Road",
    )


def make_attraction(destination: Any | None = None) -> Any:
    return _catalogue("Attraction").objects.create(
        destination=destination or make_destination(),
        name="Stone Store",
        slug=_unique("stone-store"),
        coordinates=Point(173.98, -35.22, srid=4326),
        visit_minutes=60,
    )


#: The row's integer id, for a test that writes `itinerary_item` directly.
#:
#: Legitimate there and only there: ADR 0012 stores the reference as a plain
#: integer, so a model-level test has to supply one. Anything going through
#: `services.add_item` names the row instead — the integer never leaves the
#: database (§7.2), so a service that accepted one could not be called by a
#: client.
def make_accommodation_id(destination: Any | None = None) -> int:
    return int(make_accommodation(destination).id)


def make_attraction_id(destination: Any | None = None) -> int:
    return int(make_attraction(destination).id)


def make_activity(destination: Any | None = None) -> Any:
    return _catalogue("Activity").objects.create(
        destination=destination or make_destination(),
        name="Harbour Kayak Tour",
        slug=_unique("harbour-kayak-tour"),
        coordinates=Point(174.08, -35.26, srid=4326),
        duration_minutes=180,
        price_per_person=Decimal("95.00"),
        currency="NZD",
        min_pax=2,
        max_pax=12,
    )


def make_activity_departure_id(activity: Any | None = None) -> int:
    """`inventory` owns the departure — §7.5.9, ADR 0011."""
    subject = activity or make_activity()
    schedule = _catalogue("ActivitySchedule").objects.create(
        activity=subject,
        weekday_mask=0b0111111,
        start_time=time(8, 30),
        capacity=12,
        valid_from=date(2027, 1, 1),
        valid_to=date(2027, 12, 31),
    )
    return int(
        _inventory("ActivityDeparture")
        .objects.create(
            activity_id=subject.id,
            schedule_id=schedule.id,
            departs_at=datetime(2027, 6, 1, 8, 30, tzinfo=UTC),
            capacity_total=12,
        )
        .id
    )
