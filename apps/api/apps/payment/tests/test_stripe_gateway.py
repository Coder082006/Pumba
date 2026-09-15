"""The Stripe adapter, without Stripe — ADR 0027, §21.

**Nothing here reaches the network.** The SDK is exercised against recorded
response shapes and a signature computed the way Stripe computes it, because a
test that needed an API key would be a test nobody runs, and the sandbox matrix
(§33.9) is a walkthrough rather than a unit test.

What is worth asserting is the translation, which is where an adapter goes
wrong: minor units, §21.4's statuses, §21.8's failure taxonomy, and the
refusal of anything whose signature or freshness does not check out.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any

import pytest
import stripe

from apps.common.errors import ExternalServiceError, ValidationError
from apps.common.money import Money
from apps.payment.adapters.stripe_gateway import StripeGateway, _minor
from ports.payment import PaymentIntentStatus, PaymentMethod

SECRET = "whsec_test_secret"


def gateway() -> StripeGateway:
    return StripeGateway(api_key="sk_test_x", webhook_secret=SECRET)


class _Intent:
    """The shape the SDK returns, narrowed to what the adapter reads."""

    def __init__(self, **fields: Any) -> None:
        self.id = fields.get("id", "pi_1")
        self.status = fields.get("status", "requires_payment_method")
        self.amount = fields.get("amount", 83475)
        self.currency = fields.get("currency", "usd")
        self.client_secret = fields.get("client_secret", "pi_1_secret_x")
        self.last_payment_error = fields.get("last_payment_error")


def signed(
    body: dict[str, Any], *, at: int | None = None, secret: str = SECRET
) -> tuple[bytes, dict[str, str]]:
    """A payload and the header Stripe would have sent with it."""
    payload = json.dumps(body).encode()
    timestamp = at if at is not None else int(time.time())
    signature = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return payload, {"Stripe-Signature": f"t={timestamp},v1={signature}"}


class TestAmountsCrossInMinorUnits:
    def test_a_two_place_currency_multiplies_by_a_hundred(self) -> None:
        assert _minor(Money(Decimal("834.75"), "USD")) == 83475

    def test_a_shilling_figure_survives_the_trip(self) -> None:
        assert _minor(Money(Decimal("580000.00"), "TZS")) == 58000000

    def test_sub_cent_precision_is_rounded_rather_than_truncated(self) -> None:
        """§18.5 rounds half up, once. Truncating here would undercharge."""
        assert _minor(Money(Decimal("10.005"), "USD")) == 1001


class TestCreatingAnIntent:
    def test_it_asks_for_the_amount_in_minor_units_and_returns_a_client_secret(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}
        subject = gateway()

        def create(*, params: dict[str, Any], options: dict[str, Any]) -> _Intent:
            seen.update({"params": params, "options": options})
            return _Intent(status="requires_confirmation")

        monkeypatch.setattr(subject._client.payment_intents, "create", create)

        intent = subject.create_intent(
            amount=Money(Decimal("834.75"), "USD"),
            method=PaymentMethod.CARD,
            idempotency_key="key-1",
            metadata={"trip": "TRP-2027-0000001"},
        )

        assert seen["params"]["amount"] == 83475
        assert seen["params"]["currency"] == "usd"
        assert seen["options"]["idempotency_key"] == "key-1"
        assert intent.status is PaymentIntentStatus.PENDING
        assert intent.action is not None
        assert intent.action.type == "CLIENT_SECRET"
        assert intent.action.payload["client_secret"] == "pi_1_secret_x"

    def test_mobile_money_is_refused_by_name_rather_than_charged_to_a_card(self) -> None:
        """§21.2's second rail is a second adapter, and is not built (8a)."""
        with pytest.raises(ValidationError) as exc:
            gateway().create_intent(
                amount=Money(Decimal("10.00"), "USD"),
                method=PaymentMethod.MOBILE_MONEY,
                idempotency_key="key-2",
            )

        assert exc.value.code == "UNSUPPORTED_METHOD_FOR_CURRENCY"

    def test_a_missing_key_is_refused_at_construction(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.STRIPE_SECRET_KEY = ""
        with pytest.raises(ValidationError, match="STRIPE_SECRET_KEY"):
            StripeGateway()


class TestStatusesAreSection214s:
    @pytest.mark.parametrize(
        ("stripe_status", "expected"),
        [
            ("requires_payment_method", PaymentIntentStatus.PENDING),
            ("requires_action", PaymentIntentStatus.PENDING),
            ("processing", PaymentIntentStatus.PENDING),
            ("requires_capture", PaymentIntentStatus.AUTHORISED),
            ("succeeded", PaymentIntentStatus.CAPTURED),
            ("canceled", PaymentIntentStatus.EXPIRED),
        ],
    )
    def test_each_maps(
        self, monkeypatch: pytest.MonkeyPatch, stripe_status: str, expected: PaymentIntentStatus
    ) -> None:
        subject = gateway()
        monkeypatch.setattr(
            subject._client.payment_intents,
            "retrieve",
            lambda reference: _Intent(status=stripe_status),
        )

        assert subject.fetch_status("pi_1").status is expected

    def test_an_intent_carrying_a_payment_error_is_failed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Stripe leaves a declined intent in `requires_payment_method` with the
        error attached. Reported as PENDING it would look like a tourist who
        had not paid yet, and the basket would sit there until the hold died."""
        subject = gateway()
        monkeypatch.setattr(
            subject._client.payment_intents,
            "retrieve",
            lambda reference: _Intent(last_payment_error={"code": "card_declined"}),
        )

        assert subject.fetch_status("pi_1").status is PaymentIntentStatus.FAILED


class TestFailuresAreNormalised:
    @pytest.mark.parametrize(
        ("decline_code", "expected"),
        [
            ("insufficient_funds", "INSUFFICIENT_FUNDS"),
            ("expired_card", "EXPIRED_INSTRUMENT"),
            ("authentication_required", "AUTHENTICATION_FAILED"),
            ("card_declined", "CARD_DECLINED"),
            ("some_code_stripe_added_last_tuesday", "CARD_DECLINED"),
        ],
    )
    def test_a_decline_carries_a_section_218_code(
        self, monkeypatch: pytest.MonkeyPatch, decline_code: str, expected: str
    ) -> None:
        subject = gateway()

        def create(**_: Any) -> None:
            raise stripe.CardError("declined", param=None, code=decline_code)

        monkeypatch.setattr(subject._client.payment_intents, "create", create)

        with pytest.raises(ValidationError) as exc:
            subject.create_intent(
                amount=Money(Decimal("10.00"), "USD"),
                method=PaymentMethod.CARD,
                idempotency_key="key-3",
            )

        assert exc.value.code == expected

    def test_an_outage_is_a_retryable_502_not_a_decline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§32.3: PSP_UNAVAILABLE is retryable. A decline never is — trying the
        same dead card again is not a recovery strategy."""
        subject = gateway()

        def create(**_: Any) -> None:
            raise stripe.APIConnectionError("no route to host")

        monkeypatch.setattr(subject._client.payment_intents, "create", create)

        with pytest.raises(ExternalServiceError) as exc:
            subject.create_intent(
                amount=Money(Decimal("10.00"), "USD"),
                method=PaymentMethod.CARD,
                idempotency_key="key-4",
            )

        assert exc.value.code == "PSP_UNAVAILABLE"
        assert exc.value.retryable is True


class TestWebhookVerification:
    def test_a_signed_event_is_parsed(self) -> None:
        body = {
            "id": "evt_1",
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_1", "status": "succeeded", "amount": 83475}},
        }
        payload, headers = signed(body)

        event = gateway().verify_webhook(payload=payload, headers=headers)

        assert event.event_id == "evt_1"
        assert event.psp_reference == "pi_1"
        assert event.status is PaymentIntentStatus.CAPTURED

    def test_a_failure_event_is_failed_whatever_the_object_says(self) -> None:
        """`payment_intent.payment_failed` leaves the object in
        `requires_payment_method`, which means PENDING everywhere else."""
        body = {
            "id": "evt_2",
            "type": "payment_intent.payment_failed",
            "data": {"object": {"id": "pi_2", "status": "requires_payment_method"}},
        }
        payload, headers = signed(body)

        assert gateway().verify_webhook(payload=payload, headers=headers).status is (
            PaymentIntentStatus.FAILED
        )

    def test_an_unsigned_payload_is_refused(self) -> None:
        """§9.4.8: an unverified payload must never reach the state machine."""
        with pytest.raises(ValidationError, match="signature"):
            gateway().verify_webhook(payload=b"{}", headers={})

    def test_a_payload_signed_with_the_wrong_secret_is_refused(self) -> None:
        payload, headers = signed({"id": "evt_3"}, secret="whsec_someone_elses")

        with pytest.raises(ValidationError, match="signature"):
            gateway().verify_webhook(payload=payload, headers=headers)

    def test_a_stale_event_is_refused(self) -> None:
        """§21.5's five-minute window. The signature is valid; the event is old,
        which is what a replay from a captured request looks like."""
        body = {"id": "evt_4", "type": "payment_intent.succeeded", "data": {"object": {}}}
        payload, headers = signed(body, at=int(time.time()) - 600)

        with pytest.raises(ValidationError, match="signature"):
            gateway().verify_webhook(payload=payload, headers=headers)

    def test_the_header_is_found_whatever_its_case(self) -> None:
        """Django hands headers back title-cased; a proxy may not."""
        body = {"id": "evt_5", "type": "payment_intent.succeeded", "data": {"object": {}}}
        payload, headers = signed(body)
        lowered = {key.lower(): value for key, value in headers.items()}

        assert gateway().verify_webhook(payload=payload, headers=lowered).event_id == "evt_5"


class TestRefunding:
    def test_a_settled_refund_reports_its_reference_and_amount(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        subject = gateway()
        seen: dict[str, Any] = {}

        class _Refund:
            id = "re_1"
            amount = 5500
            currency = "usd"
            status = "succeeded"

        def create(*, params: dict[str, Any], options: dict[str, Any]) -> _Refund:
            seen.update({"params": params, "options": options})
            return _Refund()

        monkeypatch.setattr(subject._client.refunds, "create", create)

        result = subject.refund(
            "pi_1",
            amount=Money(Decimal("55.00"), "USD"),
            idempotency_key="key-5",
            reason="Tourist cancelled, MODERATE_7D tier",
        )

        assert seen["params"]["amount"] == 5500
        assert seen["params"]["payment_intent"] == "pi_1"
        assert seen["params"]["metadata"]["pumba_reason"].startswith("Tourist cancelled")
        assert result.psp_refund_reference == "re_1"
        assert result.amount == Money(Decimal("55.00"), "USD")
        assert result.settled is True

    def test_a_pending_refund_is_not_settled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """So the row stays REQUESTED and the task looks again, rather than
        telling a tourist their money is back before it is."""
        subject = gateway()

        class _Refund:
            id = "re_2"
            amount = 5500
            currency = "usd"
            status = "pending"

        monkeypatch.setattr(subject._client.refunds, "create", lambda **_: _Refund())

        assert (
            subject.refund(
                "pi_1", amount=Money(Decimal("55.00"), "USD"), idempotency_key="key-6"
            ).settled
            is False
        )
