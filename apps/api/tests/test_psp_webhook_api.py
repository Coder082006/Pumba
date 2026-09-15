"""`POST /api/v1/webhooks/psp/{provider}` — §9.4.8, §21.5, TC-070, TC-071.

The whole of what payment adds to Phase 7 happens here: a capture arrives and a
reserved trip becomes a confirmed one. So this file asserts the consequences
rather than the endpoint — holds committed, bookings CONFIRMED, trip CONFIRMED,
vouchers issued — because a webhook that returned 200 and confirmed nothing
would pass any test that only read the response.

The gateway is `FakePaymentGateway`, which verifies `X-Fake-Signature: valid`
and nothing else. Stripe's real HMAC and its five-minute window are covered in
`apps/payment/tests/test_stripe_gateway.py`, without a network.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from rest_framework.test import APIClient

from apps.booking import services as booking_services
from apps.booking.models import Booking
from apps.booking.tests import scenario
from apps.payment.models import Payment, PaymentStatus, PaymentWebhookEvent, WebhookOutcome
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

WEBHOOK = reverse("v1:payment:psp-webhook", kwargs={"provider": "stripe"})
INTENT = reverse("v1:payment:payment-intent")
SIGNED = {"HTTP_X_FAKE_SIGNATURE": "valid"}


def _paying() -> tuple[APIClient, scenario.Scenario, Payment]:
    """A tourist who has reserved a trip and started paying for it."""
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    built = scenario.build(tourist_id=int(profile.id), adults=2)
    quote = booking_services.quote_trip(built.trip_public_id, tourist_id=int(profile.id))
    booking_services.create_basket(
        built.trip_public_id, tourist_id=int(profile.id), quote_token=quote.quote_token
    )
    response = client.post(
        INTENT,
        {"trip_id": str(built.trip_public_id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    assert response.status_code == 201, response.data
    return client, built, Payment.objects.get()


def _event(payment: Payment, status: str, *, event_id: str | None = None) -> dict[str, Any]:
    return {
        "event_id": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "event_type": f"payment_intent.{status.lower()}",
        "psp_reference": payment.psp_reference,
        "status": status,
    }


def _deliver(client: APIClient, body: dict[str, Any], **extra: Any) -> Any:
    return client.post(
        WEBHOOK, json.dumps(body), content_type="application/json", **{**SIGNED, **extra}
    )


def _capture(payment: Payment) -> None:
    """Tell the fake gateway the money was taken, as Stripe would have."""
    from apps.common.ports_registry import get_payment_port

    get_payment_port().capture(payment.psp_reference or "", idempotency_key=uuid.uuid4().hex)


class TestTc070PaymentSuccessConfirmsEverything:
    def test_the_payment_is_captured_and_the_trip_is_confirmed(self) -> None:
        """TC-070: "Payment CAPTURED; holds COMMITTED; bookings CONFIRMED;
        trip CONFIRMED; ledger entries written" — the ledger is 8b."""
        client, built, payment = _paying()
        _capture(payment)

        response = _deliver(client, _event(payment, "CAPTURED"))

        assert response.status_code == 200
        assert response.json()["data"]["outcome"] == WebhookOutcome.APPLIED
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.CAPTURED
        assert payment.captured_at is not None
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)
        assert trip.status == "CONFIRMED"
        assert Booking.objects.get().status == "CONFIRMED"

    def test_the_seats_move_from_held_to_sold(self) -> None:
        client, built, payment = _paying()
        _capture(payment)

        _deliver(client, _event(payment, "CAPTURED"))

        departure = django_apps.get_model("inventory", "ActivityDeparture").objects.get(
            id=built.departure_id
        )
        assert departure.capacity_held == 0
        assert departure.capacity_sold == 2
        hold = django_apps.get_model("inventory", "InventoryHold").objects.get()
        assert hold.status == "COMMITTED"

    def test_a_voucher_exists_for_the_confirmed_booking(self) -> None:
        """§41.8: vouchers generate for every confirmed booking — and until
        now nothing a tourist could do would make one."""
        client, _, payment = _paying()
        _capture(payment)

        _deliver(client, _event(payment, "CAPTURED"))

        assert django_apps.get_model("booking", "BookingVoucher").objects.count() == 1

    def test_the_transition_is_recorded_against_the_event_that_caused_it(self) -> None:
        """PM6, and what makes a dispute answerable: the PSP's words and our
        reading of them, side by side."""
        client, _, payment = _paying()
        _capture(payment)

        _deliver(client, _event(payment, "CAPTURED"))

        stored = PaymentWebhookEvent.objects.get(event_type="payment_intent.captured")
        assert stored.outcome == WebhookOutcome.APPLIED
        assert stored.processed_at is not None
        assert stored.payment_id == payment.pk
        transitions = payment.transactions.filter(to_status="CAPTURED")
        assert transitions.count() == 1
        assert transitions.get().raw_event_id == stored.pk


