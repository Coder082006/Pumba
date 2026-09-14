"""§20.9's refund evaluation — TC-100, TC-101, TC-102, TC-103, BR-040 to BR-046.

§33.10 names "cancellation at each policy tier boundary (one second either
side)" as a critical scenario, so every tier of every seeded policy is tested
one second outside and one second inside, not only MODERATE_7D's.

The §20.9 worked example is here with ADR 0025 decision 3's figures, not the
example's own: the fee sits on top of the price (§22.1), so the provider keeps
55.00 where the example says 49.50. That difference is deliberate and recorded.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from apps.booking.domain.cancellation import (
    Party,
    RefundExceedsCapturedError,
    assert_refundable,
    evaluate,
)

D = Decimal
START = datetime(2027, 6, 20, 9, 0, tzinfo=UTC)
SECOND = timedelta(seconds=1)
RETENTION = 168

MODERATE_7D = {
    "code": "MODERATE_7D",
    "tiers": [
        {"hours_before": 168, "refund_percent": "100"},
        {"hours_before": 48, "refund_percent": "50"},
    ],
}
FLEX_48H = {"code": "FLEX_48H", "tiers": [{"hours_before": 48, "refund_percent": "100"}]}
STRICT_14D = {"code": "STRICT_14D", "tiers": [{"hours_before": 336, "refund_percent": "50"}]}
NON_REFUNDABLE = {"code": "NON_REFUNDABLE", "tiers": []}


def cancel(
    snapshot: dict[str, object],
    before: timedelta,
    *,
    party: Party = Party.TOURIST,
    gross: str = "110.00",
    fee: str = "5.50",
    tax: str = "0.00",
) -> object:
    return evaluate(
        snapshot=snapshot,
        starts_at=START,
        cancelled_at=START - before,
        gross=D(gross),
        fee=D(fee),
        tax=D(tax),
        party=party,
        fee_retention_hours=RETENTION,
    )


class TestTC100AndTC101TheBoundary:
    def test_tc_100_seven_days_and_a_second_is_a_full_refund(self) -> None:
        """TC-100: "MODERATE_7D, 7 days + 1 s before | Cancel | 100% refund"."""
        result = cancel(MODERATE_7D, timedelta(days=7) + SECOND)
        assert result.refund_percent == D(100)  # type: ignore[attr-defined]
        assert result.refund_of_price == D("110.00")  # type: ignore[attr-defined]

    def test_tc_101_a_second_inside_seven_days_is_half(self) -> None:
        """TC-101: "7 days - 1 s before | Cancel | 50% refund; service fee
        retained; provider compensation accrued"."""
        result = cancel(MODERATE_7D, timedelta(days=7) - SECOND)
        assert result.refund_percent == D(50)  # type: ignore[attr-defined]
        assert result.refund_of_price == D("55.00")  # type: ignore[attr-defined]
        assert result.platform_fee_retained == D("5.50")  # type: ignore[attr-defined]
        assert result.provider_compensation == D("55.00")  # type: ignore[attr-defined]

    def test_half_a_second_past_seven_days_is_still_past_it(self) -> None:
        """Microseconds count. Truncating them would drop this into the lower tier."""
        result = cancel(MODERATE_7D, timedelta(days=7, microseconds=500_000))
        assert result.refund_percent == D(100)  # type: ignore[attr-defined]

    def test_exactly_seven_days_is_the_lower_tier(self) -> None:
        """ADR 0025: the strict boundary, inherited from `common.cancellation`."""
        assert cancel(MODERATE_7D, timedelta(days=7)).refund_percent == D(50)  # type: ignore[attr-defined]


class TestEveryTierOneSecondEitherSide:
    """§33.10's critical scenario, for every seeded policy."""

    @pytest.mark.parametrize(
        ("snapshot", "hours", "outside", "inside"),
        [
            (MODERATE_7D, 168, D(100), D(50)),
            (MODERATE_7D, 48, D(50), D(0)),
            (FLEX_48H, 48, D(100), D(0)),
            (STRICT_14D, 336, D(50), D(0)),
        ],
        ids=["moderate-7d", "moderate-48h", "flex-48h", "strict-14d"],
    )
    def test_the_percent_changes_at_exactly_the_threshold(
        self, snapshot: dict[str, object], hours: int, outside: Decimal, inside: Decimal
    ) -> None:
        threshold = timedelta(hours=hours)
        assert cancel(snapshot, threshold + SECOND).refund_percent == outside  # type: ignore[attr-defined]
        assert cancel(snapshot, threshold - SECOND).refund_percent == inside  # type: ignore[attr-defined]

    def test_non_refundable_refunds_no_price_at_any_time(self) -> None:
        assert cancel(NON_REFUNDABLE, timedelta(days=60)).refund_of_price == D("0.00")  # type: ignore[attr-defined]

    def test_after_the_start_nothing_is_refunded(self) -> None:
        assert cancel(FLEX_48H, -timedelta(hours=1)).refund_percent == D(0)  # type: ignore[attr-defined]


