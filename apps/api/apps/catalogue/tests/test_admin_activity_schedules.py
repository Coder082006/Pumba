"""The §16.2 schedule, as the §27.8 console edits it.

`tests/test_catalogue_admin_api.py` already proves the generic properties over
every entity in the registry: only an administrator reaches the surface, an
unreachable row is a 404, an unknown field is a 422, and every write leaves an
audit entry. This file asserts the four things that are true of a *schedule*
and of nothing else in that registry.

**The wire says days and the column says a bitmask.** They are the same fact in
two shapes, and the conversion is `domain.schedules`, shared with the seed
loader. A console that accepted `["mon"]` and rendered `1` could not round-trip
through itself, and the second edit of any schedule would be made against a
number the administrator had to decode by hand.

The one property of a schedule that is *not* asserted here is that editing one
cannot touch a departure — §16.2's separation, and the assumption BR-023 rests
on. It lives in `apps/inventory/tests/test_schedule_edits_leave_departures.py`,
because asserting it needs an `activity_departure` row and `private-inventory`
forbids this module from importing one. Inventory is the side of that boundary
allowed to see both, and it reaches this console the way any client does: over
HTTP.

**A retired schedule stops generating departures.** §7.7's soft delete is the
mechanism, and `active_schedules`' filter is what gives it meaning. A console
that retired the row and left the boat running would be worse than one with no
delete at all.

**A rule that cannot run is refused at the boundary.** Three layers reject a
zero mask — this serializer, `domain.schedules`, and a CHECK constraint — and
only the shallowest can name the field. A zero mask is a schedule that looks
filled in and generates nothing forever, which surfaces as an empty calendar
six weeks later with nothing to point at.
"""

from __future__ import annotations

from datetime import date, time
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.catalogue.models import Activity, ActivitySchedule
from apps.catalogue.tests.factories import make_activity
from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

CREATE = "/api/v1/admin/activity-schedules"


@pytest.fixture
def admin() -> APIClient:
    return signed_in_as(Role.CATALOGUE_ADMIN)


@pytest.fixture
def activity() -> Activity:
    return make_activity()


def _body(activity: Activity, **over: Any) -> dict[str, Any]:
    return {
        "activity": str(activity.public_id),
        "days": ["mon", "wed", "fri"],
        "start_time": "08:30:00",
        "capacity": 12,
        "valid_from": "2027-01-01",
        **over,
    }


def _create(admin: APIClient, activity: Activity, **over: Any) -> Any:
    return admin.post(CREATE, _body(activity, **over), format="json")


class TestTheWireSaysDays:
    def test_a_schedule_is_created_from_day_names(
        self, admin: APIClient, activity: Activity
    ) -> None:
        response = _create(admin, activity)
        assert response.status_code == 201, response.json()
        assert response.json()["data"]["days"] == ["mon", "wed", "fri"]

    def test_the_column_holds_the_mask_those_names_mean(
        self, admin: APIClient, activity: Activity
    ) -> None:
        """Bit 0 is Monday. mon|wed|fri is 1 + 4 + 16."""
        _create(admin, activity)
        assert ActivitySchedule.objects.get().weekday_mask == 0b0010101

    def test_days_round_trip_through_an_amendment(
        self, admin: APIClient, activity: Activity
    ) -> None:
        """The property a one-way conversion would break.

        An administrator who reads back `["sat", "sun"]` and PATCHes it back
        unchanged must not alter the row — which is only true if the render and
        the parse agree about every bit.
        """
        created = _create(admin, activity, days=["sat", "sun"]).json()["data"]
        amended = admin.patch(
            f"{CREATE}/{created['public_id']}", {"days": created["days"]}, format="json"
        )
        assert amended.status_code == 200
        assert amended.json()["data"]["days"] == ["sat", "sun"]
        assert ActivitySchedule.objects.get().weekday_mask == 0b1100000

    def test_the_mask_itself_is_not_accepted(self, admin: APIClient, activity: Activity) -> None:
        """§30.6's closed shape, and why it matters on this form in particular.

        Two spellings of one field is how a console form and a seed file come
        to mean different days. The column name is refused by name.
        """
        response = admin.post(CREATE, _body(activity) | {"weekday_mask": 5}, format="json")
        assert response.status_code == 422
        assert "weekday_mask" in str(response.json())


