"""`GET /activities/{reference}/departures` — SRS §9.3.2, SD-06, §24.10.

The endpoint SD-06 opens with, and the first thing a tourist sees of Phase 5.

Two obligations beyond "it returns rows".

**A hidden activity's calendar is a 404**, not an empty list. §30.3 makes a row
a caller may not see indistinguishable from one that does not exist, and an
empty-but-200 departures list would publish the fact that the activity is
there. `tests/test_catalogue_public_api.py` asserts the same property per
public route and fails the build for one that does not; this is that assertion
for the route `inventory` owns.

**Every row says its figure is indicative.** §17.1 I3 and §8.10. The label is
what stops a client treating a cached number as a promise, and a response that
carried it only sometimes would be one nobody reads.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.inventory import services
from apps.inventory.dto import HoldRequest
from apps.inventory.tests.catalogue_rows import ZONE
from apps.inventory.tests.factories import make_departure

pytestmark = pytest.mark.django_db


def _url(reference: str) -> str:
    return reverse("v1:inventory:activity-departures", kwargs={"reference": reference})


def _slug_of(activity_id: int) -> str:
    return str(
        django_apps.get_model("catalogue", "Activity")
        .objects.values_list("slug", flat=True)
        .get(id=activity_id)
    )


def _get(activity_id: int, **params: object) -> object:
    return APIClient().get(_url(_slug_of(activity_id)), params or None)


class TestItListsDepartures:
    def test_a_departure_is_returned(self) -> None:
        departure = make_departure()
        response = _get(departure.activity_id)
        assert response.status_code == 200  # type: ignore[attr-defined]
        assert len(response.data["data"]) == 1  # type: ignore[attr-defined]

    def test_it_reports_the_seats_left_and_not_the_counters(self) -> None:
        """Publishing `capacity_held` would tell a tourist how many seats
        somebody else is midway through paying for."""
        departure = make_departure(capacity_total=12, capacity_held=2, capacity_sold=6)
        row = _get(departure.activity_id).data["data"][0]  # type: ignore[attr-defined]
        assert row["remaining"] == 4
        assert "capacity_held" not in row
        assert "capacity_sold" not in row

    def test_every_row_says_the_figure_is_indicative(self) -> None:
        departure = make_departure()
        row = _get(departure.activity_id).data["data"][0]  # type: ignore[attr-defined]
        assert row["basis"] == "INDICATIVE"

    def test_a_departure_can_be_addressed_afterwards(self) -> None:
        """§7.2: the UUID is the identifier the API exchanges. A client that
        received no addressable id could not put the departure in a trip."""
        departure = make_departure()
        row = _get(departure.activity_id).data["data"][0]  # type: ignore[attr-defined]
        assert row["public_id"] == str(departure.public_id)

    def test_a_cancelled_departure_is_listed_rather_than_hidden(self) -> None:
        departure = make_departure(status="CANCELLED")
        row = _get(departure.activity_id).data["data"][0]  # type: ignore[attr-defined]
        assert row["status"] == "CANCELLED"
        assert row["remaining"] == 0

    def test_a_held_seat_is_gone_from_the_next_reader_s_view(self) -> None:
        """The whole point of a hold, seen from the outside."""
        departure = make_departure(capacity_total=12)
        services.hold(
            trip_id=1,
            requests=[HoldRequest(departure_id=departure.id, pax=3)],
            ttl_minutes=20,
            now=timezone.now(),
        )
        row = _get(departure.activity_id).data["data"][0]  # type: ignore[attr-defined]
        assert row["remaining"] == 9


class TestTheWindow:
    def test_it_defaults_to_the_next_thirty_days(self) -> None:
        """§24.10 shows a month. A default of "everything" would return a
        180-day horizon to a screen that renders four weeks of it."""
        near = make_departure(departs_at=timezone.now() + dt.timedelta(days=3))
        make_departure(
            activity_id=near.activity_id, departs_at=timezone.now() + dt.timedelta(days=60)
        )
        assert len(_get(near.activity_id).data["data"]) == 1  # type: ignore[attr-defined]

    def test_a_window_is_honoured(self) -> None:
        first = make_departure(departs_at=timezone.now() + dt.timedelta(days=3))
        make_departure(
            activity_id=first.activity_id, departs_at=timezone.now() + dt.timedelta(days=40)
        )
        response = _get(
            first.activity_id,
            **{"from": timezone.localdate().isoformat()},
            to=(timezone.localdate() + dt.timedelta(days=50)).isoformat(),
        )
        assert len(response.data["data"]) == 2  # type: ignore[attr-defined]

    def test_the_day_named_by_to_is_included(self) -> None:
        """A window that excluded its own end date would drop the departure a
        tourist typed the date of.

        The date is taken **in the destination's zone**, which is the whole
        point of the endpoint resolving it there. `when.date()` is the UTC
        date, and the test fixtures sit in Pacific/Auckland (UTC+12) — so a
        departure at 22:00 UTC is the *next* day locally, and asking for the
        UTC date returns an empty window. That made this test pass or fail
        according to the hour it ran at, which it did: green all afternoon and
        red at 20:00.
        """
        when = timezone.now() + dt.timedelta(days=3)
        departure = make_departure(departs_at=when)
        local_date = when.astimezone(ZoneInfo(ZONE)).date().isoformat()
        response = _get(
            departure.activity_id,
            **{"from": local_date},
            to=local_date,
        )
        assert len(response.data["data"]) == 1  # type: ignore[attr-defined]

    def test_a_backwards_window_is_refused(self) -> None:
        departure = make_departure()
        response = _get(
            departure.activity_id,
            **{"from": "2027-08-12"},
            to="2027-08-01",
        )
        assert response.status_code == 422  # type: ignore[attr-defined]

    def test_an_unbounded_window_is_refused(self) -> None:
        """One request may not ask for the whole calendar."""
        departure = make_departure()
        response = _get(
            departure.activity_id,
            **{"from": "2027-01-01"},
            to="2029-01-01",
        )
        assert response.status_code == 422  # type: ignore[attr-defined]

    def test_an_unknown_parameter_is_named_rather_than_ignored(self) -> None:
        """§30.6. A silently ignored `?date=` is a filter a client believes is
        applied."""
        departure = make_departure()
        response = _get(departure.activity_id, date="2027-08-12")
        assert response.status_code == 422  # type: ignore[attr-defined]


class TestPaxTurnsItIntoAdvice:
    def test_a_bookable_departure_says_so(self) -> None:
        departure = make_departure(capacity_total=12)
        row = _get(departure.activity_id, pax=2).data["data"][0]  # type: ignore[attr-defined]
        assert row["is_bookable"] is True
        assert row["unbookable"] is None

    def test_a_sold_out_departure_says_why(self) -> None:
        departure = make_departure(capacity_total=2, capacity_sold=2)
        row = _get(departure.activity_id, pax=2).data["data"][0]  # type: ignore[attr-defined]
        assert row["unbookable"] == "SOLD_OUT"

    def test_a_departure_past_its_cutoff_says_why(self) -> None:
        """A different sentence from sold out, leading to a different action:
        one wants another date, the other wants any date at all."""
        departure = make_departure(departs_at=timezone.now() + dt.timedelta(hours=2))
        row = _get(departure.activity_id, pax=2).data["data"][0]  # type: ignore[attr-defined]
        assert row["unbookable"] == "PAST_CUTOFF"

    def test_without_pax_no_judgement_is_offered(self) -> None:
        departure = make_departure(capacity_total=2, capacity_sold=2)
        row = _get(departure.activity_id).data["data"][0]  # type: ignore[attr-defined]
        assert row["unbookable"] is None


class TestItIsPublicAndSafe:
    def test_no_sign_in_is_needed(self) -> None:
        """§9.3.2: the catalogue is what Google indexes and what a tourist
        reads before registering."""
        departure = make_departure()
        assert _get(departure.activity_id).status_code == 200  # type: ignore[attr-defined]

    def test_an_unknown_activity_is_a_404(self) -> None:
        assert APIClient().get(_url("no-such-activity")).status_code == 404

    def test_a_withdrawn_activity_is_a_404_and_not_an_empty_list(self) -> None:
        """§30.3, from the public side. An empty 200 would publish that the
        activity exists — which is the disclosure being refused."""
        departure = make_departure()
        slug = _slug_of(departure.activity_id)
        django_apps.get_model("catalogue", "Activity").objects.filter(
            id=departure.activity_id
        ).update(is_active=False)

        assert APIClient().get(_url(slug)).status_code == 404

    def test_a_uuid_addresses_it_as_well_as_a_slug(self) -> None:
        """§7.2 makes the UUID the identifier the API exchanges; §24.8 serves
        pages from slugs. `resolve_listing_ref` accepts either, and the two
        must not disagree."""
        departure = make_departure()
        public_id = str(
            django_apps.get_model("catalogue", "Activity")
            .objects.values_list("public_id", flat=True)
            .get(id=departure.activity_id)
        )
        assert APIClient().get(_url(public_id)).status_code == 200
