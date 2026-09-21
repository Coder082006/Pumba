"""§22.2's resolution and calculation, as a table — pure, no database.

Two things decide what an operator is paid: which rule wins, and what it works
out to. Both are wrong in ways nobody notices — a listing rule quietly losing
to a provider rule takes money from one operator every booking, and a clamp
applied before rounding takes a cent from all of them.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.finance.domain.commission import Method, Rule, Scope, compute, matches, select


def rule(scope: Scope, **fields: object) -> Rule:
    defaults: dict[str, object] = {
        "id": fields.pop("id", 1),
        "scope": scope,
        "method": Method.PERCENT,
        "percent": Decimal("15.00"),
    }
    defaults.update(fields)
    return Rule(**defaults)  # type: ignore[arg-type]


class TestWhichRuleWins:
    def test_a_listing_rule_beats_a_provider_rule(self) -> None:
        """§22.2's order is scope first. A negotiated rate for one hotel is the
        whole reason the LISTING scope exists."""
        rules = [
            rule(Scope.PROVIDER, id=1, provider_id=7, percent=Decimal("20.00")),
            rule(Scope.LISTING, id=2, listing_id=99, percent=Decimal("10.00")),
        ]

        chosen = select(rules, provider_id=7, booking_type="ACTIVITY", listing_id=99)

        assert chosen is not None and chosen.id == 2

    def test_scope_beats_priority(self) -> None:
        """A high priority number on a broader rule must not overtake a
        narrower one, or the order §22.2 states would be advisory."""
        rules = [
            rule(Scope.PROVIDER, id=1, provider_id=7, priority=100),
            rule(Scope.LISTING, id=2, listing_id=99, priority=0),
        ]

        chosen = select(rules, provider_id=7, booking_type="ACTIVITY", listing_id=99)

        assert chosen is not None and chosen.id == 2

    def test_priority_breaks_a_tie_inside_one_scope(self) -> None:
        """Two rules for one provider — a seasonal one and a standing one."""
        rules = [
            rule(Scope.PROVIDER, id=1, provider_id=7, priority=0, percent=Decimal("20.00")),
            rule(Scope.PROVIDER, id=2, provider_id=7, priority=5, percent=Decimal("12.00")),
        ]

        chosen = select(rules, provider_id=7, booking_type="ACTIVITY", listing_id=None)

        assert chosen is not None and chosen.percent == Decimal("12.00")

    def test_the_lowest_id_breaks_a_complete_tie(self) -> None:
        """Not in the SRS, and deliberate: two rules tying on scope and
        priority would otherwise resolve differently depending on the order the
        database happened to return them, and a commission nobody can
        reproduce is a commission nobody can dispute."""
        rules = [
            rule(Scope.GLOBAL, id=9, percent=Decimal("20.00")),
            rule(Scope.GLOBAL, id=4, percent=Decimal("15.00")),
        ]

        chosen = select(rules, provider_id=7, booking_type="ACTIVITY", listing_id=None)

        assert chosen is not None and chosen.id == 4

    def test_a_type_rule_matches_its_type_and_no_other(self) -> None:
        rules = [rule(Scope.TYPE, booking_type="ACTIVITY")]

        assert select(rules, provider_id=7, booking_type="ACTIVITY", listing_id=None) is not None
        assert select(rules, provider_id=7, booking_type="TRANSFER", listing_id=None) is None

    def test_a_listing_rule_cannot_match_something_with_no_listing(self) -> None:
        """A transfer has none, and two `None`s comparing equal would make a
        rule for one hotel apply to every minibus."""
        rules = [rule(Scope.LISTING, listing_id=None)]

        assert select(rules, provider_id=7, booking_type="TRANSFER", listing_id=None) is None

    def test_a_global_rule_is_the_fallback(self) -> None:
        rules = [rule(Scope.GLOBAL)]

        assert select(rules, provider_id=7, booking_type="TRANSFER", listing_id=None) is not None

    def test_nothing_matching_is_none_rather_than_a_guess(self) -> None:
        assert select([], provider_id=7, booking_type="ACTIVITY", listing_id=None) is None


class TestWhenARuleApplies:
    def test_an_inactive_rule_never_matches(self) -> None:
        assert not matches(
            rule(Scope.GLOBAL, is_active=False),
            provider_id=7,
            booking_type="ACTIVITY",
            listing_id=None,
        )

    def test_a_rule_that_has_not_started_does_not_match(self) -> None:
        assert not matches(
            rule(Scope.GLOBAL, valid_from=dt.date(2027, 6, 1)),
            provider_id=7,
            booking_type="ACTIVITY",
            listing_id=None,
            on=dt.date(2027, 5, 31),
        )

    def test_a_rule_that_has_ended_does_not_match(self) -> None:
        assert not matches(
            rule(Scope.GLOBAL, valid_to=dt.date(2027, 6, 1)),
            provider_id=7,
            booking_type="ACTIVITY",
            listing_id=None,
            on=dt.date(2027, 6, 2),
        )

    def test_a_rule_matches_on_its_last_day(self) -> None:
        """Inclusive, like every other window in this system: a season that
        ends on the 30th includes the 30th."""
        assert matches(
            rule(Scope.GLOBAL, valid_to=dt.date(2027, 6, 1)),
            provider_id=7,
            booking_type="ACTIVITY",
            listing_id=None,
            on=dt.date(2027, 6, 1),
        )


class TestWhatItCharges:
    def test_a_percentage_of_the_gross(self) -> None:
        """§22.1's worked example: 15% of 110.00."""
        amount, percent = compute(rule(Scope.GLOBAL), gross=Decimal("110.00"))

        assert amount == Decimal("16.50")
        assert percent == Decimal("15.00")

    def test_a_flat_fee_reports_the_percentage_it_came_to(self) -> None:
        """BR-070 snapshots a percentage onto the booking, so a flat rule still
        has to say what rate it amounted to or the snapshot is unreadable."""
        amount, percent = compute(
            rule(Scope.GLOBAL, method=Method.FLAT, flat_amount=Decimal("22.00"), percent=None),
            gross=Decimal("110.00"),
        )

        assert amount == Decimal("22.00")
        assert percent == Decimal("20.00")

    def test_a_tiered_rule_reads_the_provider_volume(self) -> None:
        tiers = ((Decimal("0"), Decimal("18")), (Decimal("5000"), Decimal("12")))
        banded = rule(Scope.GLOBAL, method=Method.TIERED, percent=None, tiers=tiers)

        small, _ = compute(banded, gross=Decimal("100.00"), monthly_volume=Decimal("100"))
        large, _ = compute(banded, gross=Decimal("100.00"), monthly_volume=Decimal("9000"))

        assert small == Decimal("18.00")
        # The busier operator keeps more of each sale, which is what a volume
        # band is for.
        assert large == Decimal("12.00")

    def test_a_tiered_rule_with_no_bands_charges_nothing(self) -> None:
        """Visible in a statement immediately. Guessing a default would not be."""
        amount, _ = compute(
            rule(Scope.GLOBAL, method=Method.TIERED, percent=None), gross=Decimal("100.00")
        )

        assert amount == Decimal("0.00")

    def test_a_minimum_lifts_a_small_commission(self) -> None:
        amount, _ = compute(
            rule(Scope.GLOBAL, percent=Decimal("5.00"), min_fee=Decimal("10.00")),
            gross=Decimal("100.00"),
        )

        assert amount == Decimal("10.00")

    def test_a_maximum_caps_a_large_one(self) -> None:
        amount, _ = compute(
            rule(Scope.GLOBAL, percent=Decimal("15.00"), max_fee=Decimal("50.00")),
            gross=Decimal("1000.00"),
        )

        assert amount == Decimal("50.00")

    def test_a_commission_never_exceeds_the_sale(self) -> None:
        """A minimum fee larger than the booking would make the operator owe
        the platform for having worked."""
        amount, percent = compute(
            rule(Scope.GLOBAL, method=Method.FLAT, flat_amount=Decimal("500.00"), percent=None),
            gross=Decimal("100.00"),
        )

        assert amount == Decimal("100.00")
        assert percent == Decimal("100.00")

    def test_it_rounds_once_half_up(self) -> None:
        """§18.5. 15% of 33.33 is 4.9995, which is 5.00 and not 4.99."""
        amount, _ = compute(rule(Scope.GLOBAL), gross=Decimal("33.33"))

        assert amount == Decimal("5.00")

    def test_a_free_booking_has_no_rate_to_report(self) -> None:
        """Dividing by the gross to express a percentage needs a gross."""
        amount, percent = compute(rule(Scope.GLOBAL), gross=Decimal("0.00"))

        assert amount == Decimal("0.00")
        assert percent == Decimal("0")


@pytest.mark.parametrize(
    ("scope", "expected"),
    [(Scope.LISTING, 0), (Scope.PROVIDER, 1), (Scope.TYPE, 2), (Scope.GLOBAL, 3)],
)
def test_the_order_is_the_one_section_222_prints(scope: Scope, expected: int) -> None:
    """Transcribed rather than inferred, so it can be checked line by line."""
    from apps.finance.domain.commission import SCOPE_ORDER

    assert SCOPE_ORDER.index(scope) == expected
