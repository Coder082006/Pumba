"""Sharing a trip's fee and tax across its bookings — ADR 0025 decision 3.

The property that matters is the sum. A share rounded independently would leave
a cent in no account; these tests assert it never does, including on the inputs
built to make independent rounding fail.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.booking.domain.allocation import AllocationError, allocate

D = Decimal


class TestTheSharesAlwaysSumToTheTotal:
    @pytest.mark.parametrize(
        ("total", "weights"),
        [
            (D("10.00"), [D("1"), D("1"), D("1")]),
            (D("0.01"), [D("1"), D("1")]),
            (D("9500.00"), [D("190000.00"), D("111000.00"), D("76000.00")]),
            (D("5.55"), [D("33.33"), D("33.33"), D("33.34")]),
            (D("1234.57"), [D("1"), D("2"), D("3"), D("4"), D("5"), D("6"), D("7")]),
        ],
    )
    def test_exactly(self, total: Decimal, weights: list[Decimal]) -> None:
        shares = allocate(total, weights)
        assert sum(shares, D(0)) == total
        assert all(share.as_tuple().exponent == -2 for share in shares)

    def test_independent_rounding_would_have_lost_a_cent(self) -> None:
        """Three equal parts of ten: 3.33 each rounds to 9.99. The leftover cent
        goes to one of them."""
        assert allocate(D("10.00"), [D("1")] * 3) == (D("3.34"), D("3.33"), D("3.33"))


class TestProRata:
    def test_a_share_follows_its_weight(self) -> None:
        assert allocate(D("15.00"), [D("100"), D("200")]) == (D("5.00"), D("10.00"))

    def test_the_largest_remainder_gets_the_cent(self) -> None:
        """2/3 of 0.10 is 0.0666…, 1/3 is 0.0333…; the larger remainder wins."""
        assert allocate(D("0.10"), [D("2"), D("1")]) == (D("0.07"), D("0.03"))

    def test_a_zero_weighted_component_gets_nothing(self) -> None:
        assert allocate(D("5.00"), [D("100"), D("0")]) == (D("5.00"), D("0.00"))

    def test_all_free_components_share_evenly(self) -> None:
        assert allocate(D("1.00"), [D("0"), D("0")]) == (D("0.50"), D("0.50"))

    def test_nothing_to_share_is_all_zeros(self) -> None:
        assert allocate(D("0.00"), [D("5"), D("7")]) == (D("0.00"), D("0.00"))

    def test_no_components_and_nothing_to_share(self) -> None:
        assert allocate(D("0.00"), []) == ()


class TestRefusals:
    def test_a_negative_total(self) -> None:
        with pytest.raises(AllocationError):
            allocate(D("-1.00"), [D("1")])

    def test_a_negative_weight(self) -> None:
        with pytest.raises(AllocationError):
            allocate(D("1.00"), [D("-1")])

    def test_sub_cent_precision(self) -> None:
        with pytest.raises(AllocationError):
            allocate(D("1.001"), [D("1")])

    def test_money_with_nobody_to_own_it(self) -> None:
        with pytest.raises(AllocationError):
            allocate(D("1.00"), [])
