"""Catalogue display pricing — SRS §14.2, §16.1, §20.1, §24.11, TC-021.

This module computes what a tourist *sees while browsing*. It does not compute
what they will be charged. That distinction is the reason it is small and the
reason it is here rather than in `trip`:

* §24.11 requires the **total** be the headline figure with the per-unit price
  beneath it, *"so comparison is honest"* — a per-night or per-person price
  next to a four-night stay or a party of six invites the tourist to do the
  multiplication wrong.
* §20.1 fixes the arithmetic: `Decimal`, `ROUND_HALF_UP`, *"applied once per
  line total and once per aggregate; intermediate values retain full
  precision."*

**The average is derived from the total, never computed alongside it.** Two
independent computations of the same fact eventually disagree by a rounding
step, and the pair that disagrees is exactly the pair displayed one above the
other. So `nightly_average` takes a total and divides.

**Nothing here converts currency.** §20.2 puts conversion at quote time, and
the indicative display figure of the currency port is a separate value that
must never enter a subtotal — `stay_total` refuses mixed currencies outright
rather than trying, which is what makes that rule enforceable rather than
advisory.

Two of the five functions are **v2**, and the split runs through this module
rather than around it — ADR 0013.

`stay_total` and `nightly_average` price a room, and v1 does not sell one: a
STAY item is an anchor with no price. They are kept, tested and marked, because
they are cheap and will be wanted again exactly as written.

`stay_nights` is **not** v2 and is load-bearing in v1. BR-101's "check-out
strictly after check-in, maximum 30 nights" bounds a *stay anchor* exactly as
it bounded a booking, and §24.11 in its amended form validates with it. It is
also what the two deferred functions call, so moving them out would orphan it.

`per_person_total` and `group_or_per_person_total` are §16.1 activity pricing
and are untouched.

`max_nights` is a parameter — `stay.max_nights` in `system_setting`, already
registered in Appendix B. BR-101's "maximum stay is 30 nights" is that row's
default, not a constant in this file.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from apps.common.money import CurrencyMismatchError, Money

__all__ = [
    "DateRangeError",
    "stay_nights",
    "stay_total",
    "nightly_average",
    "per_person_total",
    "group_or_per_person_total",
]


class DateRangeError(ValueError):
    """TC-021: an invalid stay range. Surfaces as 422 `INVALID_DATE_RANGE`."""


def stay_nights(check_in: date, check_out: date, *, max_nights: int) -> int:
    """Nights in `[check_in, check_out)`. BR-101, TC-021.

    Check-out is exclusive: a 12th-to-16th stay is four nights, and the guest
    sleeps on the 12th, 13th, 14th and 15th. Getting this off by one used to
    misprice every stay in the catalogue by a night; since ADR 0013 it decides
    how many nights a stay anchor covers, and therefore which nights VR-16
    reports as having no anchor to plan transfers around.
    """
    if check_out <= check_in:
        raise DateRangeError("check-out must be after check-in")
    nights = (check_out - check_in).days
    if nights > max_nights:
        raise DateRangeError(f"stay of {nights} nights exceeds the maximum of {max_nights}")
    return nights


def stay_total(nightly: Sequence[Money]) -> Money:
    """**v2 — ADR 0013.** Sum of the per-night rates. §14.2, §20.1.

    Deferred with the accommodation subsystem: v1 sells no room, so there is no
    nightly rate to sum. Retained and tested against the day it returns.

    Refuses an empty sequence rather than returning zero: a stay with no nights
    is a caller error, and a free stay rendered as "$0 total" is worse than an
    exception in a log.
    """
    if not nightly:
        raise DateRangeError("a stay must have at least one night")
    total = nightly[0]
    for rate in nightly[1:]:
        try:
            total = total + rate
        except CurrencyMismatchError as exc:
            # §20.2 VR-10: one presentment currency per basket. Converting here
            # would be a display-time conversion, which §20.2 forbids.
            raise CurrencyMismatchError(total.currency, rate.currency) from exc
    return total.quantize()


def nightly_average(total: Money, nights: int) -> Money:
    """**v2 — ADR 0013.** §24.11's secondary figure, derived from the headline one.

    Derived rather than recomputed so the two cannot drift apart by a rounding
    step while sitting one line above the other on the card.
    """
    if nights < 1:
        raise DateRangeError("nights must be at least 1")
    step = Decimal(1).scaleb(-total.exponent)
    average = (total.amount / Decimal(nights)).quantize(step, rounding=ROUND_HALF_UP)
    return Money(average, total.currency)


def per_person_total(unit: Money, pax: int) -> Money:
    """§16.1 activity pricing: price per person times party size."""
    if pax < 1:
        raise ValueError("pax must be at least 1")
    return (unit * pax).quantize()


def group_or_per_person_total(
    *, price_per_person: Money | None, price_per_group: Money | None, pax: int
) -> Money:
    """§16.1: an activity carries either or both prices.

    The group price wins when present, because a provider who sets one is
    quoting for the boat rather than the seat, and charging per person as well
    would double-charge a family. Where both exist the group price is the
    provider's stated ceiling for the whole party.
    """
    if pax < 1:
        raise ValueError("pax must be at least 1")
    if price_per_group is not None:
        return price_per_group.quantize()
    if price_per_person is not None:
        return per_person_total(price_per_person, pax)
    raise ValueError("an activity must carry a per-person or a per-group price")
