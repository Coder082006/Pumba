"""Phase 4's acceptance cases — SRS §37.4, §41.

§37.4 names them: *"Acceptance TC-030, TC-031, TC-040 to TC-043 and TC-902
pass; generation of a 20-item itinerary meets NFR-P02."* One test per case,
named for its id, so the phase can be checked off against a file rather than
against somebody's memory of what was covered.

They are deliberately separate from the unit and service suites even where the
behaviour overlaps. Those tests are written to catch a *regression* in a
mechanism; these are written to answer §41's question, which is whether the
product does the thing the specification promised — and the two drift apart the
moment somebody refactors a mechanism into a different shape.

**Reading these found a defect that 2,414 passing tests had not.** TC-031 asks
for a trip starting yesterday to be refused, and `create_trip` accepted it,
because nothing had ever asked. That is the argument for running acceptance
before building anything on top of it.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from rest_framework.test import APIClient

from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
)
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db


def _today() -> date:
    """UTC, matching how the platform stores every instant (§7.2)."""
    return datetime.now(UTC).date()


#: Far enough ahead that it is future in every timezone, so a test never
#: depends on which side of midnight it runs.
START = _today() + timedelta(days=30)
END = START + timedelta(days=3)


def at(day_offset: int, hour: int) -> str:
    """An instant on a trip day, in UTC.

    The fixture destinations sit in Auckland (UTC+12), so twelve hours are
    subtracted to land on the intended *local* day — the distinction that
    caused a real defect in `generate_itinerary` and is worth keeping visible.
    """
    when = datetime.combine(START + timedelta(days=day_offset), datetime.min.time(), tzinfo=UTC)
    return (when + timedelta(hours=hour) - timedelta(hours=12)).isoformat()


def body(response: Any) -> Any:
    return response.json()["data"]


def create_trip(client: APIClient, slug: str, **overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "destination": slug,
        "start_date": START.isoformat(),
        "end_date": END.isoformat(),
        "adults": 2,
    }
    payload.update(overrides)
    return client.post("/api/v1/trips", payload, format="json")


def add_item(client: APIClient, trip_id: str, **fields: Any) -> Any:
    return client.post(f"/api/v1/trips/{trip_id}/items", fields, format="json")


@pytest.fixture
def tourist() -> APIClient:
    return signed_in_as()


@pytest.fixture
def destination() -> Any:
    return make_destination()


class TestTC030CreateTrip:
    """TC-030 · Create trip · Valid destination and dates ·
    *201; trip DRAFT; empty itinerary v1*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        response = create_trip(tourist, destination.slug)
        assert response.status_code == 201

        trip = body(response)
        assert trip["status"] == "DRAFT"
        assert trip["itinerary"]["version"] == 1
        assert trip["itinerary"]["items"] == []


class TestTC031CreateTripInThePast:
    """TC-031 · Create trip in the past · start_date yesterday · *422*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        yesterday = _today() - timedelta(days=1)
        response = create_trip(
            tourist,
            destination.slug,
            start_date=yesterday.isoformat(),
            end_date=(yesterday + timedelta(days=2)).isoformat(),
        )
        assert response.status_code == 422

    def test_starting_today_is_allowed(self, tourist: APIClient, destination: Any) -> None:
        """The boundary the case does not state, decided here rather than left
        to whoever reads `start < today` next.

        §37.4's case is "yesterday". Somebody booking a day trip for this
        afternoon is not making a mistake, so today is permitted — and the
        comparison uses the *destination's* date, since a trip starting today
        in Zanzibar is still yesterday in UTC for three hours every night.
        """
        # "Today" *where the destination is*, which is the whole point of the
        # rule. The fixture destinations sit in Auckland (UTC+12), so for
        # twelve hours a day the UTC date is already behind the local one —
        # and a test that used `datetime.now(UTC).date()` here would fail for
        # half of every day while the code was correct.
        local_today = datetime.now(ZoneInfo(destination.timezone)).date()
        response = create_trip(
            tourist,
            destination.slug,
            start_date=local_today.isoformat(),
            end_date=(local_today + timedelta(days=1)).isoformat(),
        )
        assert response.status_code == 201


class TestTC040GenerateInsertsTransfers:
    """TC-040 · Stay + activity at different locations · Generate ·
    *TRANSFER item inserted with distance and duration; times consistent*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        trip_id = body(create_trip(tourist, destination.slug))["public_id"]
        add_item(
            tourist,
            trip_id,
            item_type="STAY",
            day_number=1,
            sequence_no=1,
            title="Harbourside Lodge",
            starts_at=at(0, 14),
            ends_at=at(3, 10),
            accommodation_id=make_accommodation(destination=destination).id,
        )
        add_item(
            tourist,
            trip_id,
            item_type="ACTIVITY",
            day_number=1,
            sequence_no=2,
            title="Harbour Kayak Tour",
            starts_at=at(0, 17),
            ends_at=at(0, 19),
            activity_id=make_activity(destination=destination).id,
        )

        itinerary = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))["itinerary"]
        transfers = [i for i in itinerary["items"] if i["item_type"] == "TRANSFER"]

        assert transfers, "no transfer was inserted between two different places"
        leg = transfers[0]
        assert leg["distance_m"] > 0
        assert leg["travel_seconds"] > 0

        # "times consistent": the leg ends no later than the item it serves
        # begins, and does not run backwards.
        activity = next(i for i in itinerary["items"] if i["item_type"] == "ACTIVITY")
        assert leg["starts_at"] < leg["ends_at"] <= activity["starts_at"]


