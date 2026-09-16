"""The three emails a payment sends — §19.1, §19.2, §41.10.

Under `tests/` because composing any of them spans `identity`, `trip`,
`booking` and `payment`, and §6.4 lets none of those modules' tests import the
others.

What is worth asserting is that the message carries what the tourist actually
needs: §41.10 as amended makes the attached PDF the web client's *entire*
answer to the offline requirement, so a confirmation email without it is not a
smaller feature — it is an unmet acceptance criterion.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from rest_framework.test import APIClient

from apps.administration import handlers
from apps.booking import services as booking_services
from apps.booking.tests import scenario
from apps.common.ports_registry import get_email_port, get_payment_port
from apps.payment.models import Payment
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

WEBHOOK = reverse("v1:payment:psp-webhook", kwargs={"provider": "stripe"})
INTENT = reverse("v1:payment:payment-intent")


def _sent() -> list[dict[str, Any]]:
    return list(get_email_port().sent)  # type: ignore[attr-defined]


def _pay_for_a_trip(capture_on_commit: Any) -> tuple[APIClient, scenario.Scenario, Payment]:
    """The whole journey, from reservation to a captured webhook.

    `capture_on_commit` is `django_capture_on_commit_callbacks`, and it is not
    a workaround: §8.9 dispatches every event after commit precisely so that
    "no notification can describe a state that was rolled back", and a test
    wrapped in a transaction that never commits has to run them by hand.
    """
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    built = scenario.build(tourist_id=int(profile.id), adults=2)
    quote = booking_services.quote_trip(built.trip_public_id, tourist_id=int(profile.id))
    booking_services.create_basket(
        built.trip_public_id, tourist_id=int(profile.id), quote_token=quote.quote_token
    )
    client.post(
        INTENT,
        {"trip_id": str(built.trip_public_id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    payment = Payment.objects.get()
    get_payment_port().capture(payment.psp_reference or "", idempotency_key=uuid.uuid4().hex)
    with capture_on_commit(execute=True):
        _deliver(client, payment)
    payment.refresh_from_db()
    return client, built, payment


def _deliver(client: APIClient, payment: Payment) -> None:
    client.post(
        WEBHOOK,
        json.dumps(
            {
                "event_id": f"evt_{uuid.uuid4().hex[:10]}",
                "event_type": "payment_intent.succeeded",
                "psp_reference": payment.psp_reference,
                "status": "CAPTURED",
            }
        ),
        content_type="application/json",
        HTTP_X_FAKE_SIGNATURE="valid",
    )


class TestWhatACapturedPaymentSends:
    def test_both_a_receipt_and_a_confirmation_arrive(
        self, django_capture_on_commit_callbacks
    ) -> None:  # type: ignore[no-untyped-def]
        """§19.1 lists PAYMENT_SUCCEEDED and TRIP_CONFIRMED separately, and
        §19.2 makes the first undisableable: paying and being booked are two
        facts, and a tourist whose components all failed is still owed the
        first one."""
        _pay_for_a_trip(django_capture_on_commit_callbacks)

        subjects = [message["subject"] for message in _sent()]

        assert any("Payment received" in subject for subject in subjects)
        assert any("confirmed" in subject for subject in subjects)

    def test_the_confirmation_carries_the_itinerary_pdf(
        self, django_capture_on_commit_callbacks
    ) -> None:  # type: ignore[no-untyped-def]
        """§41.10 as amended: the emailed PDF *is* the web client's offline
        guarantee, so this attachment is the acceptance criterion rather than a
        nicety."""
        _pay_for_a_trip(django_capture_on_commit_callbacks)

        confirmation = next(message for message in _sent() if "confirmed" in message["subject"])

        assert len(confirmation["attachments"]) == 1
        attachment = confirmation["attachments"][0]
        assert attachment["filename"].endswith("-itinerary.pdf")
        assert attachment["media_type"] == "application/pdf"
        assert attachment["bytes"] > 0

    def test_the_document_holds_the_plan_and_every_voucher(
        self, django_capture_on_commit_callbacks
    ) -> None:  # type: ignore[no-untyped-def]
        """A PDF of the right size proves nothing. The fake renderer records
        the content, so this reads what the tourist would read."""
        _, built, _ = _pay_for_a_trip(django_capture_on_commit_callbacks)
        rendered = get_document_port_itineraries()

        assert len(rendered) == 1
        document = rendered[0]
        assert document.trip_reference
        assert document.days, "an itinerary with no days is not a plan"
        assert len(document.vouchers) == 1
        assert document.support_contact

    def test_it_goes_to_the_tourist_who_paid(self, django_capture_on_commit_callbacks) -> None:  # type: ignore[no-untyped-def]
        _, _, payment = _pay_for_a_trip(django_capture_on_commit_callbacks)
        profile = django_apps.get_model("identity", "TouristProfile").objects.get(
            pk=payment.tourist_id
        )

        assert all(message["recipient"] == profile.user.email for message in _sent())


class TestARefundIsAnnouncedWhenItSettles:
    def test_the_email_follows_the_settlement_not_the_cancellation(
        self, django_capture_on_commit_callbacks
    ) -> None:  # type: ignore[no-untyped-def]
        """A tourist told "refunded" on the day they cancelled calls support on
        the third day looking for money the issuer has not released yet."""
        _, built, _ = _pay_for_a_trip(django_capture_on_commit_callbacks)
        booking_row = django_apps.get_model("booking", "Booking").objects.get()
        before = len(_sent())

        booking_services.cancel_booking(
            booking_row.public_id,
            actor=booking_services.Actor.TOURIST,
            actor_user_id=None,
        )
        cancelled_only = len(_sent()) - before

        handlers.on_refund_settled(
            __import__("apps.payment.services", fromlist=["RefundSettled"]).RefundSettled(
                refund_public_id=str(uuid.uuid4()),
                payment_public_id=str(uuid.uuid4()),
                trip_id=built.trip_id,
                amount="55.00",
                currency="USD",
            )
        )

        assert cancelled_only == 0, "a cancellation must not announce a refund"
        assert any("Refund issued" in message["subject"] for message in _sent())


def get_document_port_itineraries() -> list[Any]:
    from apps.common.ports_registry import get_document_port

    return list(get_document_port().itineraries)  # type: ignore[attr-defined]
