"""PaymentGatewayPort — SRS §21.

SRS §21.1: the Platform is never in PCI scope. No card data touches this
interface — the PSP returns a client secret or a redirect and the card is
entered directly with them (SAQ A). Nothing here accepts a PAN, and nothing
here ever should.

Amounts are always `Money`, always server-computed (SRS §30.10: "amounts are
server-computed"). No method takes an amount from client input.

No provider selected (SRS Appendix D-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from apps.common.money import Money

__all__ = [
    "PaymentMethod",
    "PaymentIntentStatus",
    "PaymentIntent",
    "PaymentAction",
    "RefundResult",
    "WebhookEvent",
    "PaymentGatewayPort",
]


class PaymentMethod(StrEnum):
    CARD = "CARD"
    MOBILE_MONEY = "MOBILE_MONEY"


class PaymentIntentStatus(StrEnum):
    """Mirrors the payment machine of SRS Appendix A."""

    INITIATED = "INITIATED"
    PENDING = "PENDING"
    AUTHORISED = "AUTHORISED"
    CAPTURED = "CAPTURED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class PaymentAction:
    """What the client must do next (SRS §9.4.7).

    `type` is one of CLIENT_SECRET, REDIRECT, USSD_PUSH, NONE.
    """

    type: str
    payload: dict[str, str]


@dataclass(frozen=True, slots=True)
class PaymentIntent:
    psp_reference: str
    status: PaymentIntentStatus
    amount: Money
    action: PaymentAction | None = None


@dataclass(frozen=True, slots=True)
class RefundResult:
    psp_refund_reference: str
    amount: Money
    settled: bool


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """A signature-verified inbound event.

    SRS §9.4.8: verification checks the HMAC signature *and* timestamp
    freshness (rejecting more than 5 minutes of skew), and `event_id` is the
    deduplication key — a repeat is acknowledged without reprocessing.
    """

    event_id: str
    event_type: str
    psp_reference: str
    status: PaymentIntentStatus
    raw: dict[str, Any]


@runtime_checkable
class PaymentGatewayPort(Protocol):
    def create_intent(
        self,
        *,
        amount: Money,
        method: PaymentMethod,
        idempotency_key: str,
        return_url: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> PaymentIntent: ...

    def capture(self, psp_reference: str, *, idempotency_key: str) -> PaymentIntent: ...

    def refund(
        self, psp_reference: str, *, amount: Money, idempotency_key: str, reason: str = ""
    ) -> RefundResult: ...

    def fetch_status(self, psp_reference: str) -> PaymentIntent: ...

    def verify_webhook(self, *, payload: bytes, headers: dict[str, str]) -> WebhookEvent:
        """Verify signature and freshness, returning the parsed event.

        Raises `apps.common.errors.ValidationError` if verification fails.
        An unverified payload must never reach the state machine.
        """
        ...
