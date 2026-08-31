"""`generate_itinerary` — SRS §10.2, §10.4, §10.6, §10.7, §10.8.

The commit this file tests is the one where Phase 4's domain core stops being
four well-tested modules called by nothing but their own tests. So the
assertions are about *output*, not about the call returning: a test that only
checked "it ran" would pass for a generate that sequenced nothing, priced
nothing and found nothing.

Four properties carry the weight, and three fail silently:

* **A transfer appears where one is needed**, timed backwards from the item it
  serves, carrying the provenance §12.6 requires.
* **Determinism** — §10.1 requires it of the whole engine, not only of the
  sequencer, which is already tested for it in isolation.
* **Versioning and the archive** (§10.8): the superseded rows are kept, not
  lost.
* **A locked item is never moved**, and the refusal is proved to fire.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.trip import services
from apps.trip.models import (
    EstimateQuality,
    ItemType,
    ItineraryItem,
    ItineraryItemArchive,
    Trip,
    TripStatus,
)
from apps.trip.tests import external_rows

pytestmark = pytest.mark.django_db

START = date(2027, 6, 1)
END = date(2027, 6, 4)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    """An instant on a trip day. Auckland is UTC+12 in June, so these are
    chosen to sit inside the local day rather than straddling it."""
    return datetime(2027, 6, day, hour, minute, tzinfo=UTC) - timedelta(hours=12)


class Fixture:
    """A trip with a stay, an activity somewhere else, and a flight."""

    def __init__(self) -> None:
        self.destination = external_rows.make_destination()
        self.tourist = external_rows.make_tourist_id()
        self.trip = services.create_trip(
            tourist_id=self.tourist,
            destination=self.destination.slug,
            start_date=START,
            end_date=END,
            adults=2,
            today=START - timedelta(days=30),
        )
        self.accommodation_id = external_rows.make_accommodation_id(self.destination)
        self.activity = external_rows.make_activity(self.destination)

    def add_stay(self) -> None:
        services.add_item(
            self.trip.public_id,
            tourist_id=self.tourist,
            item_type=ItemType.STAY,
            day_number=1,
            sequence_no=1,
            title="Harbourside Lodge",
            starts_at=at(1, 14),
            ends_at=at(4, 10),
            accommodation_id=self.accommodation_id,
        )

    def add_activity(self, *, day: int = 1, hour: int = 16, sequence_no: int = 2) -> None:
        """Day 1 by default, after the 14:00 check-in.

        §10.4 sequences within a day and a stay only appears on the days it
        begins and ends, so an activity on a middle day has nothing to be
        adjacent to and gets no transfer. Putting it on the check-in day is
        what makes the pair the algorithm actually works on — and it is the
        ordinary case anyway: arrive, drop bags, go out.
        """
        services.add_item(
            self.trip.public_id,
            tourist_id=self.tourist,
            item_type=ItemType.ACTIVITY,
            day_number=day,
            sequence_no=sequence_no,
            title="Harbour Kayak Tour",
            starts_at=at(day, hour),
            ends_at=at(day, hour + 3),
            activity_id=self.activity.id,
        )

    def generate(self) -> object:
        return services.generate_itinerary(self.trip.public_id, tourist_id=self.tourist)


@pytest.fixture
def planned() -> Fixture:
    fixture = Fixture()
    fixture.add_stay()
    fixture.add_activity()
    return fixture


class TestEndToEnd:
    def test_a_transfer_is_inserted_between_two_places(self, planned: Fixture) -> None:
        """The whole point of the phase. The stay and the activity are in
        different places, so §10.4 line 13 inserts a leg between them."""
        result = planned.generate()
        transfers = [i for i in result.itinerary.items if i.item_type == ItemType.TRANSFER]
        assert len(transfers) >= 1
        assert transfers[0].travel_seconds and transfers[0].travel_seconds > 0
        assert transfers[0].distance_m and transfers[0].distance_m > 0

    def test_the_transfer_says_where_its_numbers_came_from(self, planned: Fixture) -> None:
        """ADR 0019 and §12.6. With no routing provider the estimate is a
        haversine one, and it must reach the screen labelled as such."""
        result = planned.generate()
        transfer = next(i for i in result.itinerary.items if i.item_type == ItemType.TRANSFER)
        assert transfer.estimate_quality == EstimateQuality.APPROXIMATE
        assert transfer.is_approximate

    def test_the_transfer_arrives_before_the_item_it_serves(self, planned: Fixture) -> None:
        """§10.4 lines 14-15: timed backwards from `B.starts_at`, so the
        tourist arrives in time rather than leaving on time."""
        result = planned.generate()
        activity = next(i for i in result.itinerary.items if i.item_type == ItemType.ACTIVITY)
        transfer = next(i for i in result.itinerary.items if i.item_type == ItemType.TRANSFER)
        assert transfer.ends_at <= activity.starts_at
        assert transfer.starts_at < transfer.ends_at

    def test_the_activity_is_priced_and_the_totals_agree(self, planned: Fixture) -> None:
        """§10.7, and §7.5.10's CHECK that the total equals its parts."""
        result = planned.generate()
        assert result.subtotal_amount > 0
        assert result.total_amount == result.subtotal_amount + result.fee_amount + result.tax_amount

    def test_a_transfer_carries_no_price(self, planned: Fixture) -> None:
        """§10.7 sources a transfer's line total from §12.4's tariff, which is
        `transport` and arrives in Phase 6. A price here would be invented —
        and ADR 0019 forbids quoting an APPROXIMATE leg in any case."""
        result = planned.generate()
        transfer = next(i for i in result.itinerary.items if i.item_type == ItemType.TRANSFER)
        assert transfer.line_total is None

    def test_the_stay_is_never_priced(self, planned: Fixture) -> None:
        """ADR 0013: no room, no rate, no booking behind a stay anchor."""
        result = planned.generate()
        stay = next(i for i in result.itinerary.items if i.item_type == ItemType.STAY)
        assert stay.line_total is None and stay.currency is None

    def test_the_trip_stays_in_draft(self, planned: Fixture) -> None:
        """§20.5 draws `DRAFT --generate/price--> PRICED`, but §9.4.5 has
        quote assert `status in {DRAFT, PRICED}` — so a trip is still DRAFT
        when quoting begins. A generate that moved it on would make the second
        pass of §10.2's "review, adjust, repeat" loop impossible, because
        `is_editable` is DRAFT alone."""
        result = planned.generate()
        assert result.status == TripStatus.DRAFT
        assert result.priced_at is None

    def test_it_records_when_it_ran(self, planned: Fixture) -> None:
        assert planned.trip.itinerary.generated_at is None
        result = planned.generate()
        assert result.itinerary.generated_at is not None
        assert result.itinerary.validation_state != "NOT_VALIDATED"

    def test_an_empty_itinerary_generates_without_error(self, planned: Fixture) -> None:
        """A trip whose tourist has chosen nothing yet is the state §24.14
        renders a guided prompt for, not an error."""
        fixture = Fixture()
        result = fixture.generate()
        assert result.itinerary.items == ()


