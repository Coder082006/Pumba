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
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone

from apps.catalogue import services as catalogue
from apps.common.config import get_setting
from apps.common.errors import ConflictError, NotFoundError, ValidationError
from apps.common.events import DomainEvent, publish
from apps.common.geo import Coordinates
from apps.common.money import Money
from apps.common.state_machine import IllegalTransitionError
from apps.trip import repositories as repo
from apps.trip import selectors
from apps.trip.domain.costing import PricedItem, TripCost, compute_cost
from apps.trip.domain.findings import Finding, Severity, worst_severity
from apps.trip.domain.lifecycle import TRIP_MACHINE, TripState, is_editable
from apps.trip.domain.sequencing import (
    Buffers,
    Kind,
    PlannedItem,
    SequenceResult,
    sequence_trip,
)
from apps.trip.domain.validation import ItemFacts, Limits, PartyFacts, TripFacts, validate
from apps.trip.dto import TripDTO, TripSummaryDTO
from apps.trip.models import (
    ItemType,
    Itinerary,
    ItineraryItem,
    ItineraryItemArchive,
    Trip,
    ValidationState,
)
from apps.trip.travel import build_travel_time, place_key

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
    "generate_itinerary",
    "ItineraryGenerated",
    "DEFERRED_INPUTS",
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


@dataclass(frozen=True, slots=True, kw_only=True)
class ItineraryGenerated(DomainEvent):
    """§10.8. `version` is the one just written, not the one it replaced."""

    name = "trip.itinerary_generated"
    trip_public_id: str = ""
    tourist_id: int = 0
    version: int = 0
    error_count: int = 0


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


def _destination_date(trip: Trip) -> date:
    """Today, where the trip is.

    Visibility turns on `launch_date` and `_check_dates` on "is this in the
    past", and both are questions about the destination rather than about the
    server: a date in Zanzibar is a date in UTC's tomorrow for three hours
    every night, so a server-side `date.today()` answers inconsistently
    depending on the hour somebody happened to press the button.

    Falls back to the server's date only if the destination has gone, which
    `resolve_refs` reports as an absent key rather than an error — a trip whose
    destination was deleted is a data fault for VR-09 to name, not a reason for
    an unrelated call to raise.
    """
    ref = catalogue.resolve_refs("destination", [trip.destination_id]).get(trip.destination_id)
    if ref is None:  # pragma: no cover - only if the destination row was deleted
        return timezone.localdate()
    return timezone.localtime(timezone.now(), ZoneInfo(ref.timezone)).date()


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


def _check_dates(start: date, end: date, *, today: date | None = None) -> None:
    """The database has `end >= start`; this adds the other two date rules.

    `trip.max_days` is a `system_setting` because a market that sells
    month-long safaris and one that sells weekend breaks disagree about it, and
    §4.1 will not have that need a deployment.

    **`today` is the destination's date, not the server's** (§37.4, TC-031).
    A trip starting today in Zanzibar is still yesterday in UTC for three hours
    every night, so a server-side `date.today()` would refuse an ordinary
    evening booking — and would do it inconsistently, depending on the hour
    somebody happened to press the button. It is passed in rather than read
    here so this function stays free of both the clock and the catalogue.

    A start date *of* today is allowed. §37.4's case is "start_date yesterday",
    and somebody booking a day trip for this afternoon is not making a mistake.
    """
    if end < start:
        raise ValidationError("a trip cannot end before it starts")
    if today is not None and start < today:
        raise ValidationError(f"a trip cannot start in the past; {start} is before {today}")
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
    # The destination is resolved first because the date rules need its
    # timezone: "is this in the past" is a question about where the trip is.
    ref = catalogue.resolve_planning_ref(destination, today=today or timezone.localdate())
    if ref is None:
        raise NotFoundError(f"no destination {destination!r}")

    _check_dates(
        start_date,
        end_date,
        today=today or timezone.localtime(timezone.now(), ZoneInfo(ref.timezone)).date(),
    )

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
        # No `today` here, deliberately. TC-031 is about *opening* a trip in
        # the past; a trip already under way legitimately has a start date
        # behind it, and refusing to let its owner extend the end date would
        # be the rule firing on the wrong thing.
        _check_dates(start, end)

    return _dto(repo.update_trip_row(trip, **fields))


