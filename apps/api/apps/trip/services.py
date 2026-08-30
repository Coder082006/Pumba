"""trip module — SRS §6.4.

Application layer (SRS §8.2 layer 2).

    The ONLY module boundary. Other modules call this and nothing else
    (SRS §6.5 rule 1). Orchestrates a use case in one transaction and
    emits domain events.

    Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

    Public interface: create_trip(), regenerate_itinerary(), validate_itinerary()

This half is §10.2's workflow up to the point where an itinerary is generated:
creating a trip, editing its dates and party, adding and removing the things
the tourist has chosen, and recording flights. `generate` is the next commit.

**Every use case takes `tourist_id`, and it goes into the query.** §30.3
requires a foreign principal to receive 404 rather than 403, so that absence
and inaccessibility are indistinguishable. `_owned` is the only way a trip is
loaded here, and it filters rather than checks: a function that fetched by
`public_id` and then compared owners would be one forgotten comparison away
from writing to a stranger's trip, and one early return away from confirming it
exists.

**Two different locks, and both apply.** `domain.lifecycle.is_editable` asks
whether the *trip* is open to changes at all — everything past DRAFT has money
or inventory committed against it. `item.is_locked` asks whether one row is
covered by a confirmed booking (§10.3). A DRAFT trip may contain a locked item,
and a PENDING_PAYMENT trip is closed to edits even where no single item is
locked, so neither check subsumes the other.

**No business constant is written here.** `trip.max_days` comes from
`system_setting` (NFR-M07), as does everything else this module thresholds on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.catalogue import services as catalogue
from apps.common.config import get_setting
from apps.common.errors import ConflictError, NotFoundError, ValidationError
from apps.common.events import DomainEvent, publish
from apps.common.state_machine import IllegalTransitionError
from apps.trip import repositories as repo
from apps.trip import selectors
from apps.trip.domain.lifecycle import TRIP_MACHINE, TripState, is_editable
from apps.trip.dto import TripDTO, TripSummaryDTO
from apps.trip.models import ItemType, ItineraryItem, Trip

__all__ = [
    "TripCreated",
    "TripCancelled",
    "ADDABLE_ITEM_TYPES",
    "LockedItemError",
    "create_trip",
    "update_trip",
    "add_item",
    "update_item",
    "remove_item",
    "set_flights",
    "cancel_trip",
    "get_trip",
    "list_trips",
]


# ---------------------------------------------------------------------------
# Events (§8.9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class TripCreated(DomainEvent):
    """Primitives only — a handler runs after commit, often elsewhere."""

    name = "trip.created"
    trip_public_id: str = ""
    tourist_id: int = 0


@dataclass(frozen=True, slots=True, kw_only=True)
class TripCancelled(DomainEvent):
    name = "trip.cancelled"
    trip_public_id: str = ""
    tourist_id: int = 0


class LockedItemError(ConflictError):
    """§10.8's `LOCKED_ITEM_CONFLICT`, naming the item."""

    code = "LOCKED_ITEM_CONFLICT"


# ---------------------------------------------------------------------------
# Loading, always by owner
# ---------------------------------------------------------------------------


def _owned(public_id: UUID, tourist_id: int) -> Trip:
    """The trip, or `NotFoundError` — never `PermissionDeniedError`.

    §30.3: a foreign principal and a nonexistent trip must be
    indistinguishable, so the owner is part of the query and the error does not
    depend on which of the two happened.
    """
    trip = selectors.trips_of(tourist_id).filter(public_id=public_id).first()
    if trip is None:
        raise NotFoundError(f"no trip {public_id}")
    return trip


def _editable(trip: Trip) -> Trip:
    """§20.5, through the domain's own predicate rather than a status literal."""
    if not is_editable(TripState(trip.status)):
        raise ConflictError(
            f"a trip in {trip.status} cannot be edited; "
            "its prices and any holds behind them are already committed"
        )
    return trip


def _unlocked(item: ItineraryItem) -> ItineraryItem:
    """§10.3, §10.8. The error names the item, because §24.14 renders a
    padlock against it and the tourist needs to know which one."""
    if item.is_locked:
        raise LockedItemError(
            f"item {item.public_id} is covered by a confirmed booking and cannot be changed"
        )
    return item


