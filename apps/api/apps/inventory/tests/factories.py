"""Departures and their catalogue parents, for inventory's own tests.

`catalogue_rows` builds the parent rows through `apps.get_model`, because the
`private-catalogue` contract is not relaxed for tests. This adds the departure
on top, and a party-rules override, so that a test about capacity does not have
to restate six catalogue columns to change one.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from django.apps import apps as django_apps
from django.utils import timezone

from apps.inventory.models import ActivityDeparture, DepartureStatus
from apps.inventory.tests.catalogue_rows import make_activity_id

__all__ = ["make_departure", "set_activity_rules"]


def make_departure(
    *,
    activity_id: int | None = None,
    departs_at: dt.datetime | None = None,
    capacity_total: int = 12,
    capacity_held: int = 0,
    capacity_sold: int = 0,
    status: str = DepartureStatus.OPEN,
    price_override: Any = None,
    min_pax: int = 1,
) -> ActivityDeparture:
    """One sellable instant, a week out so no test trips over a cut-off.

    `min_pax` is stated rather than inherited. `catalogue_rows` builds an
    activity with `min_pax=2`, which is a perfectly good number for a kayak
    tour and a terrible one to inherit silently: a capacity test holding one
    seat would be refused PARTY_TOO_SMALL and read as a capacity failure. A
    test that inherits a precondition instead of stating one is invisible
    exactly while the inherited value happens to suit it.

    Only applied when this call also creates the activity — passing both
    `activity_id` and `min_pax` would edit a row the caller shares with
    another departure.
    """
    if activity_id is None:
        activity = make_activity_id()
        activity_id = activity.pk
        set_activity_rules(activity_id, min_pax=min_pax)
    return ActivityDeparture.objects.create(
        activity_id=activity_id,
        departs_at=departs_at or (timezone.now() + dt.timedelta(days=7)),
        capacity_total=capacity_total,
        capacity_held=capacity_held,
        capacity_sold=capacity_sold,
        status=status,
        price_override=price_override,
    )


def set_activity_rules(
    activity_id: int,
    *,
    min_pax: int | None = None,
    max_pax: int | None = None,
    booking_cutoff_hours: int | None = None,
) -> None:
    """Change the party rules a departure inherits from its activity.

    Written through `apps.get_model` and `update()` rather than the catalogue's
    own serializer: these tests are about capacity, and routing them through
    the admin write path would make every one of them depend on it.
    """
    changes = {
        key: value
        for key, value in {
            "min_pax": min_pax,
            "max_pax": max_pax,
            "booking_cutoff_hours": booking_cutoff_hours,
        }.items()
        if value is not None
    }
    django_apps.get_model("catalogue", "Activity").objects.filter(id=activity_id).update(**changes)
