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

import hashlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from django.db import transaction
from django.utils import timezone

from apps.booking import repositories as repo
from apps.booking.domain.allocation import allocate
from apps.booking.domain.cancellation import Party, Refund, evaluate
from apps.booking.domain.lifecycle import ACTORS, Actor, BookingState, apply, force
from apps.booking.domain.voucher import money_text, party_text, policy_summary, when_text
from apps.booking.dto import BasketDTO, BookingDTO, VoucherDTO
from apps.booking.models import Booking, BookingActivity, BookingType, BookingVoucher
from apps.common.config import get_setting
from apps.common.errors import (
    ConflictError,
    InventoryUnavailableError,
    NotFoundError,
    PlatformError,
)
from apps.common.events import DomainEvent, publish
from apps.common.ports_registry import get_document_port, get_storage_port
from apps.inventory import services as inventory
from apps.inventory.dto import HoldDTO, HoldRequest
from apps.provider import services as provider
from apps.provider.dto import ProviderDTO
from apps.trip import services as trip_services
from apps.trip.dto import BasketLineDTO, TripDTO
from ports.document import VoucherContent

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
    "ComponentFailedAfterCapture",
    "BookingCancelled",
    "BookingNotAwaitingError",
    "accept_request",
    "decline_request",
    "expire_provider_responses",
    "CancellationNotPermittedError",
    "CancellationDTO",
    "preview_cancellation",
    "cancel_booking",
    "TripCancellationDTO",
    "preview_trip_cancellation",
    "cancel_trip",
    "VoucherIntegrityError",
    "issue_voucher",
    "voucher_document",
    "BookingDetailDTO",
    "list_bookings",
    "owned_trip_id",
    "booking_detail",
    "ForcedTransitionDTO",
    "force_transition",
    "reissue_voucher",
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
class ComponentFailedAfterCapture(DomainEvent):
    """§20.8 step 9: "initiate an automatic partial refund for the failed
    component, notifying the tourist with alternatives".

    Phase 7 has no payment to refund, so the obligation is published rather
    than performed; Phase 8's refund handler subscribes. Every figure a refund
    needs travels as a string (§8.9: primitives only, and a float loses a cent).
    """

    name = "booking.component_failed_after_capture"
    booking_public_id: str = ""
    reference: str = ""
    trip_id: int = 0
    reason: str = ""
    gross_amount: str = "0"
    fee_amount: str = "0"
    tax_amount: str = "0"
    currency: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ConfirmationDTO:
    """What the routine did. Empty lists and `moved=False` for a repeat run."""

    trip_id: int
    moved: bool
    confirmed: tuple[str, ...] = ()
    awaiting_provider: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()


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
    6.  The guard is evaluated before anything moves: without a captured payment
        nothing is sold (BR-030).
    7-11. Capacity settled per departure (`inventory.settle_capture`), including
        **step 9's hard case**: a hold that died while payment was in flight is
        re-acquired if the seats are still there, and if not, *that component*
        fails — "failing the entire trip because one activity sold out would be
        a worse outcome for everyone".
    12. Each surviving booking CONFIRMED, or AWAITING_PROVIDER for an on-request
        activity, with its deadline stamped. A component whose provider is no
        longer sellable fails here too (BR-037, ADR 0025's third addendum).
    13-15. `confirmed_at`, commission from the frozen rate, a history row.
    16-17. The trip CONFIRMED and its booked items locked — or CANCELLED if not
        one component could be secured.
    18. Events queued, dispatched only after commit, including the refund
        obligation for every failed component.
    """
    now = now or timezone.now()
    rows = repo.lock_of_trip(trip_id, statuses=[BookingState.PENDING.value])
    if not rows:
        return ConfirmationDTO(trip_id=trip_id, moved=False)

    if forced_by is None and not payment_captured:
        # Refused before any capacity moves. `apply` would say the same thing
        # per booking, but only after `settle_capture` had already sold seats.
        apply(
            BookingState.PENDING,
            BookingState.CONFIRMED,
            actor=Actor.SYSTEM,
            context={"payment_captured": False},
        )

    subtypes: dict[int, tuple[str, int | None]] = {
        booking_id: (mode, departure_id)
        for booking_id, mode, departure_id in BookingActivity.objects.filter(
            booking__in=rows
        ).values_list("booking_id", "confirmation_mode", "activity_departure_id")
    }
    sellers = provider.providers_by_id(sorted({row.provider_id for row in rows}))
    unsellable = {
        row.pk
        for row in rows
        if row.provider_id not in sellers or not sellers[row.provider_id].is_sellable
    }

    claims: dict[int, int] = {}
    for row in rows:
        if row.pk in subtypes and row.pk not in unsellable:
            departure_id = subtypes[row.pk][1]
            if departure_id is not None:
                claims[departure_id] = claims.get(departure_id, 0) + row.pax_count

    settled = inventory.settle_capture(trip_id=trip_id, claims=claims, now=now)
    window = timedelta(hours=int(get_setting("provider_response_hours")))

    confirmed: list[str] = []
    awaiting: list[str] = []
    failed: list[str] = []
    for row in rows:
        default: tuple[str, int | None] = ("INSTANT", None)
        mode, departure_id = subtypes.get(row.pk, default)
        why_failed = (
            "PROVIDER_NOT_VERIFIED"
            if row.pk in unsellable
            else settled.lost.get(departure_id)
            if departure_id is not None
            else None
        )

        if why_failed is not None:
            target = apply(
                BookingState.PENDING,
                BookingState.FAILED,
                actor=Actor.SYSTEM,
                context={
                    "hold_expired": row.pk not in unsellable,
                    "provider_unsellable": row.pk in unsellable,
                },
            )
            repo.set_status(row, target.value)
            repo.record_transition(
                row,
                from_status=BookingState.PENDING.value,
                to_status=target.value,
                actor_role=Actor.SYSTEM.value,
                actor_user_id=None,
                reason=f"Could not be secured at payment ({why_failed}); a refund is due.",
                occurred_at=now,
            )
            publish(
                ComponentFailedAfterCapture(
                    booking_public_id=str(row.public_id),
                    reference=row.reference,
                    trip_id=trip_id,
                    reason=why_failed,
                    gross_amount=str(row.gross_amount),
                    fee_amount=str(row.fee_amount),
                    tax_amount=str(row.tax_amount),
                    currency=row.currency,
                )
            )
            failed.append(row.reference)
            continue

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
        if target is BookingState.CONFIRMED:
            # §41.8: "Vouchers generate for every confirmed booking" — in the
            # transaction that confirms it, so one cannot exist without the other.
            issue_voucher(row, issued_by=forced_by, reason="", now=now)
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

    if confirmed or awaiting:
        kept = [row.pk for row in rows if row.reference in set(confirmed) | set(awaiting)]
        trip_services.mark_confirmed(trip_id, booking_ids=kept, now=now)
    else:
        trip_services.mark_unfulfillable(trip_id)

    return ConfirmationDTO(
        trip_id=trip_id,
        moved=True,
        confirmed=tuple(confirmed),
        awaiting_provider=tuple(awaiting),
        failed=tuple(failed),
    )


# -- §14.4: on-request activities -------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BookingCancelled(DomainEvent):
    """A booking ended CANCELLED, and what is owed back.

    `refund_amount` is computed here and carried as a string, so Phase 8's
    refund handler issues exactly what was decided (BR-043) rather than deciding
    again.
    """

    name = "booking.cancelled"
    booking_public_id: str = ""
    reference: str = ""
    trip_id: int = 0
    provider_id: int = 0
    cancelled_by: str = ""
    reason: str = ""
    refund_amount: str = "0"
    currency: str = ""


class BookingNotAwaitingError(ConflictError):
    """The booking is not waiting for its provider — §32.3's ILLEGAL_TRANSITION."""

    code = "ILLEGAL_TRANSITION"


def _awaiting(public_id: UUID) -> Booking:
    row = Booking.objects.select_for_update().filter(public_id=public_id).first()
    if row is None:
        raise NotFoundError()
    if row.status != BookingState.AWAITING_PROVIDER.value:
        raise BookingNotAwaitingError(
            f"{row.reference} is {row.status}, not waiting for its provider."
        )
    return row


def _return_capacity(row: Booking) -> None:
    """BR-048: a cancelled booking's sold seats go back on sale immediately."""
    activity = BookingActivity.objects.filter(booking=row).first()
    if activity is not None:
        inventory.return_sold(departure_id=activity.activity_departure_id, quantity=row.pax_count)


def _end_trip_if_every_component_is_cancelled(trip_id: int) -> None:
    live = {
        BookingState.PENDING.value,
        BookingState.AWAITING_PROVIDER.value,
        BookingState.CONFIRMED.value,
        BookingState.IN_PROGRESS.value,
        BookingState.COMPLETED.value,
    }
    if not Booking.objects.filter(trip_id=trip_id, status__in=live).exists():
        trip_services.mark_cancelled(trip_id)


def _cancel_for_supply(
    row: Booking,
    *,
    actor: Actor,
    actor_user_id: int | None,
    context: dict[str, object],
    code: str,
    reason: str,
    now: datetime,
) -> None:
    """AWAITING_PROVIDER → CANCELLED with BR-045's full refund.

    §14.4: a decline or a lapsed window "auto-cancels with full refund", and
    BR-045 extends that to the service fee: the tourist is never penalised for
    supply failure. So the refund is everything this component cost.
    """
    target = apply(BookingState(row.status), BookingState.CANCELLED, actor=actor, context=context)
    repo.set_status(
        row,
        target.value,
        cancelled_at=now,
        cancelled_by="PROVIDER",
        cancellation_reason=code,
    )
    repo.record_transition(
        row,
        from_status=BookingState.AWAITING_PROVIDER.value,
        to_status=target.value,
        actor_role=actor.value,
        actor_user_id=actor_user_id,
        reason=reason,
        occurred_at=now,
    )
    _return_capacity(row)
    publish(
        BookingCancelled(
            booking_public_id=str(row.public_id),
            reference=row.reference,
            trip_id=row.trip_id,
            provider_id=row.provider_id,
            cancelled_by="PROVIDER",
            reason=code,
            refund_amount=str(row.gross_amount + row.fee_amount + row.tax_amount),
            currency=row.currency,
        )
    )
    _end_trip_if_every_component_is_cancelled(row.trip_id)


@transaction.atomic
def accept_request(
    public_id: UUID,
    *,
    actor_user_id: int | None,
    now: datetime | None = None,
) -> BookingDTO:
    """§20.2 AWAITING_PROVIDER → CONFIRMED, "within provider_response_hours".

    The deadline is the rule, not the sweep (ADR 0025 decision 7): an acceptance
    one second late is refused even if the timeout job has not run yet, so the
    job's cadence never becomes extra grace for the provider.
    """
    now = now or timezone.now()
    row = _awaiting(public_id)
    target = apply(
        BookingState(row.status),
        BookingState.CONFIRMED,
        actor=Actor.PROVIDER,
        context={"now": now, "response_due_at": row.response_due_at},
    )
    repo.set_status(row, target.value, confirmed_at=now)
    issue_voucher(row, issued_by=actor_user_id, reason="", now=now)
    repo.record_transition(
        row,
        from_status=BookingState.AWAITING_PROVIDER.value,
        to_status=target.value,
        actor_role=Actor.PROVIDER.value,
        actor_user_id=actor_user_id,
        reason="Accepted by the provider.",
        occurred_at=now,
    )
    publish(
        BookingConfirmed(
            booking_public_id=str(row.public_id),
            reference=row.reference,
            trip_id=row.trip_id,
            provider_id=row.provider_id,
            status=target.value,
        )
    )
    return _booking_dto(row, "")


@transaction.atomic
def decline_request(
    public_id: UUID,
    *,
    actor_user_id: int | None,
    reason: str,
    now: datetime | None = None,
) -> BookingDTO:
    """§20.2 AWAITING_PROVIDER → CANCELLED, "rejected → automatic full refund"."""
    now = now or timezone.now()
    row = _awaiting(public_id)
    _cancel_for_supply(
        row,
        actor=Actor.PROVIDER,
        actor_user_id=actor_user_id,
        context={"provider_declined": True},
        code="PROVIDER_DECLINED",
        reason=reason or "Declined by the provider.",
        now=now,
    )
    return _booking_dto(row, "")


def expire_provider_responses(*, now: datetime | None = None) -> int:
    """§14.4's timeout: an on-request booking unanswered past its deadline
    "auto-cancels with full refund".

    One transaction per booking, re-read under lock, so a provider's acceptance
    racing the sweep resolves one way or the other and never both. Returns how
    many were cancelled.
    """
    now = now or timezone.now()
    due = list(
        Booking.objects.filter(status=BookingState.AWAITING_PROVIDER.value, response_due_at__lt=now)
        .order_by("id")
        .values_list("public_id", flat=True)
    )
    cancelled = 0
    for public_id in due:
        with transaction.atomic():
            row = Booking.objects.select_for_update().filter(public_id=public_id).first()
            if (
                row is None
                or row.status != BookingState.AWAITING_PROVIDER.value
                or row.response_due_at is None
                or row.response_due_at >= now
            ):
                continue
            _cancel_for_supply(
                row,
                actor=Actor.SYSTEM,
                actor_user_id=None,
                context={"response_window_elapsed": True},
                code="PROVIDER_RESPONSE_TIMEOUT",
                reason="The provider did not respond within the response window.",
                now=now,
            )
            cancelled += 1
    return cancelled


# -- §20.9: cancellation ---------------------------------------------------------


class CancellationNotPermittedError(ConflictError):
    """§32.3: `CANCELLATION_NOT_PERMITTED`, 409 — the state forbids it (BR-042)."""

    code = "CANCELLATION_NOT_PERMITTED"


@dataclass(frozen=True, slots=True, kw_only=True)
class CancellationDTO:
    """A cancellation, previewed or done. The same shape for both, so BR-043's
    "the preview shown must equal the refund actually issued" is a comparison
    of two values of one type."""

    booking: BookingDTO
    cancellable: bool
    refund_percent: Decimal
    refund_amount: Decimal
    refund_of_price: Decimal
    fee_refunded: Decimal
    tax_refunded: Decimal
    currency: str
    policy_code: str


_PARTY_FOR_ACTOR = {Actor.TOURIST: Party.TOURIST, Actor.PROVIDER: Party.PROVIDER}
_REASON_FOR_PARTY = {Party.TOURIST: "TOURIST_REQUEST", Party.PROVIDER: "PROVIDER_UNAVAILABLE"}


def _refund_for(row: Booking, *, party: Party, now: datetime) -> Refund:
    """§20.9 for this booking, as it stands.

    **Nothing is owed on a booking nobody has paid for.** A PENDING booking's
    payment was never captured, so its refund is zero whatever the policy says —
    a preview offering money back on an unpaid basket would be a promise the
    refund could never keep.
    """
    refund = evaluate(
        snapshot=row.cancellation_policy_snapshot or {},
        starts_at=row.starts_at,
        cancelled_at=now,
        gross=row.gross_amount,
        fee=row.fee_amount,
        tax=row.tax_amount,
        party=party,
        fee_retention_hours=int(get_setting("refund.fee_retention_hours")),
    )
    if row.status == BookingState.PENDING.value:
        zero = Decimal("0.00")
        return Refund(
            refund_percent=Decimal(0),
            refund_of_price=zero,
            fee_refunded=zero,
            tax_refunded=zero,
            refund_amount=zero,
            provider_compensation=zero,
            platform_fee_retained=zero,
        )
    return refund


def _cancellation(row: Booking, refund: Refund, *, cancellable: bool) -> CancellationDTO:
    snapshot = row.cancellation_policy_snapshot or {}
    return CancellationDTO(
        booking=_booking_dto(row, ""),
        cancellable=cancellable,
        refund_percent=refund.refund_percent,
        refund_amount=refund.refund_amount,
        refund_of_price=refund.refund_of_price,
        fee_refunded=refund.fee_refunded,
        tax_refunded=refund.tax_refunded,
        currency=row.currency,
        policy_code=str(snapshot.get("code", "")),
    )


def _cancellable_by(row: Booking, actor: Actor) -> bool:
    """Whether §20.2's Actor column lets `actor` cancel from this state.

    Read from `ACTORS` rather than restated, so BR-042's "not yet IN_PROGRESS"
    for a tourist and the provider's own rows come from the one table.
    """
    edge = (BookingState(row.status), BookingState.CANCELLED)
    return actor in ACTORS.get(edge, frozenset())


def preview_cancellation(
    row: Booking, *, actor: Actor = Actor.TOURIST, now: datetime | None = None
) -> CancellationDTO:
    """`GET /bookings/{id}/cancellation-preview` — §20.9, BR-043.

    Takes the row the caller has already resolved through its scoped selector,
    so the ownership question is answered once, where it belongs.
    """
    now = now or timezone.now()
    party = _PARTY_FOR_ACTOR[actor]
    return _cancellation(
        row,
        _refund_for(row, party=party, now=now),
        cancellable=_cancellable_by(row, actor),
    )


@transaction.atomic
def cancel_booking(
    public_id: UUID,
    *,
    actor: Actor,
    actor_user_id: int | None,
    reason: str = "",
    now: datetime | None = None,
) -> CancellationDTO:
    """`POST /bookings/{id}/cancel` — §20.2, §20.9, BR-042, BR-043, BR-048.

    The row is re-read under lock here even though the view has already found
    it: the lock is what makes two cancellations of one booking one
    cancellation and a 409, rather than two refunds.

    In order: the state must permit it (BR-042 for a tourist); the refund is
    evaluated from the snapshot (BR-040); the booking moves to CANCELLED
    through §20.2's "policy evaluated; refund computed"; its seats return to sale
    at once, before any refund settles (BR-048); the refund obligation is
    published for Phase 8 to settle; and the trip is cancelled if this was its
    last live component.
    """
    now = now or timezone.now()
    row = Booking.objects.select_for_update().filter(public_id=public_id).first()
    if row is None:
        raise NotFoundError()

    party = _PARTY_FOR_ACTOR[actor]
    if not _cancellable_by(row, actor):
        raise CancellationNotPermittedError(
            f"{row.reference} is {row.status} and can no longer be cancelled here."
        )

    was = BookingState(row.status)
    refund = _refund_for(row, party=party, now=now)
    target = apply(was, BookingState.CANCELLED, actor=actor, context={"policy_evaluated": True})
    code = _REASON_FOR_PARTY[party]
    repo.set_status(
        row, target.value, cancelled_at=now, cancelled_by=party.value, cancellation_reason=code
    )
    repo.record_transition(
        row,
        from_status=was.value,
        to_status=target.value,
        actor_role=actor.value,
        actor_user_id=actor_user_id,
        reason=reason or f"Cancelled; {refund.refund_amount} {row.currency} to refund.",
        occurred_at=now,
    )

    activity = BookingActivity.objects.filter(booking=row).first()
    if activity is not None:
        if was is BookingState.PENDING:
            inventory.release_departure(
                trip_id=row.trip_id, departure_id=activity.activity_departure_id
            )
        else:
            inventory.return_sold(
                departure_id=activity.activity_departure_id, quantity=row.pax_count
            )

    publish(
        BookingCancelled(
            booking_public_id=str(row.public_id),
            reference=row.reference,
            trip_id=row.trip_id,
            provider_id=row.provider_id,
            cancelled_by=party.value,
            reason=code,
            refund_amount=str(refund.refund_amount),
            currency=row.currency,
        )
    )
    _end_trip_if_every_component_is_cancelled(row.trip_id)
    return _cancellation(row, refund, cancellable=False)


# -- BR-046: a whole trip --------------------------------------------------------


_LIVE_FOR_CANCELLATION = (
    BookingState.PENDING.value,
    BookingState.AWAITING_PROVIDER.value,
    BookingState.CONFIRMED.value,
    BookingState.IN_PROGRESS.value,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class TripCancellationDTO:
    """§20.9: "Trip-level cancellation evaluates each component booking
    independently against its own snapshotted policy and returns an itemised
    total"."""

    trip: TripDTO
    currency: str
    refund_amount: Decimal
    components: tuple[CancellationDTO, ...]


def _owned_trip(public_id: UUID, tourist_id: int) -> tuple[int, str]:
    basis = trip_services.quote_basis(public_id, tourist_id=tourist_id)
    return basis.trip_id, basis.currency


def preview_trip_cancellation(
    public_id: UUID, *, tourist_id: int, now: datetime | None = None
) -> TripCancellationDTO:
    """What cancelling the whole trip would refund, component by component."""
    now = now or timezone.now()
    trip_id, currency = _owned_trip(public_id, tourist_id)
    rows = list(
        Booking.objects.filter(trip_id=trip_id, status__in=_LIVE_FOR_CANCELLATION).order_by("id")
    )
    components = tuple(preview_cancellation(row, now=now) for row in rows)
    trip = trip_services.get_trip(public_id, tourist_id=tourist_id)
    assert trip is not None
    return TripCancellationDTO(
        trip=trip,
        currency=currency,
        refund_amount=sum((c.refund_amount for c in components), Decimal("0.00")),
        components=components,
    )


@transaction.atomic
def cancel_trip(
    public_id: UUID,
    *,
    tourist_id: int,
    actor_user_id: int | None,
    now: datetime | None = None,
) -> TripCancellationDTO:
    """`POST /trips/{id}/cancel` — §9.3, §20.5, BR-046.

    **Every component, independently, or none.** BR-046: each is evaluated
    against its own snapshot. If any live component can no longer be cancelled
    by the tourist — one already IN_PROGRESS, say — the whole request is refused
    with every such component named, and nothing is cancelled: a trip half
    cancelled by a request that failed is a state nobody asked for.

    A trip with no bookings — a plan or a quote — is cancelled as before, and
    any capacity it still holds is released rather than left for the sweeper.
    """
    now = now or timezone.now()
    trip_id, currency = _owned_trip(public_id, tourist_id)
    rows = repo.lock_of_trip(trip_id, statuses=_LIVE_FOR_CANCELLATION)

    if not rows:
        inventory.release(trip_id=trip_id)
        trip = trip_services.cancel_trip(public_id, tourist_id=tourist_id)
        return TripCancellationDTO(
            trip=trip, currency=currency, refund_amount=Decimal("0.00"), components=()
        )

    blocked = [row for row in rows if not _cancellable_by(row, Actor.TOURIST)]
    if blocked:
        raise CancellationNotPermittedError(
            "Part of this trip can no longer be cancelled.",
            details=[{"booking": row.reference, "status": row.status} for row in blocked],
        )

    components = tuple(
        cancel_booking(
            row.public_id,
            actor=Actor.TOURIST,
            actor_user_id=actor_user_id,
            reason="Whole trip cancelled.",
            now=now,
        )
        for row in rows
    )
    after = trip_services.get_trip(public_id, tourist_id=tourist_id)
    assert after is not None
    return TripCancellationDTO(
        trip=after,
        currency=currency,
        refund_amount=sum((c.refund_amount for c in components), Decimal("0.00")),
        components=components,
    )


# -- ADR 0026: vouchers ------------------------------------------------------------


class VoucherIntegrityError(PlatformError):
    """A re-rendered voucher did not match the hash recorded when it was issued.

    A 500 and an alert, never a quietly different document: a voucher is
    something a tourist may already have printed and a provider may already
    have seen, and one that changed on a second download is worse than none.
    """

    status_code = 500
    code = "VOUCHER_INTEGRITY"


def _voucher_content(row: Booking, *, issue: int, now: datetime) -> VoucherContent:
    facts = trip_services.voucher_facts(row.trip_id, booking_ids=[row.pk]).get(row.pk)
    seller = provider.providers_by_id([row.provider_id]).get(row.provider_id)
    activity = BookingActivity.objects.filter(booking=row).first()
    party = (
        party_text(adults=activity.pax_adult, children=activity.pax_child)
        if activity is not None
        else f"{row.pax_count} traveller{'s' if row.pax_count != 1 else ''}"
    )
    snapshot = row.cancellation_policy_snapshot or {}
    return VoucherContent(
        booking_reference=row.reference,
        trip_reference=facts.trip_reference if facts else "",
        issue_number=issue,
        issued_at=now,
        service_title=facts.title if facts else row.booking_type.title(),
        service_kind=row.booking_type.title(),
        when=when_text(row.starts_at, facts.timezone if facts else "UTC"),
        party=party,
        provider_name=seller.trading_name if seller else "",
        provider_contact=(f"{seller.contact_phone} · {seller.contact_email}" if seller else ""),
        meeting_point=facts.meeting_point if facts else "",
        amount_paid=money_text(row.gross_amount + row.fee_amount + row.tax_amount, row.currency),
        cancellation_terms=policy_summary(list(snapshot.get("tiers", []))),
        support_contact=str(get_setting("support.contact")),
    )


def _render(content: VoucherContent) -> bytes:
    return get_document_port().render_voucher(content)


def issue_voucher(
    row: Booking, *, issued_by: int | None, reason: str, now: datetime | None = None
) -> VoucherDTO:
    """Issue the next voucher for a booking — ADR 0026 decisions 3 and 5.

    The content is frozen onto the record and the rendered file's hash beside
    it. The file itself goes to storage after commit, because a file stored for
    a transaction that then rolled back would describe a voucher nobody issued.
    """
    now = now or timezone.now()
    issue = (
        BookingVoucher.objects.filter(booking=row)
        .order_by("-issue_number")
        .values_list("issue_number", flat=True)
        .first()
        or 0
    ) + 1
    content = _voucher_content(row, issue=issue, now=now)
    data = _render(content)
    key = f"vouchers/{row.public_id}/{issue}.pdf"
    stored = asdict(content)
    stored["issued_at"] = content.issued_at.isoformat()
    voucher = BookingVoucher.objects.create(
        booking=row,
        issue_number=issue,
        content=stored,
        storage_key=key,
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        issued_at=now,
        issued_by_user_id=issued_by,
        reason=reason,
    )
    transaction.on_commit(
        lambda: get_storage_port().put(key=key, data=data, content_type="application/pdf")
    )
    return VoucherDTO(
        booking_reference=row.reference,
        issue_number=voucher.issue_number,
        issued_at=voucher.issued_at,
        sha256=voucher.sha256,
    )


def voucher_document(row: Booking) -> tuple[str, bytes]:
    """The latest issue's file, and its filename — `POST /bookings/{id}/voucher`.

    Read from storage when it is there. When it is not — every current
    environment stores objects in a per-process fake — the record is re-rendered
    and served **only if** its hash matches the one recorded at issue.
    """
    voucher = BookingVoucher.objects.filter(booking=row).order_by("-issue_number").first()
    if voucher is None:
        raise NotFoundError("This booking has no voucher yet; it is issued on confirmation.")

    storage = get_storage_port()
    if storage.exists(voucher.storage_key):
        data = storage.get(voucher.storage_key)
    else:
        fields = dict(voucher.content)
        fields["issued_at"] = datetime.fromisoformat(str(fields["issued_at"]))
        data = _render(VoucherContent(**fields))

    if hashlib.sha256(data).hexdigest() != voucher.sha256:
        raise VoucherIntegrityError(
            f"The voucher for {row.reference} could not be reproduced exactly."
        )
    return f"{row.reference}-voucher-{voucher.issue_number}.pdf", data


# -- API-05: reading bookings --------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class BookingDetailDTO:
    """`GET /bookings/{id}` — the booking, its history and its provider's contact."""

    public_id: UUID
    reference: str
    booking_type: str
    status: str
    title: str
    starts_at: datetime
    ends_at: datetime
    pax_count: int
    gross_amount: Decimal
    fee_amount: Decimal
    tax_amount: Decimal
    currency: str
    cancellation_policy_code: str
    confirmed_at: datetime | None
    cancelled_at: datetime | None
    response_due_at: datetime | None
    provider: dict[str, str] | None
    history: tuple[dict[str, object], ...]
    has_voucher: bool


def list_bookings(rows: Sequence[Booking]) -> list[BookingDTO]:
    """The rows the caller's scoped selector already chose, with their titles."""
    titles = trip_services.booked_titles([row.pk for row in rows])
    return [_booking_dto(row, titles.get(row.pk, "")) for row in rows]


def booking_detail(row: Booking) -> BookingDetailDTO:
    base = _booking_dto(row, trip_services.booked_titles([row.pk]).get(row.pk, ""))
    seller = provider.providers_by_id([row.provider_id]).get(row.provider_id)
    history: tuple[dict[str, object], ...] = tuple(
        {
            "from_status": entry.from_status,
            "to_status": entry.to_status,
            "actor_role": entry.actor_role,
            "reason": entry.reason,
            "occurred_at": entry.occurred_at,
        }
        for entry in row.history.order_by("occurred_at", "id")
    )
    return BookingDetailDTO(
        public_id=base.public_id,
        reference=base.reference,
        booking_type=base.booking_type,
        status=base.status,
        title=base.title,
        starts_at=base.starts_at,
        ends_at=base.ends_at,
        pax_count=base.pax_count,
        gross_amount=base.gross_amount,
        fee_amount=base.fee_amount,
        tax_amount=base.tax_amount,
        currency=base.currency,
        cancellation_policy_code=base.cancellation_policy_code,
        confirmed_at=base.confirmed_at,
        cancelled_at=base.cancelled_at,
        response_due_at=base.response_due_at,
        provider=(
            {
                "name": seller.trading_name,
                "phone": seller.contact_phone,
                "email": seller.contact_email,
            }
            if seller
            else None
        ),
        history=history,
        has_voucher=row.vouchers.exists(),
    )


# -- §27.9 / BR-038: exceptional controls -----------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class ForcedTransitionDTO:
    before: str
    after: str
    booking: BookingDTO


@transaction.atomic
def force_transition(
    public_id: UUID,
    *,
    target: str,
    actor_user_id: int,
    reason: str,
    now: datetime | None = None,
) -> ForcedTransitionDTO:
    """`POST /admin/bookings/{id}/force-transition` — BR-038.

    Guards are bypassed; §20.2's edges are not (`lifecycle.force`). What a
    forced move *does* is the same as the ordinary move to that state, so a
    forced booking is indistinguishable from a normal one except in its history:

    - **to CONFIRMED or AWAITING_PROVIDER from PENDING** runs §20.8 for the whole
      basket — capacity committed, vouchers issued — because confirming one
      component of a paid basket and not the others is a state §20.8 never
      produces;
    - **to CANCELLED** cancels as the platform, so the tourist is refunded in
      full (BR-045): an administrator overriding a booking is supply failing;
    - **anything else** moves the status and stamps its timestamp.
    """
    now = now or timezone.now()
    row = Booking.objects.select_for_update().filter(public_id=public_id).first()
    if row is None:
        raise NotFoundError()
    before = BookingState(row.status)
    wanted = BookingState(target)
    force(before, wanted)

    if before is BookingState.PENDING and wanted in (
        BookingState.CONFIRMED,
        BookingState.AWAITING_PROVIDER,
    ):
        confirm_trip(
            row.trip_id, payment_captured=False, now=now, forced_by=actor_user_id, reason=reason
        )
    elif wanted is BookingState.CANCELLED:
        _cancel_as_platform(row, actor_user_id=actor_user_id, reason=reason, now=now)
    else:
        fields: dict[str, object] = {}
        if wanted is BookingState.COMPLETED:
            fields["completed_at"] = now
        if wanted is BookingState.CONFIRMED:
            fields["confirmed_at"] = now
        repo.set_status(row, wanted.value, **fields)
        repo.record_transition(
            row,
            from_status=before.value,
            to_status=wanted.value,
            actor_role="SUPER_ADMIN",
            actor_user_id=actor_user_id,
            reason=reason,
            occurred_at=now,
        )
        if wanted is BookingState.CONFIRMED:
            issue_voucher(row, issued_by=actor_user_id, reason=reason, now=now)

    row.refresh_from_db()
    return ForcedTransitionDTO(before=before.value, after=row.status, booking=_booking_dto(row, ""))


def _cancel_as_platform(row: Booking, *, actor_user_id: int, reason: str, now: datetime) -> None:
    was = BookingState(row.status)
    refund = _refund_for(row, party=Party.PLATFORM, now=now)
    repo.set_status(
        row,
        BookingState.CANCELLED.value,
        cancelled_at=now,
        cancelled_by="PLATFORM",
        cancellation_reason="ADMIN_ACTION",
    )
    repo.record_transition(
        row,
        from_status=was.value,
        to_status=BookingState.CANCELLED.value,
        actor_role="SUPER_ADMIN",
        actor_user_id=actor_user_id,
        reason=reason,
        occurred_at=now,
    )
    activity = BookingActivity.objects.filter(booking=row).first()
    if activity is not None:
        if was is BookingState.PENDING:
            inventory.release_departure(
                trip_id=row.trip_id, departure_id=activity.activity_departure_id
            )
        elif was in (BookingState.CONFIRMED, BookingState.AWAITING_PROVIDER):
            inventory.return_sold(
                departure_id=activity.activity_departure_id, quantity=row.pax_count
            )
    publish(
        BookingCancelled(
            booking_public_id=str(row.public_id),
            reference=row.reference,
            trip_id=row.trip_id,
            provider_id=row.provider_id,
            cancelled_by="PLATFORM",
            reason="ADMIN_ACTION",
            refund_amount=str(refund.refund_amount),
            currency=row.currency,
        )
    )
    _end_trip_if_every_component_is_cancelled(row.trip_id)


def reissue_voucher(public_id: UUID, *, actor_user_id: int, reason: str) -> VoucherDTO:
    """§27.9: "re-issue a voucher", with a reason. Issue n + 1; earlier ones kept."""
    row = Booking.objects.filter(public_id=public_id).first()
    if row is None:
        raise NotFoundError()
    if not row.vouchers.exists():
        raise ConflictError("Only a confirmed booking's voucher can be re-issued.")
    return issue_voucher(row, issued_by=actor_user_id, reason=reason)


def owned_trip_id(public_id: str | UUID, *, tourist_id: int | None) -> int | None:
    """The storage id of one of this tourist's trips, or `None` for anything else.

    `None` rather than a 404, because the caller is filtering a list: a trip that
    is not yours narrows the list to nothing, exactly as a trip that does not
    exist does.
    """
    if tourist_id is None:
        return None
    try:
        return trip_services.quote_basis(UUID(str(public_id)), tourist_id=tourist_id).trip_id
    except (NotFoundError, ValueError):
        return None