class TestARuleThatCannotRun:
    def test_an_unknown_day_is_refused_and_named(
        self, admin: APIClient, activity: Activity
    ) -> None:
        response = _create(admin, activity, days=["mon", "funday"])
        assert response.status_code == 422
        assert "funday" in str(response.json())

    def test_no_days_at_all_is_refused(self, admin: APIClient, activity: Activity) -> None:
        assert _create(admin, activity, days=[]).status_code == 422

    def test_a_window_that_ends_before_it_begins_is_refused(
        self, admin: APIClient, activity: Activity
    ) -> None:
        response = _create(admin, activity, valid_from="2027-06-01", valid_to="2027-01-01")
        assert response.status_code == 422
        assert "valid_to" in str(response.json())

    def test_a_capacity_of_zero_is_refused(self, admin: APIClient, activity: Activity) -> None:
        """A rule that would generate departures nobody can board."""
        assert _create(admin, activity, capacity=0).status_code == 422

    def test_a_create_missing_the_time_is_refused(
        self, admin: APIClient, activity: Activity
    ) -> None:
        """`partial` distinguishes POST from PATCH, so this is worth pinning.

        Every field but `valid_to` and `is_active` is required on a create; the
        same serializer accepts any subset on an amendment.
        """
        body = _body(activity)
        del body["start_time"]
        response = admin.post(CREATE, body, format="json")
        assert response.status_code == 422
        assert "start_time" in str(response.json())


class TestARetiredRuleStopsGenerating:
    def test_the_materialiser_stops_seeing_it(self, admin: APIClient, activity: Activity) -> None:
        """The half of a soft delete that gives it meaning.

        `active_schedules` is what the nightly job reads. A row the console
        retired that kept appearing there would leave the boat running with
        nothing in the console to explain why.
        """
        from apps.catalogue.services import active_schedules

        created = _create(admin, activity).json()["data"]
        assert self._generates(activity)

        assert admin.delete(f"{CREATE}/{created['public_id']}").status_code == 204
        assert not self._generates(activity)

        assert admin.post(f"{CREATE}/{created['public_id']}/restore").status_code == 204
        assert self._generates(activity)

        assert active_schedules  # the import is the subject; keep it honest

    @staticmethod
    def _generates(activity: Activity) -> bool:
        from apps.catalogue.services import active_schedules

        return any(
            facts.activity_id == activity.id
            for facts in active_schedules(start=date(2027, 1, 1), horizon_days=30)
        )

    def test_deactivating_without_retiring_also_stops_it(
        self, admin: APIClient, activity: Activity
    ) -> None:
        """Two ways to stop a rule, and §16.2 wants both.

        `is_active` is the seasonal switch — a boat that does not run in the
        monsoon — and the soft delete is the rule that was wrong. Only one of
        them releases the row; both must stop generation.
        """
        created = _create(admin, activity).json()["data"]
        assert (
            admin.patch(
                f"{CREATE}/{created['public_id']}", {"is_active": False}, format="json"
            ).status_code
            == 200
        )
        assert not self._generates(activity)


class TestWhatTheConsoleGetsBack:
    def test_the_parent_is_a_uuid_not_a_row_id(self, admin: APIClient, activity: Activity) -> None:
        """§7.2. The identifier the console used to create it comes back.

        Asserted over the fields that carry identity, not by scanning the
        payload for the row id. Both of the obvious scans are unsound here: a
        small integer is a substring of most UUIDs, and `capacity` is an
        integer that can equal a row id by coincidence. Either one fails, or
        passes, for reasons unrelated to the claim.

        The general property — that no response on this surface contains a
        sequential integer — is asserted once over every entity in
        `tests/test_catalogue_admin_api.py`.
        """
        data = _create(admin, activity).json()["data"]
        assert data["activity"] == str(activity.public_id)
        assert data["public_id"] == str(ActivitySchedule.objects.get().public_id)
        assert "activity_id" not in data

    def test_an_open_ended_schedule_is_accepted(self, admin: APIClient, activity: Activity) -> None:
        """A year-round tour has no end date, and requiring one invents one."""
        data = _create(admin, activity).json()["data"]
        assert data["valid_to"] is None
        assert ActivitySchedule.objects.get().start_time == time(8, 30)
