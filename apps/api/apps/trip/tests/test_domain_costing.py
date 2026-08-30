"""Cost computation — SRS §10.7, §18.3, §18.5.

The highest-consequence arithmetic in the phase. An error here does not crash;
it misprices a trip, and surfaces much later as a reconciliation exception
against money that has already moved.

Two properties get the most attention because both fail silently:

* **Rounding happens exactly twice** — once per line, once per aggregate.
  Rounding in between drifts by cents that nobody notices until a ledger does.
* **`total = subtotal + fee + tax` holds exactly.** §7.5.10 has a database
  CHECK asserting it, so a computation that is a cent out does not display
  wrongly — it fails to write at all.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.common.money import Money
from apps.trip.domain.costing import (
    PricedItem,
    PricingError,
    TaxRule,
    compute_cost,
    price_item,
)
from apps.trip.domain.sequencing import Kind

USD = "USD"
FEE_RATE = Decimal("0.05")


def usd(amount: str) -> Money:
    return Money(Decimal(amount), USD)


def priced(
    item_id: int = 1,
    kind: Kind = Kind.ACTIVITY,
    *,
    unit: str | None = "95.00",
    quantity: int = 1,
    group: str | None = None,
) -> PricedItem:
    return PricedItem(
        item_id=item_id,
        kind=kind,
        title=f"item-{item_id}",
        unit_price=usd(unit) if unit is not None else None,
        quantity=quantity,
        group_price=usd(group) if group is not None else None,
    )


class TestLineTotals:
    def test_per_person_times_quantity(self) -> None:
        assert price_item(priced(unit="95.00", quantity=2)) == usd("190.00")

    def test_a_group_price_is_not_multiplied(self) -> None:
        """§7.5.9's `price_per_group` is the price of the whole thing.
        Multiplying it by the party size is the mistake this branch exists to
        prevent, and it would overcharge rather than undercharge."""
        assert price_item(priced(unit=None, quantity=6, group="400.00")) == usd("400.00")

    def test_a_group_price_wins_over_a_unit_price(self) -> None:
        assert price_item(priced(unit="95.00", quantity=6, group="400.00")) == usd("400.00")

    def test_an_unpriced_item_has_no_line(self) -> None:
        """`None`, not zero. §24.16 lists a priced line at 0.00 and omits an
        unpriced one entirely, so the difference reaches the screen."""
        assert price_item(priced(kind=Kind.FREE_TIME, unit=None)) is None

    def test_a_stay_anchor_has_no_line(self) -> None:
        assert price_item(priced(kind=Kind.STAY_CHECK_IN, unit=None)) is None

    def test_a_stay_anchor_carrying_a_price_is_refused(self) -> None:
        """ADR 0013: no room, no rate, no booking behind it. Returning zero
        would look identical on screen and be a different claim — and the
        database constraint would reject the row anyway, further from here."""
        with pytest.raises(PricingError, match="stay anchor"):
            price_item(priced(kind=Kind.STAY_CHECK_IN, unit="180.00"))

    def test_a_quantity_below_one_is_refused(self) -> None:
        with pytest.raises(PricingError, match="quantity"):
            priced(quantity=0)

    def test_the_line_is_rounded_once(self) -> None:
        """§18.5: ROUND_HALF_UP, and half rounds up rather than to even.
        Python's own default is banker's rounding, so this is the assertion
        that the specified mode is actually in force."""
        assert price_item(priced(unit="33.335", quantity=1)) == usd("33.34")

    def test_a_repeating_third_rounds_half_up(self) -> None:
        assert price_item(priced(unit="10.005", quantity=3)) == usd("30.02")


class TestSubtotalFeeAndTotal:
    def test_the_worked_shape_of_10_7(self) -> None:
        items = [priced(1, unit="95.00", quantity=2), priced(2, unit="38.00")]
        cost = compute_cost(items, currency=USD, platform_fee_rate=FEE_RATE)
        assert cost.subtotal == usd("228.00")
        assert cost.fee == usd("11.40")
        assert cost.tax == usd("0.00")
        assert cost.total == usd("239.40")

    def test_the_total_is_exactly_its_parts(self) -> None:
        """§7.5.10's CHECK constraint. A computation a cent out does not
        display wrongly — it fails to write at all."""
        items = [priced(n, unit="33.33", quantity=n) for n in range(1, 8)]
        cost = compute_cost(items, currency=USD, platform_fee_rate=Decimal("0.075"))
        assert cost.total == cost.subtotal + cost.fee + cost.tax

    def test_unpriced_items_do_not_appear_as_lines(self) -> None:
        items = [priced(1, unit="95.00"), priced(2, kind=Kind.STAY_CHECK_IN, unit=None)]
        cost = compute_cost(items, currency=USD, platform_fee_rate=FEE_RATE)
        assert [item_id for item_id, _ in cost.lines] == [1]
        assert cost.line_for(1) == usd("95.00")
        assert cost.line_for(2) is None
        assert cost.line_for(999) is None

    def test_an_empty_trip_costs_nothing(self) -> None:
        cost = compute_cost([], currency=USD, platform_fee_rate=FEE_RATE)
        assert cost.subtotal.is_zero and cost.fee.is_zero and cost.total.is_zero

    def test_a_zero_fee_rate_is_allowed(self) -> None:
        """A market may run without a service fee; NFR-M07 makes that an
        administrator's decision rather than a deployment."""
        cost = compute_cost([priced(unit="100.00")], currency=USD, platform_fee_rate=Decimal(0))
        assert cost.fee.is_zero
        assert cost.total == usd("100.00")

    def test_the_fee_is_rounded_once_at_the_aggregate(self) -> None:
        """Not per line. Five lines of 33.33 at 5% is 8.33 on the subtotal of
        166.65; rounding each line's share first would give 8.35."""
        items = [priced(n, unit="33.33") for n in range(1, 6)]
        cost = compute_cost(items, currency=USD, platform_fee_rate=FEE_RATE)
        assert cost.subtotal == usd("166.65")
        assert cost.fee == usd("8.33")


