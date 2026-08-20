"""ExchangeRatePort — display-time currency conversion only. SRS §9.1, §18.4.

This port exists for one screen behaviour: §9.1 lets a tourist send
`X-Currency`, and §24.11 shows them a stay total they can compare against
prices at home. It converts a *displayed* figure and nothing else.

**It is not the money path.** §18.4 gives the authoritative conversion its own
mechanism entirely: `finance.fx_rate` rows, frozen at `priced_at`, recorded on
the payment with their rate, source and timestamp, so that "every conversion in
the system stores its rate, source and timestamp — there are no untraceable
conversions" (§20.6). A quote priced today and paid tomorrow uses the rate it
was quoted at, not the rate the market is at when somebody reloads the page.

Keeping the two apart is a design requirement, not a convention:

* The return type is `IndicativeRate`, and applying it produces an
  `IndicativeAmount` (`apps.common.display_money`), which **refuses to take
  part in arithmetic**. A total cannot be built out of these figures by
  accident, because they will not add.

* The rate carries no identity. There is no `fx_rate.id` here to snapshot onto
  a booking, so there is nothing for a later writer to reach for.

* `indicative_rate` returns `None` when a rate is unavailable. The caller shows
  the listing's own currency. It does not fall back to a stale rate, a hard-coded
  one, or a guess — the same discipline as the distance chip in §12.6: a figure
  a tourist could mistake for fact is never fabricated.

No provider is selected (SRS Appendix D). Candidates are a central-bank feed or
the PSP's own published rates; the interface is narrow enough for either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

__all__ = ["IndicativeRate", "ExchangeRatePort"]


@dataclass(frozen=True, slots=True)
class IndicativeRate:
    """One unit of `base` costs `rate` units of `quote`, as of `as_of`.

    Named for what it is. Every other type in the money path is called
    something ordinary; this one announces on sight that it must not be used
    to price anything.
    """

    base: str
    quote: str
    rate: Decimal
    as_of: datetime
    source: str

    def __post_init__(self) -> None:
        if self.base == self.quote:
            raise ValueError("an indicative rate needs two different currencies")
        if not isinstance(self.rate, Decimal):
            # §18.5: never a float, anywhere, for any reason. A rate arriving
            # as a float is the most likely place one would sneak in, because
            # every upstream feed publishes JSON numbers.
            raise TypeError(f"rate must be a Decimal, got {type(self.rate).__name__}")
        if self.rate <= 0:
            raise ValueError(f"rate must be positive, got {self.rate}")
        if self.as_of.tzinfo is None:
            # §7.2: no naive datetimes. "As of when" is the entire value of the
            # timestamp, and a naive one is as-of-somewhere-unspecified.
            raise ValueError("as_of must be timezone-aware")
        if not self.source:
            raise ValueError("an indicative rate must name its source")


@runtime_checkable
class ExchangeRatePort(Protocol):
    """Indicative rates for display. Never for pricing.

    Implementations must return `None` rather than raising when a pair is
    unavailable: an unconvertible price is a page that shows the listing's own
    currency, not a page that fails.
    """

    def indicative_rate(self, *, base: str, quote: str) -> IndicativeRate | None: ...