class TestTc071DuplicateWebhook:
    def test_the_same_event_twice_changes_nothing_the_second_time(self) -> None:
        """TC-071: "200; no state change; no duplicate ledger entry"."""
        client, built, payment = _paying()
        _capture(payment)
        body = _event(payment, "CAPTURED")
        _deliver(client, body)

        response = _deliver(client, body)

        assert response.status_code == 200
        assert response.json()["data"]["outcome"] == WebhookOutcome.DUPLICATE
        assert PaymentWebhookEvent.objects.filter(psp_event_id=body["event_id"]).count() == 1
        assert payment.transactions.filter(to_status="CAPTURED").count() == 1
        assert django_apps.get_model("booking", "BookingVoucher").objects.count() == 1

    def test_a_second_capture_event_with_a_new_id_does_not_reapply(self) -> None:
        """Deduplication by event id is not enough on its own: a PSP that
        re-sends a capture under a fresh id must still not confirm twice, and
        §21.5's "advances the state" rule is what stops it."""
        client, _, payment = _paying()
        _capture(payment)
        _deliver(client, _event(payment, "CAPTURED"))

        response = _deliver(client, _event(payment, "CAPTURED"))

        assert response.json()["data"]["outcome"] == WebhookOutcome.IGNORED_NOT_ADVANCING
        assert payment.transactions.filter(to_status="CAPTURED").count() == 1


class TestOutOfOrderAndUnknown:
    def test_an_authorised_event_arriving_after_the_capture_is_ignored(self) -> None:
        """§21.5: events may arrive out of order. Applied, this would
        un-capture a trip that is already confirmed and paid for."""
        client, _, payment = _paying()
        _capture(payment)
        _deliver(client, _event(payment, "CAPTURED"))

        response = _deliver(client, _event(payment, "AUTHORISED"))

        assert response.status_code == 200
        assert response.json()["data"]["outcome"] == WebhookOutcome.IGNORED_NOT_ADVANCING
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.CAPTURED

    def test_an_event_for_a_payment_we_do_not_have_is_stored_and_flagged(self) -> None:
        """§21.9's "unmatched at PSP" — always investigated, never dropped."""
        client, _, _ = _paying()

        response = _deliver(
            client,
            {
                "event_id": "evt_stranger",
                "event_type": "payment_intent.succeeded",
                "psp_reference": "pi_nobody_here",
                "status": "CAPTURED",
            },
        )

        assert response.status_code == 200
        assert response.json()["data"]["outcome"] == WebhookOutcome.UNMATCHED
        assert PaymentWebhookEvent.objects.get(psp_event_id="evt_stranger").payment_id is None


class TestTc072PaymentFailure:
    def test_a_failure_releases_the_holds_and_returns_the_trip_to_priced(self) -> None:
        """TC-072: "Payment FAILED; holds RELEASED; bookings FAILED; trip PRICED"."""
        client, built, payment = _paying()

        response = _deliver(client, _event(payment, "FAILED"))

        assert response.status_code == 200
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.FAILED
        assert Booking.objects.get().status == "FAILED"
        assert django_apps.get_model("inventory", "InventoryHold").objects.get().status == (
            "RELEASED"
        )
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)
        assert trip.status == "PRICED"

    def test_the_tourist_may_then_pay_again(self) -> None:
        """BR-062 permits one *live* payment per trip, and a failed one is not
        live — which is the difference between a declined card and a dead end."""
        client, built, payment = _paying()
        _deliver(client, _event(payment, "FAILED"))
        fresh = booking_services.quote_trip(built.trip_public_id, tourist_id=payment.tourist_id)
        booking_services.create_basket(
            built.trip_public_id,
            tourist_id=payment.tourist_id,
            quote_token=fresh.quote_token,
        )

        again = client.post(
            INTENT,
            {"trip_id": str(built.trip_public_id)},
            format="json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )

        assert again.status_code == 201, again.data
        assert Payment.objects.count() == 2


class TestVerification:
    def test_an_unsigned_body_is_refused_and_stores_nothing(self) -> None:
        """§9.4.8: an unverified payload must never reach the state machine."""
        client, _, payment = _paying()

        response = client.post(
            WEBHOOK,
            json.dumps(_event(payment, "CAPTURED")),
            content_type="application/json",
        )

        assert response.status_code == 400
        assert PaymentWebhookEvent.objects.count() == 0
        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PENDING

    def test_no_session_is_needed(self) -> None:
        """The PSP has no account here. Signature is the authentication."""
        _, _, payment = _paying()
        _capture(payment)

        response = _deliver(APIClient(), _event(payment, "CAPTURED"))

        assert response.status_code == 200
        assert response.json()["data"]["outcome"] == WebhookOutcome.APPLIED
