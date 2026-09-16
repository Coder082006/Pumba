"""administration module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Event handlers: the three emails a
payment sends.

**Here because nowhere else can be.** §19.1 wants `PAYMENT_SUCCEEDED`,
`TRIP_CONFIRMED` (with the itinerary PDF) and `REFUND_ISSUED` sent to the
tourist. Composing any of them needs the tourist's address, the trip's plan and
the bookings' vouchers at once — and §6.4 gives `payment`, `booking` and `trip`
no sight of `identity`. `administration` is the one module with "all (read via
interfaces)", which is the same argument that puts the consoles here.

**`notify` is Phase 10 and is not pre-empted.** That module owns templates,
per-channel preferences, delivery records and retries. What is here is the
narrow thing §41.9 requires of Phase 8 — that a captured payment "dispatches
every notification" — written as three plain messages through `EmailPort`.
When `notify` lands, these become three template ids and this file gets
shorter.

**A failed email never fails a payment.** `publish` dispatches after commit and
swallows what a handler raises, which is exactly right here: a tourist whose
trip is confirmed and whose receipt bounced is in a better position than one
whose payment was rolled back because a mail server was down.
"""

from __future__ import annotations

import logging

from django.utils import timezone

from apps.booking import services as booking
from apps.common.config import get_setting
from apps.common.events import subscribe
from apps.common.ports_registry import get_document_port, get_email_port
from apps.identity import services as identity
from apps.payment.services import PaymentCaptured, RefundSettled
from apps.trip import services as trip_services
from apps.trip.services import TripConfirmed
from ports.document import ItineraryContent, ItineraryDay
from ports.notification import Attachment

logger = logging.getLogger(__name__)

__all__ = [
    "on_trip_confirmed",
    "on_payment_captured",
    "on_refund_settled",
    "register",
]


def _support() -> str:
    return str(get_setting("support.contact"))


def _send(*, to: str, subject: str, lines: list[str], attachments: list[Attachment]) -> None:
    text = "\n".join(lines)
    html = "<p>" + "</p><p>".join(line for line in lines if line) + "</p>"
    get_email_port().send(
        to=to,
        subject=subject,
        html_body=html,
        text_body=text,
        attachments=attachments or None,
    )


def on_trip_confirmed(event: TripConfirmed) -> None:
    """§19.1's TRIP_CONFIRMED — "Push, Email (with itinerary PDF)".

    §41.10 as amended makes that PDF the web client's entire answer to the
    offline requirement, so the attachment is the point of the message rather
    than an extra: the tourist must be able to read their whole trip, every
    voucher included, with no signal.
    """
    contact = identity.contact_for_tourist(event.tourist_id)
    if contact is None:
        logger.warning("no_contact_for_trip", extra={"trip": event.trip_public_id})
        return

    trip_id = trip_services.trip_id_of(event.trip_public_id)
    if trip_id is None:
        return
    facts = trip_services.itinerary_facts(trip_id)
    if facts is None:
        return

    vouchers = booking.vouchers_of_trip(trip_id)
    document = ItineraryContent(
        trip_reference=facts.trip_reference,
        title=facts.title,
        destination=facts.destination,
        dates=facts.dates,
        party=facts.party,
        days=tuple(ItineraryDay(heading=day.heading, lines=day.lines) for day in facts.days),
        vouchers=vouchers,
        total_paid=facts.total_paid,
        support_contact=_support(),
        issued_at=timezone.now(),
    )
    pdf = get_document_port().render_itinerary(document)

    greeting = f"Hello {contact.first_name}," if contact.first_name else "Hello,"
    _send(
        to=contact.email,
        subject=f"Your trip is confirmed — {facts.trip_reference}",
        lines=[
            greeting,
            f"Your trip to {facts.destination} is confirmed for {facts.dates}.",
            "The attached PDF has your full day-by-day plan and every booking "
            "voucher, so you can read it with no signal.",
            f"Total paid: {facts.total_paid}.",
            f"If anything changes, contact us on {_support()}.",
        ],
        attachments=[Attachment(filename=f"{facts.trip_reference}-itinerary.pdf", content=pdf)],
    )
    logger.info("trip_confirmed_email_sent", extra={"trip": facts.trip_reference})


def on_payment_captured(event: PaymentCaptured) -> None:
    """§19.1's PAYMENT_SUCCEEDED — a receipt, distinct from the confirmation.

    Two messages for one moment, and deliberately: §19.2 makes PAYMENT_*
    undisableable because it is a financial fact, while the confirmation is
    about the trip. A tourist who paid for a trip where every component then
    failed still gets this one.
    """
    contact = identity.contact_for_tourist(event.tourist_id)
    if contact is None:
        return
    _send(
        to=contact.email,
        subject=f"Payment received — {event.amount} {event.currency}",
        lines=[
            f"Hello {contact.first_name}," if contact.first_name else "Hello,",
            f"We have received your payment of {event.amount} {event.currency}.",
            "Your booking confirmation follows separately.",
            f"Questions: {_support()}.",
        ],
        attachments=[],
    )


def on_refund_settled(event: RefundSettled) -> None:
    """§19.1's REFUND_ISSUED — "Refund settled", not "refund decided".

    Sent from the settlement rather than from the cancellation, because the
    two are days apart at a card issuer and a tourist told "refunded" on the
    day they cancelled will call support on the third day looking for it.
    """
    trip_id = event.trip_id
    tourist_id = trip_services.tourist_id_of(trip_id)
    if tourist_id is None:
        return
    contact = identity.contact_for_tourist(tourist_id)
    if contact is None:
        return
    _send(
        to=contact.email,
        subject=f"Refund issued — {event.amount} {event.currency}",
        lines=[
            f"Hello {contact.first_name}," if contact.first_name else "Hello,",
            f"A refund of {event.amount} {event.currency} has been sent back to "
            "the card or wallet you paid with.",
            "Your bank decides how long it takes to appear — usually a few " "working days.",
            f"Questions: {_support()}.",
        ],
        attachments=[],
    )


def register() -> None:
    """Called from `AdministrationConfig.ready()`, once per process."""
    subscribe(TripConfirmed, on_trip_confirmed)
    subscribe(PaymentCaptured, on_payment_captured)
    subscribe(RefundSettled, on_refund_settled)
