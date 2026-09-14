"""booking module — SRS §6.4.

    Owns:       booking, booking_activity, booking_transfer,
                booking_status_history
    Interface:  quote_trip(), create_basket()
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

The quote *is* that transaction, one phase early, and it still creates no
booking. Phase 7 adds the basket (§9.4.6), which does: `create_basket` turns an
accepted quote into one PENDING booking per component (ADR 0025).

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
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction
from django.utils import timezone

from apps.booking import repositories as repo
from apps.booking.domain.allocation import allocate
from apps.booking.domain.lifecycle import Actor, BookingState, apply, force
from apps.booking.dto import BasketDTO, BookingDTO
from apps.booking.models import Booking, BookingActivity, BookingType
from apps.common.config import get_setting
from apps.common.errors import ConflictError, InventoryUnavailableError
from apps.common.events import DomainEvent, publish
from apps.inventory import services as inventory
from apps.inventory.dto import HoldDTO, HoldRequest
from apps.provider import services as provider
from apps.provider.dto import ProviderDTO
from apps.trip import services as trip_services
from apps.trip.dto import BasketLineDTO, TripDTO

__all__ = [
    "QuoteResult",
    "quote_trip",
    "ItineraryNotQuotableError",
    "QuoteExpiredError",
    "TripNotPayableError",
    "NotBookableError",
    "create_basket",
    "BasketFailure",
    "fail_basket",
    "BookingConfirmed",
    "ConfirmationDTO",
    "confirm_trip",
]


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
    return _token_for(trip.public_id, trip.priced_at)


def _token_for(public_id: UUID, priced_at: datetime | None) -> UUID:
    stamp = priced_at.isoformat() if priced_at else ""
    return uuid5(NAMESPACE_URL, f"quote:{public_id}:{stamp}")


# -- §9.4.6: the basket -------------------------------------------------------


class QuoteExpiredError(ConflictError):
    """§32.3: `QUOTE_EXPIRED`, 409 — "Quote TTL elapsed", re-quote to continue.

    Also raised for a token from a superseded quote. The tourist's remedy is the
    same in both cases, and telling them apart would tell a stranger holding an
    old token whether the trip had been re-priced since.
    """

    code = "QUOTE_EXPIRED"


class TripNotPayableError(ConflictError):
    """§32.3: `TRIP_NOT_PAYABLE`, 409 — the trip is in the wrong state."""

    code = "TRIP_NOT_PAYABLE"


class NotBookableError(ConflictError):
    """A component nobody can currently sell — BR-037, BR-033, ADR 0025.

    `details` names each component and why, so a client can say which item to
    remove rather than refusing the whole trip with one sentence.
    """

    code = "NOT_BOOKABLE"


def _seller_problems(
    lines: Sequence[BasketLineDTO], now: datetime
) -> tuple[dict[int, ProviderDTO], list[dict[str, str]]]:
    """Who sells each line, and every line nobody can.

    Collected rather than raised one at a time: a trip with two unsellable
    components should say so once, naming both.
    """
    activity_sellers = provider.providers_by_id(
        [line.provider_id for line in lines if line.provider_id is not None]
    )
    sellers: dict[int, ProviderDTO] = {}
    problems: list[dict[str, str]] = []
    for line in lines:
        where = {"item": str(line.item_public_id), "title": line.title}
        if line.starts_at <= now:
            # BR-033: "A booking may not start in the past".
            problems.append({**where, "reason": "STARTS_IN_THE_PAST"})
            continue
        if line.item_type == BookingType.TRANSFER:
            seller = (
                None
                if line.origin_region_id is None
                else provider.transport_provider_for(line.origin_region_id)
            )
        else:
            seller = None if line.provider_id is None else activity_sellers.get(line.provider_id)
        if seller is None:
            problems.append({**where, "reason": "NO_PROVIDER"})
        elif not seller.is_sellable:
            # BR-037, checked at the basket as well as at confirmation: a
            # tourist should not pay for something already known unsellable.
            problems.append({**where, "reason": "PROVIDER_NOT_VERIFIED"})
        else:
            sellers[line.item_id] = seller
    return sellers, problems


def _commission_rate(seller: ProviderDTO) -> Decimal:
    """§22.2's resolution, as far as Phase 7 can take it.

    `commission_rule` is Phase 8's table, so every scope above GLOBAL is empty
    and the rate is the global default. The snapshot is taken now all the same —
    TC-060 — and Phase 8 changes what is resolved, not where it is stored.
    """
    return Decimal(str(get_setting("commission.default_percent"))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


def _booking_dto(row: Booking, title: str) -> BookingDTO:
    snapshot = row.cancellation_policy_snapshot or {}
    return BookingDTO(
        public_id=row.public_id,
        reference=row.reference,
        booking_type=row.booking_type,
        status=row.status,
        title=title,
        starts_at=row.starts_at,
        ends_at=row.ends_at,
        pax_count=row.pax_count,
        gross_amount=row.gross_amount,
        fee_amount=row.fee_amount,
        tax_amount=row.tax_amount,
        currency=row.currency,
        cancellation_policy_code=str(snapshot.get("code", "")),
        confirmed_at=row.confirmed_at,
        cancelled_at=row.cancelled_at,
        response_due_at=row.response_due_at,
    )


@transaction.atomic
def create_basket(
    public_id: UUID,
    *,
    tourist_id: int,
    quote_token: UUID,
    actor_user_id: int | None = None,
    now: datetime | None = None,
) -> BasketDTO:
    """§9.4.6, `POST /trips/{id}/confirm`: an accepted quote becomes a basket.

    In order, and each step is §9.4.6's or a rule it names:

    1. The quote must be the current one and still standing — otherwise
       `QUOTE_EXPIRED`, and **no booking is created** (TC-061).
    2. Every component must be sellable: in the future (BR-033), with a
       verified seller (BR-037). Every failure is reported at once.
    3. One PENDING booking per component, with its policy and commission rate
       snapshotted (TC-060, BR-041) and its share of the fee and tax allocated
       to the cent.
    4. The items are linked and the trip moves to PENDING_PAYMENT.
    5. The holds are extended to the payment window.

    "No inventory is committed and no provider is notified at this point — that
    happens only on payment capture." Nothing here does either.

    **One transaction, no external call** (hard rule 11). A failure at step 4 or
    5 rolls back step 3, so a basket is either whole or absent.
    """
    now = now or timezone.now()
    basis = trip_services.basket_basis(public_id, tourist_id=tourist_id)

    if basis.status != "PRICED":
        raise TripNotPayableError(
            f"a trip in {basis.status} cannot be booked; get a price for it first"
        )
    if (
        basis.quote_expires_at is None
        or now >= basis.quote_expires_at
        or quote_token != _token_for(basis.public_id, basis.priced_at)
    ):
        raise QuoteExpiredError("This price is no longer available. Get a new price to continue.")
    if not basis.lines:
        raise TripNotPayableError(
            "this trip has nothing to book: stays and attractions are not sold here"
        )

    sellers, problems = _seller_problems(basis.lines, now)
    if problems:
        raise NotBookableError(
            "Some parts of this trip cannot be booked right now.", details=problems
        )

    # §20.2's "hold live". A payment that failed leaves the quote standing and
    # the seats released, so the token alone does not prove capacity is held.
    held = inventory.held_departures(trip_id=basis.trip_id, now=now)
    unheld = [
        line
        for line in basis.lines
        if line.item_type == BookingType.ACTIVITY and line.activity_departure_id not in held
    ]
    if unheld:
        raise InventoryUnavailableError(
            "The seats for this trip are no longer held. Get a new price to hold them again.",
            code="HOLD_EXPIRED",
            details=[{"item": str(line.item_public_id), "title": line.title} for line in unheld],
        )

    fees = allocate(basis.fee_amount, [line.gross_amount for line in basis.lines])
    taxes = allocate(basis.tax_amount, [line.gross_amount for line in basis.lines])

    created: list[BookingDTO] = []
    links: dict[UUID, int] = {}
    for line, fee, tax in zip(basis.lines, fees, taxes, strict=True):
        seller = sellers[line.item_id]
        row = repo.create_booking(
            trip_id=basis.trip_id,
            tourist_id=tourist_id,
            provider_id=seller.id,
            booking_type=line.item_type,
            status=BookingState.PENDING.value,
            starts_at=line.starts_at,
            ends_at=line.ends_at,
            pax_count=line.pax,
            gross_amount=line.gross_amount,
            fee_amount=fee,
            tax_amount=tax,
            currency=line.currency,
            commission_rate=_commission_rate(seller),
            cancellation_policy_id=line.cancellation_policy_id,
            cancellation_policy_snapshot=line.policy_snapshot,
        )
        if line.item_type == BookingType.ACTIVITY:
            repo.create_activity(
                row,
                activity_id=line.activity_id,
                activity_departure_id=line.activity_departure_id,
                pax_adult=line.pax_adult,
                pax_child=line.pax_child,
                meeting_at=line.starts_at,
                confirmation_mode=line.confirmation_mode,
            )
        else:
            assert line.pickup_lonlat is not None and line.dropoff_lonlat is not None
            repo.create_transfer(
                row,
                pickup_lonlat=line.pickup_lonlat,
                dropoff_lonlat=line.dropoff_lonlat,
                origin_destination_id=line.origin_destination_id,
                target_destination_id=line.target_destination_id,
                pickup_at=line.starts_at,
                distance_m=line.distance_m,
                travel_seconds=line.travel_seconds,
                estimate_quality=line.estimate_quality or "APPROXIMATE",
                vehicle_class=line.vehicle_class,
                luggage_count=line.luggage_count,
                is_airport_transfer=line.is_airport_transfer,
                corridor_id=line.rule_id if line.match_kind == "CORRIDOR" else None,
                tariff_id=line.rule_id if line.match_kind == "TARIFF" else None,
            )
        repo.record_transition(
            row,
            from_status=None,
            to_status=BookingState.PENDING.value,
            actor_role="TOURIST",
            actor_user_id=actor_user_id,
            reason="Basket created from an accepted quote.",
            occurred_at=now,
        )
        links[line.item_public_id] = int(row.pk)
        created.append(_booking_dto(row, line.title))

    moved = trip_services.open_payment(public_id, tourist_id=tourist_id, bookings=links)

    window = now + timedelta(minutes=int(get_setting("payment.window_minutes")))
    inventory.extend_holds(trip_id=basis.trip_id, until=window, now=now)

    return BasketDTO(
        trip_public_id=basis.public_id,
        trip_status=moved.status,
        currency=basis.currency,
        total_amount=basis.total_amount,
        payment_expires_at=window,
        bookings=tuple(created),
    )


# -- releasing a basket -------------------------------------------------------


class BasketFailure(StrEnum):
    """Why a basket will not be paid for — §20.2's PENDING → FAILED guard."""

    HOLD_EXPIRED = "HOLD_EXPIRED"
    PAYMENT_FAILED = "PAYMENT_FAILED"


