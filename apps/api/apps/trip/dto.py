"""trip module — SRS §6.4.

Data transfer objects.

    Importable across module boundaries alongside services (SRS §6.5
    rule 1). Plain frozen dataclasses — no ORM, no Django.

§6.5 rule 5: *"every module's services.py exposes only DTOs and primitives —
never ORM instances — across module boundaries."* `tests/test_architecture.py`
asserts it.

Four properties are carried deliberately rather than incidentally.

**No `id`, anywhere.** §7.2: *"Sequential integers are never returned to
clients."* Every DTO identifies itself by `public_id`. That matters more here
than in `catalogue`, because this module stores cross-module references *as*
integers (ADR 0012) — `accommodation_id`, `activity_id`, `attraction_id`. Those
are storage, not identity, and the DTO is where they stop.

`ListingRefDTO` — public_id, slug, name — is imported from `catalogue.dto`
rather than restated here. §6.5 rule 1 makes a module's DTOs importable across
the boundary alongside its services, and `catalogue.services.resolve_refs` is
what produces them; a parallel type in this module would be a second definition
of the same three fields, converted at every call site for no gain.

**A transfer always says where its numbers came from.** `estimate_quality` is
non-optional on a transfer and absent on everything else, mirroring the
database constraint. §12.6 requires the UI to render an explicit "approximate"
label, and a DTO that made the field optional would let a serializer omit it
and a component forget to look.

**A stay anchor has no money.** ADR 0013: no room, no rate, no booking. The
DTO shape is where that stops being a claim in a document and becomes
something a serializer cannot get wrong.

**Findings are part of the itinerary, not an error channel.** §10.6 returns
them from a successful generate, with `item_ids` so §24.14 can anchor each one
against the row it concerns. They are data about a valid response, which is why
they are a field here rather than an exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from apps.catalogue.dto import ListingRefDTO

__all__ = [
    "FindingDTO",
    "TripFlightDTO",
    "ItineraryItemDTO",
    "ItineraryDTO",
    "TripDTO",
    "TripSummaryDTO",
]


@dataclass(frozen=True, slots=True)
class FindingDTO:
    """§10.6's `{code, severity, message, item_ids[], suggested_action}`.

    `item_ids` are `public_id`s here, not the integers the domain works in:
    the domain sorts and compares items by a cheap key, and the wire needs the
    identifier §7.2 permits. The translation happens once, in `selectors`.
    """

    code: str
    severity: str
    message: str
    item_ids: tuple[UUID, ...] = ()
    suggested_action: str = "NONE"
    context: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TripFlightDTO:
    """§11.2. `actual_at` being null is the ordinary case: V1 integrates no
    flight-status feed, so a schedule is all the platform knows until a tourist
    or driver says otherwise."""

    direction: str
    flight_number: str
    airline_iata: str
    gateway: ListingRefDTO
    scheduled_at: datetime
    actual_at: datetime | None = None
    terminal: str | None = None
    pax_count: int = 1
    luggage_count: int = 0


@dataclass(frozen=True, slots=True)
class ItineraryItemDTO:
    """§7.5.11, as the client needs it.

    The nullable subject references mirror the five shapes the database
    constrains: exactly one of them is set for STAY, ACTIVITY and ATTRACTION;
    a TRANSFER names its endpoints; FREE_TIME names nothing.
    """

    public_id: UUID
    day_number: int
    sequence_no: int
    item_type: str
    title: str
    starts_at: datetime
    ends_at: datetime

    #: Whichever of these the item's type permits. ADR 0012: never an integer.
    accommodation: ListingRefDTO | None = None
    activity: ListingRefDTO | None = None
    attraction: ListingRefDTO | None = None
    origin_destination: ListingRefDTO | None = None
    target_destination: ListingRefDTO | None = None

    #: TRANSFER only, and all three together or not at all — §12.6, ADR 0019.
    distance_m: int | None = None
    travel_seconds: int | None = None
    estimate_quality: str | None = None

    quantity: int = 1
    pax_count: int | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    currency: str | None = None

    #: §10.3: true once the covering booking is confirmed. The client renders a
    #: padlock and refuses to drag it (§24.14).
    is_locked: bool = False

    @property
    def is_approximate(self) -> bool:
        """§12.6: the UI must render an explicit label for these.

        A property rather than something each caller recomputes, so the rule
        has one home and a component cannot accidentally test the wrong value.
        """
        return self.estimate_quality == "APPROXIMATE"


@dataclass(frozen=True, slots=True)
class ItineraryDTO:
    """§7.3's `itinerary`, with its items and the findings from the last run."""

    version: int
    validation_state: str
    generated_at: datetime | None
    total_distance_m: int | None
    total_travel_seconds: int | None
    items: tuple[ItineraryItemDTO, ...] = ()
    findings: tuple[FindingDTO, ...] = ()

    @property
    def has_errors(self) -> bool:
        """§10.6: an ERROR blocks quoting. The client's Continue button reads
        this, so it is computed once here rather than by each surface."""
        return any(finding.severity == "ERROR" for finding in self.findings)


@dataclass(frozen=True, slots=True)
class TripSummaryDTO:
    """A trip as it appears in a list — §24.20's My Trips.

    Deliberately not `TripDTO` with the heavy fields left empty. A list
    endpoint that returns the detail shape teaches its callers to depend on
    fields it will later have to stop loading, and the query behind it grows
    joins nobody asked for.
    """

    public_id: UUID
    reference: str
    title: str | None
    status: str
    destination: ListingRefDTO
    start_date: date
    end_date: date
    adults: int
    children: int
    infants: int
    currency: str
    total_amount: Decimal


@dataclass(frozen=True, slots=True)
class TripDTO:
    """§7.5.10 in full, with the itinerary and flights §24.14 renders."""

    public_id: UUID
    reference: str
    title: str | None
    status: str
    destination: ListingRefDTO
    start_date: date
    end_date: date
    adults: int
    children: int
    infants: int
    currency: str
    subtotal_amount: Decimal
    fee_amount: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    priced_at: datetime | None = None
    quote_expires_at: datetime | None = None
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    version: int = 0
    itinerary: ItineraryDTO | None = None
    flights: tuple[TripFlightDTO, ...] = ()

    @property
    def party_size(self) -> int:
        """Adults and children. Infants are excluded for the reason
        `validation.PartyFacts` gives: §16.3's capacity is seats, and a lap
        infant does not occupy one. Defined in both places because the domain
        may not import this module, and `test_dto.py` pins that they agree."""
        return self.adults + self.children
