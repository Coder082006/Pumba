"""The day-sequencing algorithm — SRS §10.4.

§10.1 makes determinism a requirement rather than a nicety: "the same inputs,
the same catalogue state and the same tariff configuration always produce the
same itinerary and the same total". §10.4 then writes the algorithm out in
twenty-one numbered lines "so that two implementations produce identical
output".

That shapes these tests. Several of them assert an *exact* ordering or an exact
placement where a looser assertion would pass — because the looser assertion
would also pass for an implementation that reorders on a whim, and §10.1 is
what makes that unacceptable. `test_a_shuffled_input_gives_the_same_output` is
the load-bearing one: it is the only test here that would catch a sort key
accidentally depending on input order.

Times are UTC and fixed. A test that depends on when it ran is a test that
fails on a Tuesday.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from apps.trip.domain.findings import Severity
from apps.trip.domain.sequencing import (
    NO_SLOT_FOR_ATTRACTION,
    Buffers,
    Kind,
    LocationKey,
    PlannedItem,
    TravelEstimate,
    sequence_day,
    sequence_trip,
)

DAY = datetime(2027, 6, 1, tzinfo=UTC)


def at(hour: int, minute: int = 0) -> datetime:
    return DAY + timedelta(hours=hour, minutes=minute)


def fixed_travel(minutes: int, *, quality: str = "APPROXIMATE"):
    """A travel-time function that always answers the same thing."""

    def _travel(origin: LocationKey, target: LocationKey) -> TravelEstimate:
        return TravelEstimate(seconds=minutes * 60, metres=minutes * 750, quality=quality)

    return _travel


def never_called(origin: LocationKey, target: LocationKey) -> TravelEstimate:
    """For days where no transfer should be needed at all.

    A stub that raises is stronger than one that returns zero: it distinguishes
    "no transfer was inserted" from "a transfer was inserted with a zero
    duration", and only the first is correct when two items share a location.
    """
    raise AssertionError(f"travel_time should not have been called ({origin} -> {target})")


def item(
    item_id: int,
    kind: Kind,
    *,
    hour: int | None = None,
    minutes: int = 60,
    where: str | None = "hotel",
    ends_where: str | None = None,
    visit_minutes: int = 0,
    **overrides: object,
) -> PlannedItem:
    starts_at = at(hour) if hour is not None else None
    return PlannedItem(
        item_id=item_id,
        kind=kind,
        title=f"item-{item_id}",
        day_number=1,
        starts_at=starts_at,
        ends_at=starts_at + timedelta(minutes=minutes) if starts_at else None,
        start_location=where,
        end_location=ends_where if ends_where is not None else where,
        visit_minutes=visit_minutes,
        **overrides,  # type: ignore[arg-type]
    )


class TestTravelEstimate:
    def test_it_must_say_where_it_came_from(self) -> None:
        """ADR 0019. An estimate with no provenance is a number the quote path
        has no way to refuse."""
        with pytest.raises(ValueError, match="where it came from"):
            TravelEstimate(seconds=600, metres=8000, quality="")

    @pytest.mark.parametrize(("seconds", "metres"), [(-1, 100), (100, -1)])
    def test_it_may_not_be_negative(self, seconds: int, metres: int) -> None:
        with pytest.raises(ValueError, match="negative"):
            TravelEstimate(seconds=seconds, metres=metres, quality="ROUTED")


class TestTheTotalOrder:
    def test_items_sort_by_start_time(self) -> None:
        result = sequence_day(
            [
                item(2, Kind.ACTIVITY, hour=14),
                item(1, Kind.ACTIVITY, hour=9),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert [i.item_id for i in result.items] == [1, 2]

    def test_a_tie_is_broken_by_the_specified_rank(self) -> None:
        """§10.4: TRANSFER (0) < STAY check-out (1) < ACTIVITY (2) <
        ATTRACTION (3) < STAY check-in (4) < FREE_TIME (5).

        All five at the same instant, submitted in reverse, so nothing here
        can pass by accident of input order.
        """
        kinds = [
            Kind.FREE_TIME,
            Kind.STAY_CHECK_IN,
            Kind.ATTRACTION,
            Kind.ACTIVITY,
            Kind.STAY_CHECK_OUT,
            Kind.TRANSFER,
        ]
        result = sequence_day(
            [item(10 + n, kind, hour=9, minutes=0) for n, kind in enumerate(kinds)],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert [i.kind for i in result.items] == [
            Kind.TRANSFER,
            Kind.STAY_CHECK_OUT,
            Kind.ACTIVITY,
            Kind.ATTRACTION,
            Kind.STAY_CHECK_IN,
            Kind.FREE_TIME,
        ]

    def test_identical_time_and_rank_fall_back_to_id(self) -> None:
        """§10.4 line 7's third key. Without it the order of two activities
        starting together is whatever the sort was handed, and §10.1 forbids
        that."""
        result = sequence_day(
            [
                item(7, Kind.ACTIVITY, hour=9, minutes=0),
                item(3, Kind.ACTIVITY, hour=9, minutes=0),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert [i.item_id for i in result.items] == [3, 7]

    def test_sequence_numbers_restart_at_one(self) -> None:
        """§10.4 line 20."""
        result = sequence_day(
            [item(1, Kind.ACTIVITY, hour=9), item(2, Kind.ACTIVITY, hour=14)],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert [i.sequence_no for i in result.items] == [1, 2]


class TestTransferInsertion:
    def test_none_is_inserted_between_items_in_the_same_place(self) -> None:
        """Line 11's `IF origin != target`. `never_called` proves the routing
        function was not consulted at all, not merely that the answer was
        discarded."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.ACTIVITY, hour=14, where="hotel"),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert all(i.kind is not Kind.TRANSFER for i in result.items)

    def test_one_is_inserted_between_different_places(self) -> None:
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.ACTIVITY, hour=14, where="beach"),
            ],
            travel_time=fixed_travel(30),
            buffers=Buffers(activity_minutes=0),
        )
        transfers = [i for i in result.items if i.kind is Kind.TRANSFER]
        assert len(transfers) == 1
        leg = transfers[0]
        assert leg.start_location == "hotel" and leg.end_location == "beach"

    def test_it_is_timed_backwards_from_the_item_it_serves(self) -> None:
        """§10.4 lines 14-15: `ends_at = B.starts_at - buffer_before(B)` and
        `starts_at = ends_at - t`. Backwards, so the tourist arrives in time
        rather than leaving on time — which is the same arithmetic and a
        different promise."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.ACTIVITY, hour=14, where="beach"),
            ],
            travel_time=fixed_travel(45),
            buffers=Buffers(activity_minutes=15),
        )
        leg = next(i for i in result.items if i.kind is Kind.TRANSFER)
        assert leg.ends_at == at(13, 45)  # 14:00 less the 15-minute buffer
        assert leg.starts_at == at(13, 0)  # less 45 minutes of driving

    def test_it_carries_the_estimate_and_its_provenance(self) -> None:
        """ADR 0019: the quality the caller resolved travels into the item,
        and this module neither sets nor inspects it."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.ACTIVITY, hour=14, where="beach"),
            ],
            travel_time=fixed_travel(20, quality="ROUTED"),
            buffers=Buffers(),
        )
        leg = next(i for i in result.items if i.kind is Kind.TRANSFER)
        assert leg.travel_seconds == 1200
        assert leg.distance_m == 15_000
        assert leg.estimate_quality == "ROUTED"

    def test_an_airport_departure_gets_its_own_buffer(self) -> None:
        """§10.4's `buffer.airport_departure_minutes`, default 180. The one
        case where getting the buffer wrong means a missed flight."""
        result = sequence_day(
            [
                item(1, Kind.STAY_CHECK_OUT, hour=10, where="hotel"),
                item(
                    2,
                    Kind.ACTIVITY,
                    hour=18,
                    where="airport",
                    is_airport_departure=True,
                ),
            ],
            travel_time=fixed_travel(40),
            buffers=Buffers(airport_departure_minutes=180),
        )
        leg = next(i for i in result.items if i.kind is Kind.TRANSFER)
        assert leg.ends_at == at(15, 0)  # 18:00 less three hours
        assert leg.starts_at == at(14, 20)

    def test_an_item_with_no_place_gets_no_transfer(self) -> None:
        """FREE_TIME at no particular location. Inserting a leg to nowhere
        would be worse than leaving the gap."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.FREE_TIME, hour=14, where=None),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert all(i.kind is not Kind.TRANSFER for i in result.items)

    def test_a_transfer_uses_the_end_of_the_earlier_item(self) -> None:
        """Line 9 is `end_location(A)`, not `start_location(A)`. They differ
        for a stored transfer, and using the wrong one routes from where the
        tourist set off rather than where they arrived."""
        result = sequence_day(
            [
                item(1, Kind.TRANSFER, hour=8, where="airport", ends_where="hotel"),
                item(2, Kind.ACTIVITY, hour=14, where="beach"),
            ],
            travel_time=fixed_travel(25),
            buffers=Buffers(activity_minutes=0),
        )
        leg = next(i for i in result.items if i.is_inserted)
        assert leg.start_location == "hotel"

    def test_inserted_transfers_have_negative_ids(self) -> None:
        """They have no row yet, and a positive id would collide with one that
        does. The application layer uses the sign to tell what it must create
        from what it must keep."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.ACTIVITY, hour=12, where="beach"),
                item(3, Kind.ACTIVITY, hour=16, where="forest"),
            ],
            travel_time=fixed_travel(20),
            buffers=Buffers(),
        )
        inserted = [i.item_id for i in result.items if i.is_inserted]
        assert len(inserted) == 2
        assert all(i < 0 for i in inserted)
        assert len(set(inserted)) == 2


