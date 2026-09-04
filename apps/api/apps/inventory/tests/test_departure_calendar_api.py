"""§26.5's availability calendar, over HTTP — BR-023 in particular.

    §26.5: "A month-grid calendar per room type or activity showing, per date,
    availability, held, sold and rate. Bulk editing supports a date range, a
    weekday mask, and set-availability / set-rate / open / close operations in
    one submission. A conflict (attempting to reduce availability below what is
    already sold or held) is rejected with the specific dates named."

    BR-023: "A provider may not reduce availability below what is already held
    or sold."

**Administrator-owned, and that is a deviation §26.4 should be read against.**
An activity's capacity is a claim its operator makes, and the operator cannot
make it yet: `apps/provider/` has no table and `activity.provider_id` is a
column nothing writes, so a `/provider/...` route has no principal and its
authorisation test cannot be written — there is no second provider to be
foreign. ADR 0022 records that. What is asserted here is the half that does not
depend on which principal holds the role: the rule, the refusal, and the dates
in it. `Resource.ACTIVITY_DEPARTURE` already scopes providers by
`activity__provider_id`, so Phase 11 adds a route and changes none of this.

**The BR-023 check is under the lock, and the concurrency test is what proves
it.** A reduction validated against an unlocked read is validated against a
number another transaction is in the middle of changing, which is §17.1 I2's
whole subject. `TestAQuoteInFlight` runs a real parallel transaction against a
real barrier for that reason: without `select_for_update` in
`lock_departures_between` the check reads the pre-hold counters and the
reduction is allowed through.
"""

from __future__ import annotations

import datetime as dt
import threading
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from django.db import connections, transaction
from rest_framework.test import APIClient

from apps.common.authz import Role
from apps.inventory.models import ActivityDeparture, DepartureStatus
from apps.inventory.tests.catalogue_rows import make_activity_id
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

#: `catalogue_rows` builds its destination in Pacific/Auckland, which is UTC+12
#: or +13. Every window below is stated in local dates for that reason: an
#: 20:00Z instant is the *next* day there, and a calendar that resolved the
#: window in UTC would silently include or drop a date at either end.
ZONE = "Pacific/Auckland"


def _url(activity: Any) -> str:
    return f"/api/v1/admin/activities/{activity.public_id}/departures"


@pytest.fixture
def admin() -> APIClient:
    return signed_in_as(Role.CATALOGUE_ADMIN)


@pytest.fixture
def activity() -> Any:
    return make_activity_id()


def _departure(activity: Any, *, day: int, hour: int = 8, **over: Any) -> ActivityDeparture:
    """A departure at 08:00 **local** on the given day of March 2027.

    Built from the local wall time rather than from a UTC instant, because that
    is what a departure is (§16.2) and because in March in Pacific/Auckland it
    lands on the *previous* UTC day — which is exactly the off-by-one a
    UTC-resolved window or weekday mask would produce. These fixtures are the
    ones that catch it, and they only catch it if they are built this way
    round.
    """
    fields: dict[str, Any] = {
        "activity_id": activity.id,
        "departs_at": dt.datetime(2027, 3, day, hour, 0, tzinfo=ZoneInfo(ZONE)),
        "capacity_total": 12,
        "capacity_held": 0,
        "capacity_sold": 0,
    }
    fields.update(over)
    return ActivityDeparture.objects.create(**fields)


def _edit(admin: APIClient, activity: Any, **body: Any) -> Any:
    payload = {"from": "2027-03-01", "to": "2027-03-31", **body}
    return admin.put(_url(activity), payload, format="json")


