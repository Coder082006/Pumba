"""Application layer (SRS §8.2 layer 2). Boundary types.

§7.2: no integer identifier crosses a module boundary or reaches the wire, so
everything here carries `public_id`. §9.1: money crosses as a decimal string
with its currency beside it, which `MoneyDTO` already does everywhere else —
these dataclasses hold `Decimal` and the serializers format it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

__all__ = ["PaymentActionDTO", "PaymentDTO", "RefundDTO", "RefundFactsDTO"]


@dataclass(frozen=True, slots=True)
class PaymentActionDTO:
    """§9.4.7's "method-specific action object" — what the client does next.

    `CLIENT_SECRET` for a card (the browser hands it to Stripe's element),
    `REDIRECT` for a bank page, `USSD_PUSH` for mobile money, `NONE` when
    there is nothing left for the tourist to do and the answer is a webhook
    away.
    """

    type: str
    payload: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PaymentDTO:
    public_id: UUID
    trip_public_id: UUID
    status: str
    method: str
    currency: str
    amount: Decimal
    action: PaymentActionDTO | None = None
    failure_code: str = ""
    expires_at: datetime | None = None
    captured_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RefundDTO:
    public_id: UUID
    payment_public_id: UUID
    booking_public_id: UUID | None
    status: str
    currency: str
    amount: Decimal
    reason_code: str
    requested_at: datetime
    settled_at: datetime | None = None
    failure_code: str = ""


@dataclass(frozen=True, slots=True)
class RefundFactsDTO:
    """What a settled refund did, for `finance` to post against (§22.6).

    `provider_compensation` travels because §20.9 decided it when the tourist
    was shown the preview (BR-043); a ledger that recomputed it would be a
    second reading of the policy, free to disagree with the first.
    """

    booking_id: int
    amount: Decimal
    provider_compensation: Decimal
    currency: str
