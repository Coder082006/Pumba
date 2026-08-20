"""A converted price, in a type that cannot be priced with. SRS §9.1, §18.4.

`Money` is the money type: it adds, it subtracts, it multiplies by a quantity,
and §20.1 rounds it once per line and once per aggregate. Everything that ends
up on an invoice is one.

`IndicativeAmount` is deliberately none of those things. It is what a tourist
sees next to a price when they send `X-Currency`, and §18.4 is explicit that
the authoritative conversion is a different mechanism — `finance.fx_rate` rows
frozen at `priced_at`, recorded with rate, source and timestamp. The two must
stay visibly apart, and the way to keep them apart is to make the display type
refuse to behave like money:

    >>> total = sum(indicative_amounts)      # raises
    >>> a + b                                # raises
    >>> Money(...) + indicative              # raises

Every one of those raises `IndicativeAmountError` with the reason, rather than
`TypeError`, because a `TypeError` reads like a bug somebody should fix by
adding an `__add__` — and adding one is exactly the mistake this type exists to
prevent. A refusal that explains itself is a refusal that survives.

The amount keeps the converted figure and the rate that produced it together,
for the same reason `domain.geo.Distance` keeps its `EstimateQuality`: a caller
cannot obtain the number without also holding the evidence of where it came
from, so a template that wants to say "approximately, at today's rate" has what
it needs, and one that wants to hide that has to work at it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NoReturn

from apps.common.money import Money, minor_unit_exponent
from ports.exchange_rate import IndicativeRate

__all__ = ["IndicativeAmount", "IndicativeAmountError", "convert_for_display"]


class IndicativeAmountError(TypeError):
    """An indicative figure was used as though it were money.

    A `TypeError` subclass so that `sum()` and friends still fail in the shape
    callers expect, but with a message that says *why* rather than inviting
    somebody to add the missing operator.
    """


def _refuse(*_: Any, **__: Any) -> NoReturn:
    raise IndicativeAmountError(
        "an indicative amount is a display figure and cannot be used in arithmetic; "
        "totals are computed in the listing currency with Money, and the "
        "authoritative conversion uses finance.fx_rate frozen at priced_at (SRS 18.4)"
    )


@dataclass(frozen=True, slots=True)
class IndicativeAmount:
    """`amount` in `currency`, obtained by applying `rate` to `source`."""

    #: What the tourist asked to see it in.
    amount: Decimal
    currency: str

    #: The price as the listing actually stores it. Kept because §24.11 shows
    #: both, and because the listing currency is the one anything is charged in.
    source: Money

    #: The rate used, with its own `as_of` and `source`. §20.6: no untraceable
    #: conversions, and that applies to a figure on a page as much as to one in
    #: a ledger.
    rate: IndicativeRate

    # Arithmetic is refused, loudly and by name.
    __add__ = _refuse
    __radd__ = _refuse
    __sub__ = _refuse
    __rsub__ = _refuse
    __mul__ = _refuse
    __rmul__ = _refuse
    __truediv__ = _refuse
    __rtruediv__ = _refuse
    __neg__ = _refuse

    @property
    def is_converted(self) -> bool:
        """False when nothing was converted, which is not an error state.

        The page shows the listing currency and says nothing about rates.
        """
        return self.currency != self.source.currency


def convert_for_display(money: Money, *, rate: IndicativeRate) -> IndicativeAmount:
    """Apply an indicative rate to a price, for display only.

    Rounded once, to the minor unit of the target currency, because the result
    is shown and never carried forward. That is the one respect in which this
    is simpler than §20.1: there is no aggregate to round a second time, since
    these do not add.
    """
    if money.currency != rate.base:
        raise IndicativeAmountError(
            f"rate converts from {rate.base}, but the price is in {money.currency}"
        )

    exponent = Decimal(1).scaleb(-minor_unit_exponent(rate.quote))
    converted = (money.amount * rate.rate).quantize(exponent, rounding=ROUND_HALF_UP)
    return IndicativeAmount(amount=converted, currency=rate.quote, source=money, rate=rate)