class TestTheCalendarShowsWhatAnOperatorDecidesOn:
    def test_it_publishes_all_three_counters(self, admin: APIClient, activity: Any) -> None:
        """The split the public calendar deliberately withholds.

        Eight taken seats of which four are holds is a boat being booked; eight
        of which four are sales is a boat half full with four people who may
        yet not pay. An operator deciding whether to cancel needs to tell those
        apart, and `remaining` alone cannot.
        """
        _departure(activity, day=2, capacity_held=3, capacity_sold=5)
        response = admin.get(f"{_url(activity)}?from=2027-03-01&to=2027-03-31")
        assert response.status_code == 200
        row = response.json()["data"][0]
        assert (row["capacity_total"], row["capacity_held"], row["capacity_sold"]) == (12, 3, 5)
        assert row["remaining"] == 4

    def test_it_says_which_zone_the_dates_are_in(self, admin: APIClient, activity: Any) -> None:
        """A grid of dates with no zone is a grid somebody will read locally."""
        _departure(activity, day=2)
        response = admin.get(f"{_url(activity)}?from=2027-03-01&to=2027-03-31")
        assert response.json()["meta"]["timezone"] == ZONE

    def test_the_window_is_resolved_in_the_destinations_zone(
        self, admin: APIClient, activity: Any
    ) -> None:
        """The 1 March departure is the previous day in UTC.

        A window resolved in UTC would exclude it from a March calendar, and
        the operator would edit a month with a hole in it they cannot see.
        """
        _departure(activity, day=1)
        response = admin.get(f"{_url(activity)}?from=2027-03-01&to=2027-03-31")
        assert len(response.json()["data"]) == 1

    def test_a_withdrawn_activity_still_has_a_calendar(
        self, admin: APIClient, activity: Any
    ) -> None:
        """The public route answers 404 here, and must.

        This one must not: a season's departures are published before the
        listing goes live, so a calendar reachable only for visible activities
        could not be filled in before launch — which is the ordinary case, not
        the edge one.
        """
        activity.is_active = False
        activity.save(update_fields=["is_active"])
        _departure(activity, day=2)
        assert admin.get(f"{_url(activity)}?from=2027-03-01&to=2027-03-31").status_code == 200
        assert APIClient().get(f"/api/v1/activities/{activity.slug}/departures").status_code == 404


class TestBR023:
    """*A provider may not reduce availability below what is already held or
    sold.* The rule, the refusal, and the dates in it."""

    def test_a_reduction_below_sold_seats_is_refused(self, admin: APIClient, activity: Any) -> None:
        departure = _departure(activity, day=2, capacity_sold=8)
        response = _edit(admin, activity, capacity_total=4)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "CAPACITY_BELOW_COMMITTED"

        departure.refresh_from_db()
        assert departure.capacity_total == 12

    def test_a_held_seat_blocks_it_just_as_a_sold_one_does(
        self, admin: APIClient, activity: Any
    ) -> None:
        """The half that is easy to drop.

        A hold is a seat a tourist is partway through paying for under a live
        TTL (§17.2). Counting only `capacity_sold` would let an operator shrink
        a boat out from under them between the quote and the payment.
        """
        _departure(activity, day=2, capacity_held=9)
        assert _edit(admin, activity, capacity_total=4).status_code == 409

    def test_the_specific_dates_are_named(self, admin: APIClient, activity: Any) -> None:
        """§26.5 says the dates, plural, and the arithmetic with them.

        An operator told only that "some dates conflict" has to find them by
        hand across a month grid.
        """
        _departure(activity, day=2, capacity_sold=10)
        _departure(activity, day=5)
        _departure(activity, day=9, capacity_held=7)

        response = _edit(admin, activity, capacity_total=6)
        details = response.json()["error"]["details"]

        assert len(details) == 2
        assert [entry["committed"] for entry in details] == [10, 7]
        assert all(entry["requested"] == 6 for entry in details)
        assert [entry["departs_at"][:10] for entry in details] == ["2027-03-01", "2027-03-08"]

    def test_reducing_to_exactly_what_is_committed_is_allowed(
        self, admin: APIClient, activity: Any
    ) -> None:
        """BR-023 says *below*, and the boundary is where a broken boat lands.

        A smaller vessel that takes only the people already booked is the
        commonest legitimate reduction there is.
        """
        departure = _departure(activity, day=2, capacity_sold=8)
        assert _edit(admin, activity, capacity_total=8).status_code == 200
        departure.refresh_from_db()
        assert departure.capacity_total == 8

    def test_a_reduction_to_zero_is_allowed_where_nothing_is_committed(
        self, admin: APIClient, activity: Any
    ) -> None:
        departure = _departure(activity, day=2)
        assert _edit(admin, activity, capacity_total=0).status_code == 200
        departure.refresh_from_db()
        assert departure.capacity_total == 0

    def test_one_bad_date_refuses_the_whole_submission(
        self, admin: APIClient, activity: Any
    ) -> None:
        """All or nothing, because §26.5's submission is one edit.

        A partial application would leave the operator with a month in an
        unknown state and a 409 that does not say which half took.
        """
        clean = _departure(activity, day=2)
        blocked = _departure(activity, day=9, capacity_sold=10)

        assert _edit(admin, activity, capacity_total=4).status_code == 409
        clean.refresh_from_db()
        blocked.refresh_from_db()
        assert (clean.capacity_total, blocked.capacity_total) == (12, 12)

    def test_raising_capacity_is_never_refused(self, admin: APIClient, activity: Any) -> None:
        _departure(activity, day=2, capacity_sold=12)
        assert _edit(admin, activity, capacity_total=20).status_code == 200

    def test_a_cancelled_departure_still_protects_its_passengers(
        self, admin: APIClient, activity: Any
    ) -> None:
        """`sellable` is zero for a cancelled departure; `committed` is not.

        Eight people sold onto a cancelled boat are eight people who need
        telling, and a reduction that treated the date as empty would erase the
        only record of how many.
        """
        _departure(activity, day=2, capacity_sold=8, status=DepartureStatus.CANCELLED)
        assert _edit(admin, activity, capacity_total=2).status_code == 409


