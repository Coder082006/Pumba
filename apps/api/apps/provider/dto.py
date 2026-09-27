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

__all__ = ["ProviderDTO", "StatusChangeDTO", "DriverCandidateDTO"]


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


@dataclass(frozen=True, slots=True, kw_only=True)
class DriverCandidateDTO:
    """One driver-and-vehicle pair that could serve a transfer — §11.6.

    A pair and not a driver, because §11.6's rules 3 and 4 are about the
    vehicle: a driver with a saloon and a minibus is two candidates for a
    party of two and one candidate for a party of nine.

    Everything the score needs travels here except the driver's position.
    `driver_location` is `location`'s table and `provider` may not see it
    (§6.4), so the dispatcher joins the anchor on afterwards. What this DTO
    carries instead is `home_destination_id`, which is the fallback §11.6
    names when the position is missing or stale.
    """

    driver_id: int
    driver_public_id: UUID
    user_id: int
    provider_id: int
    home_destination_id: int
    languages: tuple[str, ...]
    is_online: bool

    rating_avg: Decimal
    acceptance_rate: Decimal
    completed_trips: int

    vehicle_id: int
    vehicle_public_id: UUID
    vehicle_class: str
    seat_capacity: int
    luggage_capacity: int
