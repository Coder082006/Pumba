"""inventory module — SRS §6.4.

Data transfer objects.

    Importable across module boundaries alongside services (SRS §6.5 rule 1).
    Plain frozen dataclasses — no ORM, no Django.

**Every departure says how it was measured.** `basis` is the field
`docs/PHASE-3-PLAN.md` promised would arrive additively:

    `basis` is an enum with exactly one legal value in Phase 3. Phase 5 adds
    `AUTHORITATIVE`, and no client has to change shape to receive it.

§17.1 I3 is why it exists at all: *"Search may read cached or stale capacity;
committing a booking may not."* A number with no provenance invites a client to
treat the two the same, and the whole oversell guarantee rests on nobody doing
that. A search result carrying `INDICATIVE` is making a weaker claim than the
same integer read under a row lock, and it says so.

**`remaining` rather than the three counters.** §16.3's arithmetic is
`capacity_total - capacity_held - capacity_sold`, and publishing the parts would
tell a tourist how many seats somebody else is midway through paying for — a
figure that is nobody's business, changes without anything happening, and reads
as availability disappearing for no reason.

**A hold is named by its token.** §7.3's `hold_token`; §7.2 keeps the BIGSERIAL
inside the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from apps.inventory.domain.capacity import Unbookable

__all__ = [
    "AvailabilityBasis",
    "DepartureDTO",
    "HoldRequest",
    "HoldDTO",
    "DriftDTO",
]


class AvailabilityBasis(StrEnum):
    """How much weight a capacity figure will bear."""

    #: Read without a lock, possibly from cache. §17.1 I3, §8.10: *"a cached
    #: availability figure may never confirm a booking."*
    INDICATIVE = "INDICATIVE"
    #: Read under `FOR UPDATE` inside the transaction that acted on it. Only
    #: `hold()` produces this, and only for the instant it was true.
    AUTHORITATIVE = "AUTHORITATIVE"


@dataclass(frozen=True, slots=True, kw_only=True)
class DepartureDTO:
    """One sellable instant, as §24.10 and SD-06 need it."""

    public_id: UUID
    departs_at: datetime
    status: str
    remaining: int
    basis: AvailabilityBasis

    #: §7.5.9. Overrides the activity's `price_per_person` for this departure
    #: alone; None means the activity's own price applies. Carried rather than
    #: resolved here, because resolving it is `costing`'s job and doing it in
    #: two places is how two prices for one seat happen.
    price_override: Decimal | None = None

    #: Why this party cannot take this departure, or None. Populated only when
    #: the caller supplied a party size — §24.10 renders "sold out" and
    #: "too late" differently, and both differently from a bookable row.
    unbookable: Unbookable | None = None

    @property
    def is_bookable(self) -> bool:
        return self.unbookable is None


@dataclass(frozen=True, slots=True, kw_only=True)
class HoldRequest:
    """What a caller wants held, on the way in.

    Carries `departure_id` as the integer primary key, which is the one place
    in this module's surface that happens. §7.2 forbids sequential integers
    reaching a *client*; `booking` is not a client, and both modules are
    addressing the same row. Resolving a UUID here and back again would add a
    query to the inside of the critical section (§17.3) for no gain.
    """

    departure_id: int
    pax: int


@dataclass(frozen=True, slots=True, kw_only=True)
class HoldDTO:
    """A hold, as its owner sees it."""

    hold_token: UUID
    quantity: int
    expires_at: datetime
    status: str


@dataclass(frozen=True, slots=True, kw_only=True)
class DriftDTO:
    """One departure whose counter disagrees with the holds behind it.

    §17.4's reconciliation output. Both numbers are carried rather than their
    difference: which way a counter drifted says which half of the system to
    look at, and a signed delta is one sign error away from saying the
    opposite.
    """

    departure_public_id: UUID
    capacity_held: int
    held_by_live_holds: int