class TestFloatIsProhibited:
    """§18.5, and brief rule 6. Never a float, not even once."""

    def test_a_float_fee_rate_is_refused(self) -> None:
        with pytest.raises(PricingError, match="float"):
            compute_cost([], currency=USD, platform_fee_rate=0.05)  # type: ignore[arg-type]

    def test_a_float_tax_rate_is_refused(self) -> None:
        with pytest.raises(PricingError, match="float"):
            TaxRule(tax_code="VAT", rate_percent=18.0)  # type: ignore[arg-type]

    def test_a_negative_fee_rate_is_refused(self) -> None:
        with pytest.raises(PricingError, match="negative"):
            compute_cost([], currency=USD, platform_fee_rate=Decimal("-0.05"))

    def test_a_negative_tax_rate_is_refused(self) -> None:
        with pytest.raises(PricingError, match="negative"):
            TaxRule(tax_code="VAT", rate_percent=Decimal("-1"))


class TestTax:
    """§18.3's two formulas.

    No `tax_rule` table exists in Phase 4 and §18.3 says Tanzania's treatment
    "must be confirmed with a tax adviser before launch", so nothing supplies
    rules yet. The arithmetic is built and tested now so that the day the rows
    arrive, the only new thing is where the rates come from.
    """

    def test_no_rules_means_no_tax(self) -> None:
        cost = compute_cost([priced(unit="100.00")], currency=USD, platform_fee_rate=FEE_RATE)
        assert cost.tax.is_zero

    def test_an_exclusive_rate_adds_to_the_total(self) -> None:
        """`tax = round(line_total * rate, 2)`; `total = line_total + tax`."""
        rule = TaxRule(tax_code="VAT", rate_percent=Decimal("18"))
        cost = compute_cost(
            [priced(unit="100.00")],
            currency=USD,
            platform_fee_rate=Decimal(0),
            tax_rules=[rule],
        )
        assert cost.tax == usd("18.00")
        assert cost.total == usd("118.00")

    def test_an_inclusive_rate_identifies_tax_already_in_the_price(self) -> None:
        """`tax = round(line_total * rate / (1 + rate), 2)`.

        The formula that gets written wrong. 18% inclusive of 118.00 is 18.00,
        not 21.24 — the tax was always inside the price, and this says how
        much of it was.
        """
        rule = TaxRule(tax_code="VAT", rate_percent=Decimal("18"), is_inclusive=True)
        assert rule.tax_on(usd("118.00")) == usd("18.00")

    def test_several_rules_accumulate(self) -> None:
        """§18.3 allows an infrastructure levy alongside VAT."""
        rules = [
            TaxRule(tax_code="VAT", rate_percent=Decimal("18")),
            TaxRule(tax_code="LEVY", rate_percent=Decimal("1.5")),
        ]
        cost = compute_cost(
            [priced(unit="200.00")],
            currency=USD,
            platform_fee_rate=Decimal(0),
            tax_rules=rules,
        )
        assert cost.tax == usd("39.00")

    def test_tax_applies_per_line_not_to_the_subtotal(self) -> None:
        """§10.7: "tax := sum(applicable tax rules)", and §18.3's rules are
        per service type. Applying a rate to the subtotal instead would give
        the same answer today and a different one the moment two lines carry
        different rates."""
        rule = TaxRule(tax_code="VAT", rate_percent=Decimal("18"))
        cost = compute_cost(
            [priced(1, unit="0.03"), priced(2, unit="0.03")],
            currency=USD,
            platform_fee_rate=Decimal(0),
            tax_rules=[rule],
        )
        # 0.03 * 0.18 = 0.0054, rounding to 0.01 per line, twice.
        assert cost.tax == usd("0.02")


class TestCurrency:
    def test_a_foreign_line_is_refused(self) -> None:
        """VR-10 reports this as an error; reaching here means something
        bypassed validation. §18.4 freezes an FX rate at `priced_at`, and this
        module has no rate and no business inventing one."""
        item = PricedItem(
            item_id=1, kind=Kind.ACTIVITY, title="x", unit_price=Money(Decimal("10.00"), "EUR")
        )
        with pytest.raises(PricingError, match="VR-10"):
            compute_cost([item], currency=USD, platform_fee_rate=FEE_RATE)

    def test_every_figure_carries_the_trip_currency(self) -> None:
        cost = compute_cost([priced(unit="10.00")], currency=USD, platform_fee_rate=FEE_RATE)
        assert {cost.subtotal.currency, cost.fee.currency, cost.tax.currency} == {USD}
