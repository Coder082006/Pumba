"""Data transfer objects.

Importable across module boundaries alongside services (SRS §6.5
rule 1). Plain frozen dataclasses — no ORM, no Django.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

__all__ = ["BookingDTO", "BasketDTO", "VoucherDTO"]


@dataclass(frozen=True, slots=True, kw_only=True)
class BookingDTO:
    """One component booking, as its owner and the basket response see it."""

    public_id: UUID
    reference: str
    booking_type: str
    status: str
    title: str
    starts_at: datetime
    ends_at: datetime
    pax_count: int
    gross_amount: Decimal
    fee_amount: Decimal
    tax_amount: Decimal
    currency: str
    cancellation_policy_code: str
    confirmed_at: datetime | None = None
    cancelled_at: datetime | None = None
    response_due_at: datetime | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class BasketDTO:
    """§9.4.6's 201: "the basket and the total payable"."""

    trip_public_id: UUID
    trip_status: str
    currency: str
    total_amount: Decimal
    #: When the held capacity is released if payment has not completed.
    payment_expires_at: datetime
    bookings: tuple[BookingDTO, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class VoucherDTO:
    """One issue of a booking's voucher, as a caller outside the module sees it."""

    booking_reference: str
    issue_number: int
    issued_at: datetime
    sha256: str