def _item_of(trip: Trip, item_public_id: UUID) -> ItineraryItem:
    itinerary = getattr(trip, "itinerary", None)
    if itinerary is None:
        raise NotFoundError(f"no item {item_public_id}")
    item: ItineraryItem | None = itinerary.items.filter(public_id=item_public_id).first()
    if item is None:
        raise NotFoundError(f"no item {item_public_id}")
    return item


def _dto(trip: Trip) -> TripDTO:
    """Reload through the read path so a write returns exactly what a read
    would. Two shapes for the same trip is how a client starts to disagree
    with itself about what it has."""
    trip.refresh_from_db()
    dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
    if dto is None:  # pragma: no cover - only reachable if the destination went
        raise NotFoundError(f"no trip {trip.public_id}")
    return dto


# ---------------------------------------------------------------------------
# Reads, re-exported so callers need one import (§6.5 rule 1)
# ---------------------------------------------------------------------------


def get_trip(public_id: UUID, *, tourist_id: int) -> TripDTO | None:
    return selectors.get_trip(public_id, tourist_id=tourist_id)


def list_trips(*, tourist_id: int) -> tuple[TripSummaryDTO, ...]:
    return selectors.list_trips(tourist_id=tourist_id)


# ---------------------------------------------------------------------------
# §10.2's workflow
# ---------------------------------------------------------------------------


#: Item types a tourist may add directly. TRANSFER is absent on purpose:
#: §10.4 *inserts* transfers, so one written by hand would be rewritten by the
#: next generate — or worse, survive it and disagree with the leg beside it.
#: §24.17's "add-custom-leg" is a Phase 6 surface over `transport`, not this.
ADDABLE_ITEM_TYPES = frozenset(
    {ItemType.STAY, ItemType.ACTIVITY, ItemType.ATTRACTION, ItemType.FREE_TIME}
)


def _check_dates(start: date, end: date) -> None:
    """The database has `end >= start`; this adds §41's ceiling.

    `trip.max_days` is a `system_setting` because a market that sells
    month-long safaris and one that sells weekend breaks disagree about it, and
    §4.1 will not have that need a deployment.
    """
    if end < start:
        raise ValidationError("a trip cannot end before it starts")
    span = (end - start).days + 1
    limit = int(get_setting("trip.max_days"))
    if span > limit:
        raise ValidationError(f"a trip may not be longer than {limit} days; this one is {span}")


@transaction.atomic
def create_trip(
    *,
    tourist_id: int,
    destination: str | UUID,
    start_date: date,
    end_date: date,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    title: str | None = None,
    today: date | None = None,
) -> TripDTO:
    """§10.2: `POST /trips` — DRAFT, with itinerary v1 created empty.

    Both rows land in one transaction. A trip with no itinerary is a shape no
    read path expects, and leaving one behind on a partial failure would make
    the very first thing the client does after creating a trip return a null
    it has no branch for.

    The destination is resolved through `catalogue.services`, which applies
    visibility: choosing a destination is a public read, so one that is not
    open today is indistinguishable from one that does not exist (§30.3).
    """
    _check_dates(start_date, end_date)

    ref = catalogue.resolve_planning_ref(destination, today=today or timezone.localdate())
    if ref is None:
        raise NotFoundError(f"no destination {destination!r}")

    trip = repo.create_trip_row(
        tourist_id=tourist_id,
        destination_id=ref.storage_id,
        title=title,
        start_date=start_date,
        end_date=end_date,
        adults=adults,
        children=children,
        infants=infants,
        # §4.2: resolved from the destination, never a hard-coded "TZS".
        currency=ref.default_currency,
    )
    repo.create_itinerary(trip)

    transaction.on_commit(
        lambda: publish(TripCreated(trip_public_id=str(trip.public_id), tourist_id=tourist_id))
    )
    return _dto(trip)


@transaction.atomic
def update_trip(
    public_id: UUID,
    *,
    tourist_id: int,
    **fields: Any,
) -> TripDTO:
    """§10.2: `PATCH /trips/{id}` — dates, party and title.

    The day skeleton §10.2 mentions is not rebuilt here. Days are derived from
    the trip's dates every time the itinerary is generated (§10.4 line 1), so
    there is no skeleton to keep in step — and materialising one would create a
    second source of truth for how many days a trip has.

    Shortening a trip can strand items outside the new range. They are left
    alone deliberately: VR-01 reports them against the item the tourist can
    see and move, where silently deleting them would remove something they
    chose without telling them.
    """
    trip = _editable(_owned(public_id, tourist_id))

    start = fields.get("start_date", trip.start_date)
    end = fields.get("end_date", trip.end_date)
    if "start_date" in fields or "end_date" in fields:
        _check_dates(start, end)

    return _dto(repo.update_trip_row(trip, **fields))