#: The listing a given item type refers to, as the request names it and as the
#: row stores it. §7.5.11 gives `itinerary_item` one nullable reference column
#: per kind and the five CHECK constraints on the model make exactly one of
#: them mandatory for its type, so this map is the same fact in the shape the
#: request arrives in.
#:
#: FREE_TIME is absent because it refers to nothing: §10.4 line 18 inserts it
#: to describe a gap, and there is no catalogue row for an afternoon off.
ITEM_LISTING: Mapping[ItemType, tuple[str, str]] = MappingProxyType(
    {
        ItemType.STAY: ("accommodation", "accommodation_id"),
        ItemType.ACTIVITY: ("activity", "activity_id"),
        ItemType.ATTRACTION: ("attraction", "attraction_id"),
    }
)


@transaction.atomic
def add_item(
    public_id: UUID, *, tourist_id: int, today: date | None = None, **fields: Any
) -> TripDTO:
    """§10.2: `POST /trips/{id}/items`.

    The item is placed where the caller says. Sequencing is `generate`'s job
    (§10.4), and doing it here would time an item against an itinerary that is
    about to be rewritten anyway.

    **The listing arrives named and leaves numbered.** The request carries a
    slug or UUID — the thing a client actually holds, since §7.2 keeps
    sequential integers inside the database — and `catalogue.resolve_listing_ref`
    turns it into the `*_id` column ADR 0012 stores. Visibility applies there,
    so adding a withdrawn listing is a 404 and not a trip that silently
    contains something nobody can sell.

    **The title comes from the listing, not the request.** A client-supplied
    one would let two tourists' plans disagree about what the same activity is
    called, and would let one of them write anything at all into a document the
    platform later emails as a confirmation. A caller may still title a
    FREE_TIME block, which names no listing.

    **`today` is the trip's destination's date.** The same reason `create_trip`
    resolves it that way: a listing's visibility turns on `launch_date`, and
    the server's date is the wrong one for three hours every night.
    """
    item_type = fields.get("item_type")
    if item_type not in ADDABLE_ITEM_TYPES:
        raise ValidationError(
            f"{item_type!r} cannot be added directly; "
            f"choose one of {sorted(t.value for t in ADDABLE_ITEM_TYPES)}"
        )

    trip = _editable(_owned(public_id, tourist_id))

    kind, column = ITEM_LISTING.get(ItemType(item_type), ("", ""))
    named = fields.pop(kind, None) if kind else None
    # Every field this map knows about is consumed here, whichever type it is,
    # so naming an accommodation on an ACTIVITY does not leave a stray keyword
    # for the repository to reject with a message about the ORM.
    for other, _ in ITEM_LISTING.values():
        fields.pop(other, None)

    if kind:
        if named is None:
            raise ValidationError(f"a {item_type} item must name an {kind}")
        listing = catalogue.resolve_listing_ref(kind, named, today=today or _destination_date(trip))
        if listing is None:
            raise NotFoundError(f"no {kind} {named!r}")
        fields[column] = listing.storage_id
        fields["title"] = listing.name
    elif not fields.get("title"):
        raise ValidationError(f"a {item_type} item must carry a title")

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


# ---------------------------------------------------------------------------
# §10.2 step 6 onwards — generate
# ---------------------------------------------------------------------------


def _local_date(instant: datetime, zone: ZoneInfo) -> date:
    """The calendar date an instant falls on **where the trip is**.

    Not `timezone.localtime`, which reads the server's zone. The server runs
    UTC and Zanzibar is UTC+3, so an activity at 01:00 local is 22:00 the
    previous day in UTC — and a day number derived from that is off by one for
    every evening item in the catalogue's real market. It first showed up as a
    `day_number` of 0 failing the one-based CHECK, which is the constraint
    doing its job; without it the item would simply have been filed under the
    wrong day.
    """
    return instant.astimezone(zone).date()