class TestTheBulkOperations:
    def test_a_weekday_mask_selects_only_those_days(self, admin: APIClient, activity: Any) -> None:
        """2027-03-01 is a Monday. Local, not UTC — see `_departure`."""
        monday = _departure(activity, day=1)
        tuesday = _departure(activity, day=2)

        assert _edit(admin, activity, days=["mon"], status="CLOSED").status_code == 200

        monday.refresh_from_db()
        tuesday.refresh_from_db()
        assert (monday.status, tuesday.status) == ("CLOSED", "OPEN")

    def test_the_mask_is_evaluated_in_the_destinations_zone(
        self, admin: APIClient, activity: Any
    ) -> None:
        """2027-03-01 08:00 in Auckland is 2027-02-28 in UTC — Sunday, not Monday.

        A mask applied to the UTC date would close a boat the operator did not
        name, on a day they did not choose.
        """
        departure = _departure(activity, day=1)
        assert departure.departs_at.astimezone(dt.UTC).weekday() == 6  # Sunday, in UTC
        assert departure.departs_at.astimezone(ZoneInfo(ZONE)).weekday() == 0  # Monday, there

        assert _edit(admin, activity, days=["mon"], status="CLOSED").status_code == 200
        departure.refresh_from_db()
        assert departure.status == "CLOSED"

    def test_capacity_price_and_status_apply_in_one_submission(
        self, admin: APIClient, activity: Any
    ) -> None:
        """§26.5: *"in one submission"* — not three round trips."""
        departure = _departure(activity, day=2)
        response = _edit(admin, activity, capacity_total=6, price_override="55.00", status="CLOSED")
        assert response.status_code == 200
        assert response.json()["data"]["departures_changed"] == 1

        departure.refresh_from_db()
        assert departure.capacity_total == 6
        assert departure.price_override == Decimal("55.00")
        assert departure.status == "CLOSED"

    def test_an_override_can_be_removed(self, admin: APIClient, activity: Any) -> None:
        """The reason `clear_price` exists.

        `price_override: null` has to mean "leave it alone", or every capacity
        edit would silently drop a special rate — so removing one needs a flag
        of its own, or the field is write-once in a form that looks like it is
        not.
        """
        departure = _departure(activity, day=2, price_override=Decimal("55.00"))
        assert _edit(admin, activity, clear_price=True).status_code == 200
        departure.refresh_from_db()
        assert departure.price_override is None

    def test_an_unrelated_edit_leaves_the_override_alone(
        self, admin: APIClient, activity: Any
    ) -> None:
        departure = _departure(activity, day=2, price_override=Decimal("55.00"))
        assert _edit(admin, activity, capacity_total=6).status_code == 200
        departure.refresh_from_db()
        assert departure.price_override == Decimal("55.00")

    def test_setting_and_clearing_the_price_together_is_refused(
        self, admin: APIClient, activity: Any
    ) -> None:
        """A contradiction, not a precedence rule for somebody to remember."""
        response = _edit(admin, activity, price_override="55.00", clear_price=True)
        assert response.status_code == 422

    def test_a_window_with_no_operation_is_refused(self, admin: APIClient, activity: Any) -> None:
        """Otherwise it locks a month, changes nothing, and answers 200 with a
        count of zero — indistinguishable from a window that matched nothing."""
        assert _edit(admin, activity).status_code == 422

    def test_departures_outside_the_window_are_untouched(
        self, admin: APIClient, activity: Any
    ) -> None:
        inside = _departure(activity, day=2)
        outside = _departure(activity, day=15)
        response = admin.put(
            _url(activity),
            {"from": "2027-03-01", "to": "2027-03-05", "status": "CLOSED"},
            format="json",
        )
        assert response.json()["data"]["departures_changed"] == 1
        inside.refresh_from_db()
        outside.refresh_from_db()
        assert (inside.status, outside.status) == ("CLOSED", "OPEN")

    def test_lowering_capacity_does_not_cancel_the_departure(
        self, admin: APIClient, activity: Any
    ) -> None:
        """Separate operations because they are separate decisions.

        A smaller boat still sails. Folding one into the other would cancel
        departures nobody asked to cancel, on the day capacity was trimmed.
        """
        departure = _departure(activity, day=2, capacity_sold=4)
        _edit(admin, activity, capacity_total=4)
        departure.refresh_from_db()
        assert departure.status == "OPEN"

    def test_cancelling_does_not_release_the_sold_seats(
        self, admin: APIClient, activity: Any
    ) -> None:
        """§14.6's refund path is Phase 8 and is deliberate, not incidental.

        Zeroing `capacity_sold` here would be the quietest possible place for
        somebody's money to stop being tracked.
        """
        departure = _departure(activity, day=2, capacity_sold=8, capacity_held=2)
        _edit(admin, activity, status="CANCELLED")
        departure.refresh_from_db()
        assert (departure.capacity_sold, departure.capacity_held) == (8, 2)

    def test_the_edit_bumps_the_version(self, admin: APIClient, activity: Any) -> None:
        """§7.4. A row whose contents changed without its version moving would
        make the column a lie for the paths that do read it."""
        departure = _departure(activity, day=2)
        before = departure.version
        _edit(admin, activity, capacity_total=6)
        departure.refresh_from_db()
        assert departure.version == before + 1