class TestTC041ValidationCatchesUnreachableSchedule:
    """TC-041 · Two activities 90 min apart, 2 h travel · Generate ·
    *Finding VR-03 severity ERROR; quoting blocked*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        trip_id = body(create_trip(tourist, destination.slug))["public_id"]

        # Two activities far enough apart that the estimate exceeds the gap.
        # The second destination is ~500 km away, so the haversine estimate is
        # hours rather than the ninety minutes between them.
        from django.contrib.gis.geos import Point

        far_away = make_destination(
            destination.region, slug="far-away", centroid=Point(178.0, -41.0, srid=4326)
        )
        near = make_activity(destination=destination, slug="near")
        # Coordinates, not just a different destination: `make_activity`
        # defaults its own `coordinates` whatever destination it is attached
        # to, so two "different" activities would sit on the same point and
        # the estimate between them would be zero.
        far = make_activity(
            destination=far_away, slug="far", coordinates=Point(178.0, -41.0, srid=4326)
        )

        add_item(
            tourist,
            trip_id,
            item_type="ACTIVITY",
            day_number=1,
            sequence_no=1,
            title="Near",
            starts_at=at(0, 9),
            ends_at=at(0, 10),
            activity_id=near.id,
        )
        add_item(
            tourist,
            trip_id,
            item_type="ACTIVITY",
            day_number=1,
            sequence_no=2,
            title="Far",
            starts_at=at(0, 11),
            ends_at=at(0, 12),
            activity_id=far.id,
        )

        itinerary = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))["itinerary"]
        vr03 = [f for f in itinerary["findings"] if f["code"] == "VR-03"]

        assert vr03, f"expected VR-03; got {[f['code'] for f in itinerary['findings']]}"
        assert vr03[0]["severity"] == "ERROR"
        assert itinerary["has_errors"] is True


class TestTC042ValidationWarnsOnOpeningHours:
    """TC-042 · Attraction visit on a closed day · Generate ·
    *Finding VR-12 severity WARNING; quoting permitted*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        trip_id = body(create_trip(tourist, destination.slug))["public_id"]

        # Open on Mondays only. The visit below is scheduled on whichever
        # weekday the trip's first day falls on; if that is a Monday the case
        # would not be testing anything, so the hours are set to the day
        # *after* it.
        local_weekday = (START + timedelta(days=1)).weekday()
        keys = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
        open_on_another_day = {keys[(local_weekday + 2) % 7]: [["09:00", "17:00"]]}

        attraction = make_attraction(destination=destination, opening_hours=open_on_another_day)
        add_item(
            tourist,
            trip_id,
            item_type="ATTRACTION",
            day_number=2,
            sequence_no=1,
            title="Stone Store",
            starts_at=at(1, 11),
            ends_at=at(1, 12),
            attraction_id=attraction.id,
        )

        itinerary = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))["itinerary"]
        vr12 = [f for f in itinerary["findings"] if f["code"] == "VR-12"]

        assert vr12, f"expected VR-12; got {[f['code'] for f in itinerary['findings']]}"
        assert vr12[0]["severity"] == "WARNING"
        # "quoting permitted": a warning does not block, and §10.6 is explicit
        # that only an ERROR does.
        assert itinerary["has_errors"] is False


