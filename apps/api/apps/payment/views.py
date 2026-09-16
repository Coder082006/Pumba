"""payment module — SRS §6.4.

Interface layer (SRS §8.2 layer 1). §9.4.7's endpoints.

Thin, like every other view here: parse, call one service function, serialise.
`TripNotPayableError`, `QuoteExpiredError` and the port's own `ValidationError`
are deliberately not caught — §8.7's hierarchy carries the status code and
§9.2's handler builds the envelope, so a `try/except` here would be a second
place deciding what a lapsed quote's HTTP status is.

**`Idempotency-Key` is required on the intent** (§9.1, A6: "required on all
POST that create bookings, payments or assignments"). A retry after a timeout
must return the payment it already created rather than taking a second one,
which is TC-074 — and the decorator is what makes that true even when the first
response never reached the browser.
"""

from __future__ import annotations

from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.config import get_setting
from apps.common.envelope import success_envelope
from apps.common.errors import ValidationError
from apps.common.idempotency import idempotent
from apps.common.permissions import IsTourist, tourist_id_of
from apps.common.throttling import PaymentIntentThrottle
from apps.payment import serializers as ser
from apps.payment import services
from apps.payment.models import PaymentMethod

__all__ = [
    "PaymentIntentView",
    "PaymentDetailView",
    "PaymentMethodsView",
    "PaymentVerifyView",
    "PspWebhookView",
]

_TAGS = ["payment"]


class PaymentIntentView(APIView):
    """`POST /payments/intents` — §9.4.7."""

    permission_classes = [IsTourist]
    throttle_classes = [PaymentIntentThrottle]

    @extend_schema(
        request=ser.PaymentIntentSerializer,
        responses={201: ser.PaymentSerializer},
        summary="Start paying for a reserved trip",
        description=(
            "The amount is computed from the trip and is never taken from the "
            "request (BR-060). Returns a method-specific action: a client "
            "secret for a card, a redirect, or a mobile-money prompt. "
            "`Idempotency-Key` is required."
        ),
        tags=_TAGS,
    )
    @idempotent
    def post(self, request: Request) -> Response:
        payload = ser.PaymentIntentSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        body = payload.validated_data
        payment = services.initiate(
            body["trip_id"],
            tourist_id=tourist_id_of(request),
            method=PaymentMethod(body.get("method", PaymentMethod.CARD)),
            return_url=body.get("return_url") or None,
            client_amount=body.get("amount"),
        )
        return Response(
            success_envelope(ser.PaymentSerializer(payment).data),
            status=status.HTTP_201_CREATED,
        )


class PaymentDetailView(APIView):
    """`GET /payments/{id}` — §9.3.7.

    A payment that is not this tourist's is absent rather than forbidden
    (§30.3), which the service enforces by filtering rather than comparing.
    """

    permission_classes = [IsTourist]

    @extend_schema(
        responses={200: ser.PaymentSerializer},
        summary="Payment status",
        tags=_TAGS,
    )
    def get(self, request: Request, public_id: UUID) -> Response:
        payment = services.payment_detail(public_id, tourist_id=tourist_id_of(request))
        return Response(success_envelope(ser.PaymentSerializer(payment).data))


class PaymentVerifyView(APIView):
    """`POST /payments/{id}/verify` — §9.3.7.

    What the waiting screen calls. A tourist completing a 3-D Secure challenge
    watches a page that has no way of knowing the webhook arrived, and polling
    the gateway on their behalf is better than either a spinner that never
    stops or a page that claims success it has not been told about.

    No `Idempotency-Key`: this creates nothing. Asking twice asks the gateway
    twice and writes whatever it says, which is the same answer.
    """

    permission_classes = [IsTourist]

    @extend_schema(
        request=None,
        responses={200: ser.PaymentSerializer},
        summary="Refresh a payment's status from the provider",
        tags=_TAGS,
    )
    def post(self, request: Request, public_id: UUID) -> Response:
        payment = services.verify(public_id, tourist_id=tourist_id_of(request))
        return Response(success_envelope(ser.PaymentSerializer(payment).data))


class PaymentMethodsView(APIView):
    """`GET /payments/methods` — §9.3.7.

    §9.3.7 describes this as the methods available "for the tourist's country
    and currency". In 8a there is one rail and the honest answer is short: cards
    in the currencies §18.3 supports, and mobile money named as not yet
    available rather than omitted — a tourist in Zanzibar who is offered no
    mobile money should be told why, not left to wonder.
    """

    permission_classes = [IsTourist]

    @extend_schema(
        responses={200: ser.PaymentMethodSerializer(many=True)},
        summary="Payment methods available to this tourist",
        tags=_TAGS,
    )
    def get(self, request: Request) -> Response:
        currencies = list(get_setting("currency.enabled"))
        methods = [
            {
                "method": "CARD",
                "display_name": "Card",
                "currencies": currencies,
                "available": True,
                "unavailable_reason": "",
            },
            {
                "method": "MOBILE_MONEY",
                "display_name": "Mobile money",
                "currencies": ["TZS"],
                "available": False,
                "unavailable_reason": "Mobile money is not available yet.",
            },
        ]
        return Response(success_envelope(ser.PaymentMethodSerializer(methods, many=True).data))


class PspWebhookView(APIView):
    """`POST /webhooks/psp/{provider}` — §9.4.8.

    **Unauthenticated by session, authenticated by signature.** There is no
    principal here and no token: the PSP proves who it is with an HMAC over the
    body, which the adapter verifies along with §21.5's five-minute freshness
    window. `AllowAny` is therefore not a gap — the authentication is the
    signature, and a request that fails it never reaches the state machine.

    **Always 200 once the event is durably stored**, whatever happened next.
    §9.4.8 is explicit about this, and the reason is operational: a PSP that
    receives anything else retries, and a retry storm against a handler that is
    already failing turns one broken transition into thousands. A verification
    failure is the exception — 400, because that request was not from the PSP
    at all and there is nothing to retry.

    The raw body is read rather than the parsed payload, because the signature
    is over the bytes: DRF's JSON round-trip would re-order keys and change
    whitespace, and the HMAC would stop matching a body that was never altered.
    """

    permission_classes = [AllowAny]
    authentication_classes: list[type] = []

    @extend_schema(
        request=None,
        responses={200: None},
        summary="Payment provider callback",
        description=(
            "Signature-verified and idempotent. A duplicate event id is "
            "acknowledged without reprocessing (TC-071); an event that would "
            "not advance the payment is recorded and ignored (§21.5)."
        ),
        tags=_TAGS,
    )
    def post(self, request: Request, provider: str) -> Response:
        try:
            outcome = services.ingest_webhook(
                provider=provider,
                payload=request.body,
                headers={key: str(value) for key, value in request.headers.items()},
            )
        except ValidationError:
            # Never the envelope's 422: an unverified payload is not a
            # malformed request from a client we know, it is a request from
            # somebody we cannot identify.
            return Response({"received": False}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"received": True, "outcome": outcome.value})