class TestWhoMayReachIt:
    def test_an_anonymous_request_is_refused(self, activity: Any) -> None:
        assert APIClient().get(_url(activity)).status_code == 401

    def test_a_tourist_is_refused(self, activity: Any) -> None:
        assert signed_in_as().get(_url(activity)).status_code == 403

    def test_a_support_agent_is_refused(self, activity: Any) -> None:
        """§5.2: *"cannot alter payments or catalogue"*. Capacity is catalogue."""
        assert (
            signed_in_as(Role.SUPPORT_AGENT)
            .put(
                _url(activity),
                {"from": "2027-03-01", "to": "2027-03-31", "status": "CLOSED"},
                format="json",
            )
            .status_code
            == 403
        )

    def test_an_activity_that_does_not_exist_is_a_404(self, admin: APIClient) -> None:
        import uuid

        assert admin.get(f"/api/v1/admin/activities/{uuid.uuid4()}/departures").status_code == 404


@pytest.mark.django_db(transaction=True)
class TestAQuoteInFlight:
    """The BR-023 check must run under the lock, not before it — §17.1 I2.

    A reduction validated against an unlocked read is validated against a
    number another transaction is in the middle of changing. The seats it would
    strand belong to a tourist who is midway through paying for them.

    **The shape of this test is the point.** The holder writes nine held seats,
    signals, and then *stays inside its transaction* waiting for the editor to
    finish. It never gets that signal, because with `FOR UPDATE` the editor is
    blocked on the row — so the holder's wait times out, it commits, and the
    editor then reads nine and refuses. Remove `select_for_update` from
    `lock_departures_between` and the editor reads zero immediately, allows the
    reduction, and this fails.

    Verified that way round: with the lock deleted, this is the test that goes
    red. A version that released the holder before asserting would pass either
    way and prove nothing, which is the easy mistake here.

    What it goes red *with* is worth recording. The application check passes on
    the stale zero, the UPDATE is issued, and PostgreSQL refuses it —
    `activity_departure_no_oversell`, the CHECK that §17.3 calls the backstop:
    *"If it ever fires, that is a defect and the transaction aborts rather than
    overselling."* So the constraint would in fact save the tourist's seat. It
    would do it by returning an IntegrityError to an operator who asked a
    reasonable question, instead of a 409 naming the dates — which is the
    difference between a system that is safe and one that is also usable.
    """

    #: Long enough that the editor's whole unlocked path — imports, a query and
    #: an update — finishes well inside it, so an "allowed" result means the
    #: read was stale rather than that the holder happened to commit first.
    HOLDER_PATIENCE = 3.0

    def test_a_reduction_cannot_slip_past_a_hold_being_taken(self) -> None:
        activity = make_activity_id()
        departure = ActivityDeparture.objects.create(
            activity_id=activity.id,
            departs_at=dt.datetime(2027, 3, 1, 8, 0, tzinfo=ZoneInfo(ZONE)),
            capacity_total=12,
        )

        holding = threading.Event()
        edited = threading.Event()
        outcome: dict[str, Any] = {}

        def take_the_seats() -> None:
            """Hold nine seats and sit inside the transaction."""
            try:
                with transaction.atomic():
                    row = (
                        ActivityDeparture.objects.select_for_update().filter(id=departure.id).get()
                    )
                    row.capacity_held = 9
                    row.save(update_fields=["capacity_held"])
                    holding.set()
                    # Waits for an editor that cannot answer while it is
                    # blocked on this row. The timeout is what ends the standoff
                    # and commits the nine seats.
                    edited.wait(timeout=self.HOLDER_PATIENCE)
            except Exception as error:
                outcome["holder"] = repr(error)
            finally:
                holding.set()
                connections.close_all()

        def reduce_it() -> None:
            from apps.inventory.dto import DepartureEdit
            from apps.inventory.services import CapacityReductionError, edit_departures

            try:
                assert holding.wait(timeout=10)
                edit_departures(
                    activity.id,
                    DepartureEdit(
                        since=dt.date(2027, 3, 1),
                        until=dt.date(2027, 3, 31),
                        capacity_total=4,
                    ),
                    timezone_name=ZONE,
                )
                outcome["result"] = "allowed"
            except CapacityReductionError:
                outcome["result"] = "refused"
            except Exception as error:
                outcome["result"] = f"error: {error!r}"
            finally:
                edited.set()
                connections.close_all()

        holder = threading.Thread(target=take_the_seats)
        editor = threading.Thread(target=reduce_it)
        holder.start()
        editor.start()
        holder.join(timeout=20)
        editor.join(timeout=20)

        assert outcome.get("holder") is None, outcome
        assert outcome["result"] == "refused", outcome

        departure.refresh_from_db()
        assert departure.capacity_total == 12
        assert departure.capacity_held == 9