def _stay_anchors(
    item: ItineraryItem, trip: Trip, zone: ZoneInfo
) -> list[tuple[Kind, date, datetime]]:
    """Every day a STAY is present on — §10.4 as amended by ADR 0020.

    §10.4 line 4 lists a stay as "check-in/out", so it appeared on the day it
    began and the day it ended and on no day between. Line 11 only inserts a
    transfer between *adjacent* items, so a middle day holding one activity had
    nothing to be adjacent to and the tourist was shown something to do with no
    way of getting to it. ADR 0020 amends the line: a stay anchors every day it
    covers.

    Three kinds of anchor, and the rank decides where each sits in the day:

    * **check-in** on the first day, rank 4, sorting late — you arrive, do
      things, and then check in.
    * **check-out** on the last day, rank 1, sorting early.
    * **a departure anchor** on every day between, also rank 1, because
      "leaving the accommodation" is exactly what rank 1 means. No new rank is
      introduced; §10.4's tie-break list is untouched.

    **The middle-day anchor sits at local midnight, and that is not a claim
    about when anybody gets up.** It exists to order the day. The transfer's
    real times are derived from the item it serves — line 14 times a leg
    backwards from `B.starts_at` less the buffer — so the tourist is told when
    to leave in order to arrive, which is computed rather than invented.

    No evening anchor, and therefore no return leg. Nothing knows when a day
    ends, and an anchor at the end of the local day would plan a journey at
    23:30 — worse than planning none. ADR 0020 records that.
    """
    start = _local_date(item.starts_at, zone)
    end = _local_date(item.ends_at, zone)

    anchors: list[tuple[Kind, date, datetime]] = [(Kind.STAY_CHECK_IN, start, item.starts_at)]
    if end != start:
        anchors.append((Kind.STAY_CHECK_OUT, end, item.ends_at))

    day = start + timedelta(days=1)
    while day < end:
        anchors.append((Kind.STAY_CHECK_OUT, day, datetime.combine(day, time.min, tzinfo=zone)))
        day += timedelta(days=1)

    return [
        (kind, when, at) for kind, when, at in anchors if trip.start_date <= when <= trip.end_date
    ]


_KIND_FOR_TYPE = {
    ItemType.ACTIVITY: Kind.ACTIVITY,
    ItemType.ATTRACTION: Kind.ATTRACTION,
    ItemType.TRANSFER: Kind.TRANSFER,
    ItemType.FREE_TIME: Kind.FREE_TIME,
}


@dataclass(frozen=True, slots=True)
class _Facts:
    """Everything the catalogue was asked for, gathered once."""

    places: dict[str, Coordinates]
    activities: dict[int, catalogue.ActivityFacts]
    attractions: dict[int, catalogue.AttractionFacts]
    destinations: dict[int, catalogue.PlaceFacts]
    accommodations: dict[int, catalogue.PlaceFacts]
    open_at: dict[int, bool | None]


def _gather(items: Sequence[ItineraryItem], trip: Trip) -> _Facts:
    """One call per catalogue table, whatever the itinerary contains.

    The N+1 this shape invites is four queries per item. `test_services_
    generate.py` pins the count and adds items to prove it does not move.
    """
    activity_ids = [i.activity_id for i in items if i.activity_id]
    attraction_ids = [i.attraction_id for i in items if i.attraction_id]
    accommodation_ids = [i.accommodation_id for i in items if i.accommodation_id]
    destination_ids = [trip.destination_id, *(i.origin_destination_id for i in items)]
    destination_ids += [i.target_destination_id for i in items]

    activities = catalogue.activity_facts(activity_ids)
    attractions = catalogue.attraction_facts(attraction_ids)
    accommodations = catalogue.place_facts("accommodation", accommodation_ids)
    destinations = catalogue.place_facts(
        "destination", [d for d in destination_ids if d is not None]
    )

    places: dict[str, Coordinates] = {}
    for activity in activities.values():
        places[place_key("activity", activity.place.storage_id)] = activity.place.coordinates
    for attraction in attractions.values():
        places[place_key("attraction", attraction.place.storage_id)] = attraction.place.coordinates
    for place in accommodations.values():
        places[place_key("accommodation", place.storage_id)] = place.coordinates
    for place in destinations.values():
        places[place_key("destination", place.storage_id)] = place.coordinates

    # VR-12 only asks about attractions that are actually scheduled.
    open_at = catalogue.opening_status(
        [
            (i.attraction_id, i.starts_at)
            for i in items
            if i.attraction_id and i.item_type == ItemType.ATTRACTION
        ]
    )
    return _Facts(places, activities, attractions, destinations, accommodations, open_at)