@transaction.atomic
def fail_basket(trip_id: int, *, cause: BasketFailure, now: datetime | None = None) -> int:
    """§20.2 PENDING → FAILED for every booking in the basket, and what follows.

    TC-072's shape: "Payment FAILED; holds RELEASED; bookings FAILED; trip PRICED".
    A hold that expired instead is the same basket ending for a different reason,
    and the trip goes to DRAFT rather than PRICED because the offer itself lapsed
    (`trip.abandon_payment`).

    Returns how many bookings failed; zero means the trip had no basket, which
    lets the expiry sweeper fall back to expiring a plain quote.

    **Order: bookings, then capacity, then the trip.** The bookings are locked
    first in ascending id (hard rule 12) so a confirmation racing this sees them
    FAILED and stops. Releasing capacity is idempotent — after an expiry the
    sweeper has already given it back — and the trip moves last, so it never
    reads as editable while its bookings still claim seats.

    §17.5 asks the sweeper to "defer once and raise an alert rather than
    releasing under an in-flight payment". There is no payment table until
    Phase 8, so there is never a payment in flight to defer for; the check
    arrives with the table it reads.
    """
    now = now or timezone.now()
    rows = repo.lock_of_trip(trip_id, statuses=[BookingState.PENDING.value])
    if not rows:
        return 0

    context = {
        "hold_expired": cause is BasketFailure.HOLD_EXPIRED,
        "payment_failed": cause is BasketFailure.PAYMENT_FAILED,
    }
    for row in rows:
        target = apply(
            BookingState(row.status), BookingState.FAILED, actor=Actor.SYSTEM, context=context
        )
        repo.set_status(row, target.value)
        repo.record_transition(
            row,
            from_status=BookingState.PENDING.value,
            to_status=target.value,
            actor_role=Actor.SYSTEM.value,
            actor_user_id=None,
            reason=(
                "The held capacity expired before payment completed."
                if cause is BasketFailure.HOLD_EXPIRED
                else "Payment failed."
            ),
            occurred_at=now,
        )

    inventory.release(trip_id=trip_id)
    trip_services.abandon_payment(trip_id, quote_still_stands=cause is BasketFailure.PAYMENT_FAILED)
    return len(rows)


