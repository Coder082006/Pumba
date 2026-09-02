"""trip module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). All ORM writes.

Three rules, the same three `catalogue.repositories` established, and each
matters more here than it did there.

**`full_clean()` before every save.** §8.6 puts validation in the model layer
so the API, the console and any loader cannot disagree about what a valid row
is. This module's most interesting rules are cross-field — the five per-item
shape constraints of §7.5.11 — and they are enforced by the database anyway;
`full_clean` is what turns an `IntegrityError` from Postgres into a
`ValidationError` naming the field, before the transaction is poisoned.

**Explicit writable field sets.** Each function names what it accepts and
refuses anything else, rather than iterating over whatever the caller passed.
The columns this would expose are not cosmetic: `is_locked` decides whether a
regeneration may touch an item, `booking_id` ties it to money, and
`estimate_quality` is the label §12.6 requires. A repository that `setattr`s
arbitrary keys turns any future serializer bug into a way to set all three from
the wire.

**No `DELETE` where the schema expects a state change.** §7.2 keeps trips out
of the soft-delete set deliberately — a journey somebody planned is a record,
and abandoning one is `CANCELLED` with its own timestamp, which is a state in
§20.5. Itinerary items are different: an unlocked item the tourist removed
never happened, and §10.8's archive is where a *superseded* version's rows go.

**Nothing here checks ownership.** That is `selectors.trips_of` and the service
layer above it, and the separation is deliberate: a repository that also
enforced authorisation would be doing it in a second place, and the place that
forgets is the one that matters. Every function here takes a row that a caller
has already proved it may write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, TypeVar

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Model
from django.utils import timezone

from apps.common.errors import ValidationError
from apps.common.reference import new_reference
from apps.trip.domain.lifecycle import TRIP_MACHINE, TripState
from apps.trip.models import Itinerary, ItineraryItem, Trip, TripFlight, TripStatus

__all__ = [
    "UnwritableFieldError",
    "TRIP_REFERENCE_PREFIX",
    "create_trip_row",
    "update_trip_row",
    "create_itinerary",
    "create_item",
    "update_item",
    "delete_item",
    "replace_flights",
    "bind_departure",
    "price_trip",
    "unprice_trip",
]

_M = TypeVar("_M", bound=Model)

#: §7.5.10's `TRP-YYYY-NNNNNNN`.
TRIP_REFERENCE_PREFIX = "TRP"

#: How many fresh references to try before giving up. The space is ten million
#: per year, so two collisions in a row is already vanishingly unlikely; five
#: is here so that a genuinely exhausted year fails loudly rather than looping.
_REFERENCE_ATTEMPTS = 5


class UnwritableFieldError(ValueError):
    """A field this repository will not accept from a caller."""


#: What each write may set. Read as the answer to "what can a request change",
#: because that is exactly what it is.
_WRITABLE: dict[type[Model], frozenset[str]] = {
    Trip: frozenset(
        {
            "tourist_id",
            "destination_id",
            "title",
            "start_date",
            "end_date",
            "adults",
            "children",
            "infants",
            "currency",
        }
    ),
    ItineraryItem: frozenset(
        {
            "itinerary",
            "day_number",
            "sequence_no",
            "item_type",
            "title",
            "starts_at",
            "ends_at",
            "accommodation_id",
            "activity_id",
            "activity_departure_id",
            "attraction_id",
            "origin_destination_id",
            "target_destination_id",
            "location_point",
            "origin_point",
            "target_point",
            "distance_m",
            "travel_seconds",
            "estimate_quality",
            "quantity",
            "pax_count",
            "unit_price",
            "line_total",
            "currency",
        }
    ),
    TripFlight: frozenset(
        {
            "trip",
            "direction",
            "flight_number",
            "airline_iata",
            "gateway_destination_id",
            "scheduled_at",
            "actual_at",
            "terminal",
            "pax_count",
            "luggage_count",
        }
    ),
}

#: Columns no request may set, whatever else changes. Stated separately from
#: `_WRITABLE` so the *reason* survives: `status` moves only through §20.5's
#: machine, the money columns only through §10.7's computation, `is_locked` and
#: `booking_id` only through §20.8's confirmation routine, and `reference` and
#: `public_id` are identity.
NEVER_WRITABLE = frozenset(
    {
        "public_id",
        "reference",
        "status",
        "version",
        "is_locked",
        "booking_id",
        "subtotal_amount",
        "fee_amount",
        "tax_amount",
        "total_amount",
        "priced_at",
        "quote_expires_at",
        "confirmed_at",
        "cancelled_at",
    }
)


def _check(model: type[Model], fields: Mapping[str, Any]) -> None:
    offered = set(fields)
    allowed = _WRITABLE[model]
    unwritable = offered - allowed
    if unwritable:
        raise UnwritableFieldError(
            f"{model.__name__} will not accept {sorted(unwritable)}; "
            f"writable fields are {sorted(allowed)}"
        )


def _save(row: _M) -> _M:
    """`full_clean` then save, translating Django's error into the platform's.

    §32 gives the API one error envelope, and a `django.core.exceptions.
    ValidationError` escaping this layer would reach the handler as a 500
    rather than as the 422 it is.
    """
    try:
        row.full_clean()
    except DjangoValidationError as exc:
        raise ValidationError(_readable(exc)) from exc
    row.save()
    return row


def _readable(exc: DjangoValidationError) -> str:
    parts = [
        f"{field}: {'; '.join(str(m) for m in messages)}"
        for field, messages in sorted(getattr(exc, "message_dict", {}).items())
    ]
    return " | ".join(parts) or str(exc)


@transaction.atomic
def create_trip_row(**fields: Any) -> Trip:
    """A trip in DRAFT with a fresh reference — §7.5.10, §10.2.

    The reference is retried rather than pre-checked. `new_reference` explains
    why at length: a check followed by an insert is a race that appears exactly
    when two people create a trip at the same moment, and the UNIQUE constraint
    is the only authority that is not racing.

    The savepoint per attempt is what makes the retry possible at all. A failed
    `INSERT` marks the whole transaction unusable in Postgres, so without one
    the second attempt would fail on a poisoned transaction rather than on its
    own merits.
    """
    _check(Trip, fields)
    last: IntegrityError | None = None
    for _ in range(_REFERENCE_ATTEMPTS):
        try:
            with transaction.atomic():
                return _save(Trip(reference=new_reference(TRIP_REFERENCE_PREFIX), **fields))
        except IntegrityError as exc:
            if "reference" not in str(exc):
                raise
            last = exc
    raise ValidationError(
        "could not allocate a unique trip reference after " f"{_REFERENCE_ATTEMPTS} attempts"
    ) from last


@transaction.atomic
def update_trip_row(trip: Trip, **fields: Any) -> Trip:
    """A partial update. Absent keys are left alone, not blanked.

    `full_clean` runs over the whole row rather than the changed fields,
    because the rules worth catching are cross-field — an end date before a
    start, a total that no longer equals its parts — and validating only what
    changed would pass every one of them.
    """
    _check(Trip, fields)
    for name, value in fields.items():
        setattr(trip, name, value)
    return _save(trip)


@transaction.atomic
def create_itinerary(trip: Trip) -> Itinerary:
    """§10.2: "itinerary v1 created (empty)", in the same transaction as the
    trip. A trip without one is a shape no read path expects and no writer
    should be able to leave behind."""
    return _save(Itinerary(trip=trip))


@transaction.atomic
def create_item(**fields: Any) -> ItineraryItem:
    return _save(ItineraryItem(**_checked(ItineraryItem, fields)))


@transaction.atomic
def update_item(item: ItineraryItem, **fields: Any) -> ItineraryItem:
    _check(ItineraryItem, fields)
    for name, value in fields.items():
        setattr(item, name, value)
    return _save(item)


# ---------------------------------------------------------------------------
# The quote — §9.4.5, ADR 0022
# ---------------------------------------------------------------------------
#
# Three writes `booking` cannot make and `trip` will not expose as fields. Each
# touches a column in `NEVER_WRITABLE`, which is exactly why each is a named
# function: `status` moves only through §20.5's machine, the money columns only
# through §10.7's computation, and `priced_at` and `quote_expires_at` only
# through a quote. A `update_trip_row(status="PRICED")` would make all three
# reachable from any serializer that grew a field.


@transaction.atomic
def bind_departure(item: ItineraryItem, *, departure_id: int) -> ItineraryItem:
    """Point an ACTIVITY item at the `activity_departure` it was quoted on.

    §7.5.11 has carried this column since Phase 4 with nothing to write it —
    `trip.services.DEFERRED_INPUTS["VR-06"]` records why — because the
    departure lives in `inventory` and §6.4 forbids `trip -> inventory`. The
    id arrives from `booking`, which may see both (ADR 0022).

    The SQL foreign key added by `trip/0001` is what refuses an id that names
    no departure, so there is no lookup here to disagree with it.
    """
    item.activity_departure_id = departure_id
    return _save(item)


@transaction.atomic
def price_trip(
    trip: Trip,
    *,
    items: Sequence[ItineraryItem],
    cost: Any,
    expires_at: datetime,
) -> Trip:
    """§9.4.5 steps 6 and 7: the totals, the state, and the clock.

    One function rather than three, because the three are one fact. A trip
    whose `status` said PRICED while its `quote_expires_at` was null would be
    priced forever, and a total written without a status would be a figure
    nobody had offered.

    `status` is set here rather than through `TRIP_MACHINE.transition` for the
    case the machine has no edge for: a re-quote of an already-PRICED trip.
    §20.5 draws `DRAFT -> PRICED` and `PRICED -> DRAFT` and no self-loop, and
    §9.4.5 permits quoting from either state — so the service layer validates
    the source state and this writes the result. `common.state_machine` refuses
    a duplicate edge at construction time, which is why adding one was not the
    answer.
    """
    line_totals = dict(cost.lines)
    for row in items:
        money = line_totals.get(row.pk)
        if money is None:
            continue
        row.line_total = money.amount
        row.currency = money.currency
        row.save(update_fields=["line_total", "currency", "updated_at"])

    trip.subtotal_amount = cost.subtotal.amount
    trip.fee_amount = cost.fee.amount
    trip.tax_amount = cost.tax.amount
    trip.total_amount = cost.total.amount
    trip.status = TripStatus.PRICED
    trip.priced_at = timezone.now()
    trip.quote_expires_at = expires_at
    trip.version += 1
    trip.save(
        update_fields=[
            "subtotal_amount",
            "fee_amount",
            "tax_amount",
            "total_amount",
            "status",
            "priced_at",
            "quote_expires_at",
            "version",
            "updated_at",
        ]
    )
    return trip


@transaction.atomic
def unprice_trip(trip: Trip) -> Trip:
    """§20.5's `PRICED --quote expired--> DRAFT`, driven by §17.5's sweeper.

    **The totals stay.** Only the offer expired, not the arithmetic: the trip
    still costs what it costs, and blanking the figures would leave a tourist
    who walked away for half an hour looking at a plan that appeared to have
    lost its price. `priced_at` and `quote_expires_at` are cleared, because
    those describe an offer that no longer stands — and §9.4.7 asserts
    `holds are live` against exactly that pair.
    """
    trip.status = TRIP_MACHINE.transition(TripState(trip.status), TripState.DRAFT)
    trip.priced_at = None
    trip.quote_expires_at = None
    trip.version += 1
    trip.save(update_fields=["status", "priced_at", "quote_expires_at", "version", "updated_at"])
    return trip


def _checked(model: type[Model], fields: Mapping[str, Any]) -> dict[str, Any]:
    _check(model, fields)
    return dict(fields)


@transaction.atomic
def delete_item(item: ItineraryItem) -> None:
    """A real delete, unlike anything in `catalogue`.

    An unlocked item the tourist removed never happened; there is no audit
    interest in it and no foreign key pointing at it. A *superseded* item is a
    different thing entirely and goes to §10.8's archive on regeneration, which
    is the only other way a row leaves this table.
    """
    item.delete()


@transaction.atomic
def replace_flights(trip: Trip, flights: Sequence[Mapping[str, Any]]) -> list[TripFlight]:
    """§9.4.2's `PUT /trips/{id}/flights` — the whole set, or none of it.

    A `PUT` replaces, so this deletes what is there and writes what was given,
    inside one transaction. Doing it as a diff would need to decide what an
    absent direction means, and §11.2's answer is that the tourist no longer
    has that flight — which is a deletion, not a silent retention.

    R19 bounds this at two, one per direction, and the database enforces it.
    """
    trip.flights.all().delete()
    written: list[TripFlight] = []
    for flight in flights:
        # `trip` is supplied here, never taken from the caller: a payload that
        # named a different trip would write one tourist's flight onto
        # another's, and the ownership check upstream would have passed.
        fields = {k: v for k, v in flight.items() if k != "trip"}
        written.append(_save(TripFlight(trip=trip, **_checked(TripFlight, fields))))
    return written