def _location_of(item: ItineraryItem) -> str | None:
    """The key the sequencer compares and routes between.

    A free-entry stay anchor has no catalogue row (ADR 0013), so its key is
    derived from the item itself — it still needs to be *a* place, or the
    planner would route straight through it.
    """
    if item.accommodation_id:
        return place_key("accommodation", item.accommodation_id)
    if item.activity_id:
        return place_key("activity", item.activity_id)
    if item.attraction_id:
        return place_key("attraction", item.attraction_id)
    if item.location_point is not None:
        return place_key("item", item.public_id)
    return None


def _planned(
    items: Sequence[ItineraryItem], trip: Trip, facts: _Facts, zone: ZoneInfo
) -> list[PlannedItem]:
    """Rows into the sequencer's own shape, with stays expanded."""
    planned: list[PlannedItem] = []
    for item in items:
        if item.is_locked:
            # §10.3: locked items are never rewritten, but the sequencer still
            # needs to see them — a transfer has to be planned *around* one.
            pass
        location = _location_of(item)
        day = (_local_date(item.starts_at, zone) - trip.start_date).days + 1

        if item.item_type == ItemType.STAY:
            for kind, when, at_local in _stay_anchors(item, trip, zone):
                anchor_day = (when - trip.start_date).days + 1
                planned.append(
                    PlannedItem(
                        item_id=item.pk,
                        kind=kind,
                        title=item.title,
                        day_number=anchor_day,
                        starts_at=at_local,
                        ends_at=at_local,
                        start_location=location,
                        end_location=location,
                        is_locked=item.is_locked,
                    )
                )
            continue

        kind = _KIND_FOR_TYPE[ItemType(item.item_type)]
        visit = 0
        if item.attraction_id and item.attraction_id in facts.attractions:
            visit = facts.attractions[item.attraction_id].visit_minutes or 0

        planned.append(
            PlannedItem(
                item_id=item.pk,
                kind=kind,
                title=item.title,
                day_number=day,
                starts_at=item.starts_at,
                ends_at=item.ends_at,
                start_location=location,
                end_location=location,
                visit_minutes=visit,
                is_locked=item.is_locked,
                distance_m=item.distance_m,
                travel_seconds=item.travel_seconds,
                estimate_quality=item.estimate_quality,
            )
        )
    return planned


