"""Transfer pricing — SRS §12.4.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§12.4's ladder, verbatim, first match wins::

    1. Active transfer_corridor for (origin_dest, target_dest, class)
    2. Active transfer_corridor for the reverse pair when is_bidirectional
    3. Active transfer_tariff for (region of origin, class)
    4. Active transfer_tariff for (country default, class)
    No match -> 422 NO_TARIFF_CONFIGURED (never guess a price)

and its metered computation::

    price = base_fare + per_km_rate     x (distance_m / 1000)
                      + per_minute_rate x (travel_seconds / 60)
    price = max(price, minimum_fare)
    IF pickup_at local time within [night_from, night_to]:
        price = price x (1 + night_surcharge_pct/100)
    IF origin or target is a gateway destination:
        price = price + airport_surcharge
    price = round(price, 2)

**The order of those five lines is the specification, not an implementation
detail.** `minimum_fare` is a floor on the metered sum and is applied *before*
both surcharges, so a short night-time airport run pays the surcharges on top
of the floor rather than being floored after them. Multiplying before flooring
would produce a different number for exactly the trips a tourist is most likely
to take, and the difference would look like a rounding disagreement.

**Rounding happens once, at the end.** §18.5 allows one rounding per line and
one per aggregate; this whole function is one line. Rounding the distance
component before adding the time component would put a cent of error inside a
figure that §41.6 requires to "reproduce exactly".

**The ladder takes facts, not identifiers.** `Endpoint` carries the region, the
country and `is_gateway` already resolved, because §6.4 gives
`transport -> location, provider` and every one of those is a `catalogue` fact.
ADR 0023: the caller resolves, this module decides. `is_gateway` is a boolean
rather than a destination id precisely so there is nothing here to look up.

**No step ever guesses.** `resolve` returns `None` and the caller raises
`NO_TARIFF_CONFIGURED`. §4.2's prohibited example is `NUNGWI_TRANSFER_PRICE =
38`; the required instead is this table, and a fallback constant would be the
same mistake wearing a default argument.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from apps.common.money import Money

__all__ = [
    "TariffError",
    "MatchKind",
    "VehicleClassSpec",
    "Endpoint",
    "CorridorRule",
    "MeteredRule",
    "TariffMatch",
    "Fare",
    "eligible",
    "cheapest_fitting",
    "is_night",
    "metered",
    "resolve",
    "price",
]

_TWO_PLACES = Decimal("0.01")
_METRES_PER_KM = Decimal(1000)
_SECONDS_PER_MINUTE = Decimal(60)
_HUNDRED = Decimal(100)


class TariffError(ValueError):
    """A fare could not be computed from what was supplied."""


def _round(amount: Decimal) -> Decimal:
    """§12.4's `round(price, 2)`, at §18.5's ROUND_HALF_UP.

    Two places rather than the currency's own exponent, matching
    `trip.domain.costing._round` and for the same reason: §7.5.10 gives every
    money column `NUMERIC(14,2)`, and a zero-exponent currency is stored in the
    same shape and simply never has a fractional part. Two roundings that
    disagree about the number of places is how a transfer line and a trip
    subtotal start to differ by a cent.
    """
    return amount.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


class MatchKind(StrEnum):
    """Which table answered.

    §12.4 stores "the matched rule id ... on `booking_transfer.tariff_id`", and
    one column cannot address two tables. Carrying the kind alongside the id is
    what makes the price reproducible forever rather than reproducible until
    somebody guesses which table to look in.
    """

    CORRIDOR = "CORRIDOR"
    TARIFF = "TARIFF"


@dataclass(frozen=True, slots=True, kw_only=True)
class VehicleClassSpec:
    """§12.4's class table, as the filter needs it."""

    code: str
    seats: int
    luggage_capacity: int
    display_order: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class Endpoint:
    """One end of a leg, with every catalogue fact the ladder needs resolved.

    `destination_id` is `None` for a custom point the tourist dropped on a map
    (§12.1's last leg pattern). Such a leg can never match a corridor — a
    corridor is defined between two destinations — and falls through to the
    metered steps, which is the correct behaviour and not a gap.
    """

    destination_id: int | None
    region_id: int | None
    country_id: int | None
    is_gateway: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class _Dated:
    """Effective dating, checked here rather than trusted from the caller.

    §12.4 says "active" on all four rungs. A repository that forgot the window
    would hand this module an expired rate and get a confident answer; making
    the ladder do its own filtering means the rule cannot be bypassed by a
    caller in a hurry.
    """

    rule_id: int
    vehicle_class: str
    currency: str
    is_active: bool = True
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None

    def applies_on(self, day: dt.date) -> bool:
        if not self.is_active:
            return False
        if self.valid_from is not None and day < self.valid_from:
            return False
        # Half-open, matching the exclusion constraint in `models.py`: a rate
        # valid *to* the 1st has been replaced by the one valid *from* the 1st.
        return not (self.valid_to is not None and day >= self.valid_to)


