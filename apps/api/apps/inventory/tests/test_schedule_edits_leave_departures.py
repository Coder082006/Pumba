"""Editing a §16.2 rule cannot reach a departure — the assumption BR-023 rests on.

§16.2 separates the recurring rule from the sellable instant. The §27.8 console
edits the first; `activity_departure` is the second, and it carries its own
counters. Lowering a schedule's `capacity`, dropping a weekday, deactivating it
or retiring it all change what the nightly materialiser generates *next* — and
none of them can touch a departure with seats already held or sold.

That is why the console's schedule form has no BR-023 check and why the
departure calendar will need one: there is nothing on the rule to oversell. The
whole split rests on it, so it is asserted rather than argued.

**Why this file is in `inventory` and not beside the other console tests.**
Asserting it needs to read an `activity_departure`, and `private-inventory`
forbids `apps.catalogue` from importing one. Inventory is the side of that
boundary allowed to see both — and it needs no catalogue import at all here,
because it reaches the console the way any client does: over HTTP.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.common.authz import Role
from apps.inventory.models import ActivityDeparture
from apps.inventory.tests.catalogue_rows import make_activity_id
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

CONSOLE = "/api/v1/admin/activity-schedules"


@pytest.fixture
def admin() -> APIClient:
    return signed_in_as(Role.CATALOGUE_ADMIN)


@pytest.fixture
def activity() -> Any:
    return make_activity_id()


@pytest.fixture
def schedule(admin: APIClient, activity: Any) -> dict[str, Any]:
    response = admin.post(
        CONSOLE,
        {
            "activity": str(activity.public_id),
            "days": ["mon", "wed", "fri"],
            "start_time": "08:30:00",
            "capacity": 12,
            "valid_from": "2027-01-01",
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    return dict(response.json()["data"])


@pytest.fixture
def sold(activity: Any) -> ActivityDeparture:
    """A departure that is half sold and part held — the row nothing may move.

    Both counters are non-zero deliberately. A schedule edit that reset one and
    not the other would still be a defect, and a fixture with `capacity_held`
    at zero could not see it.
    """
    return ActivityDeparture.objects.create(
        activity_id=activity.id,
        departs_at=datetime(2027, 3, 1, 5, 30, tzinfo=UTC),
        capacity_total=12,
        capacity_held=2,
        capacity_sold=8,
    )


def _counters(departure: ActivityDeparture) -> tuple[int, int, int, str]:
    departure.refresh_from_db()
    return (
        departure.capacity_total,
        departure.capacity_held,
        departure.capacity_sold,
        departure.status,
    )


UNTOUCHED = (12, 2, 8, "OPEN")


class TestARuleEditLeavesSoldSeatsAlone:
    def test_lowering_the_capacity_below_what_is_sold(
        self, admin: APIClient, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        """Two below the eight already sold, and it is still allowed.

        This is the case that looks like it should be refused and must not be.
        The rule says what tomorrow's boat holds; the departure says what
        Monday's boat holds and who is on it. Refusing this edit would stop a
        provider shrinking next season's capacity because somebody booked this
        season — which is BR-023 applied to the wrong row.
        """
        response = admin.patch(f"{CONSOLE}/{schedule['public_id']}", {"capacity": 2}, format="json")
        assert response.status_code == 200
        assert _counters(sold) == UNTOUCHED

    def test_dropping_the_weekday_the_departure_falls_on(
        self, admin: APIClient, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        """2027-03-01 is a Monday, and the rule stops running on Mondays."""
        assert sold.departs_at.weekday() == 0
        response = admin.patch(
            f"{CONSOLE}/{schedule['public_id']}", {"days": ["fri"]}, format="json"
        )
        assert response.status_code == 200
        assert _counters(sold) == UNTOUCHED

    def test_ending_the_validity_window_before_it(
        self, admin: APIClient, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        response = admin.patch(
            f"{CONSOLE}/{schedule['public_id']}", {"valid_to": "2027-01-31"}, format="json"
        )
        assert response.status_code == 200
        assert _counters(sold) == UNTOUCHED

    def test_deactivating_the_rule(
        self, admin: APIClient, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        response = admin.patch(
            f"{CONSOLE}/{schedule['public_id']}", {"is_active": False}, format="json"
        )
        assert response.status_code == 200
        assert _counters(sold) == UNTOUCHED

    def test_retiring_the_rule(
        self, admin: APIClient, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        """§7.7's soft delete. The departure is not a cascade target.

        `activity_departure.schedule_id` is a plain `BigIntegerField` with no
        foreign key (ADR 0012), so there is no database-level cascade to get
        this wrong — but nothing stops a future service from doing by hand what
        the schema declines to do for it.
        """
        assert admin.delete(f"{CONSOLE}/{schedule['public_id']}").status_code == 204
        assert _counters(sold) == UNTOUCHED
        assert ActivityDeparture.objects.filter(id=sold.id).exists()


class TestTheRuleStillGovernsWhatComesNext:
    """The other half, without which the tests above would pass on a no-op.

    A console whose edits did nothing at all would satisfy every assertion in
    the class above. What makes those meaningful is that the same edit *does*
    reach the departures that have not been generated yet.
    """

    def test_a_lowered_capacity_reaches_the_next_materialisation(
        self, admin: APIClient, activity: Any, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        from apps.inventory.services import materialise_departures

        admin.patch(f"{CONSOLE}/{schedule['public_id']}", {"capacity": 4}, format="json")
        materialise_departures(start=sold.departs_at.date(), horizon_days=30)

        generated = ActivityDeparture.objects.filter(activity_id=activity.id).exclude(id=sold.id)
        assert generated.exists()
        assert {row.capacity_total for row in generated} == {4}
        assert _counters(sold) == UNTOUCHED

    def test_a_retired_rule_generates_nothing_further(
        self, admin: APIClient, activity: Any, schedule: dict[str, Any], sold: ActivityDeparture
    ) -> None:
        from apps.inventory.services import materialise_departures

        admin.delete(f"{CONSOLE}/{schedule['public_id']}")
        materialise_departures(start=sold.departs_at.date(), horizon_days=30)

        assert (
            not ActivityDeparture.objects.filter(activity_id=activity.id)
            .exclude(id=sold.id)
            .exists()
        )
        assert _counters(sold) == UNTOUCHED