def _item_facts(
    items: Sequence[ItineraryItem],
    planned: Sequence[PlannedItem],
    facts: _Facts,
    zone: ZoneInfo,
) -> list[ItemFacts]:
    """§10.6's inputs, resolved from the catalogue rather than guessed."""
    by_id = {i.pk: i for i in items}
    out: list[ItemFacts] = []
    for entry in planned:
        row = by_id.get(entry.item_id)
        activity = facts.activities.get(row.activity_id) if row and row.activity_id else None

        nights: tuple[date, ...] = ()
        if row is not None and row.item_type == ItemType.STAY:
            start = _local_date(row.starts_at, zone)
            end = _local_date(row.ends_at, zone)
            # The nights a stay covers are its dates minus the last: a stay
            # from the 1st to the 4th covers three nights, not four. VR-04 and
            # VR-16 both count nights, and an off-by-one here would report a
            # departure day as unaccommodated on every trip.
            nights = tuple(start + timedelta(days=n) for n in range((end - start).days))

        listing_active = True
        if activity is not None:
            listing_active = activity.place.is_active
        elif row is not None and row.attraction_id in facts.attractions:
            listing_active = facts.attractions[row.attraction_id].place.is_active
        elif row is not None and row.accommodation_id in facts.accommodations:
            # `place_facts` returns a PlaceFacts directly; only the activity
            # and attraction shapes wrap one in `.place`.
            listing_active = facts.accommodations[row.accommodation_id].is_active

        out.append(
            ItemFacts(
                item_id=entry.item_id,
                kind=entry.kind,
                day_number=entry.day_number,
                title=entry.title,
                starts_at=entry.starts_at,
                ends_at=entry.ends_at,
                start_location=entry.start_location,
                end_location=entry.end_location,
                currency=row.currency if row else None,
                min_pax=activity.min_pax if activity else None,
                max_pax=activity.max_pax if activity else None,
                booking_cutoff_hours=activity.booking_cutoff_hours if activity else None,
                # `departs_at` is the *departure's* scheduled time, not the
                # item's own start. Passing the item's start would compare a
                # time against itself and make VR-06 fire on every activity,
                # which is exactly what it did until a test caught it.
                #
                # Departures live in `inventory`, which §6.4 does not let this
                # module import, and nothing binds `activity_departure_id`
                # until Phase 5. So it is None here and VR-06 is inert — the
                # rule is written and tested, and has no input yet. Recorded
                # in DEFERRED_INPUTS below rather than left to be discovered.
                departs_at=None,
                min_age=activity.min_age if activity else None,
                is_open_at_scheduled_time=(
                    facts.open_at.get(row.attraction_id) if row and row.attraction_id else None
                ),
                listing_is_active=listing_active,
                travel_seconds=entry.travel_seconds,
                covered_nights=nights,
            )
        )
    return out


#: Validation rules that are implemented and tested but cannot fire yet,
#: because nothing supplies their input. Stated here for the same reason
#: `test_ports_registry.DELIBERATELY_UNREGISTERED` states its absences: a rule
#: that silently never runs is indistinguishable from one that always passes.
DEFERRED_INPUTS: dict[str, str] = {
    "VR-06": (
        "Needs activity_departure.departs_at. Departures are `inventory`'s "
        "(§7.5.9) and §6.4 does not permit trip -> inventory; nothing binds "
        "itinerary_item.activity_departure_id until Phase 5. The rule is "
        "written and tested in domain/validation.py and receives departs_at "
        "of None until then."
    ),
    "VR-09 (provider half)": (
        "Needs provider.is_active. `provider` is a Phase 1 skeleton, so "
        "provider_is_active is None — meaning unknown, never fine."
    ),
}


def _priced(
    items: Sequence[ItineraryItem], planned: Sequence[PlannedItem], facts: _Facts, party: int
) -> list[PricedItem]:
    """§10.7's inputs.

    **A transfer carries no price in Phase 4.** §10.7 sources a transfer's line
    total from §12.4's tariff, which belongs to `transport` and arrives in
    Phase 6. So the subtotal is activities only, and a transfer is a timed,
    labelled leg with no money on it. Worth saying outright, because silence
    here reads as "transfers are free" — and ADR 0019 forbids quoting an
    APPROXIMATE leg in any case, so a price would be unusable even if it
    existed.

    A stay anchor is priced by nothing at all (ADR 0013), and `price_item`
    refuses one that carries a price rather than quietly zeroing it.
    """
    by_id = {i.pk: i for i in items}
    out: list[PricedItem] = []
    for entry in planned:
        row = by_id.get(entry.item_id)
        activity = facts.activities.get(row.activity_id) if row and row.activity_id else None
        if activity is None:
            out.append(PricedItem(item_id=entry.item_id, kind=entry.kind, title=entry.title))
            continue
        out.append(
            PricedItem(
                item_id=entry.item_id,
                kind=entry.kind,
                title=entry.title,
                unit_price=Money(activity.price_per_person, activity.currency),
                quantity=party,
                group_price=(
                    Money(activity.price_per_group, activity.currency)
                    if activity.price_per_group is not None
                    else None
                ),
            )
        )
    return out