class TestTC043DeterministicOrdering:
    """TC-043 · Two items with identical start times · Generate twice ·
    *Identical sequence_no both times*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        trip_id = body(create_trip(tourist, destination.slug))["public_id"]
        for n, activity in enumerate(
            [
                make_activity(destination=destination, slug="one"),
                make_activity(destination=destination, slug="two"),
            ],
            start=1,
        ):
            add_item(
                tourist,
                trip_id,
                item_type="ACTIVITY",
                day_number=1,
                sequence_no=n,
                title=f"Activity {n}",
                # Identical start times — the tie §10.4's rank exists for.
                starts_at=at(0, 10),
                ends_at=at(0, 11),
                activity_id=activity.id,
            )

        def positions() -> list[tuple[str, int]]:
            """The tourist's own items, by identity.

            Transfers are excluded: §10.4 re-inserts them on every run, so a
            regenerated leg is a new row with a new `public_id`. TC-043 asks
            whether two items that *tie* land in the same order, and that is a
            question about the rows the tourist put there.
            """
            itinerary = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))[
                "itinerary"
            ]
            return [
                (i["public_id"], i["sequence_no"])
                for i in itinerary["items"]
                if i["item_type"] != "TRANSFER"
            ]

        assert positions() == positions()


class TestTC902Determinism:
    """TC-902 · Same trip, same catalogue state · Generate twice ·
    *Byte-identical itinerary structure and totals*."""

    def test_it_passes(self, tourist: APIClient, destination: Any) -> None:
        trip_id = body(create_trip(tourist, destination.slug))["public_id"]
        add_item(
            tourist,
            trip_id,
            item_type="STAY",
            day_number=1,
            sequence_no=1,
            title="Lodge",
            starts_at=at(0, 14),
            ends_at=at(3, 10),
            accommodation_id=make_accommodation(destination=destination).id,
        )
        add_item(
            tourist,
            trip_id,
            item_type="ACTIVITY",
            day_number=1,
            sequence_no=2,
            title="Kayak",
            starts_at=at(0, 17),
            ends_at=at(0, 19),
            activity_id=make_activity(destination=destination).id,
        )

        def shape() -> Any:
            trip = body(tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate"))
            return {
                "totals": (
                    trip["subtotal_amount"],
                    trip["fee_amount"],
                    trip["tax_amount"],
                    trip["total_amount"],
                ),
                # `public_id` is excluded from the item comparison: a
                # regeneration writes new transfer rows, so their identifiers
                # legitimately differ. §10.1's promise is about the *plan*,
                # not about row identity.
                "items": [
                    (
                        i["item_type"],
                        i["day_number"],
                        i["sequence_no"],
                        i["starts_at"],
                        i["ends_at"],
                        i["line_total"],
                        i["estimate_quality"],
                    )
                    for i in trip["itinerary"]["items"]
                ],
            }

        assert shape() == shape()


class TestNFRP02:
    """*"generate completes within 2.5 s at p95 for an itinerary of up to 20
    items"* — SRS §29, measured by LT-02.

    **The number this produces is a floor, not a pass, and saying so is the
    point of the test.** NFR-P02 reads "with all routes served from cache or
    matrix", and neither exists: both are caches of a routing provider's
    answers and Appendix D-2 has not chosen one. What is measured here is the
    haversine path, which performs no I/O at all and is therefore faster than
    the real thing can ever be.

    So a green result is evidence that the sequencing, validation and costing
    are not themselves slow. It is *not* evidence that NFR-P02 is met, and
    reporting it as one would be exactly the kind of claim this project keeps
    finding and removing. The real measurement is LT-02, against a provider,
    after D-2.
    """

    #: Deliberately looser than NFR-P02's 2.5 s. This runs single-threaded in
    #: a container against a cold connection, and a tight bound here would
    #: fail for reasons that have nothing to do with the planner.
    CEILING_SECONDS = 5.0

    def test_a_twenty_item_itinerary_generates_promptly(
        self, tourist: APIClient, destination: Any
    ) -> None:
        trip_id = body(create_trip(tourist, destination.slug))["public_id"]
        activity = make_activity(destination=destination)

        for n in range(20):
            add_item(
                tourist,
                trip_id,
                item_type="ACTIVITY",
                day_number=(n % 4) + 1,
                sequence_no=n + 1,
                title=f"Activity {n}",
                starts_at=at(n % 4, 8 + (n // 4)),
                ends_at=at(n % 4, 9 + (n // 4)),
                activity_id=activity.id,
            )

        started = time.perf_counter()
        response = tourist.post(f"/api/v1/trips/{trip_id}/itinerary/generate")
        elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert len(body(response)["itinerary"]["items"]) >= 20
        assert elapsed < self.CEILING_SECONDS, (
            f"a 20-item generate took {elapsed:.2f}s. This is the no-I/O path, "
            "so it is a floor for NFR-P02 rather than a measurement of it."
        )
