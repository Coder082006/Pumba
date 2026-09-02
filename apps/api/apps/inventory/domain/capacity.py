"""Whether a departure can be sold — SRS §16.3, §16.6, BR-022, BR-034.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§16.3 states the whole rule in five lines:

    sellable(departure) = capacity_total - capacity_held - capacity_sold

    Bookable iff sellable >= pax
              and departure.status = 'OPEN'
              and now() <= departs_at - booking_cutoff_hours
              and min_pax <= pax <= max_pax

Three things about how that is expressed here.

**The answer is a reason, not a boolean.** §9.4.5 must return
`409 INVENTORY_UNAVAILABLE` with *"a details array naming each unavailable
item"*, and §24.10 has to tell a tourist why a departure they can see is not
one they can take. "Sold out" and "you left it too late" are different
sentences and lead to different next actions — one wants another date, the
other wants any date at all. A predicate would throw that away at the point it
is cheapest to keep.

**Nothing here reads a clock or a setting.** `now` and `booking_cutoff_hours`
arrive as arguments. §4.2 and NFR-M07 both push the same way, and the practical
consequence is that the cut-off boundary can be tested exactly rather than
approximately.

**This module answers about *one* departure.** Whether a party of six can be
split across two departures, or whether the trip as a whole is quotable, is
§9.4.5's composition and not a capacity question.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

__all__ = [
    "DepartureState",
    "Unbookable",
    "Departure",
    "PartyRules",
    "sellable",
    "why_not_bookable",
    "is_bookable",
]


class DepartureState(StrEnum):
    """§7.5.9's four statuses.

    Mirrors `models.DepartureStatus`, because this module may not import
    Django; `test_capacity.py` compares the two sets.

    Only OPEN sells. The other three are distinct on purpose: FULL is arithmetic
    the provider did not choose, CLOSED is a provider deciding to stop selling a
    departure that is still running, and CANCELLED is a departure that is not
    running at all. A tourist holding a booking against the last of those needs
    to be told something quite different from the first.
    """

    OPEN = "OPEN"
    FULL = "FULL"
    CANCELLED = "CANCELLED"
    CLOSED = "CLOSED"


class Unbookable(StrEnum):
    """Why a departure cannot take this party.

    These strings reach a client, inside §9.4.5's `details` array and §24.10's
    per-departure labels, so they are part of the API contract rather than an
    internal diagnostic.
    """

    #: BR-022. Not enough seats left for the whole party.
    SOLD_OUT = "SOLD_OUT"
    #: The provider stopped selling this departure (§16.2).
    CLOSED = "CLOSED"
    #: The departure is not running (§16.2 — weather, and the like).
    CANCELLED = "CANCELLED"
    #: BR-034. Past `departs_at - booking_cutoff_hours` (§16.6).
    PAST_CUTOFF = "PAST_CUTOFF"
    #: Below the activity's `min_pax`.
    PARTY_TOO_SMALL = "PARTY_TOO_SMALL"
    #: Above the activity's `max_pax`, whatever the remaining capacity says.
    PARTY_TOO_LARGE = "PARTY_TOO_LARGE"


@dataclass(frozen=True, slots=True, kw_only=True)
class Departure:
    """The counter row's facts, without the row."""

    departs_at: datetime
    capacity_total: int
    capacity_held: int
    capacity_sold: int
    status: DepartureState


@dataclass(frozen=True, slots=True, kw_only=True)
class PartyRules:
    """The activity's constraints on who may come, from `catalogue`."""

    min_pax: int
    max_pax: int
    booking_cutoff_hours: int


def sellable(departure: Departure) -> int:
    """§16.3's expression, and §17.1 I3's warning about it.

    Indicative when read from a search or a cache; authoritative only inside
    the transaction that holds the row lock. The arithmetic is the same either
    way — what differs is whether anything can have changed since.

    Clamped at zero. The database CHECK makes a negative result impossible, so
    one would mean the constraint had been dropped or bypassed; returning it
    would let a negative propagate into a subtraction somewhere and turn a
    detectable fault into a plausible number.
    """
    if departure.status is not DepartureState.OPEN:
        return 0
    return max(0, departure.capacity_total - departure.capacity_held - departure.capacity_sold)


def why_not_bookable(
    departure: Departure,
    rules: PartyRules,
    *,
    pax: int,
    now: datetime,
) -> Unbookable | None:
    """The first reason this party cannot take this departure, or None.

    Ordered deliberately, most permanent first. A cancelled departure that is
    also sold out and also past its cut-off should say CANCELLED: the other two
    invite the tourist to try a smaller party or hurry up, and neither would
    help. Reporting the reason a different date would fix, when the real
    problem is that this one is not running, is a worse answer than no answer.
    """
    if departure.status is DepartureState.CANCELLED:
        return Unbookable.CANCELLED
    if departure.status is DepartureState.CLOSED:
        return Unbookable.CLOSED

    # Party bounds before capacity: a party of twelve on a six-seat activity is
    # refused the same way whether or not today's departure happens to be
    # empty, and "try another date" would be false advice.
    if pax < rules.min_pax:
        return Unbookable.PARTY_TOO_SMALL
    if pax > rules.max_pax:
        return Unbookable.PARTY_TOO_LARGE

    # §16.6 / BR-034. The boundary is inclusive — `now == latest` is still in
    # time — because a cut-off is the last moment that works, not the first
    # that does not.
    if now > departure.departs_at - timedelta(hours=rules.booking_cutoff_hours):
        return Unbookable.PAST_CUTOFF

    # FULL folds into SOLD_OUT rather than carrying its own reason: it is the
    # same fact, once as a status the provider's tooling set and once as
    # arithmetic, and a tourist reading two different words for one situation
    # would reasonably wonder what the difference was.
    if sellable(departure) < pax:
        return Unbookable.SOLD_OUT

    return None


def is_bookable(
    departure: Departure,
    rules: PartyRules,
    *,
    pax: int,
    now: datetime,
) -> bool:
    """§16.3's "bookable iff", for callers that do not need the reason."""
    return why_not_bookable(departure, rules, pax=pax, now=now) is None