@transaction.atomic
def add_item(public_id: UUID, *, tourist_id: int, **fields: Any) -> TripDTO:
    """§10.2: `POST /trips/{id}/items`.

    The item is placed where the caller says. Sequencing is `generate`'s job
    (§10.4), and doing it here would time an item against an itinerary that is
    about to be rewritten anyway.
    """
    item_type = fields.get("item_type")
    if item_type not in ADDABLE_ITEM_TYPES:
        raise ValidationError(
            f"{item_type!r} cannot be added directly; "
            f"choose one of {sorted(t.value for t in ADDABLE_ITEM_TYPES)}"
        )

    trip = _editable(_owned(public_id, tourist_id))
    itinerary = getattr(trip, "itinerary", None)
    if itinerary is None:
        itinerary = repo.create_itinerary(trip)

    repo.create_item(itinerary=itinerary, **fields)
    return _dto(trip)


@transaction.atomic
def update_item(
    public_id: UUID, item_public_id: UUID, *, tourist_id: int, **fields: Any
) -> TripDTO:
    """§9.4.2: "Modify an unlocked item"."""
    trip = _editable(_owned(public_id, tourist_id))
    repo.update_item(_unlocked(_item_of(trip, item_public_id)), **fields)
    return _dto(trip)


@transaction.atomic
def remove_item(public_id: UUID, item_public_id: UUID, *, tourist_id: int) -> TripDTO:
    """§9.4.2: "Remove an unlocked item"."""
    trip = _editable(_owned(public_id, tourist_id))
    repo.delete_item(_unlocked(_item_of(trip, item_public_id)))
    return _dto(trip)


@transaction.atomic
def set_flights(
    public_id: UUID,
    *,
    tourist_id: int,
    flights: Sequence[Mapping[str, Any]],
    today: date | None = None,
) -> TripDTO:
    """§10.2: `PUT /trips/{id}/flights` — the whole set, replacing what is there.

    Each flight names its gateway by slug or UUID and it is resolved the same
    way a destination is, so a gateway that is not open today is not selectable
    — the transfer to it could not be planned in any case.

    §7.5.6's `is_gateway` is not checked here. A destination the tourist
    believes they are flying into is a fact about their trip, and the planner's
    business is to route to it; refusing one because the catalogue has not been
    flagged would be the platform's bookkeeping surfacing as the tourist's
    problem. VR-07 and VR-08 are what care about the timing.
    """
    trip = _editable(_owned(public_id, tourist_id))
    when = today or timezone.localdate()

    resolved: list[dict[str, Any]] = []
    for flight in flights:
        gateway = flight.get("gateway")
        ref = catalogue.resolve_planning_ref(gateway, today=when) if gateway else None
        if ref is None:
            raise NotFoundError(f"no destination {gateway!r} for the flight gateway")
        resolved.append(
            {
                **{k: v for k, v in flight.items() if k != "gateway"},
                "gateway_destination_id": ref.storage_id,
            }
        )

    repo.replace_flights(trip, resolved)
    return _dto(trip)


@transaction.atomic
def cancel_trip(public_id: UUID, *, tourist_id: int) -> TripDTO:
    """§20.5: "Any state -> CANCELLED", bounded by the terminal states.

    The transition goes through the machine rather than assigning the status,
    so a completed trip is refused for the reason §20.5 gives — a journey that
    has happened cannot be made not to have happened, and the path for that is
    §21's refund.
    """
    trip = _owned(public_id, tourist_id)
    try:
        TRIP_MACHINE.transition(TripState(trip.status), TripState.CANCELLED)
    except IllegalTransitionError as exc:
        raise ConflictError(str(exc)) from exc

    trip.status = TripState.CANCELLED.value
    trip.cancelled_at = timezone.now()
    trip.save(update_fields=["status", "cancelled_at"])

    transaction.on_commit(
        lambda: publish(TripCancelled(trip_public_id=str(trip.public_id), tourist_id=tourist_id))
    )
    return _dto(trip)
