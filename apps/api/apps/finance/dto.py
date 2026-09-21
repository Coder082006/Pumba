"""Application layer (SRS §8.2 layer 2). Boundary types.

§6.5 rule 5: "every module's `services.py` exposes only DTOs and primitives —
never ORM instances — across module boundaries", and `test_architecture.py`
re-derives it on every run.

It is not a formality here. A console holding a live `Payout` could save it,
and a `LedgerEntry` handed out is a row somebody could try to edit — which the
database would refuse, loudly, at the worst possible moment. Handing back a
frozen copy makes the ledger read-only by construction rather than by trigger
alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

__all__ = [
    "CommissionRuleDTO",
    "LedgerEntryDTO",
    "ProviderBalanceDTO",
    "PayoutItemDTO",
    "PayoutDTO",
    "ProviderStatementDTO",
    "LedgerExceptionDTO",
]


@dataclass(frozen=True, slots=True)
class CommissionRuleDTO:
    """§22.2's rule, as a console reads it."""

    public_id: UUID
    scope: str
    method: str
    priority: int
    booking_type: str
    percent: Decimal | None
    flat_amount: Decimal | None
    tiers: list[dict[str, str]]
    min_fee: Decimal | None
    max_fee: Decimal | None
    valid_from: date | None
    valid_to: date | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class LedgerEntryDTO:
    """One posted entry. Frozen, because the row behind it is immutable."""

    id: int
    account: str
    journal_id: UUID
    entry_type: str
    direction: str
    amount: Decimal
    currency: str
    booking_id: int | None
    provider_id: int | None
    payout_id: int | None
    memo: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class ProviderBalanceDTO:
    currency: str
    pending: Decimal
    available: Decimal


@dataclass(frozen=True, slots=True)
class PayoutItemDTO:
    """§22.5's line item: which service this money is for."""

    booking_id: int
    amount: Decimal
    memo: str


@dataclass(frozen=True, slots=True)
class PayoutDTO:
    public_id: UUID
    provider_id: int
    currency: str
    amount: Decimal
    status: str
    period_start: date
    period_end: date
    approved_at: datetime | None = None
    rail_reference: str = ""
    paid_at: datetime | None = None
    items: tuple[PayoutItemDTO, ...] = ()


@dataclass(frozen=True, slots=True)
class ProviderStatementDTO:
    """§26.7's figures: what an operator earned, and what they were paid."""

    balances: tuple[ProviderBalanceDTO, ...] = ()
    accrued: Decimal = Decimal("0")
    reversed_amount: Decimal = Decimal("0")
    compensation: Decimal = Decimal("0")
    commission: Decimal = Decimal("0")
    paid_out: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class LedgerExceptionDTO:
    """One thing BR-064's nightly check found wrong.

    `kind` rather than a message, because an operator's worklist groups by what
    went wrong and a sentence cannot be grouped.
    """

    kind: str
    currency: str
    difference: Decimal
    booking_id: int | None = None
    provider_id: int | None = None
    details: dict[str, str] = field(default_factory=dict)
