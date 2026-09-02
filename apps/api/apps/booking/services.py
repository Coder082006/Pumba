"""booking module — SRS §6.4.

    Owns:       booking, booking_activity, booking_transfer,
                booking_status_history, basket  (all Phase 7)
    Interface:  quote_trip()
    Depends on: inventory, trip, provider
    Layer:      L4

Application layer (SRS §8.2 layer 2).

**This module has a use case three phases before it has a table**, and ADR 0022
is the record of why. §9.4.5's `POST /trips/{id}/quote` reads an itinerary,
locks capacity counters and moves a trip's state — three modules' rows — and
`.importlinter` gives `trip -> catalogue, transport` and nothing else. `booking`
is the module §6.4 hands `inventory`, `trip` and `provider` to, for no other
reason than this, and §43 forbids splitting it from `inventory` and `payment`
because the three share the atomic transaction that makes a basket correct.

The quote *is* that transaction, one phase early. **No booking row is created
here.** The booking models, their state machine and the basket remain Phase 7.

**One transaction, and it holds nothing open across an external call** (§8.4,
hard rule 11). Everything below is local work: a read of the trip, a locked
read of the counters, and three writes.

**The order matters and is §9.4.5's.** Validate, release this trip's prior
holds, hold, bind and price. Pricing before holding would tell a tourist a
total for seats somebody else got; holding before validating would take
capacity for an itinerary that cannot be sold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction
from django.utils import timezone

from apps.common.config import get_setting
from apps.common.errors import ConflictError
from apps.inventory import services as inventory
from apps.inventory.dto import HoldDTO, HoldRequest
from apps.trip import services as trip_services
from apps.trip.dto import TripDTO

__all__ = ["QuoteResult", "quote_trip", "ItineraryNotQuotableError"]


class ItineraryNotQuotableError(ConflictError):
    """§9.4.5: *"409 TRIP_NOT_QUOTABLE"*."""

    code = "TRIP_NOT_QUOTABLE"


@dataclass(frozen=True, slots=True, kw_only=True)
class QuoteResult:
    """§9.4.5's 200: *"the full cost breakdown plus `quote_expires_at` and a
    `quote_token` that must be presented at confirmation"*.

    The breakdown is the `TripDTO`, which already carries every figure §24.21
    renders — a second money shape here would be a second thing to keep in step
    with `costing`.
    """

    trip: TripDTO
    quote_token: UUID
    expires_at: datetime
    held_seats: int


@transaction.atomic
def quote_trip(public_id: UUID, *, tourist_id: int) -> QuoteResult:
    """§9.4.5, end to end.

    1. assert the trip may be quoted and its itinerary passes validation
    2. resolve each ACTIVITY item to the departure its start instant names
    3. hold capacity for all of them at once, under lock (§17.3)
    4. bind the departures, recompute the totals, and price the trip

    **A STAY is skipped entirely** — ADR 0013 and §9.4.5 as amended: an anchor
    locks nothing, holds nothing and prices nothing. **A TRANSFER holds nothing
    either**: §9.4.5 has it reserve a vehicle class rather than a driver, and
    the tariff that would price it is §12.4, in Phase 6.

    **Step 2 is a lookup, not a search.** `UNIQUE(activity_id, departs_at)`
    (§7.5.9) means an item's `starts_at` names at most one departure, and the
    tourist chose that instant from a list of real ones. It is how a departure
    is bound without `trip` ever seeing `inventory` (ADR 0022).

    An ACTIVITY whose instant matches no departure is refused rather than
    quietly quoted unheld. Silently pricing it would sell a seat on a boat that
    is not running, which is the exact failure every layer beneath this exists
    to prevent.
    """
    basis = trip_services.quote_basis(public_id, tourist_id=tourist_id)

    if not basis.generated:
        raise ItineraryNotQuotableError(
            "plan the days before asking for a price: a quote holds capacity "
            "against a sequenced itinerary."
        )
    if basis.has_errors:
        # §24.20's "blocking errors disable Continue". §10.6 computed this
        # once; recounting the findings here would be a second quote gate.
        raise ItineraryNotQuotableError(
            "this itinerary has errors that must be fixed before it can be priced."
        )

    requests: list[HoldRequest] = []
    bindings: dict[UUID, int] = {}
    for line in basis.lines:
        if line.item_type != "ACTIVITY" or line.activity_id is None:
            continue
        departure_id = inventory.resolve_departure_at(line.activity_id, departs_at=line.starts_at)
        if departure_id is None:
            raise ItineraryNotQuotableError(
                "one of the activities on this trip is no longer offered at the "
                "time it was added. Open it and choose another departure."
            )
        bindings[line.item_public_id] = departure_id
        requests.append(HoldRequest(departure_id=departure_id, pax=line.pax))

    now = timezone.now()
    ttl = int(get_setting("quote.ttl_minutes"))

    # Raises `InventoryUnavailableError` naming every unavailable departure and
    # its alternatives — §9.4.5's `409 INVENTORY_UNAVAILABLE`. Nothing has been
    # written at this point, and the transaction rolls back what has.
    held = inventory.hold(trip_id=basis.trip_id, requests=requests, ttl_minutes=ttl, now=now)

    expires_at = _expiry(held, now, ttl)

    priced = trip_services.mark_priced(
        public_id,
        tourist_id=tourist_id,
        departures=bindings,
        expires_at=expires_at,
    )

    return QuoteResult(
        trip=priced,
        # §9.4.5's `quote_token`, "presented at confirmation". The trip's own
        # `public_id` would identify the trip rather than *this* quote, and
        # §9.4.6 has to be able to tell a stale token from a current one.
        quote_token=_token(priced),
        expires_at=expires_at,
        held_seats=sum(hold.quantity for hold in held),
    )


def _expiry(held: Sequence[HoldDTO], now: datetime, ttl: int) -> datetime:
    """When this quote stops standing.

    Taken from the holds where there are any, so the trip's clock and the
    capacity's clock are the same instant rather than two computed a
    microsecond apart — §9.4.7 asserts `holds are live` against `trip.status`,
    and a trip that expired first would fail that check while its seats were
    still held.
    """
    stamps = [hold.expires_at for hold in held]
    if stamps:
        return min(stamps)
    # A trip of stays and attractions holds nothing and is still quotable.
    return now + timedelta(minutes=ttl)


def _token(trip: TripDTO) -> UUID:
    """A quote's identity.

    Derived from `priced_at` and the trip so that re-quoting produces a
    different token without a column to store one: §9.4.6 must refuse a token
    from a superseded quote, and the thing that changes between quotes is the
    moment they were made.
    """
    stamp = trip.priced_at.isoformat() if trip.priced_at else ""
    return uuid5(NAMESPACE_URL, f"quote:{trip.public_id}:{stamp}")
