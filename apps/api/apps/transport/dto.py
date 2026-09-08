"""transport module — SRS §6.4.

Data transfer objects.

    Importable across module boundaries alongside services (SRS §6.5
    rule 1). Plain frozen dataclasses — no ORM, no Django.

**A leg arrives already resolved.** §6.4 gives `transport -> location,
provider`, and §12.4's ladder branches on the region of the origin, the country
default and whether either endpoint is a gateway — every one of them a
`catalogue` fact on the `destination` row. So `LegEndpoint` carries those facts
rather than an id this module would have to look up, and `is_gateway` is a
boolean for exactly that reason: there is nothing here to resolve, so there is
no temptation to resolve it. ADR 0023.

**A leg with no usable distance carries `None`, not a number.** §12.6 permits
cache and matrix for quoting and forbids the haversine fallback "for a priced
corridor without a fixed corridor price". The caller owns the estimate and its
quality (ADR 0019's `estimate_quality`), and translates `APPROXIMATE` into
absence here. That is deliberate: a corridor match needs no distance at all and
must still be quotable during a routing outage, so whether the absence matters
can only be decided *after* the ladder has run — which is what
`services.quote_transfer` does, refusing only the metered path.

**Transfers hold no inventory.** §9.4.5: "transfers hold no inventory but
reserve a vehicle class, not a specific driver." There is no hold token here,
no expiry and no counter, because there is nothing to count. Supply is secured
after payment by dispatch, and a leg nobody accepts becomes `UNFULFILLED` —
the commercial risk this model accepts, recorded in ADR 0023.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from apps.common.money import Money
from apps.transport.domain.tariffs import MatchKind

__all__ = [
    "LegEndpoint",
    "LegRequest",
    "VehicleClassDTO",
    "FareBreakdown",
    "TariffMatchDTO",
    "FareOption",
    "LegQuote",
    "CorridorDTO",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class LegEndpoint:
    """One end of a leg, with its catalogue facts already resolved.

    `label` is carried for the response and for error messages: §24.16's
    "transfers to this location are not yet available" is unhelpful without the
    location's name, and this module cannot look one up.
    """

    label: str
    destination_id: int | None = None
    region_id: int | None = None
    country_id: int | None = None
    is_gateway: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class LegRequest:
    """§9.4.4's `legs[]`, resolved.

    `pickup_local` is naive local time in the destination's zone, because
    §12.4 evaluates the night window against "pickup_at local time" and a UTC
    comparison would shift a Zanzibar 22:00-06:00 surcharge to 01:00-09:00.
    `depart_at` keeps the aware instant for the response and the audit trail.
    """

    reference: str
    origin: LegEndpoint
    target: LegEndpoint
    depart_at: datetime
    pickup_local: datetime
    pax: int
    luggage: int

    #: `None` when the only estimate available is APPROXIMATE (§12.6).
    distance_m: int | None = None
    travel_seconds: int | None = None

    @property
    def on(self) -> date:
        """The day the ladder's effective dating is evaluated against."""
        return self.pickup_local.date()

    @property
    def touches_a_gateway(self) -> bool:
        """§12.4: "IF origin or target is a gateway destination"."""
        return self.origin.is_gateway or self.target.is_gateway


@dataclass(frozen=True, slots=True, kw_only=True)
class VehicleClassDTO:
    """§9.3.4's `GET /transport/vehicle-classes`."""

    public_id: UUID
    code: str
    name: str
    description: str
    seats: int
    luggage_capacity: int
    has_air_conditioning: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class FareBreakdown:
    """§9.4.4's `breakdown`, and it adds up.

    `surcharges` is the remainder rather than an independent accumulation, so
    `base + distance + time + surcharges == total` exactly. §12.4's worked
    example depends on it: 12.00 + 22.96 + 0.00 + 3.04 = 38.00.
    """

    base: Decimal
    distance: Decimal
    time: Decimal
    surcharges: Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class TariffMatchDTO:
    """Which rule priced this, and on which rung of §12.4's ladder.

    Two identifiers rather than one `tariff_id`: steps 1-2 match a corridor and
    steps 3-4 a tariff, and a single column cannot address two tables. Phase 7
    snapshots both onto `booking_transfer` so BR-054's frozen price stays
    explicable.

    `step` is what §27.11's preview tool shows an administrator. A fare that
    fell through to the country default when a corridor was expected is a
    misconfiguration indistinguishable from a correct answer without it.
    """

    kind: MatchKind
    rule_id: int
    rule_public_id: UUID
    step: int


@dataclass(frozen=True, slots=True, kw_only=True)
class FareOption:
    """One vehicle class a party fits into, and what it costs."""

    vehicle_class: VehicleClassDTO
    price: Money
    breakdown: FareBreakdown
    match: TariffMatchDTO


@dataclass(frozen=True, slots=True, kw_only=True)
class LegQuote:
    """§9.4.4's response element.

    `options` is empty only when no class fits the party — never when nothing
    is configured, which raises instead. The two states look the same on a
    screen and mean opposite things: "your party is too large for anything we
    run" is a fact about the party, and "we have no price for this route" is a
    fact about our configuration.
    """

    reference: str
    distance_m: int | None
    travel_seconds: int | None
    options: tuple[FareOption, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CorridorDTO:
    """§9.3.4's `GET /transport/corridors` — a published route list.

    No price. The endpoint is public and a corridor's fare depends on the class
    and the date; publishing one number would be a quote nobody asked for and
    could not be held to.
    """

    public_id: UUID
    origin_destination_id: int
    target_destination_id: int
    vehicle_class_code: str
    is_bidirectional: bool
