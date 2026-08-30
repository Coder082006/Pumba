"""Cost computation — SRS §10.7, §18.3, §18.5.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§10.7, as amended in v1.2::

    FOR each item:
      line_total := unit_price * quantity   (STAY: none - an anchor has no
                                             price, ADR 0013)
                                            (ACTIVITY: per-person * pax, or
                                             group price)
                                            (TRANSFER: tariff result, §12.4)
    subtotal := sum(line_total)
    fee      := round(subtotal * platform_fee_rate, 2)   # system_setting
    tax      := sum(applicable tax rules)                # §18.3
    total    := subtotal + fee + tax

**Rounding happens exactly twice, and that is the whole of §18.5's rule.**
"Money arithmetic uses Decimal with ROUND_HALF_UP at two decimal places,
applied once per line and once per aggregate. Floating point is prohibited
anywhere in the pricing path." So a line total is quantized when it is
computed, and the subtotal, fee and tax are quantized once each — never in
between. `Money` is built for exactly this: it does not quantize on
construction or after every operation, because doing so would round
intermediate values and break the `total = subtotal + fee + tax` invariant
that §7.5.10's CHECK constraint asserts before a trip is written.

**Tax is computed here and configured elsewhere.** §18.3 makes tax rules rows
— `{country_id, service_type, tax_code, rate_percent, is_inclusive,
valid_from, valid_to}` — and says Tanzania's treatment "must be confirmed with
a tax adviser before launch". There is no `tax_rule` table in Phase 4 and no
adviser has been consulted, so callers pass no rules and tax is zero. What this
module does provide is the arithmetic §18.3 specifies for both shapes, tested,
so the day the rows exist the sums are already right and the only new thing is
where the rates come from. An inclusive rate is the one that would otherwise be
got wrong: it does not add to the total, it identifies the portion of a price
that was always tax.

**A stay anchor has no price at all.** Not zero — absent. ADR 0013 removed
rates and inventory from v1, so a STAY contributes nothing to the subtotal and
`price_item` refuses to give one a line total rather than quietly returning
zero, which would look identical on screen and be a different claim.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.common.money import Money
from apps.trip.domain.sequencing import Kind

__all__ = [
    "TaxRule",
    "PricedItem",
    "TripCost",
    "PricingError",
    "price_item",
    "compute_cost",
]

_TWO_PLACES = Decimal("0.01")


class PricingError(ValueError):
    """A cost could not be computed from what was supplied."""


def _round(amount: Decimal) -> Decimal:
    """§18.5: ROUND_HALF_UP at two decimal places.

    Two places rather than the currency's own exponent because §7.5.10 gives
    every money column `NUMERIC(14,2)` — a zero-exponent currency is stored in
    the same shape and simply never has a fractional part.
    """
    return amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class TaxRule:
    """§18.3, as the engine needs it.

    `rate_percent` rather than a fraction, because that is how §18.3 stores it
    and converting at the boundary is one place to be wrong instead of every
    call site.
    """

    tax_code: str
    rate_percent: Decimal
    is_inclusive: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.rate_percent, float):
            raise PricingError("a tax rate must be Decimal; §18.5 prohibits float")
        if self.rate_percent < 0:
            raise PricingError(f"{self.tax_code} has a negative rate")

    @property
    def rate(self) -> Decimal:
        return self.rate_percent / Decimal(100)

    def tax_on(self, line_total: Money) -> Money:
        """§18.3's two formulas, verbatim.

        exclusive: ``tax = round(line_total * rate, 2)``; the total becomes
        ``line_total + tax``.

        inclusive: ``tax = round(line_total * rate / (1 + rate), 2)``; the
        total stays ``line_total``. The tax was always inside the price, and
        this identifies how much of it was.
        """
        if self.is_inclusive:
            return Money(
                _round(line_total.amount * self.rate / (Decimal(1) + self.rate)),
                line_total.currency,
            )
        return Money(_round(line_total.amount * self.rate), line_total.currency)


@dataclass(frozen=True, slots=True)
class PricedItem:
    """An item's contribution to the subtotal.

    `unit_price` is `None` for a stay anchor and for free time. That is not
    the same as zero, and the difference reaches the screen: §24.16's cost
    breakdown lists a priced line at 0.00 and omits an unpriced one entirely.
    """

    item_id: int
    kind: Kind
    title: str
    unit_price: Money | None = None
    quantity: int = 1
    #: Where a provider publishes a whole-group price rather than a per-person
    #: one (§7.5.9's `price_per_group`). When present it *replaces* the
    #: per-unit arithmetic rather than adding to it.
    group_price: Money | None = None

    def __post_init__(self) -> None:
        if self.quantity < 1:
            raise PricingError(f"item {self.item_id} has a quantity below one")


@dataclass(frozen=True, slots=True)
class TripCost:
    """§10.7's four figures, plus the lines they came from."""

    currency: str
    lines: tuple[tuple[int, Money], ...]
    subtotal: Money
    fee: Money
    tax: Money
    total: Money

    def line_for(self, item_id: int) -> Money | None:
        for candidate, amount in self.lines:
            if candidate == item_id:
                return amount
        return None


