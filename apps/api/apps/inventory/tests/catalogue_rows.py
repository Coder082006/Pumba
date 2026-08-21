"""Catalogue rows for inventory's tests, built without importing catalogue.

Contract `private-catalogue` forbids `apps.inventory` from importing
`apps.catalogue.models`, and the ban is not relaxed for tests: a test that
reaches through a boundary is a test that stops the linter noticing when
production code does the same, because the chain is already permitted.

`apps.get_model` is Django's own answer to "I need a model I must not import" -
it is what every migration uses, for the same reason. Nothing here touches a
catalogue *behaviour*; it inserts the parent rows the foreign keys in
`0001_availability_tables` require and returns their ids.

The market is a made-up one in a zone unlike the seed market's, matching
`apps.catalogue.tests.factories`, so nothing passes here by being in East
Africa.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal
from typing import Any

from django.apps import apps
from django.contrib.gis.geos import Point

__all__ = ["make_activity_id", "make_activity_schedule_id"]

ZONE = "Pacific/Auckland"


def _model(name: str) -> Any:
    return apps.get_model("catalogue", name)


def _destination() -> Any:
    country = _model("Country").objects.create(
        iso_code="NZ", name="New Zealand", default_currency="NZD", default_timezone=ZONE
    )
    region = _model("Region").objects.create(country=country, name="Northland", slug="northland")
    return _model("Destination").objects.create(
        region=region,
        name="Bay of Islands",
        slug="bay-of-islands",
        centroid=Point(174.05, -35.28, srid=4326),
        timezone=ZONE,
        default_currency="NZD",
        is_active=True,
    )


def make_activity_id(*, slug: str = "harbour-kayak-tour") -> Any:
    return _model("Activity").objects.create(
        destination=_destination(),
        name="Harbour Kayak Tour",
        slug=slug,
        coordinates=Point(174.08, -35.26, srid=4326),
        duration_minutes=180,
        price_per_person=Decimal("95.00"),
        currency="NZD",
        min_pax=2,
        max_pax=12,
    )


def make_activity_schedule_id(activity: Any) -> Any:
    return _model("ActivitySchedule").objects.create(
        activity=activity,
        weekday_mask=0b0111111,
        start_time=time(8, 30),
        capacity=12,
        valid_from=date(2027, 1, 1),
        valid_to=date(2027, 12, 31),
    )