@dataclass(frozen=True, slots=True, kw_only=True)
class CorridorRule(_Dated):
    """§12.4's `transfer_corridor`. Steps 1 and 2."""

    origin_destination_id: int
    target_destination_id: int
    fixed_price: Decimal
    is_bidirectional: bool = True

    def serves(self, *, origin: Endpoint, target: Endpoint) -> bool:
        return (
            self.origin_destination_id == origin.destination_id
            and self.target_destination_id == target.destination_id
        )

    def serves_reversed(self, *, origin: Endpoint, target: Endpoint) -> bool:
        return self.is_bidirectional and (
            self.origin_destination_id == target.destination_id
            and self.target_destination_id == origin.destination_id
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class MeteredRule(_Dated):
    """§12.4's `transfer_tariff`. Steps 3 and 4."""

    region_id: int | None = None
    country_id: int | None = None

    base_fare: Decimal
    per_km_rate: Decimal = Decimal(0)
    per_minute_rate: Decimal = Decimal(0)
    minimum_fare: Decimal = Decimal(0)

    night_surcharge_pct: Decimal = Decimal(0)
    night_from: dt.time | None = None
    night_to: dt.time | None = None

    airport_surcharge: Decimal = Decimal(0)


@dataclass(frozen=True, slots=True, kw_only=True)
class TariffMatch:
    """Which rule answered, and on which rung.

    `step` is not decoration. §27.11's quote-preview tool exists so an
    administrator can see *why* a corridor prices the way it does, and a fare
    that fell through to the country default when a corridor was expected is a
    misconfiguration that looks identical to a correct answer without it.
    """

    kind: MatchKind
    rule_id: int
    step: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Fare:
    """A priced leg, with §9.4.4's breakdown.

    The four components sum exactly to `total`, because `surcharges` is
    computed as the remainder rather than accumulated independently. §12.4's
    worked example depends on that: `12.00 + 22.96 + 0.00 + 3.04 = 38.00`, and
    a breakdown that did not add up would be the first thing a tourist queried.
    """

    total: Money
    base: Money
    distance: Money
    time: Money
    surcharges: Money
    match: TariffMatch

    def __post_init__(self) -> None:
        parts = self.base + self.distance + self.time + self.surcharges
        if parts != self.total:
            raise TariffError(
                f"breakdown {parts.amount} does not sum to the total {self.total.amount}"
            )


# -- vehicle classes ---------------------------------------------------------


def eligible(
    classes: Iterable[VehicleClassSpec], *, pax: int, luggage: int
) -> tuple[VehicleClassSpec, ...]:
    """§12.4: "the quote returns only classes whose seats >= pax and luggage >=
    luggage_count".

    A filter, not a selection. §24.16 shows the survivors as cards and §24.17
    lets the tourist change their mind per leg; a function that picked one
    would make both screens impossible to build honestly.
    """
    if pax < 1:
        raise TariffError("a leg carries at least one passenger")
    if luggage < 0:
        raise TariffError("a luggage count cannot be negative")
    return tuple(
        sorted(
            (c for c in classes if c.seats >= pax and c.luggage_capacity >= luggage),
            key=lambda c: (c.display_order, c.code),
        )
    )


def cheapest_fitting(
    priced: Iterable[tuple[VehicleClassSpec, Fare]],
) -> VehicleClassSpec | None:
    """The default the planner inserts a transfer with (ADR 0023 decision 5).

    A starting point, not the answer: §24.17 gives the tourist per-leg
    selection, and this exists because the sequencer has to write *some* class
    onto the row it creates. Ties break on `display_order` then `code`, so
    regenerating an itinerary twice produces the same class both times — TC-902
    wants byte-identical totals and a tie broken by dictionary order would not
    deliver them.
    """
    candidates = sorted(
        priced, key=lambda pair: (pair[1].total.amount, pair[0].display_order, pair[0].code)
    )
    return candidates[0][0] if candidates else None


# -- the metered computation -------------------------------------------------


def is_night(when: dt.time, *, night_from: dt.time | None, night_to: dt.time | None) -> bool:
    """§12.4: "IF pickup_at local time within [night_from, night_to]".

    Inclusive at both ends, as the square brackets say, and **wrapping**: a
    night surcharge that ran 22:00 to 06:00 and was evaluated as
    `22:00 <= t <= 06:00` would apply to nothing at all, every night, silently.
    That is the failure this function exists to make impossible.
    """
    if night_from is None or night_to is None:
        return False
    if night_from <= night_to:
        return night_from <= when <= night_to
    return when >= night_from or when <= night_to


def metered(
    rule: MeteredRule,
    *,
    distance_m: int,
    travel_seconds: int,
    pickup_local: dt.datetime,
    is_airport: bool,
    match: TariffMatch,
) -> Fare:
    """§12.4's five lines, in their order.

    `pickup_local` is local time in the destination's zone, not UTC. §12.4 says
    "pickup_at local time", and evaluating a 22:00-06:00 window in UTC would
    apply a Zanzibar night surcharge between 01:00 and 09:00 local — wrong in
    both directions, and wrong differently in a market three hours the other
    way.
    """
    if distance_m < 0 or travel_seconds < 0:
        raise TariffError("a leg cannot have negative distance or duration")
    for name in ("base_fare", "per_km_rate", "per_minute_rate", "minimum_fare"):
        if isinstance(getattr(rule, name), float):
            raise TariffError(f"{name} must be Decimal; §18.5 prohibits float")

    currency = rule.currency

    distance_component = rule.per_km_rate * (Decimal(distance_m) / _METRES_PER_KM)
    time_component = rule.per_minute_rate * (Decimal(travel_seconds) / _SECONDS_PER_MINUTE)

    running = rule.base_fare + distance_component + time_component
    running = max(running, rule.minimum_fare)

    at_pickup = pickup_local.time()
    if is_night(at_pickup, night_from=rule.night_from, night_to=rule.night_to):
        running = running * (Decimal(1) + rule.night_surcharge_pct / _HUNDRED)

    if is_airport:
        running = running + rule.airport_surcharge

    total = Money(_round(running), currency)
    base = Money(_round(rule.base_fare), currency)
    distance = Money(_round(distance_component), currency)
    time = Money(_round(time_component), currency)

    return Fare(
        total=total,
        base=base,
        distance=distance,
        time=time,
        # The remainder, so the four parts sum to the total exactly. It carries
        # the minimum-fare uplift as well as the two surcharges, which is what
        # "surcharges" means to somebody reading a receipt: everything charged
        # that was not distance, time or the flag-fall.
        surcharges=total - base - distance - time,
        match=match,
    )


# -- the ladder --------------------------------------------------------------


def resolve(
    *,
    corridors: Sequence[CorridorRule],
    tariffs: Sequence[MeteredRule],
    origin: Endpoint,
    target: Endpoint,
    vehicle_class: str,
    on: dt.date,
) -> tuple[CorridorRule | MeteredRule, TariffMatch] | None:
    """§12.4's four rungs, in order, first match wins.

    `None` means no rule matched, and the caller answers
    `422 NO_TARIFF_CONFIGURED`. It does not mean "free", it does not mean
    "use the default", and there is no default to use.

    Ties within a rung are broken by the most recently effective rule, then by
    the highest id. The database exclusion constraints make a tie impossible
    for live rows; this makes the answer deterministic anyway, because a
    function whose result depends on the order a queryset happened to return is
    a function TC-902 will eventually catch and nobody will be able to explain.
    """
    if origin.destination_id is not None and origin.destination_id == target.destination_id:
        raise TariffError(
            "§12.2: a leg whose origin and target resolve to the same place is not a leg"
        )

    live_corridors = [c for c in corridors if c.vehicle_class == vehicle_class and c.applies_on(on)]
    live_tariffs = [t for t in tariffs if t.vehicle_class == vehicle_class and t.applies_on(on)]

    def newest(rules: Sequence[_Dated]) -> _Dated | None:
        ordered = sorted(
            rules,
            key=lambda r: (r.valid_from or dt.date.min, r.rule_id),
            reverse=True,
        )
        return ordered[0] if ordered else None

    # Step 1 — a corridor for this pair, in this direction.
    forward = newest([c for c in live_corridors if c.serves(origin=origin, target=target)])
    if isinstance(forward, CorridorRule):
        return forward, TariffMatch(kind=MatchKind.CORRIDOR, rule_id=forward.rule_id, step=1)

    # Step 2 — the same corridor the other way round, when it says it is
    # bidirectional. The fare is the fare; an asymmetric route gets two rows.
    back = newest([c for c in live_corridors if c.serves_reversed(origin=origin, target=target)])
    if isinstance(back, CorridorRule):
        return back, TariffMatch(kind=MatchKind.CORRIDOR, rule_id=back.rule_id, step=2)

    # Step 3 — the metered fallback for the region the leg *starts* in. §12.4
    # says "region of origin" and means it: a leg out of Stone Town is priced
    # on Stone Town's rates wherever it is going.
    if origin.region_id is not None:
        regional = newest([t for t in live_tariffs if t.region_id == origin.region_id])
        if isinstance(regional, MeteredRule):
            return regional, TariffMatch(kind=MatchKind.TARIFF, rule_id=regional.rule_id, step=3)

    # Step 4 — the country default. ADR 0023 decision 4: there is no market
    # step between these two, and the reasoning is recorded there rather than
    # left for the next reader to re-derive.
    if origin.country_id is not None:
        national = newest([t for t in live_tariffs if t.country_id == origin.country_id])
        if isinstance(national, MeteredRule):
            return national, TariffMatch(kind=MatchKind.TARIFF, rule_id=national.rule_id, step=4)

    return None


def price(
    rule: CorridorRule | MeteredRule,
    match: TariffMatch,
    *,
    distance_m: int | None,
    travel_seconds: int | None,
    pickup_local: dt.datetime,
    is_airport: bool,
) -> Fare:
    """Turn a matched rule into a fare.

    A corridor needs neither distance nor duration — that is the whole point of
    a fixed corridor price, and it is why §12.6 permits a corridor to be quoted
    when routing is unavailable and forbids the metered path from being. So
    `distance_m` and `travel_seconds` are optional here, and a metered rule
    that is handed `None` raises rather than substituting a zero, which would
    quietly bill the flag-fall for a hundred-kilometre drive.
    """
    if isinstance(rule, CorridorRule):
        total = Money(_round(rule.fixed_price), rule.currency)
        zero = Money.zero(rule.currency)
        return Fare(total=total, base=total, distance=zero, time=zero, surcharges=zero, match=match)

    if distance_m is None or travel_seconds is None:
        raise TariffError(
            "a metered tariff needs a distance and a duration; §12.6 forbids "
            "estimating them for a priced leg (502 ROUTING_UNAVAILABLE)"
        )
    return metered(
        rule,
        distance_m=distance_m,
        travel_seconds=travel_seconds,
        pickup_local=pickup_local,
        is_airport=is_airport,
        match=match,
    )
