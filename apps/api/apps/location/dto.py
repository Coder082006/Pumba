"""Data transfer objects.

Importable across module boundaries alongside services (SRS §6.5
rule 1). Plain frozen dataclasses — no ORM, no Django.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["DriverPositionDTO"]


@dataclass(frozen=True, slots=True, kw_only=True)
class DriverPositionDTO:
    """Where a driver was, for a caller that may not read the table.

    `recorded_at` travels because §11.6's freshness rule is the caller's to
    apply twice: `last_known` filters on it, and the dispatcher records on the
    offer whether the anchor was live or guessed. A DTO that dropped the time
    would make the second impossible.
    """

    driver_id: int
    lat: float
    lng: float
    accuracy_m: int | None
    recorded_at: datetime