def _refuse_if_a_locked_item_moved(
    before: Sequence[PlannedItem], after: Sequence[PlannedItem]
) -> None:
    """§10.8: "A regeneration that would alter a locked item is rejected with
    409 LOCKED_ITEM_CONFLICT, naming the item."

    Compared on times rather than on presence: the sequencer returns every item
    it was given, so a locked one is always in the output. What decides the
    refusal is whether it came back at a different hour.
    """
    original = {p.item_id: (p.starts_at, p.ends_at) for p in before if p.is_locked}
    for item in after:
        was = original.get(item.item_id)
        if was is not None and was != (item.starts_at, item.ends_at):
            raise LockedItemError(
                f"regenerating would move {item.title!r}, which is covered by a "
                "confirmed booking"
            )


def _persist(
    trip: Trip,
    itinerary: Itinerary,
    items: Sequence[ItineraryItem],
    superseded: Sequence[ItineraryItem],
    result: SequenceResult,
    cost: TripCost,
    worst: str,
    places: Mapping[str, Coordinates],
) -> None:
    """§10.8's versioning, and §10.7's totals.

    The previous version's rows are archived before anything is rewritten, so
    a failure part-way leaves the transaction with either both or neither —
    which is the whole point of an archive that exists for dispute
    investigation. `superseded` is archived too: those legs were part of the
    version being replaced, and a dispute about what a tourist was shown needs
    them most of all.
    """
    ItineraryItemArchive.objects.bulk_create(
        [
            ItineraryItemArchive(
                itinerary=itinerary,
                version=itinerary.version,
                day_number=row.day_number,
                sequence_no=row.sequence_no,
                item_type=row.item_type,
                title=row.title,
                starts_at=row.starts_at,
                ends_at=row.ends_at,
                accommodation_id=row.accommodation_id,
                activity_id=row.activity_id,
                activity_departure_id=row.activity_departure_id,
                attraction_id=row.attraction_id,
                origin_destination_id=row.origin_destination_id,
                target_destination_id=row.target_destination_id,
                location_point=row.location_point,
                origin_point=row.origin_point,
                target_point=row.target_point,
                distance_m=row.distance_m,
                travel_seconds=row.travel_seconds,
                estimate_quality=row.estimate_quality,
                quantity=row.quantity,
                pax_count=row.pax_count,
                unit_price=row.unit_price,
                line_total=row.line_total,
                currency=row.currency,
                booking_id=row.booking_id,
                is_locked=row.is_locked,
            )
            for row in [*items, *superseded]
        ]
    )

    # Archived, then removed. §10.4 will have re-inserted whatever legs the
    # new plan needs.
    ItineraryItem.objects.filter(pk__in=[row.pk for row in superseded]).delete()

    by_id = {i.pk: i for i in items}
    line_totals = dict(cost.lines)
    seen: set[int] = set()

    for entry in result.items:
        if entry.is_inserted:
            _write_inserted_transfer(itinerary, entry, places)
            continue
        row = by_id.get(entry.item_id)
        if row is None or row.pk in seen:
            # A STAY yields two anchors from one row; the row is written once.
            continue
        seen.add(row.pk)
        row.day_number = entry.day_number
        row.sequence_no = entry.sequence_no

        # A STAY keeps its own dates. They are the tourist's booking — check-in
        # on one day, check-out on another — and the anchors the sequencer sees
        # are a derived view of that row, not a replacement for it. Writing an
        # anchor's instant back would collapse the stay onto a single moment,
        # which is exactly what happened: `ends_at` took the check-in time, the
        # stay covered no nights, and the next regeneration produced none of
        # the per-day anchors ADR 0020 added.
        if (
            not row.is_locked
            and row.item_type != ItemType.STAY
            and entry.starts_at is not None
            and entry.ends_at is not None
        ):
            row.starts_at = entry.starts_at
            row.ends_at = entry.ends_at
        money = line_totals.get(entry.item_id)
        if money is not None:
            row.line_total = money.amount
            row.currency = money.currency
        row.save()

    itinerary.version += 1
    itinerary.generated_at = timezone.now()
    itinerary.validation_state = worst
    itinerary.total_distance_m = sum(i.distance_m or 0 for i in result.items)
    itinerary.total_travel_seconds = sum(i.travel_seconds or 0 for i in result.items)
    itinerary.save()

    trip.subtotal_amount = cost.subtotal.amount
    trip.fee_amount = cost.fee.amount
    trip.tax_amount = cost.tax.amount
    trip.total_amount = cost.total.amount
    trip.save(
        update_fields=["subtotal_amount", "fee_amount", "tax_amount", "total_amount", "updated_at"]
    )


