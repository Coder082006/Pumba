"""Catalogue display pricing and occupancy — SRS §14.2, §20.1, §24.11, BR-101,
BR-102, TC-021.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from apps.catalogue.domain.occupancy import OccupancyError, party_fits, rooms_required
from apps.catalogue.domain.pricing import (
    DateRangeError,
    group_or_per_person_total,
    nightly_average,
    per_person_total,
    stay_nights,
    stay_total,
)
from apps.common.money import CurrencyMismatchError, Money

MAX_NIGHTS = 30


def usd(amount: str) -> Money:
    return Money(Decimal(amount), "USD")


class TestStayNights:
    def test_twelfth_to_sixteenth_is_four_nights(self) -> None:
        # Check-out is exclusive: the guest sleeps on the 12th, 13th, 14th and
        # 15th. Off by one here misprices every stay in the catalogue.
        got = stay_nights(date(2027, 8, 12), date(2027, 8, 16), max_nights=MAX_NIGHTS)
        assert got == 4

    def test_one_night(self) -> None:
        assert stay_nights(date(2027, 8, 12), date(2027, 8, 13), max_nights=MAX_NIGHTS) == 1

    def test_checkout_before_checkin_is_rejected(self) -> None:
        # TC-021 → 422 INVALID_DATE_RANGE.
        with pytest.raises(DateRangeError, match="after check-in"):
            stay_nights(date(2027, 8, 16), date(2027, 8, 12), max_nights=MAX_NIGHTS)

    def test_same_day_checkout_is_rejected(self) -> None:
        with pytest.raises(DateRangeError, match="after check-in"):
            stay_nights(date(2027, 8, 12), date(2027, 8, 12), max_nights=MAX_NIGHTS)

    def test_the_maximum_stay_is_inclusive(self) -> None:
        assert stay_nights(date(2027, 8, 1), date(2027, 8, 31), max_nights=30) == 30

    def test_a_stay_beyond_the_maximum_is_rejected(self) -> None:
        # BR-101: "maximum stay is 30 nights".
        with pytest.raises(DateRangeError, match="exceeds the maximum"):
            stay_nights(date(2027, 8, 1), date(2027, 9, 1), max_nights=30)

    def test_the_maximum_is_a_parameter_not_a_constant(self) -> None:
        # stay.max_nights is an Appendix B setting row.
        assert stay_nights(date(2027, 8, 1), date(2027, 9, 1), max_nights=60) == 31

    def test_it_spans_a_month_boundary(self) -> None:
        assert stay_nights(date(2027, 8, 30), date(2027, 9, 2), max_nights=MAX_NIGHTS) == 3

    def test_it_spans_a_leap_day(self) -> None:
        assert stay_nights(date(2028, 2, 27), date(2028, 3, 1), max_nights=MAX_NIGHTS) == 3


class TestStayTotal:
    def test_it_sums_the_nightly_rates(self) -> None:
        assert stay_total([usd("100.00")] * 4) == usd("400.00")

    def test_it_honours_a_rate_override_on_one_night(self) -> None:
        # §14.2: room_availability.rate_override for that night, else base_rate.
        nightly = [usd("100.00"), usd("150.00"), usd("100.00"), usd("100.00")]
        assert stay_total(nightly) == usd("450.00")

    def test_rounding_is_applied_once_at_the_aggregate(self) -> None:
        # §20.1: "intermediate values retain full precision". Three nights at
        # 33.333 is 99.999, which rounds to 100.00 — not to 99.99, which is
        # what rounding each night first would give.
        assert stay_total([usd("33.333")] * 3) == usd("100.00")

    def test_rounding_is_half_up(self) -> None:
        assert stay_total([usd("0.005")]) == usd("0.01")

    def test_an_empty_stay_is_rejected(self) -> None:
        # A free stay rendered as "$0 total" is worse than an exception.
        with pytest.raises(DateRangeError, match="at least one night"):
            stay_total([])

    def test_mixed_currencies_are_refused_rather_than_converted(self) -> None:
        # §20.2 puts conversion at quote time. Converting here would be a
        # display-time conversion, which the SRS forbids.
        with pytest.raises(CurrencyMismatchError):
            stay_total([usd("100.00"), Money(Decimal("100.00"), "TZS")])

    def test_a_zero_decimal_currency_rounds_to_whole_units(self) -> None:
        nightly = [Money(Decimal("117000.4"), "JPY")]
        assert stay_total(nightly) == Money(Decimal("117000"), "JPY")


class TestNightlyAverage:
    def test_it_divides_the_total(self) -> None:
        assert nightly_average(usd("1240.00"), 4) == usd("310.00")

    def test_it_is_derived_from_the_total_not_recomputed(self) -> None:
        # §24.11 shows the two figures one above the other. Computing the
        # average independently lets them disagree by a rounding step, and the
        # tourist sees 3 x 33.33 = 100.00 and calls it a bug.
        nightly = [usd("33.333")] * 3
        total = stay_total(nightly)
        assert total == usd("100.00")
        assert nightly_average(total, 3) == usd("33.33")

    def test_rounding_is_half_up(self) -> None:
        assert nightly_average(usd("100.00"), 3) == usd("33.33")
        assert nightly_average(usd("101.00"), 3) == usd("33.67")

    def test_zero_nights_is_rejected(self) -> None:
        with pytest.raises(DateRangeError, match="at least 1"):
            nightly_average(usd("100.00"), 0)

    def test_it_keeps_the_currency(self) -> None:
        got = nightly_average(Money(Decimal("400"), "TZS"), 4)
        assert got.currency == "TZS"


class TestPerPersonTotal:
    def test_it_multiplies_by_the_party_size(self) -> None:
        assert per_person_total(usd("45.00"), 2) == usd("90.00")

    def test_a_single_traveller(self) -> None:
        assert per_person_total(usd("45.00"), 1) == usd("45.00")

    def test_zero_pax_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="pax must be at least 1"):
            per_person_total(usd("45.00"), 0)

    def test_rounding_is_applied_once_to_the_line(self) -> None:
        assert per_person_total(usd("33.335"), 3) == usd("100.01")


class TestGroupOrPerPerson:
    def test_the_group_price_wins_when_present(self) -> None:
        # A provider quoting for the boat is not also quoting for the seat;
        # charging both would double-charge a family.
        got = group_or_per_person_total(
            price_per_person=usd("45.00"), price_per_group=usd("200.00"), pax=6
        )
        assert got == usd("200.00")

    def test_the_per_person_price_is_used_when_there_is_no_group_price(self) -> None:
        got = group_or_per_person_total(price_per_person=usd("45.00"), price_per_group=None, pax=2)
        assert got == usd("90.00")

    def test_a_group_only_activity_ignores_party_size(self) -> None:
        got = group_or_per_person_total(price_per_person=None, price_per_group=usd("200.00"), pax=2)
        assert got == usd("200.00")

    def test_an_activity_with_neither_price_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="per-person or a per-group price"):
            group_or_per_person_total(price_per_person=None, price_per_group=None, pax=2)

    def test_zero_pax_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="pax must be at least 1"):
            group_or_per_person_total(price_per_person=usd("1.00"), price_per_group=None, pax=0)


class TestPartyFits:
    def test_a_party_within_both_allowances_fits(self) -> None:
        assert party_fits(max_adults=2, max_children=2, adults=2, children=2) is True

    def test_a_smaller_party_fits(self) -> None:
        assert party_fits(max_adults=2, max_children=2, adults=1, children=0) is True

    def test_children_do_not_overflow_into_adult_capacity(self) -> None:
        # The naive `adults + children <= max_adults + max_children` accepts
        # this, the property refuses the guests at the desk, and the platform
        # has already taken the money.
        assert party_fits(max_adults=2, max_children=2, adults=4, children=0) is False

    def test_adults_do_not_overflow_into_child_capacity(self) -> None:
        assert party_fits(max_adults=2, max_children=2, adults=3, children=1) is False

    def test_too_many_children_does_not_fit(self) -> None:
        assert party_fits(max_adults=2, max_children=1, adults=2, children=2) is False

    def test_a_party_with_no_adults_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one adult"):
            party_fits(max_adults=2, max_children=2, adults=0, children=2)

    def test_negative_children_are_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            party_fits(max_adults=2, max_children=2, adults=1, children=-1)

    def test_a_room_type_holding_no_adults_is_rejected(self) -> None:
        with pytest.raises(OccupancyError, match="at least one adult"):
            party_fits(max_adults=0, max_children=2, adults=1, children=0)

    def test_an_adults_only_room_type_is_valid(self) -> None:
        assert party_fits(max_adults=2, max_children=0, adults=2, children=0) is True
        assert party_fits(max_adults=2, max_children=0, adults=2, children=1) is False


class TestRoomsRequired:
    def test_a_fitting_party_needs_one_room(self) -> None:
        assert rooms_required(max_adults=2, max_children=2, adults=2, children=2) == 1

    def test_excess_adults_need_a_second_room(self) -> None:
        # BR-102: "excess parties must book multiple rooms".
        assert rooms_required(max_adults=2, max_children=2, adults=4, children=0) == 2

    def test_an_odd_party_rounds_up(self) -> None:
        assert rooms_required(max_adults=2, max_children=2, adults=5, children=0) == 3

    def test_children_can_drive_the_requirement(self) -> None:
        # 2 adults fit in one room; 6 children need three.
        assert rooms_required(max_adults=2, max_children=2, adults=2, children=6) == 3

    def test_the_larger_of_the_two_requirements_wins(self) -> None:
        assert rooms_required(max_adults=2, max_children=2, adults=6, children=2) == 3

    def test_it_never_returns_zero(self) -> None:
        assert rooms_required(max_adults=4, max_children=4, adults=1, children=0) == 1

    def test_a_room_type_that_takes_no_children_refuses_a_family(self) -> None:
        # Returning a large integer would let the caller price a stay the
        # property will refuse at the desk.
        with pytest.raises(OccupancyError, match="does not accept children"):
            rooms_required(max_adults=2, max_children=0, adults=2, children=1)

    def test_an_adults_only_room_type_still_serves_adults(self) -> None:
        assert rooms_required(max_adults=2, max_children=0, adults=4, children=0) == 2

    def test_a_party_with_no_adults_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one adult"):
            rooms_required(max_adults=2, max_children=2, adults=0, children=2)

    def test_it_agrees_with_party_fits_on_the_single_room_case(self) -> None:
        for adults in range(1, 6):
            for children in range(0, 6):
                fits = party_fits(max_adults=2, max_children=2, adults=adults, children=children)
                needed = rooms_required(
                    max_adults=2, max_children=2, adults=adults, children=children
                )
                assert fits is (needed == 1)