# -- §20.8: the confirmation routine -------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BookingConfirmed(DomainEvent):
    """§20.8 step 18, one per booking. `status` says which of step 12's two."""

    name = "booking.confirmed"
    booking_public_id: str = ""
    reference: str = ""
    trip_id: int = 0
    provider_id: int = 0
    status: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationDTO:
    """What the routine did. Empty lists and `moved=False` for a repeat run."""

    trip_id: int
    moved: bool
    confirmed: tuple[str, ...] = ()
    awaiting_provider: tuple[str, ...] = ()


def _commission(row: Booking) -> tuple[Decimal, Decimal]:
    """§20.8 step 14 from the rate frozen at the basket (BR-070).

    `gross * rate / 100`, to the cent, half up; net is what is left. The rate
    cannot have moved since TC-060 snapshotted it, which is the whole reason it
    was snapshotted then.
    """
    rate = row.commission_rate or Decimal("0")
    amount = (row.gross_amount * rate / Decimal(100)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return amount, row.gross_amount - amount


@transaction.atomic
def confirm_trip(
    trip_id: int,
    *,
    payment_captured: bool,
    now: datetime | None = None,
    forced_by: int | None = None,
    reason: str = "",
) -> ConfirmationDTO:
    """§20.8, the confirmation routine: a paid basket becomes bookings.

    Called by Phase 8's webhook with `payment_captured=True`, and in Phase 7 by
    SUPER_ADMIN's force-transition (`forced_by`, BR-038), which bypasses §20.2's
    guards and never its edges. The tourist cannot reach it (ADR 0025).

    The steps, in §20.8's order:

    2.  Idempotent: a trip with no PENDING booking left is a no-op.
    5.  Every PENDING booking locked, ascending id (hard rule 12).
    7-11. The trip's holds committed — `inventory.commit`, which re-checks
        each hold under the counter lock and refuses a dead one (BR-026).
    12. Each booking CONFIRMED, or AWAITING_PROVIDER for an on-request activity
        with its deadline stamped (ADR 0025 decision 7).
    13. `confirmed_at`.
    14. Commission amount and net from the frozen rate.
    15. A history row each.
    16-17. The trip CONFIRMED and its booked items locked.
    18. Events queued, dispatched only after commit.

    Step 3 (payment captured, settlement amount, FX rate) is Phase 8's table.
    Step 9's hard case — a hold that expired while payment was in flight — is
    not yet partial: `inventory.commit` refuses the whole trip. The next commit
    makes it fail that one booking and confirm the rest, as §20.8 requires.
    """
    now = now or timezone.now()
    rows = repo.lock_of_trip(trip_id, statuses=[BookingState.PENDING.value])
    if not rows:
        return ConfirmationDTO(trip_id=trip_id, moved=False)

    inventory.commit(trip_id=trip_id, now=now)

    modes = dict(
        BookingActivity.objects.filter(booking__in=rows).values_list(
            "booking_id", "confirmation_mode"
        )
    )
    window = timedelta(hours=int(get_setting("provider_response_hours")))

    confirmed: list[str] = []
    awaiting: list[str] = []
    for row in rows:
        mode = modes.get(row.pk, "INSTANT")
        wanted = BookingState.AWAITING_PROVIDER if mode == "ON_REQUEST" else BookingState.CONFIRMED
        if forced_by is not None:
            target = force(BookingState(row.status), wanted)
        else:
            target = apply(
                BookingState(row.status),
                wanted,
                actor=Actor.SYSTEM,
                context={
                    "payment_captured": payment_captured,
                    "hold_committed": True,
                    "confirmation_mode": mode,
                },
            )

        commission, net = _commission(row)
        fields: dict[str, object] = {"commission_amount": commission, "net_amount": net}
        if target is BookingState.CONFIRMED:
            fields["confirmed_at"] = now
            confirmed.append(row.reference)
        else:
            fields["response_due_at"] = now + window
            awaiting.append(row.reference)
        repo.set_status(row, target.value, **fields)
        repo.record_transition(
            row,
            from_status=BookingState.PENDING.value,
            to_status=target.value,
            actor_role="SUPER_ADMIN" if forced_by is not None else Actor.SYSTEM.value,
            actor_user_id=forced_by,
            reason=reason or "Payment captured.",
            occurred_at=now,
        )
        publish(
            BookingConfirmed(
                booking_public_id=str(row.public_id),
                reference=row.reference,
                trip_id=trip_id,
                provider_id=row.provider_id,
                status=target.value,
            )
        )

    trip_services.mark_confirmed(trip_id, booking_ids=[row.pk for row in rows], now=now)
    return ConfirmationDTO(
        trip_id=trip_id,
        moved=True,
        confirmed=tuple(confirmed),
        awaiting_provider=tuple(awaiting),
    )