def _write_inserted_transfer(
    itinerary: Itinerary, entry: PlannedItem, places: Mapping[str, Coordinates]
) -> None:
    """A leg §10.4 invented, given a row.

    `estimate_quality` is non-null by construction here, which is what the
    database constraint requires of a transfer and what §12.6 requires of the
    screen. The endpoints are points rather than destination ids: a leg from a
    hotel to an attraction has coordinates at both ends and a destination at
    neither.
    """
    assert entry.starts_at is not None and entry.ends_at is not None
    ItineraryItem.objects.create(
        itinerary=itinerary,
        day_number=entry.day_number,
        sequence_no=entry.sequence_no,
        item_type=ItemType.TRANSFER,
        title=entry.title[:160],
        starts_at=entry.starts_at,
        ends_at=entry.ends_at,
        origin_point=_point(entry.start_location, places),
        target_point=_point(entry.end_location, places),
        distance_m=entry.distance_m,
        travel_seconds=entry.travel_seconds,
        estimate_quality=entry.estimate_quality,
    )


def _point(key: str | None, places: Mapping[str, Coordinates]) -> Point | None:
    """The stored geometry for a location key.

    The map is passed down rather than held at module level. A module-level
    dict would be shared by every request in the process, so two tourists
    generating at the same moment would write each other's coordinates onto
    each other's transfers — a corruption that is invisible in a single-request
    test and produces a leg to the wrong place in production.
    """
    coordinates = places.get(key) if key else None
    if coordinates is None:
        return None
    return Point(float(coordinates.lon), float(coordinates.lat), srid=4326)


