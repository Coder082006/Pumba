"""identity module — SRS §6.4.

Data transfer objects.

Importable across module boundaries alongside services (SRS §6.5
rule 1). Plain frozen dataclasses — no ORM, no Django.

SRS §6.5 rule 5: "every module's services.py exposes only DTOs and
primitives — never ORM instances — across module boundaries." These are that
boundary, and `tests/test_architecture.py` asserts it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

__all__ = ["UserDTO", "TouristProfileDTO", "TokenPair", "LoginResult", "DeviceDTO"]


@dataclass(frozen=True, slots=True)
class TouristProfileDTO:
    public_id: UUID
    first_name: str
    last_name: str
    nationality: str | None
    locale: str
    preferred_currency: str
    marketing_opt_in: bool


@dataclass(frozen=True, slots=True)
class UserDTO:
    """What leaves the module for a user.

    No `id`, ever — §7.2: "Sequential integers are never returned to
    clients." No `password_hash`, no `mfa_secret`: a DTO that carries a
    credential is one careless serializer away from publishing it.
    """

    public_id: UUID
    email: str
    status: str
    email_verified: bool
    mfa_enrolled: bool
    roles: frozenset[str]
    created_at: datetime
    profile: TouristProfileDTO | None = None


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int = 0
    """Seconds until the *access* token expires — §9.4.2."""


@dataclass(frozen=True, slots=True)
class LoginResult:
    tokens: TokenPair
    user: UserDTO
    #: §9.4.2 returns "principal (roles, provider id where applicable)".
    roles: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class DeviceDTO:
    public_id: UUID
    platform: str
    device_name: str
    last_seen_at: datetime
