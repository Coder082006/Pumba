"""Whether a departure can be sold — SRS §16.3, §16.6, BR-022, BR-034.

Pure-domain tests: no database, no clock. Every boundary here is asserted
exactly rather than approximately, which is only possible because `now` and
`booking_cutoff_hours` are arguments — a module that read a clock could be
tested a second either side of a cut-off and never on it.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.inventory.domain.capacity import (
    Departure,
    DepartureState,
    PartyRules,
    Unbookable,
    is_bookable,
    sellable,
    why_not_bookable,
)
from apps.inventory.models import DepartureStatus

DEPARTS = dt.datetime(2027, 8, 12, 8, 30, tzinfo=dt.UTC)
RULES = PartyRules(min_pax=1, max_pax=12, booking_cutoff_hours=24)
#: Comfortably inside the cut-off, so a test that fails is failing about the
#: thing it names.
EARLY = DEPARTS - dt.timedelta(days=7)


def _departure(**overrides: object) -> Departure:
    values: dict[str, object] = {
        "departs_at": DEPARTS,
        "capacity_total": 12,
        "capacity_held": 0,
        "capacity_sold": 0,
        "status": DepartureState.OPEN,
    }
    values.update(overrides)
    return Departure(**values)  # type: ignore[arg-type]


class TestSellable:
    """§16.3's expression."""

    def test_an_empty_departure_sells_all_of_it(self) -> None:
        assert sellable(_departure()) == 12

    def test_held_and_sold_both_count_against_it(self) -> None:
        """Held seats are as unavailable as sold ones. A `sellable` that
        subtracted only `capacity_sold` would resell every seat somebody was
        halfway through paying for."""
        assert sellable(_departure(capacity_held=2, capacity_sold=6)) == 4

    def test_a_full_departure_sells_nothing(self) -> None:
        assert sellable(_departure(capacity_held=5, capacity_sold=7)) == 0

    @pytest.mark.parametrize(
        "status", [DepartureState.FULL, DepartureState.CLOSED, DepartureState.CANCELLED]
    )
    def test_only_an_open_departure_sells_at_all(self, status: DepartureState) -> None:
        """Empty seats on a cancelled departure are not capacity."""
        assert sellable(_departure(status=status)) == 0

    def test_a_negative_result_is_clamped(self) -> None:
        """The database CHECK makes this state unreachable, so reaching it
        would mean the constraint had been dropped or bypassed. Returning a
        negative would let it propagate into a subtraction somewhere and turn
        a detectable fault into a plausible number."""
        assert sellable(_departure(capacity_total=4, capacity_held=3, capacity_sold=3)) == 0


class TestTheHappyPath:
    def test_a_party_that_fits_may_book(self) -> None:
        assert is_bookable(_departure(), RULES, pax=4, now=EARLY)

    def test_no_reason_is_given_when_there_is_none(self) -> None:
        assert why_not_bookable(_departure(), RULES, pax=4, now=EARLY) is None

    def test_the_last_seats_may_be_taken(self) -> None:
        """`sellable >= pax`, not `> pax`. An off-by-one here strands the final
        seat on every departure in the catalogue."""
        assert is_bookable(_departure(capacity_sold=8), RULES, pax=4, now=EARLY)


class TestCapacity:
    """BR-022: an activity may never be booked beyond its departure capacity."""

    def test_a_party_larger_than_what_is_left_is_refused(self) -> None:
        reason = why_not_bookable(_departure(capacity_sold=10), RULES, pax=3, now=EARLY)
        assert reason is Unbookable.SOLD_OUT

    def test_held_seats_block_a_second_party(self) -> None:
        """The point of a hold. Somebody else's twenty-minute window is not
        capacity anybody may sell."""
        reason = why_not_bookable(_departure(capacity_held=10), RULES, pax=3, now=EARLY)
        assert reason is Unbookable.SOLD_OUT

    def test_a_full_departure_reads_as_sold_out_not_as_a_status(self) -> None:
        """FULL is the same fact as the arithmetic, once as a status the
        provider's tooling set. Two words for one situation would leave a
        tourist wondering what the difference was."""
        reason = why_not_bookable(_departure(status=DepartureState.FULL), RULES, pax=1, now=EARLY)
        assert reason is Unbookable.SOLD_OUT


