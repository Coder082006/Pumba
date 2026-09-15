"""The words on a voucher — ADR 0026 decision 2."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.booking.domain.voucher import money_text, party_text, policy_summary, when_text


class TestPolicySummary:
    def test_a_two_tier_policy(self) -> None:
        tiers = [
            {"hours_before": 168, "refund_percent": "100"},
            {"hours_before": 48, "refund_percent": "50"},
        ]
        assert policy_summary(tiers) == (
            "Full refund more than 168 hours before the start; "
            "50% refund more than 48 hours before; no refund after that."
        )

    def test_a_single_partial_tier(self) -> None:
        assert policy_summary([{"hours_before": 336, "refund_percent": "50"}]) == (
            "50% refund more than 336 hours before the start; no refund after that."
        )

    def test_a_fractional_percent_is_written_exactly(self) -> None:
        tiers = [{"hours_before": 24, "refund_percent": "12.5"}]
        assert policy_summary(tiers).startswith("12.5% refund")

    def test_no_tiers_is_non_refundable(self) -> None:
        assert policy_summary([]) == "Non-refundable: no refund at any time."


class TestPartyText:
    @pytest.mark.parametrize(
        ("adults", "children", "expected"),
        [
            (2, 0, "2 adults"),
            (1, 0, "1 adult"),
            (2, 1, "2 adults, 1 child"),
            (1, 3, "1 adult, 3 children"),
            (0, 0, "No travellers"),
        ],
    )
    def test_it_reads_naturally(self, adults: int, children: int, expected: str) -> None:
        assert party_text(adults=adults, children=children) == expected


class TestMoneyText:
    def test_grouped_to_two_places_with_the_currency(self) -> None:
        assert money_text(Decimal("76000"), "TZS") == "76,000.00 TZS"
        assert money_text(Decimal("110.5"), "USD") == "110.50 USD"


class TestWhenText:
    def test_it_is_the_destination_s_own_time_and_names_the_zone(self) -> None:
        """16:45 in Zanzibar is 13:45 UTC."""
        instant = datetime(2027, 9, 14, 13, 45, tzinfo=UTC)
        assert when_text(instant, "Africa/Dar_es_Salaam") == (
            "Tue 14 Sep 2027, 16:45 (Africa/Dar_es_Salaam)"
        )
