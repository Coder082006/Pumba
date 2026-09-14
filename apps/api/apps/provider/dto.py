"""Data transfer objects.

    Importable across module boundaries alongside services (SRS §6.5
    rule 1). Plain frozen dataclasses — no ORM, no Django.

`ProviderDTO.id` crosses the boundary on purpose. Other modules store a
provider as a plain id (ADR 0012) — `activity.provider_id`, and in Phase 7
`booking.provider_id` — so the caller that writes one needs it. It is never
serialised: the wire carries `public_id` (hard rule 8).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

__all__ = ["ProviderDTO", "StatusChangeDTO"]


@dataclass(frozen=True, slots=True, kw_only=True)
class ProviderDTO:
    id: int
    public_id: UUID
    legal_name: str
    trading_name: str
    provider_type: str
    contact_email: str
    contact_phone: str
    region_id: int
    verify_status: str
    verified_at: datetime | None
    payout_account_ref: str | None
    payout_currency: str
    rating_avg: Decimal
    rating_count: int
    is_sellable: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class StatusChangeDTO:
    """What `change_status` did, for the caller to audit.

    `steps` is every state passed through, in order, ending at the target. An
    administrator's single "verify" can be three declared edges, and the audit
    trail records all three rather than one jump §26.2 does not draw.
    """

    before: str
    steps: tuple[str, ...]
    provider: ProviderDTO