class TestTC102SupplyFailure:
    """TC-102 and BR-045: "full refund regardless of tier … and the platform
    service fee is also refunded"."""

    @pytest.mark.parametrize("party", [Party.PROVIDER, Party.DRIVER, Party.PLATFORM])
    def test_everything_comes_back_inside_every_tier(self, party: Party) -> None:
        result = cancel(NON_REFUNDABLE, timedelta(hours=1), party=party, tax="11.00")
        assert result.refund_percent == D(100)  # type: ignore[attr-defined]
        assert result.refund_amount == D("126.50")  # type: ignore[attr-defined]
        assert result.platform_fee_retained == D("0.00")  # type: ignore[attr-defined]
        assert result.provider_compensation == D("0.00")  # type: ignore[attr-defined]

    def test_the_tourist_is_not_treated_as_supply(self) -> None:
        assert cancel(NON_REFUNDABLE, timedelta(hours=1)).refund_amount == D("0.00")  # type: ignore[attr-defined]


class TestTheWorkedExampleUnderADR0025:
    def test_three_days_out_on_moderate_7d(self) -> None:
        """§20.9: gross 110.00, MODERATE_7D, tourist, 3 days before.

        The example gives refund 55.00, fee 5.50 retained, provider 49.50 —
        with the fee inside the price. ADR 0025 decision 3 puts the fee on top
        (§22.1), so the tourist still gets 55.00 and the platform still keeps
        5.50, and the provider keeps 55.00. Do not "fix" this to 49.50.
        """
        result = cancel(MODERATE_7D, timedelta(days=3))
        assert result.refund_amount == D("55.00")  # type: ignore[attr-defined]
        assert result.platform_fee_retained == D("5.50")  # type: ignore[attr-defined]
        assert result.provider_compensation == D("55.00")  # type: ignore[attr-defined]


class TestTheFee:
    def test_more_than_the_retention_window_ahead_the_fee_comes_back(self) -> None:
        """§20.9: the fee is retained unless the cancellation is "> 7 days"."""
        result = cancel(MODERATE_7D, timedelta(hours=RETENTION) + SECOND)
        assert result.fee_refunded == D("5.50")  # type: ignore[attr-defined]

    def test_at_the_retention_window_the_fee_is_kept(self) -> None:
        result = cancel(MODERATE_7D, timedelta(hours=RETENTION))
        assert result.fee_refunded == D("0.00")  # type: ignore[attr-defined]

    def test_a_full_refund_tier_inside_the_window_never_goes_negative(self) -> None:
        """ADR 0025: §20.9's formula gives the provider gross - gross - fee here."""
        result = cancel(FLEX_48H, timedelta(days=3))
        assert result.refund_percent == D(100)  # type: ignore[attr-defined]
        assert result.provider_compensation == D("0.00")  # type: ignore[attr-defined]
        assert result.platform_fee_retained == D("5.50")  # type: ignore[attr-defined]


class TestTheFiguresReconcile:
    @pytest.mark.parametrize("days", [0.5, 1, 2, 3, 6, 8, 15])
    def test_nothing_is_lost_or_invented(self, days: float) -> None:
        result = cancel(MODERATE_7D, timedelta(days=days), gross="333.33", fee="16.67", tax="3.01")
        gross, fee = D("333.33"), D("16.67")
        assert result.refund_of_price + result.provider_compensation == gross  # type: ignore[attr-defined]
        assert result.fee_refunded + result.platform_fee_retained == fee  # type: ignore[attr-defined]
        assert result.refund_amount <= gross + fee + D("3.01")  # type: ignore[attr-defined]


class TestTC103BR044:
    def test_tc_103_refunding_200_of_a_110_booking_is_refused(self) -> None:
        """TC-103: "Refund 200 of a 110 booking | Submit | 422 REFUND_EXCEEDS_CAPTURED"."""
        with pytest.raises(RefundExceedsCapturedError):
            assert_refundable(D("200.00"), paid=D("110.00"), already_refunded=D("0.00"))

    def test_the_whole_payment_may_be_refunded(self) -> None:
        assert_refundable(D("110.00"), paid=D("110.00"), already_refunded=D("0.00"))

    def test_what_was_already_refunded_counts(self) -> None:
        with pytest.raises(RefundExceedsCapturedError):
            assert_refundable(D("60.00"), paid=D("110.00"), already_refunded=D("55.00"))

    def test_a_negative_refund_is_refused(self) -> None:
        with pytest.raises(RefundExceedsCapturedError):
            assert_refundable(D("-1.00"), paid=D("110.00"), already_refunded=D("0.00"))