def price_item(item: PricedItem) -> Money | None:
    """One line total, quantized once (§18.5).

    Returns `None` for an item that carries no price at all, which the caller
    must distinguish from zero — see `PricedItem`.
    """
    if item.kind in (Kind.STAY_CHECK_IN, Kind.STAY_CHECK_OUT):
        if item.unit_price is not None or item.group_price is not None:
            raise PricingError(
                f"item {item.item_id} is a stay anchor and cannot carry a price "
                "(ADR 0013: no room, no rate, no booking behind it)"
            )
        return None

    if item.group_price is not None:
        # §7.5.9: a group price is the price of the whole thing. Multiplying
        # it by the party size is the mistake this branch exists to prevent.
        return Money(_round(item.group_price.amount), item.group_price.currency)

    if item.unit_price is None:
        return None

    return Money(_round(item.unit_price.amount * item.quantity), item.unit_price.currency)


def compute_cost(
    items: Sequence[PricedItem],
    *,
    currency: str,
    platform_fee_rate: Decimal,
    tax_rules: Sequence[TaxRule] = (),
) -> TripCost:
    """§10.7, end to end.

    `platform_fee_rate` arrives from `system_setting` (NFR-M07) rather than
    being named here, and is a `Decimal` because §18.5 prohibits float
    anywhere on this path — a float rate would reintroduce binary rounding
    into a total that a database CHECK constraint is about to verify.
    """
    if isinstance(platform_fee_rate, float):
        raise PricingError("platform_fee_rate must be Decimal; §18.5 prohibits float")
    if platform_fee_rate < 0:
        raise PricingError("platform_fee_rate may not be negative")

    lines: list[tuple[int, Money]] = []
    subtotal = Money.zero(currency)
    tax = Money.zero(currency)

    for item in items:
        line = price_item(item)
        if line is None:
            continue
        if line.currency != currency:
            # VR-10 has already reported this as an error; reaching here means
            # something bypassed validation. Refusing is the only safe answer:
            # §18.4 freezes an FX rate at `priced_at`, and this module has no
            # rate and no business inventing one.
            raise PricingError(
                f"item {item.item_id} is priced in {line.currency}, "
                f"but the trip is presented in {currency} (VR-10)"
            )
        lines.append((item.item_id, line))
        subtotal = subtotal + line
        for rule in tax_rules:
            tax = tax + rule.tax_on(line)

    subtotal = Money(_round(subtotal.amount), currency)
    fee = Money(_round(subtotal.amount * platform_fee_rate), currency)
    tax = Money(_round(tax.amount), currency)

    # `total = subtotal + fee + tax`, and each part is already at two places,
    # so the sum is exact and matches §7.5.10's CHECK constraint by
    # construction rather than by a final rounding that could disagree with it.
    total = subtotal + fee + tax

    return TripCost(
        currency=currency,
        lines=tuple(lines),
        subtotal=subtotal,
        fee=fee,
        tax=tax,
        total=total,
    )
