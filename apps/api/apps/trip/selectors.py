"""trip module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). Read queries; returns DTOs.

**Ownership is a filter, never a check after the fact.** §30.3 requires a
foreign principal to receive 404 rather than 403, so that absence and
inaccessibility are indistinguishable. That is achieved by never loading
somebody else's trip in the first place: every entry point takes `tourist_id`
and it goes into the `WHERE` clause. A function that fetched by `public_id` and
then compared owners would be one forgotten comparison away from leaking a
stranger's itinerary, and one early return away from leaking its existence.

**Reading a whole trip is a fixed number of queries.** An itinerary carries a
catalogue reference on most of its rows across four tables, and the naive
shape is an N+1 per table per row. `to_trip_dto` gathers every referenced id
first, resolves each kind in one call to `catalogue.services.resolve_refs`, and
hands the result down. `test_selectors.py` pins the count, which is the only
way that stays true — the query that reintroduces an N+1 is always the one
somebody adds later for a good reason.

**The catalogue is reached through its service, never its tables.** ADR 0012
and contract `private-catalogue`: `apps.catalogue.models` and
`apps.catalogue.selectors` are closed to this module in both directions, so
`resolve_refs` is the whole of the seam.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from uuid import UUID

from django.db.models import QuerySet

from apps.catalogue import services as catalogue
from apps.catalogue.dto import ListingRefDTO
from apps.trip.domain.findings import Finding
from apps.trip.dto import (
    FindingDTO,
    ItineraryDTO,
    ItineraryItemDTO,
    TripDTO,
    TripFlightDTO,
    TripSummaryDTO,
)
from apps.trip.models import Itinerary, ItineraryItem, Trip, TripFlight

__all__ = [
    "trips_of",
    "get_trip",
    "list_trips",
    "to_trip_dto",
    "to_summary_dto",
    "to_item_dto",
    "to_finding_dto",
]

#: Which `itinerary_item` column resolves against which catalogue table.
#: One place, so a new reference cannot be added to the model and silently
#: render as nothing.
_ITEM_REFERENCES: tuple[tuple[str, str], ...] = (
    ("accommodation_id", "accommodation"),
    ("activity_id", "activity"),
    ("attraction_id", "attraction"),
    ("origin_destination_id", "destination"),
    ("target_destination_id", "destination"),
)

#: `kind -> {id: ListingRefDTO}`, built once per trip.
_Refs = dict[str, dict[int, ListingRefDTO]]


def trips_of(tourist_id: int) -> QuerySet[Trip]:
    """Every trip this tourist owns. The only door into the table.

    Public so that services compose from it rather than writing their own
    `Trip.objects.filter(...)`, which is where an ownership predicate goes
    missing.
    """
    return Trip.objects.filter(tourist_id=tourist_id)


def _resolve_all(items: Sequence[ItineraryItem], destination_ids: Iterable[int]) -> _Refs:
    """One `resolve_refs` call per catalogue table, for the whole trip."""
    wanted: dict[str, set[int]] = {}
    for column, kind in _ITEM_REFERENCES:
        for item in items:
            value = getattr(item, column)
            if value is not None:
                wanted.setdefault(kind, set()).add(value)
    for value in destination_ids:
        wanted.setdefault("destination", set()).add(value)

    return {kind: catalogue.resolve_refs(kind, sorted(ids)) for kind, ids in wanted.items()}


def _ref(refs: _Refs, kind: str, value: int | None) -> ListingRefDTO | None:
    if value is None:
        return None
    return refs.get(kind, {}).get(value)


def to_finding_dto(finding: Finding, item_ids: dict[int, UUID]) -> FindingDTO:
    """A domain finding, with its item references translated for the wire.

    The domain identifies items by the integer primary key, because it sorts
    and compares by it and §10.4's tie-break depends on a total order that is
    cheap to compute. §7.2 does not permit that integer to reach a client, so
    the swap happens here — once, where the map is already to hand.

    An id with no known item is dropped rather than passed through. It can only
    mean a finding about an item the caller did not supply, and a null in an
    `item_ids` array is something the client would have to defend against on
    every render.
    """
    return FindingDTO(
        code=finding.code,
        severity=finding.severity.value,
        message=finding.message,
        item_ids=tuple(item_ids[i] for i in finding.item_ids if i in item_ids),
        suggested_action=finding.suggested_action.value,
        context=dict(finding.context),
    )


def to_item_dto(item: ItineraryItem, refs: _Refs) -> ItineraryItemDTO:
    return ItineraryItemDTO(
        public_id=item.public_id,
        day_number=item.day_number,
        sequence_no=item.sequence_no,
        item_type=item.item_type,
        title=item.title,
        starts_at=item.starts_at,
        ends_at=item.ends_at,
        accommodation=_ref(refs, "accommodation", item.accommodation_id),
        activity=_ref(refs, "activity", item.activity_id),
        attraction=_ref(refs, "attraction", item.attraction_id),
        origin_destination=_ref(refs, "destination", item.origin_destination_id),
        target_destination=_ref(refs, "destination", item.target_destination_id),
        distance_m=item.distance_m,
        travel_seconds=item.travel_seconds,
        estimate_quality=item.estimate_quality,
        quantity=item.quantity,
        pax_count=item.pax_count,
        unit_price=item.unit_price,
        line_total=item.line_total,
        currency=item.currency,
        is_locked=item.is_locked,
    )


def to_flight_dto(flight: TripFlight, refs: _Refs) -> TripFlightDTO | None:
    """`None` where the gateway destination could not be resolved.

    That means the row was soft-deleted out from under a stored trip, which is
    a data-repair problem rather than something to render. A flight with no
    gateway has no meaning — §11.3 times the pickup from it — so returning a
    half-populated DTO would push the decision onto every consumer.
    """
    gateway = _ref(refs, "destination", flight.gateway_destination_id)
    if gateway is None:
        return None
    return TripFlightDTO(
        direction=flight.direction,
        flight_number=flight.flight_number,
        airline_iata=flight.airline_iata,
        gateway=gateway,
        scheduled_at=flight.scheduled_at,
        actual_at=flight.actual_at,
        terminal=flight.terminal,
        pax_count=flight.pax_count,
        luggage_count=flight.luggage_count,
    )


def to_itinerary_dto(
    itinerary: Itinerary,
    items: Sequence[ItineraryItem],
    refs: _Refs,
    findings: Sequence[Finding] = (),
) -> ItineraryDTO:
    item_ids = {item.pk: item.public_id for item in items}
    return ItineraryDTO(
        version=itinerary.version,
        validation_state=itinerary.validation_state,
        generated_at=itinerary.generated_at,
        total_distance_m=itinerary.total_distance_m,
        total_travel_seconds=itinerary.total_travel_seconds,
        items=tuple(to_item_dto(item, refs) for item in items),
        findings=tuple(to_finding_dto(finding, item_ids) for finding in findings),
    )


def to_summary_dto(trip: Trip, refs: _Refs) -> TripSummaryDTO | None:
    destination = _ref(refs, "destination", trip.destination_id)
    if destination is None:
        return None
    return TripSummaryDTO(
        public_id=trip.public_id,
        reference=trip.reference,
        title=trip.title,
        status=trip.status,
        destination=destination,
        start_date=trip.start_date,
        end_date=trip.end_date,
        adults=trip.adults,
        children=trip.children,
        infants=trip.infants,
        currency=trip.currency,
        total_amount=trip.total_amount,
    )


def to_trip_dto(trip: Trip, *, findings: Sequence[Finding] = ()) -> TripDTO | None:
    """The whole trip, in a fixed number of queries.

    Returns `None` when the trip's destination has been soft-deleted, for the
    same reason `to_flight_dto` does: §7.5.10 makes `destination_id` NOT NULL,
    so a trip without one is not a trip with a missing field, it is a broken
    row. The caller turns that into a 404 rather than rendering a shape the
    client's types say cannot exist.
    """
    itinerary = getattr(trip, "itinerary", None)
    items = list(itinerary.items.all()) if itinerary is not None else []
    flights = list(trip.flights.all())

    refs = _resolve_all(
        items,
        [trip.destination_id, *(f.gateway_destination_id for f in flights)],
    )

    destination = _ref(refs, "destination", trip.destination_id)
    if destination is None:
        return None

    return TripDTO(
        public_id=trip.public_id,
        reference=trip.reference,
        title=trip.title,
        status=trip.status,
        destination=destination,
        start_date=trip.start_date,
        end_date=trip.end_date,
        adults=trip.adults,
        children=trip.children,
        infants=trip.infants,
        currency=trip.currency,
        subtotal_amount=trip.subtotal_amount,
        fee_amount=trip.fee_amount,
        tax_amount=trip.tax_amount,
        total_amount=trip.total_amount,
        priced_at=trip.priced_at,
        quote_expires_at=trip.quote_expires_at,
        confirmed_at=trip.confirmed_at,
        cancelled_at=trip.cancelled_at,
        version=trip.version,
        itinerary=(
            to_itinerary_dto(itinerary, items, refs, findings) if itinerary is not None else None
        ),
        flights=tuple(
            dto for dto in (to_flight_dto(flight, refs) for flight in flights) if dto is not None
        ),
    )


def get_trip(public_id: UUID, *, tourist_id: int) -> TripDTO | None:
    """One trip, or `None` — which the view renders as 404 (§30.3).

    `tourist_id` is part of the query, not a check afterwards. A foreign
    principal gets the same answer as somebody asking for a trip that never
    existed, which is what §30.3 means by making absence and inaccessibility
    indistinguishable.
    """
    trip = (
        trips_of(tourist_id)
        .filter(public_id=public_id)
        .select_related("itinerary")
        .prefetch_related("itinerary__items", "flights")
        .first()
    )
    return to_trip_dto(trip) if trip is not None else None


def list_trips(*, tourist_id: int) -> tuple[TripSummaryDTO, ...]:
    """§24.20's My Trips.

    Summaries rather than full trips: the list does not render an itinerary,
    and loading one per row would be a fortnight of items per card.
    """
    trips = list(trips_of(tourist_id))
    refs = _resolve_all([], [trip.destination_id for trip in trips])
    return tuple(dto for dto in (to_summary_dto(trip, refs) for trip in trips) if dto is not None)