class TestTheDepartureIsNotRunning:
    def test_a_cancelled_departure_says_so(self) -> None:
        reason = why_not_bookable(
            _departure(status=DepartureState.CANCELLED), RULES, pax=1, now=EARLY
        )
        assert reason is Unbookable.CANCELLED

    def test_a_closed_departure_says_so(self) -> None:
        """§16.2: a provider may close a departure without cancelling it — it
        is still running, they have just stopped selling it."""
        reason = why_not_bookable(_departure(status=DepartureState.CLOSED), RULES, pax=1, now=EARLY)
        assert reason is Unbookable.CLOSED


class TestPartyBounds:
    def test_a_party_below_the_minimum_is_refused(self) -> None:
        rules = PartyRules(min_pax=4, max_pax=12, booking_cutoff_hours=24)
        assert why_not_bookable(_departure(), rules, pax=2, now=EARLY) is Unbookable.PARTY_TOO_SMALL

    def test_a_party_above_the_maximum_is_refused(self) -> None:
        assert (
            why_not_bookable(_departure(), RULES, pax=13, now=EARLY) is Unbookable.PARTY_TOO_LARGE
        )

    def test_the_bounds_are_inclusive(self) -> None:
        rules = PartyRules(min_pax=4, max_pax=8, booking_cutoff_hours=24)
        assert is_bookable(_departure(), rules, pax=4, now=EARLY)
        assert is_bookable(_departure(), rules, pax=8, now=EARLY)

    def test_max_pax_binds_even_where_the_seats_exist(self) -> None:
        """§16.1's `max_pax` is about how many the activity can take at once —
        a guide, a boat, a licence — not about how many seats are free."""
        rules = PartyRules(min_pax=1, max_pax=6, booking_cutoff_hours=24)
        assert why_not_bookable(_departure(), rules, pax=8, now=EARLY) is Unbookable.PARTY_TOO_LARGE


class TestTheBookingCutoff:
    """§16.6 / BR-034: `booking_cutoff_hours` prevents last-minute bookings the
    provider cannot staff."""

    def test_a_booking_at_the_cutoff_is_still_in_time(self) -> None:
        """Inclusive: a cut-off is the last moment that works, not the first
        that does not."""
        latest = DEPARTS - dt.timedelta(hours=24)
        assert is_bookable(_departure(), RULES, pax=2, now=latest)

    def test_a_second_after_the_cutoff_is_too_late(self) -> None:
        just_late = DEPARTS - dt.timedelta(hours=24) + dt.timedelta(seconds=1)
        assert why_not_bookable(_departure(), RULES, pax=2, now=just_late) is Unbookable.PAST_CUTOFF

    def test_a_departed_departure_is_too_late(self) -> None:
        after = DEPARTS + dt.timedelta(hours=1)
        assert why_not_bookable(_departure(), RULES, pax=2, now=after) is Unbookable.PAST_CUTOFF

    def test_a_zero_hour_cutoff_permits_booking_up_to_departure(self) -> None:
        """Not every activity needs notice, and zero must mean zero rather
        than falling through to a default."""
        rules = PartyRules(min_pax=1, max_pax=12, booking_cutoff_hours=0)
        assert is_bookable(_departure(), rules, pax=2, now=DEPARTS)


class TestWhichReasonIsReported:
    """The order matters, because the reason is advice."""

    def test_cancelled_beats_everything(self) -> None:
        """A cancelled, sold-out, past-cut-off departure should say CANCELLED.
        The other two invite the tourist to try a smaller party or hurry up,
        and neither would help."""
        reason = why_not_bookable(
            _departure(status=DepartureState.CANCELLED, capacity_sold=12),
            RULES,
            pax=99,
            now=DEPARTS,
        )
        assert reason is Unbookable.CANCELLED

    def test_a_party_that_never_fits_is_told_so_rather_than_sold_out(self) -> None:
        """ "Try another date" would be false advice: a party of twelve on a
        six-seat activity is refused on every date there is."""
        rules = PartyRules(min_pax=1, max_pax=6, booking_cutoff_hours=24)
        reason = why_not_bookable(_departure(capacity_sold=12), rules, pax=8, now=EARLY)
        assert reason is Unbookable.PARTY_TOO_LARGE


class TestItAgreesWithTheColumn:
    def test_the_statuses_match_the_model_s_choices(self) -> None:
        """Written twice because the domain may not import Django."""
        assert {s.value for s in DepartureState} == set(DepartureStatus.values)
