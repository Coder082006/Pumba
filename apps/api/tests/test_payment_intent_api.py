"""`POST /api/v1/payments/intents` over HTTP — SRS §9.4.7, TC-073, TC-074.

Under `tests/` because a signed-in tourist spans `identity` and `payment`, and
§6.4 lets neither module's tests import the other.

The gateway is `FakePaymentGateway`, which `config.settings.ci` names (ADR
0027): the port is reachable only when something names an adapter, and a test
suite naming the fake is the one legitimate way to reach it. What is asserted
here is the contract §9.4.7 states — the charge comes from the trip, the action
comes back, the key is required — not Stripe's behaviour, which
`apps/payment/tests/test_stripe_gateway.py` covers without a network.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from rest_framework.test import APIClient

from apps.booking import services as booking_services
from apps.booking.tests import scenario
from apps.payment.models import Payment, PaymentStatus
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

INTENT = reverse("v1:payment:payment-intent")


def _signed_in() -> tuple[APIClient, int]:
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    return client, int(profile.id)


def _reserved(**kwargs: object) -> tuple[APIClient, scenario.Scenario]:
    """A tourist with a trip in PENDING_PAYMENT — a basket, unpaid."""
    client, tourist_id = _signed_in()
    built = scenario.build(tourist_id=tourist_id, **kwargs)  # type: ignore[arg-type]
    quote = booking_services.quote_trip(built.trip_public_id, tourist_id=tourist_id)
    booking_services.create_basket(
        built.trip_public_id, tourist_id=tourist_id, quote_token=quote.quote_token
    )
    return client, built


def _intent(client: APIClient, body: dict[str, Any], *, key: str | None = None) -> Any:
    headers = {} if key == "" else {"HTTP_IDEMPOTENCY_KEY": key or uuid.uuid4().hex}
    return client.post(INTENT, body, format="json", **headers)


class TestTheHappyPath:
    def test_it_returns_201_with_an_action_the_browser_can_use(self) -> None:
        client, built = _reserved()

        response = _intent(client, {"trip_id": str(built.trip_public_id)})

        assert response.status_code == 201, response.data
        data = response.data["data"]
        assert data["status"] == "PENDING"
        assert data["action"]["type"] == "CLIENT_SECRET"
        assert data["action"]["payload"]["client_secret"]

    def test_the_charge_is_the_trips_own_total(self) -> None:
        """BR-060, PM2: computed server-side, from the trip."""
        client, built = _reserved()
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)

        data = _intent(client, {"trip_id": str(built.trip_public_id)}).data["data"]

        assert Decimal(data["amount"]) == trip.total_amount
        assert data["currency"] == trip.currency

    def test_the_payment_row_names_the_trip_and_the_gateway(self) -> None:
        client, built = _reserved()

        _intent(client, {"trip_id": str(built.trip_public_id)})

        payment = Payment.objects.get()
        assert payment.trip_id == built.trip_id
        assert payment.status == PaymentStatus.PENDING
        assert payment.psp_reference
        assert payment.expires_at is not None

    def test_the_holds_run_to_the_payment_window(self) -> None:
        """§17.2: extended "from the moment a payment intent is created", so a
        3-D Secure challenge does not cost the tourist their seats."""
        client, built = _reserved()
        holds = django_apps.get_model("inventory", "InventoryHold")
        before = holds.objects.filter(trip_id=built.trip_id).first()
        assert before is not None

        _intent(client, {"trip_id": str(built.trip_public_id)})

        after = holds.objects.get(pk=before.pk)
        assert after.expires_at > before.expires_at


class TestTc073AmountTampering:
    def test_the_submitted_amount_is_ignored_and_the_trip_total_is_charged(self) -> None:
        """TC-073: "Basket total 834.75, client submits 8.34 → charge is 834.75"."""
        client, built = _reserved()
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)

        data = _intent(client, {"trip_id": str(built.trip_public_id), "amount": "8.34"}).data[
            "data"
        ]

        assert Decimal(data["amount"]) == trip.total_amount
        assert Payment.objects.get().presentment_amount == trip.total_amount

    def test_the_attempt_is_audited(self) -> None:
        """§21.8: "audited as a potential tampering attempt". One stale page is
        a shrug; the same tourist doing it nightly is a pattern, and only the
        log can tell the two apart."""
        client, built = _reserved()

        _intent(client, {"trip_id": str(built.trip_public_id), "amount": "8.34"})

        entries = django_apps.get_model("administration", "AuditLog").objects.filter(
            action="payment.amount_mismatch"
        )
        assert entries.count() == 1
        entry = entries.get()
        assert entry.before["submitted"] == "8.34"
        assert entry.after["charged"] != "8.34"

    def test_an_amount_that_matches_is_not_audited(self) -> None:
        client, built = _reserved()
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)

        _intent(client, {"trip_id": str(built.trip_public_id), "amount": str(trip.total_amount)})

        assert (
            not django_apps.get_model("administration", "AuditLog")
            .objects.filter(action="payment.amount_mismatch")
            .exists()
        )


class TestTc074Idempotency:
    def test_the_same_key_twice_makes_one_payment(self) -> None:
        """TC-074: "one payment row; identical response replayed"."""
        client, built = _reserved()
        key = uuid.uuid4().hex
        body = {"trip_id": str(built.trip_public_id)}

        first = _intent(client, body, key=key)
        second = _intent(client, body, key=key)

        assert first.status_code == second.status_code == 201
        assert first.data["data"]["id"] == second.data["data"]["id"]
        assert Payment.objects.count() == 1

    def test_the_key_is_required(self) -> None:
        """§9.1, A6: required on every POST that creates a payment."""
        client, built = _reserved()

        response = _intent(client, {"trip_id": str(built.trip_public_id)}, key="")

        assert response.status_code == 422
        assert Payment.objects.count() == 0

    def test_a_second_intent_without_a_key_returns_the_live_payment(self) -> None:
        """BR-062 allows one live payment per trip, so a tourist who reloaded
        the page is given the intent they already have rather than a refusal."""
        client, built = _reserved()
        first = _intent(client, {"trip_id": str(built.trip_public_id)})

        second = _intent(client, {"trip_id": str(built.trip_public_id)})

        assert second.status_code == 201
        assert second.data["data"]["id"] == first.data["data"]["id"]
        assert Payment.objects.count() == 1


class TestWhatCannotBePaidFor:
    def test_a_trip_that_was_never_reserved_is_409(self) -> None:
        client, tourist_id = _signed_in()
        built = scenario.build(tourist_id=tourist_id)

        response = _intent(client, {"trip_id": str(built.trip_public_id)})

        assert response.status_code == 409
        assert response.data["error"]["code"] == "TRIP_NOT_PAYABLE"

    def test_a_lapsed_quote_is_409_quote_expired(self) -> None:
        client, built = _reserved()
        trip_model = django_apps.get_model("trip", "Trip")
        trip = trip_model.objects.get(id=built.trip_id)
        trip.quote_expires_at = trip.quote_expires_at.replace(year=2020)
        trip.save(update_fields=["quote_expires_at"])

        response = _intent(client, {"trip_id": str(built.trip_public_id)})

        assert response.status_code == 409
        assert response.data["error"]["code"] == "QUOTE_EXPIRED"

    def test_a_stranger_cannot_pay_for_somebody_elses_trip(self) -> None:
        """§30.3: 404, and the same 404 a trip that never existed would give."""
        _, built = _reserved()
        stranger = signed_in_as()

        refused = _intent(stranger, {"trip_id": str(built.trip_public_id)})
        absent = _intent(stranger, {"trip_id": str(uuid.uuid4())})

        assert refused.status_code == absent.status_code == 404
        assert refused.data["error"]["code"] == absent.data["error"]["code"]
        assert Payment.objects.count() == 0

    def test_an_anonymous_caller_is_refused(self) -> None:
        response = APIClient().post(
            INTENT, {"trip_id": str(uuid.uuid4())}, format="json", HTTP_IDEMPOTENCY_KEY="k"
        )

        assert response.status_code in (401, 403)


class TestReadingItBack:
    def test_the_tourist_can_read_their_own_payment(self) -> None:
        client, built = _reserved()
        created = _intent(client, {"trip_id": str(built.trip_public_id)}).data["data"]

        response = client.get(
            reverse("v1:payment:payment-detail", kwargs={"public_id": created["id"]})
        )

        assert response.status_code == 200
        assert response.data["data"]["trip_id"] == str(built.trip_public_id)

    def test_a_stranger_reads_a_404(self) -> None:
        client, built = _reserved()
        created = _intent(client, {"trip_id": str(built.trip_public_id)}).data["data"]

        response = signed_in_as().get(
            reverse("v1:payment:payment-detail", kwargs={"public_id": created["id"]})
        )

        assert response.status_code == 404

    def test_the_methods_list_says_what_is_not_available_and_why(self) -> None:
        client, _ = _signed_in()

        response = client.get(reverse("v1:payment:payment-methods"))

        assert response.status_code == 200
        methods = {row["method"]: row for row in response.data["data"]}
        assert methods["CARD"]["available"] is True
        assert methods["CARD"]["display_name"] == "Card"
        assert methods["MOBILE_MONEY"]["available"] is False
        assert methods["MOBILE_MONEY"]["unavailable_reason"]
