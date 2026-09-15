"""The real gateway — Stripe behind `PaymentGatewayPort`. ADR 0027, §21.

Hard rule 13 and the `vendor-sdks-only-in-adapters` contract: the Stripe SDK is
imported here and nowhere else. Swapping acquirer touches this file and nothing
that calls the port — which is the property Appendix D-1 is relying on, since
the provider decision is commercial and still open.

**No card data passes through this process** (PM1, SAQ A). `create_intent`
returns a client secret; the card is entered in Stripe's own field in the
browser and never reaches us. Nothing here accepts a PAN, and the only
instrument details ever stored are the token, brand and last four that Stripe
hands back after the fact.

**Amounts cross in minor units.** Stripe counts in the currency's smallest
unit, `Money` counts in the major unit with an explicit exponent, and the
conversion is one place (`_minor`, `_major`) so a factor of a hundred cannot
appear in a business rule.

**Failures are normalised before they leave.** §21.8 defines the taxonomy the
rest of the system reasons about — `CARD_DECLINED`, `INSUFFICIENT_FUNDS`,
`AUTHENTICATION_FAILED`, `EXPIRED_INSTRUMENT`, `GATEWAY_UNAVAILABLE` — so a
Stripe decline code never reaches a screen, a ledger or a test.

Mobile money is not this adapter's rail. §21.2 asks for a second adapter for
Tanzanian mobile money and Phase 8a does not build it; asking for it here is
refused by name rather than silently charged to a card.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal, cast

import stripe
from django.conf import settings

from apps.common.errors import ExternalServiceError, ValidationError
from apps.common.money import Money
from ports.payment import (
    PaymentAction,
    PaymentIntent,
    PaymentIntentStatus,
    PaymentMethod,
    RefundResult,
    WebhookEvent,
)

__all__ = ["StripeGateway"]


#: Stripe's PaymentIntent statuses, mapped onto §21.4's machine.
#:
#: Four Stripe states mean "the customer has not finished yet" and collapse to
#: PENDING: the rest of the system has no use for the difference between
#: waiting for a card and waiting for a 3-D Secure challenge, and §21.4 does
#: not give it a state. `requires_capture` is AUTHORISED — unreachable while
#: capture is automatic (§21.4's note), and mapped because the on-request flow
#: will want it.
_STATUS = {
    "requires_payment_method": PaymentIntentStatus.PENDING,
    "requires_confirmation": PaymentIntentStatus.PENDING,
    "requires_action": PaymentIntentStatus.PENDING,
    "processing": PaymentIntentStatus.PENDING,
    "requires_capture": PaymentIntentStatus.AUTHORISED,
    "succeeded": PaymentIntentStatus.CAPTURED,
    "canceled": PaymentIntentStatus.EXPIRED,
}

#: §21.8's normalised failure taxonomy, from Stripe's decline codes. Anything
#: unlisted is a card decline: the tourist's next step is the same, and
#: inventing a code per issuer message would be a taxonomy nobody could act on.
_FAILURE = {
    "insufficient_funds": "INSUFFICIENT_FUNDS",
    "expired_card": "EXPIRED_INSTRUMENT",
    "incorrect_cvc": "CARD_DECLINED",
    "card_declined": "CARD_DECLINED",
    "authentication_required": "AUTHENTICATION_FAILED",
    "payment_intent_authentication_failure": "AUTHENTICATION_FAILED",
}

#: Stripe's refund reasons are a closed set of three. Ours are §20.9's
#: cancellation vocabulary, which does not map onto them, so every refund is
#: `requested_by_customer` and the real reason travels in metadata where it can
#: be read back during a dispute.
_REFUND_REASON: Literal["requested_by_customer"] = "requested_by_customer"


def _minor(amount: Money) -> int:
    """Major units to Stripe's minor units, exactly.

    Quantized first: §18.5 rounds once per aggregate, and an amount carrying
    sub-cent precision into a multiplication would truncate rather than round.
    """
    quantized = amount.quantize()
    return int(quantized.amount.scaleb(quantized.exponent))


def _major(value: int, currency: str) -> Money:
    return Money(Decimal(value).scaleb(-Money.zero(currency).exponent), currency)


class StripeGateway:
    """`PaymentGatewayPort` over Stripe PaymentIntents.

    The client is built per instance from `STRIPE_SECRET_KEY`. An absent key is
    refused at construction rather than at the first charge: the registry
    builds the adapter on first use, so this surfaces as "no key" on the first
    payment rather than as a 401 from Stripe in a webhook an hour later.
    """

    def __init__(self, api_key: str | None = None, webhook_secret: str | None = None) -> None:
        key = api_key or settings.STRIPE_SECRET_KEY
        if not key:
            raise ValidationError(
                "STRIPE_SECRET_KEY is not set. See .env.example; test keys come "
                "from https://dashboard.stripe.com/test/apikeys."
            )
        self._client = stripe.StripeClient(key)
        self._webhook_secret = (
            webhook_secret if webhook_secret is not None else settings.STRIPE_WEBHOOK_SECRET
        )

    # -- outbound ---------------------------------------------------------

    def create_intent(
        self,
        *,
        amount: Money,
        method: PaymentMethod,
        idempotency_key: str,
        return_url: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> PaymentIntent:
        if method is not PaymentMethod.CARD:
            raise ValidationError(
                f"{method} is not available through this gateway.",
                code="UNSUPPORTED_METHOD_FOR_CURRENCY",
            )
        params: dict[str, Any] = {
            "amount": _minor(amount),
            "currency": amount.currency.lower(),
            # Stripe decides which methods to offer for the currency and the
            # tourist's location; we do not enumerate them here, because a
            # hard-coded list is a list that goes stale the week a country is
            # added.
            "automatic_payment_methods": {"enabled": True},
            "metadata": metadata or {},
        }
        if return_url:
            params["metadata"] = {**params["metadata"], "return_url": return_url}
        # `cast` rather than building the SDK's TypedDict: the shape above is
        # the one Stripe documents, and importing its parameter types here
        # would put a vendor type in a signature this file does not own.
        intent = self._call(
            lambda: self._client.payment_intents.create(
                params=cast(Any, params), options={"idempotency_key": idempotency_key}
            )
        )
        return self._intent(intent)

    def capture(self, psp_reference: str, *, idempotency_key: str) -> PaymentIntent:
        intent = self._call(
            lambda: self._client.payment_intents.capture(
                psp_reference, options={"idempotency_key": idempotency_key}
            )
        )
        return self._intent(intent)

    def refund(
        self, psp_reference: str, *, amount: Money, idempotency_key: str, reason: str = ""
    ) -> RefundResult:
        refund = self._call(
            lambda: self._client.refunds.create(
                params=cast(
                    Any,
                    {
                        "payment_intent": psp_reference,
                        "amount": _minor(amount),
                        "reason": _REFUND_REASON,
                        "metadata": {"pumba_reason": reason[:500]} if reason else {},
                    },
                ),
                options={"idempotency_key": idempotency_key},
            )
        )
        return RefundResult(
            psp_refund_reference=str(refund.id),
            amount=_major(int(refund.amount), str(refund.currency).upper()),
            # "succeeded" is settled at Stripe; "pending" is a refund that will
            # settle and is reported as unsettled so the row stays REQUESTED
            # and the task looks again.
            settled=str(refund.status) == "succeeded",
        )

    def fetch_status(self, psp_reference: str) -> PaymentIntent:
        intent = self._call(lambda: self._client.payment_intents.retrieve(psp_reference))
        return self._intent(intent)

    # -- inbound ----------------------------------------------------------

    def verify_webhook(self, *, payload: bytes, headers: dict[str, str]) -> WebhookEvent:
        """§9.4.8: HMAC signature and a five-minute freshness window.

        `construct_event` does both — `tolerance` is the skew check — and
        raises on either failure. The distinction between a forged signature
        and a stale one is deliberately not surfaced: both mean the payload is
        not something the state machine may read.
        """
        if not self._webhook_secret:
            raise ValidationError("STRIPE_WEBHOOK_SECRET is not set; webhooks cannot be verified.")
        signature = next(
            (value for key, value in headers.items() if key.lower() == "stripe-signature"), ""
        )
        try:
            event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
                payload,
                signature,
                self._webhook_secret,
                tolerance=settings.PSP_WEBHOOK_MAX_SKEW_SECONDS,
            )
        except (stripe.SignatureVerificationError, ValueError) as exc:
            raise ValidationError(f"Webhook signature verification failed: {exc}") from exc

        obj = dict(event["data"]["object"])
        status = _STATUS.get(str(obj.get("status", "")), PaymentIntentStatus.PENDING)
        if str(event["type"]).endswith(".payment_failed"):
            status = PaymentIntentStatus.FAILED
        return WebhookEvent(
            event_id=str(event["id"]),
            event_type=str(event["type"]),
            psp_reference=str(obj.get("payment_intent") or obj.get("id") or ""),
            status=status,
            raw=dict(event),
        )

    # -- internals --------------------------------------------------------

    def _intent(self, intent: Any) -> PaymentIntent:
        status = _STATUS.get(str(intent.status), PaymentIntentStatus.PENDING)
        error = getattr(intent, "last_payment_error", None)
        if error is not None and status is PaymentIntentStatus.PENDING:
            status = PaymentIntentStatus.FAILED
        return PaymentIntent(
            psp_reference=str(intent.id),
            status=status,
            amount=_major(int(intent.amount), str(intent.currency).upper()),
            action=self._action(intent),
        )

    def _action(self, intent: Any) -> PaymentAction:
        """What the browser does next.

        A client secret for everything, because Stripe's own element drives the
        redirect and the 3-D Secure challenge from it. `NONE` once there is
        nothing left to do, so a screen can tell "waiting for the tourist" from
        "waiting for Stripe".
        """
        if str(intent.status) in {"succeeded", "processing", "canceled"}:
            return PaymentAction(type="NONE", payload={})
        return PaymentAction(
            type="CLIENT_SECRET", payload={"client_secret": str(intent.client_secret or "")}
        )

    def _call(self, operation: Any) -> Any:
        """Translate Stripe's exceptions into this system's.

        A declined card is not an outage and an outage is not a declined card:
        the first is an answer about this payment and carries a §21.8 code, the
        second is 502 `PSP_UNAVAILABLE` and is retryable (§32.3).
        """
        try:
            return operation()
        except stripe.CardError as exc:
            code = _FAILURE.get(str(exc.code or ""), "CARD_DECLINED")
            raise ValidationError(str(exc.user_message or exc), code=code) from exc
        except stripe.InvalidRequestError as exc:
            raise ValidationError(str(exc)) from exc
        except stripe.StripeError as exc:
            raise ExternalServiceError(
                "The payment provider could not be reached.", code="PSP_UNAVAILABLE"
            ) from exc
