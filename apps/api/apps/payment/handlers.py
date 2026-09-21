"""payment module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Event handlers.

**A handler writes a row and does nothing else.** §8.9's bus dispatches after
commit and swallows a handler's failure so one consumer cannot roll back its
publisher — which is right, and which means a refund *issued* inside a handler
would vanish silently the first time the gateway timed out. There is no sweeper
behind refunds the way §17.5's sweeper stands behind holds.

So the obligation becomes a `refund` row in REQUESTED, and `tasks.settle_refunds`
calls the PSP with retries. The event is the trigger; the row is the record
(ADR 0027 decision 3).

**The amount is never recomputed here.** BR-043 requires the refund issued to
equal the preview shown, and `booking` already decided it: `BookingCancelled`
carries `refund_amount` as a decimal string for exactly this reason. A handler
that evaluated the policy again would be a second implementation of §20.9, free
to disagree with the one the tourist was shown.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from django.utils import timezone

from apps.booking import services as booking_services
from apps.booking.services import BookingCancelled, ComponentFailedAfterCapture
from apps.common.events import subscribe
from apps.payment.models import Payment, Refund, RefundStatus

logger = logging.getLogger(__name__)

__all__ = ["on_booking_cancelled", "on_component_failed_after_capture", "register"]


def _live_payment(trip_id: int) -> Payment | None:
    """The payment a refund would come out of.

    A captured payment, or one already partly refunded. Anything else means
    nobody has paid for this trip: a PENDING basket that was cancelled owes
    nothing, which is what `booking` already says by computing a zero refund
    for an unpaid booking.
    """
    return (
        Payment.objects.filter(
            trip_id=trip_id,
            status__in=["CAPTURED", "PARTIALLY_REFUNDED"],
        )
        .order_by("-id")
        .first()
    )


def _request_refund(
    *,
    trip_id: int,
    booking_public_id: str,
    amount: Decimal,
    currency: str,
    reason_code: str,
    reason: str,
    provider_compensation: Decimal = Decimal("0"),
) -> Refund | None:
    if amount <= Decimal("0"):
        return None
    payment = _live_payment(trip_id)
    if payment is None:
        # Nothing was captured, so there is nothing to give back. Not an error:
        # a basket that failed or was cancelled before payment is the ordinary
        # case, and a refund row for zero money would be a queue entry nobody
        # could settle.
        return None

    booking_id = booking_services.booking_id_for(booking_public_id)
    existing = Refund.objects.filter(
        payment=payment,
        booking_id=booking_id,
        status__in=[RefundStatus.REQUESTED, RefundStatus.SETTLED],
    ).first()
    if existing is not None:
        # One obligation per booking. A cancellation published twice — a retried
        # task, a replayed event — must not owe the tourist twice.
        return existing

    return Refund.objects.create(
        payment=payment,
        booking_id=booking_id,
        amount=amount,
        provider_compensation=provider_compensation,
        currency=currency or payment.presentment_currency,
        reason_code=reason_code,
        reason=reason[:500],
        idempotency_key=uuid.uuid4().hex,
        requested_at=timezone.now(),
    )


def on_booking_cancelled(event: BookingCancelled) -> None:
    """§20.9's decision, recorded as an obligation to pay it out.

    `refund_amount` is what the preview showed (BR-043). A zero refund — a
    late cancellation under a strict policy — writes nothing, because an
    obligation for nothing is not an obligation.
    """
    refund = _request_refund(
        trip_id=event.trip_id,
        booking_public_id=event.booking_public_id,
        amount=Decimal(event.refund_amount or "0"),
        currency=event.currency,
        reason_code=f"CANCELLED_BY_{event.cancelled_by or 'TOURIST'}",
        reason=event.reason,
        provider_compensation=Decimal(event.provider_compensation or "0"),
    )
    if refund is not None:
        logger.info(
            "refund_requested",
            extra={"booking": event.reference, "amount": str(refund.amount)},
        )


def on_component_failed_after_capture(event: ComponentFailedAfterCapture) -> None:
    """§20.8 step 9: the component that could not be secured after capture.

    "Initiate an automatic partial refund for the failed component" — gross,
    fee and tax, because the tourist is getting none of that component and
    §20.9's tiers do not apply to a failure that was not theirs (BR-045).
    """
    owed = (
        Decimal(event.gross_amount or "0")
        + Decimal(event.fee_amount or "0")
        + Decimal(event.tax_amount or "0")
    )
    refund = _request_refund(
        trip_id=event.trip_id,
        booking_public_id=event.booking_public_id,
        amount=owed,
        currency=event.currency,
        reason_code="SUPPLY_FAILED_AT_CAPTURE",
        reason=event.reason,
    )
    if refund is not None:
        logger.info(
            "partial_refund_requested",
            extra={"booking": event.reference, "amount": str(refund.amount)},
        )


def register() -> None:
    """Called from `PaymentConfig.ready()`, which runs once per process."""
    subscribe(BookingCancelled, on_booking_cancelled)
    subscribe(ComponentFailedAfterCapture, on_component_failed_after_capture)