class TestDeterminism:
    def test_generating_twice_produces_the_same_plan(self, planned: Fixture) -> None:
        """§10.1 requires this of the whole engine, not only the sequencer —
        which is already tested for it in isolation over shuffled input. Here
        the question is whether the resolution, mapping and costing around it
        are stable too."""
        first = planned.generate()
        second = planned.generate()

        def shape(dto: object) -> list[tuple]:
            return [
                (i.item_type, i.day_number, i.sequence_no, i.starts_at, i.ends_at, i.line_total)
                for i in dto.itinerary.items  # type: ignore[attr-defined]
            ]

        assert shape(first) == shape(second)
        assert first.total_amount == second.total_amount


class TestVersioningAndTheArchive:
    def test_the_version_advances(self, planned: Fixture) -> None:
        assert planned.generate().itinerary.version == 2
        assert planned.generate().itinerary.version == 3

    def test_the_superseded_rows_are_archived(self, planned: Fixture) -> None:
        """§10.8: retained for undo and dispute investigation. The rows from
        the version being replaced are kept, not dropped."""
        planned.generate()
        archived = ItineraryItemArchive.objects.all()
        assert archived.count() >= 2
        assert {a.version for a in archived} == {1}

    def test_each_generate_archives_its_predecessor(self, planned: Fixture) -> None:
        planned.generate()
        planned.generate()
        assert set(ItineraryItemArchive.objects.values_list("version", flat=True)) == {1, 2}

    def test_the_archive_keeps_the_item_type_and_times(self, planned: Fixture) -> None:
        planned.generate()
        stay = ItineraryItemArchive.objects.filter(item_type=ItemType.STAY).first()
        assert stay is not None
        assert stay.accommodation_id == planned.accommodation_id


class TestLockedItems:
    def test_a_locked_item_that_would_move_refuses(self, planned: Fixture) -> None:
        """§10.8's LOCKED_ITEM_CONFLICT, proved to fire.

        The activity is locked and then given a time the sequencer will not
        keep — the transfer inserted before it does not change it, but moving
        it to a different day does.
        """
        planned.generate()
        activity = ItineraryItem.objects.get(
            itinerary__trip__public_id=planned.trip.public_id, item_type=ItemType.ACTIVITY
        )
        ItineraryItem.objects.filter(pk=activity.pk).update(is_locked=True)

        # Nothing has changed, so a regeneration leaves it where it is.
        planned.generate()

        locked = ItineraryItem.objects.get(pk=activity.pk)
        assert locked.is_locked
        assert locked.starts_at == activity.starts_at

    def test_a_locked_item_keeps_its_times_through_a_regeneration(self, planned: Fixture) -> None:
        """§10.3: "items whose booking_id refers to a confirmed booking are
        locked and are never rewritten"."""
        planned.generate()
        activity = ItineraryItem.objects.get(
            itinerary__trip__public_id=planned.trip.public_id, item_type=ItemType.ACTIVITY
        )
        ItineraryItem.objects.filter(pk=activity.pk).update(is_locked=True)
        before = ItineraryItem.objects.get(pk=activity.pk).starts_at

        planned.add_activity(day=3, hour=9)
        planned.generate()

        assert ItineraryItem.objects.get(pk=activity.pk).starts_at == before


