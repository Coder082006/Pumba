"""finance module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Event handlers: what the ledger
records, and when.

Three events, three moments §22.3 and §22.4 separate on purpose:

- **`payment.captured`** — money arrives in clearing, and the platform's own
  service fee is earned. Nobody is owed anything yet.
- **`booking.completed`** — BR-071. The service happened, so the operator has
  earned their share and the platform its commission. This is the moment
  clearing stops holding somebody else's money.
- **`payment.refund_settled`** — §22.6. What the reversal does depends on
  whether the accrual had already happened, which the ledger knows.

**A handler that raises loses a ledger entry, not a payment.** `publish`
dispatches after commit and swallows what a handler raises, which is right —
a tourist's trip must not be rolled back because an accrual failed. The nightly
check (BR-064, TC-111) is what finds the gap, and it names the booking.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from apps.booking import services as booking
from apps.booking.services import BookingCompleted
from apps.common.events import subscribe
from apps.finance import services
from apps.payment import services as payment
from apps.payment.services import PaymentCaptured, RefundSettled

logger = logging.getLogger(__name__)

__all__ = [
    "on_payment_captured",
    "on_booking_completed",
    "on_refund_settled",
    "register",
]


def on_payment_captured(event: PaymentCaptured) -> None:
    """§22.3: CUSTOMER_PAYMENT into clearing, SERVICE_FEE_REVENUE earned.

    The fee is summed across the trip's bookings rather than taken from the
    payment, because §18.3 makes it a per-component charge that the basket
    allocated by largest remainder — and the ledger records what was allocated,
    not what it would have re-derived.
    """
    rows = booking.fee_and_provider_of_trip(event.trip_id)
    if not rows:
        logger.warning("capture_without_bookings", extra={"trip_id": event.trip_id})
        return

    services.accrue_capture(
        payment_id=0,
        lines=[
            services.CapturedLine(
                booking_id=row.booking_id,
                paid=row.gross_amount + row.fee_amount + row.tax_amount,
                service_fee=row.fee_amount,
                currency=row.currency,
            )
            for row in rows
        ],
    )


def on_booking_completed(event: BookingCompleted) -> None:
    """BR-071: the operator earns when the service has happened.

    Every figure comes from the event, which took them from the booking's own
    snapshot (BR-070). Nothing here consults a commission rule — that is
    TC-110, and it is true by construction rather than by care.
    """
    booking_id = booking.booking_id_for(event.booking_public_id)
    if booking_id is None:
        logger.warning("completion_without_booking", extra={"reference": event.reference})
        return

    services.accrue_completion(
        booking_id=booking_id,
        provider_id=event.provider_id,
        gross=Decimal(event.gross_amount),
        commission=Decimal(event.commission_amount),
        net=Decimal(event.net_amount),
        currency=event.currency,
    )


def on_refund_settled(event: RefundSettled) -> None:
    """§22.6, when the money is actually back with the tourist.

    From the settlement rather than the cancellation, like the email: what the
    ledger records is money that moved, and a refund decided on Monday and
    settled on Thursday moved on Thursday.
    """
    refund = payment.refund_facts(event.refund_public_id)
    if refund is None:
        logger.warning("refund_settled_without_row", extra={"refund": event.refund_public_id})
        return

    provider_id = _provider_of(refund.booking_id, event.trip_id)
    if provider_id is None:
        return

    services.reverse_for_refund(
        booking_id=refund.booking_id,
        provider_id=provider_id,
        refund_amount=Decimal(event.amount),
        provider_compensation=refund.provider_compensation,
        currency=event.currency,
    )


def _provider_of(booking_id: int, trip_id: int) -> int | None:
    """Which operator a booking belongs to, through `booking`'s own door."""
    for row in booking.fee_and_provider_of_trip(trip_id):
        if row.booking_id == booking_id:
            return row.provider_id
    return None


def register() -> None:
    """Called from `FinanceConfig.ready()`, once per process."""
    subscribe(PaymentCaptured, on_payment_captured)
    subscribe(BookingCompleted, on_booking_completed)
    subscribe(RefundSettled, on_refund_settled)