class TestFlexiblePlacement:
    def test_it_lands_in_the_largest_gap(self) -> None:
        """§10.4 line 17. Three fixed items make two gaps — 09:00-12:00 and
        13:00-17:00 — and the attraction belongs in the second."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=8, minutes=60, where="hotel"),
                item(2, Kind.ACTIVITY, hour=12, minutes=60, where="hotel"),
                item(3, Kind.ACTIVITY, hour=17, minutes=60, where="hotel"),
                item(4, Kind.ATTRACTION, where="hotel", visit_minutes=60),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        placed = next(i for i in result.items if i.item_id == 4)
        assert placed.starts_at == at(13, 0)
        assert placed.ends_at == at(14, 0)

    def test_travel_in_and_out_is_charged_against_the_gap(self) -> None:
        """Line 18: `travel_in + visit_minutes + travel_out`. A 60-minute
        visit needing 20 minutes each way does not fit a 90-minute gap."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, minutes=60, where="hotel"),
                item(2, Kind.ACTIVITY, hour=11, minutes=30, where="hotel"),
                item(3, Kind.ATTRACTION, where="museum", visit_minutes=60),
            ],
            travel_time=fixed_travel(20),
            buffers=Buffers(),
        )
        assert [f.code for f in result.findings] == [NO_SLOT_FOR_ATTRACTION]

    def test_it_starts_after_the_travel_in(self) -> None:
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, minutes=60, where="hotel"),
                item(2, Kind.ACTIVITY, hour=15, minutes=60, where="hotel"),
                item(3, Kind.ATTRACTION, where="museum", visit_minutes=60),
            ],
            travel_time=fixed_travel(30),
            buffers=Buffers(),
        )
        placed = next(i for i in result.items if i.item_id == 3)
        assert placed.starts_at == at(10, 30)

    def test_an_unplaceable_item_is_reported_and_kept(self) -> None:
        """§10.4 line 19: emit the finding "and leave it unscheduled".
        Dropping something the tourist chose is worse than saying it does not
        fit."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, minutes=60, where="hotel"),
                item(2, Kind.ACTIVITY, hour=10, minutes=60, where="hotel"),
                item(3, Kind.ATTRACTION, where="hotel", visit_minutes=180),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        finding = result.findings[0]
        assert finding.code == NO_SLOT_FOR_ATTRACTION
        assert finding.severity is Severity.WARNING
        assert finding.item_ids == (3,)
        assert finding.context["required_minutes"] == 180
        assert [i.item_id for i in result.unscheduled] == [3]

    def test_a_day_with_one_fixed_item_has_no_bounded_gap(self) -> None:
        """A gap needs two sides. An attraction dropped into the open end of a
        day has no relationship to anything, and §24.14 would render it as
        though the planner had decided something."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, where="hotel"),
                item(2, Kind.ATTRACTION, where="hotel", visit_minutes=30),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert [f.code for f in result.findings] == [NO_SLOT_FOR_ATTRACTION]

    def test_two_flexible_items_compete_in_a_fixed_order(self) -> None:
        """§10.1 again: with one gap that holds only one of them, which wins
        must not depend on input order.

        Rank then id, so the ATTRACTION takes the slot and the FREE_TIME is
        reported — regardless of which arrives first. The gap is 90 minutes
        and each wants 60, so exactly one can fit.
        """
        candidates = [
            item(9, Kind.FREE_TIME, where="hotel", visit_minutes=60),
            item(4, Kind.ATTRACTION, where="hotel", visit_minutes=60),
        ]
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, minutes=60, where="hotel"),
                PlannedItem(
                    item_id=2,
                    kind=Kind.ACTIVITY,
                    title="item-2",
                    day_number=1,
                    starts_at=at(11, 30),
                    ends_at=at(12, 30),
                    start_location="hotel",
                    end_location="hotel",
                ),
                *candidates,
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        placed = next(i for i in result.items if i.item_id == 4)
        assert placed.starts_at == at(10, 0)
        assert [i.item_id for i in result.unscheduled] == [9]

    def test_a_second_flexible_item_uses_what_the_first_left(self) -> None:
        """Placement is sequential, not simultaneous: each item is placed
        against the day as it stands after the previous one. A 120-minute gap
        therefore holds two 60-minute visits, back to back."""
        result = sequence_day(
            [
                item(1, Kind.ACTIVITY, hour=9, minutes=60, where="hotel"),
                item(2, Kind.ACTIVITY, hour=12, minutes=60, where="hotel"),
                item(9, Kind.FREE_TIME, where="hotel", visit_minutes=60),
                item(4, Kind.ATTRACTION, where="hotel", visit_minutes=60),
            ],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert result.unscheduled == ()
        assert next(i for i in result.items if i.item_id == 4).starts_at == at(10, 0)
        assert next(i for i in result.items if i.item_id == 9).starts_at == at(11, 0)


class TestDeterminism:
    """§10.1's headline requirement, checked directly."""

    def _day(self) -> list[PlannedItem]:
        return [
            item(1, Kind.STAY_CHECK_OUT, hour=8, minutes=0, where="hotel"),
            item(2, Kind.ACTIVITY, hour=10, minutes=90, where="harbour"),
            item(3, Kind.ACTIVITY, hour=15, minutes=60, where="forest"),
            item(4, Kind.ATTRACTION, where="museum", visit_minutes=45),
            item(5, Kind.STAY_CHECK_IN, hour=19, minutes=0, where="lodge"),
        ]

    def test_the_same_input_gives_the_same_output(self) -> None:
        first = sequence_day(self._day(), travel_time=fixed_travel(15), buffers=Buffers())
        second = sequence_day(self._day(), travel_time=fixed_travel(15), buffers=Buffers())
        assert first == second

    def test_a_shuffled_input_gives_the_same_output(self) -> None:
        """The load-bearing test of this file.

        Every other ordering assertion would also pass for an implementation
        whose sort key quietly depended on the order it was handed. This is
        the one that would not.
        """
        baseline = sequence_day(self._day(), travel_time=fixed_travel(15), buffers=Buffers())
        rng = random.Random(20260830)
        for _ in range(20):
            shuffled = self._day()
            rng.shuffle(shuffled)
            assert (
                sequence_day(shuffled, travel_time=fixed_travel(15), buffers=Buffers()) == baseline
            )


class TestLockedItems:
    def test_a_locked_item_keeps_its_times(self) -> None:
        """§10.3: locked items "are never rewritten". The sequencer may still
        insert transfers around one — that is §10.3's own example of adding
        day 4 to a confirmed trip without disturbing days 1 to 3."""
        locked = item(1, Kind.ACTIVITY, hour=9, where="hotel", is_locked=True)
        result = sequence_day(
            [locked, item(2, Kind.ACTIVITY, hour=14, where="beach")],
            travel_time=fixed_travel(30),
            buffers=Buffers(),
        )
        kept = next(i for i in result.items if i.item_id == 1)
        assert kept.starts_at == locked.starts_at
        assert kept.ends_at == locked.ends_at
        assert any(i.kind is Kind.TRANSFER for i in result.items)


class TestWholeTrip:
    def test_each_day_is_sequenced_independently(self) -> None:
        items = [
            item(1, Kind.ACTIVITY, hour=9, where="hotel"),
            PlannedItem(
                item_id=2,
                kind=Kind.ACTIVITY,
                title="day two",
                day_number=2,
                starts_at=at(9),
                ends_at=at(10),
                start_location="hotel",
                end_location="hotel",
            ),
        ]
        result = sequence_trip(
            items, day_numbers=[1, 2], travel_time=never_called, buffers=Buffers()
        )
        assert [i.sequence_no for i in result.items] == [1, 1]

    def test_an_empty_day_is_not_an_error(self) -> None:
        """§10.4 line 1 builds the day list from the trip dates, so a middle
        day with nothing planned is an ordinary day."""
        result = sequence_trip(
            [item(1, Kind.ACTIVITY, hour=9, where="hotel")],
            day_numbers=[1, 2, 3],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert len(result.items) == 1
        assert result.findings == ()

    def test_an_item_outside_the_trip_days_is_returned_untouched(self) -> None:
        """It is a VR-01 error, reported by the validator. Dropping it here
        would hide the thing VR-01 exists to report."""
        stray = PlannedItem(
            item_id=99,
            kind=Kind.ACTIVITY,
            title="stray",
            day_number=9,
            starts_at=at(9),
            ends_at=at(10),
        )
        result = sequence_trip(
            [item(1, Kind.ACTIVITY, hour=9, where="hotel"), stray],
            day_numbers=[1, 2],
            travel_time=never_called,
            buffers=Buffers(),
        )
        assert stray in result.items


class TestBuffers:
    def test_the_defaults_match_appendix_b(self) -> None:
        """Present so a test can build one without naming every field. The
        application layer always passes values read from `system_setting`,
        because NFR-M07 forbids a business threshold living in code."""
        buffers = Buffers()
        assert buffers.activity_minutes == 15
        assert buffers.airport_departure_minutes == 180
        assert buffers.check_in_minutes == 0

    def test_an_attraction_gets_no_buffer(self) -> None:
        """§10.4 names three buffers, and none of them is an attraction's.
        Returning the activity buffer here would be a plausible guess and
        would silently shift every attraction by fifteen minutes."""
        assert Buffers().before(Kind.ATTRACTION) == timedelta(0)

    def test_check_in_uses_its_own_buffer(self) -> None:
        assert Buffers(check_in_minutes=30).before(Kind.STAY_CHECK_IN) == timedelta(minutes=30)