@transaction.atomic
def generate_itinerary(public_id: UUID, *, tourist_id: int) -> TripDTO:
    """§10.2 steps 1-6 and §10.8's versioning — `POST /trips/{id}/itinerary/generate`.

    The whole of Phase 4's domain core runs here for the first time: §10.4's
    sequencer, §12.6's travel resolver, §10.6's rule set and §10.7's costing.
    Every input they need is gathered from the catalogue first, in a fixed
    number of queries, and none of them touches the database itself.

    **The trip stays in DRAFT.** §20.5 draws `DRAFT --generate/price-->
    PRICED`, which reads as though generating prices the trip. §9.4.5 settles
    it: `POST /trips/{id}/quote` asserts `status in {DRAFT, PRICED}`, so a trip
    is still DRAFT when quoting begins — and `is_editable` is DRAFT alone, so a
    generate that moved it on would make the second pass of §10.2's "review,
    adjust, repeat" loop impossible. The totals written here are provisional,
    `priced_at` stays null, and the "price" half of that arrow is quote, in
    Phase 7.

    **A locked item is never rewritten (§10.3).** It is still handed to the
    sequencer, because a transfer has to be planned around it — §10.3's own
    example is adding day 4 to a confirmed trip without disturbing days 1 to 3
    — and the refusal fires only if sequencing would actually move it.

    **A stay is present on every day it covers** — §10.4 as amended by ADR
    0020. It used to appear only on the days it began and ended, which left a
    middle day holding one activity with nothing to be adjacent to and so no
    transfer to reach it. That was §10.4 as written; the amendment is recorded
    in the ADR rather than made silently here, because §10.1's promise that two
    implementations agree is only meaningful if they implement something
    written down.
    """
    trip = _editable(_owned(public_id, tourist_id))
    itinerary = getattr(trip, "itinerary", None)
    if itinerary is None:
        itinerary = repo.create_itinerary(trip)

    stored = list(itinerary.items.all())

    # §10.8: a regeneration "rewrites unlocked items". A TRANSFER the planner
    # inserted is derived data — it exists only because §10.4 put it between
    # two things — so re-planning starts from what the tourist actually chose
    # and the old legs are dropped.
    #
    # Without this each generate appended another leg to the previous set: the
    # count grew on every call, and because a stored transfer resolves to no
    # location key it could not even be recognised as the leg it duplicated.
    # A locked transfer is kept, because a confirmed booking is behind it.
    items = [row for row in stored if row.item_type != ItemType.TRANSFER or row.is_locked]
    superseded = [row for row in stored if row not in items]

    facts = _gather(items, trip)

    # Every calendar question below — which day an item falls on, which nights
    # a stay covers, where the trip's window begins — is asked where the trip
    # is, not where the server is.
    destination = facts.destinations.get(trip.destination_id)
    zone = ZoneInfo(destination.timezone if destination else "UTC")

    planned = _planned(items, trip, facts, zone)

    buffers = Buffers(
        activity_minutes=int(get_setting("buffer.activity_minutes")),
        airport_departure_minutes=int(get_setting("buffer.airport_departure_minutes")),
        check_in_minutes=int(get_setting("buffer.check_in_minutes")),
    )

    total_days = (trip.end_date - trip.start_date).days + 1
    result = sequence_trip(
        planned,
        day_numbers=list(range(1, total_days + 1)),
        travel_time=build_travel_time(facts.places),
        buffers=buffers,
    )
    _refuse_if_a_locked_item_moved(planned, result.items)

    findings = validate(
        _item_facts(items, result.items, facts, zone),
        trip=TripFacts(
            start_date=trip.start_date,
            end_date=trip.end_date,
            # The destination's zone, not the server's: §10.6's VR-01 turns
            # dates into an instant range and §15.2 reads opening hours
            # locally, so UTC here would move every boundary by hours.
            timezone=str(zone),
            currency=trip.currency,
            party=PartyFacts(adults=trip.adults, children=trip.children, infants=trip.infants),
        ),
        buffers=buffers,
        limits=Limits(
            items_per_day=int(get_setting("limit.items_per_day")),
            travel_minutes_per_day=int(get_setting("limit.travel_minutes_per_day")),
            arrival_processing_minutes=int(get_setting("buffer.arrival_processing_minutes")),
        ),
    )

    cost = compute_cost(
        _priced(items, result.items, facts, trip.adults + trip.children),
        currency=trip.currency,
        platform_fee_rate=Decimal(str(get_setting("platform_fee_rate"))),
    )

    severity = worst_severity(findings + result.findings)
    state = {
        None: ValidationState.VALID,
        Severity.WARNING: ValidationState.WARNINGS,
        Severity.ERROR: ValidationState.ERRORS,
    }[severity]

    _persist(trip, itinerary, items, superseded, result, cost, state.value, facts.places)

    all_findings = (*findings, *result.findings)
    publish(
        ItineraryGenerated(
            trip_public_id=str(trip.public_id),
            tourist_id=tourist_id,
            version=itinerary.version,
            error_count=sum(1 for f in all_findings if f.blocks_quoting),
        )
    )
    return _dto_with(trip, all_findings)


def _dto_with(trip: Trip, findings: Sequence[Finding]) -> TripDTO:
    """The read shape, with this run's findings attached (§10.6)."""
    trip.refresh_from_db()
    reloaded = (
        selectors.trips_of(trip.tourist_id)
        .filter(public_id=trip.public_id)
        .select_related("itinerary")
        .prefetch_related("itinerary__items", "flights")
        .first()
    )
    if reloaded is None:  # pragma: no cover
        raise NotFoundError(f"no trip {trip.public_id}")
    dto = selectors.to_trip_dto(reloaded, findings=findings)
    if dto is None:  # pragma: no cover
        raise NotFoundError(f"no trip {trip.public_id}")
    return dto
