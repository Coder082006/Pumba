"""Row builders for catalogue tests.

Deliberately plain functions rather than a factory library: the catalogue's
interesting cases are about *which* flags are set, and a builder that fills
every field with plausible noise makes the one field a test cares about harder
to see, not easier.

Nothing here is Zanzibar-shaped. The default market is a made-up country in a
zone unlike the seed market's, so a test that passes only because it happens to
sit in East Africa fails here.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal
from typing import Any

from django.contrib.gis.geos import Point

from apps.catalogue.models import (
    Accommodation,
    Attraction,
    CancellationPolicy,
    Country,
    Destination,
    PropertyType,
    Region,
    RoomType,
    Tag,
)

__all__ = [
    "make_country",
    "make_region",
    "make_destination",
    "make_tag",
    "make_attraction",
    "make_cancellation_policy",
    "make_accommodation",
    "make_room_type",
]

#: Two zones that disagree with each other and with UTC for most of the day.
DEFAULT_ZONE = "Pacific/Auckland"
OTHER_ZONE = "America/Santiago"


def make_country(**overrides: Any) -> Country:
    values: dict[str, Any] = {
        "iso_code": "NZ",
        "name": "New Zealand",
        "default_currency": "NZD",
        "default_timezone": DEFAULT_ZONE,
    }
    values.update(overrides)
    return Country.objects.create(**values)


def make_region(country: Country | None = None, **overrides: Any) -> Region:
    values: dict[str, Any] = {
        "country": country or make_country(),
        "name": "Northland",
        "slug": "northland",
    }
    values.update(overrides)
    return Region.objects.create(**values)


def make_destination(region: Region | None = None, **overrides: Any) -> Destination:
    values: dict[str, Any] = {
        "region": region or make_region(),
        "name": "Bay of Islands",
        "slug": "bay-of-islands",
        "centroid": Point(174.05, -35.28, srid=4326),
        "timezone": DEFAULT_ZONE,
        "default_currency": "NZD",
        "is_active": True,
    }
    values.update(overrides)
    return Destination.objects.create(**values)


def make_tag(**overrides: Any) -> Tag:
    values: dict[str, Any] = {"slug": "coastal", "label": "Coastal", "sort_order": 10}
    values.update(overrides)
    return Tag.objects.create(**values)


def make_attraction(destination: Destination | None = None, **overrides: Any) -> Attraction:
    values: dict[str, Any] = {
        "destination": destination or make_destination(),
        "name": "The Lighthouse",
        "slug": "the-lighthouse",
        "coordinates": Point(174.06, -35.29, srid=4326),
        "entrance_fee": Decimal("12.00"),
        "fee_currency": "NZD",
        "visit_minutes": 90,
    }
    values.update(overrides)
    return Attraction.objects.create(**values)


def make_cancellation_policy(**overrides: Any) -> CancellationPolicy:
    values: dict[str, Any] = {
        "code": "MODERATE_7D",
        "name": "Moderate",
        # §14.6, as data: full refund beyond 7 days, half between 7 days and
        # 48 hours, nothing thereafter.
        "tiers": [
            {"hours_before": 168, "refund_percent": 100},
            {"hours_before": 48, "refund_percent": 50},
        ],
    }
    values.update(overrides)
    return CancellationPolicy.objects.create(**values)


def make_accommodation(destination: Destination | None = None, **overrides: Any) -> Accommodation:
    values: dict[str, Any] = {
        "destination": destination or make_destination(),
        "name": "The Harbour Lodge",
        "slug": "the-harbour-lodge",
        "property_type": PropertyType.LODGE,
        "coordinates": Point(174.07, -35.27, srid=4326),
        "address_line": "1 Quay Street",
        "star_rating": 4,
        "check_in_time": time(14, 0),
        "check_out_time": time(10, 0),
    }
    values.update(overrides)
    return Accommodation.objects.create(**values)


def make_room_type(accommodation: Accommodation | None = None, **overrides: Any) -> RoomType:
    values: dict[str, Any] = {
        "accommodation": accommodation or make_accommodation(),
        "name": "Harbour View Double",
        "max_adults": 2,
        "max_children": 1,
        "bed_configuration": "1 queen",
        "base_rate": Decimal("180.00"),
        "currency": "NZD",
        "total_rooms": 8,
    }
    values.update(overrides)
    return RoomType.objects.create(**values)