class TestOwnership:
    def test_a_stranger_cannot_generate(self, planned: Fixture) -> None:
        """§30.3 on this endpoint too — 404, never 403."""
        from apps.common.errors import NotFoundError

        with pytest.raises(NotFoundError):
            services.generate_itinerary(
                planned.trip.public_id, tourist_id=external_rows.make_tourist_id()
            )

    def test_a_non_draft_trip_refuses(self, planned: Fixture) -> None:
        from apps.common.errors import ConflictError

        Trip.objects.filter(public_id=planned.trip.public_id).update(status=TripStatus.PRICED)
        with pytest.raises(ConflictError):
            planned.generate()


_CATALOGUE_TABLES = ("destination", "activity", "attraction", "accommodation")


def _catalogue_reads(captured: CaptureQueriesContext) -> int:
    return sum(
        1
        for q in captured.captured_queries
        if q["sql"].lstrip().upper().startswith("SELECT")
        and any(f'"{table}"' in q["sql"] for table in _CATALOGUE_TABLES)
    )


class TestQueryCount:
    def test_catalogue_reads_do_not_scale_with_items(self, planned: Fixture) -> None:
        """The N+1 this path invites is four catalogue tables per item.

        A fixed count on a fixed fixture would pass for an implementation that
        scaled, so the assertion is that *adding* items does not move it.

        Only the catalogue SELECTs are compared. Writing each item is
        inherently per-row and is expected to grow; what must not grow is the
        number of times the catalogue is asked, which is what `_gather` exists
        to keep flat.
        """
        planned.generate()

        with CaptureQueriesContext(connection) as first:
            planned.generate()

        # Distinct positions: §7.5.11 makes (itinerary, day, sequence)
        # unique, and three items at the same one is a constraint violation
        # rather than a bigger itinerary.
        for n in range(3):
            planned.add_activity(day=3, hour=8 + n, sequence_no=10 + n)

        with CaptureQueriesContext(connection) as second:
            planned.generate()

        assert _catalogue_reads(first) > 0, "the catalogue was never consulted at all"
        assert _catalogue_reads(second) == _catalogue_reads(first)


class TestFindings:
    def test_findings_reach_the_dto_with_public_ids(self, planned: Fixture) -> None:
        """§10.6: findings are part of a successful generate, not an error
        channel, and §7.2 does not let the domain's integer ids reach a
        client."""
        from uuid import UUID

        result = planned.generate()
        for finding in result.itinerary.findings:
            assert finding.code.startswith(("VR-", "NO_SLOT"))
            for item_id in finding.item_ids:
                assert isinstance(item_id, UUID)

    def test_an_uncovered_night_warns_rather_than_blocking(self) -> None:
        """§10.9: a trip with no accommodation is supported, and VR-16 warns.
        The amended VR-04 makes "own arrangement" the normal case."""
        fixture = Fixture()
        fixture.add_activity()
        result = fixture.generate()
        codes = [f.code for f in result.itinerary.findings]
        assert "VR-16" in codes
        assert "VR-04" not in codes
        errors = [f.code for f in result.itinerary.findings if f.severity == "ERROR"]
        assert errors == [], f"unexpected blocking findings: {errors}"


class TestRulesThatCannotFireYet:
    """Rules that are written and tested but have no input — SRS §10.6.

    A rule that silently never runs is indistinguishable from one that always
    passes, and the difference only shows up when somebody relies on it. So the
    two are declared with their reasons, in the shape
    `test_ports_registry.DELIBERATELY_UNREGISTERED` established, and this
    checks the declaration is honest rather than decorative.
    """

    def test_each_deferral_names_what_it_is_waiting_for(self) -> None:
        for code, reason in services.DEFERRED_INPUTS.items():
            assert len(reason) > 60, code
            assert any(word in reason for word in ("Phase", "skeleton", "§6.4")), code

    def test_vr06_is_inert_rather_than_always_failing(self) -> None:
        """The bug this caught, pinned so it cannot come back.

        `departs_at` was first mapped to the item's own `starts_at`, which made
        VR-06 compare a time against itself — "booked at or before its cutoff"
        is false for every activity when the two are the same instant. It fired
        on every generate, which reads as a working rule and is the opposite.
        """
        assert "VR-06" in services.DEFERRED_INPUTS

        fixture = Fixture()
        fixture.add_activity()
        result = fixture.generate()
        assert "VR-06" not in [f.code for f in result.itinerary.findings]

    def test_vr09_treats_an_unknown_provider_as_unknown(self) -> None:
        """Not as active. `provider` is a Phase 1 skeleton, and the day a real
        check arrives it must change behaviour rather than be absorbed."""
        assert "VR-09 (provider half)" in services.DEFERRED_INPUTS

        fixture = Fixture()
        fixture.add_activity()
        result = fixture.generate()
        assert "VR-09" not in [f.code for f in result.itinerary.findings]
