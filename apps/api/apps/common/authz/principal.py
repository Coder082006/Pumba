"""The authenticated principal, as the authorisation layer sees it.

A plain frozen dataclass rather than a Django user, deliberately. It means
every permission and ownership decision is a pure function of a value object,
testable without a database, and it means the authorisation logic physically
cannot reach through an ORM relation to widen its own access.

`user_id` is the internal BIGSERIAL and is present because the ownership
filters compare against foreign keys, which are internal ids. It is never
serialised — SRS §7.2: "Sequential integers are never returned to clients."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from apps.common.authz.roles import Permission, Role, mfa_mandatory, permissions_for

__all__ = ["Principal"]


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: int
    user_public_id: UUID
    roles: frozenset[Role] = field(default_factory=frozenset)
    tourist_id: int | None = None
    driver_id: int | None = None
    provider_id: int | None = None
    is_email_verified: bool = False
    mfa_satisfied: bool = False

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.roles)

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions

    def has_role(self, role: Role) -> bool:
        return role in self.roles

    @property
    def mfa_required(self) -> bool:
        """§30.2 — whether this principal's roles oblige TOTP enrolment."""
        return mfa_mandatory(self.roles)

    @property
    def mfa_ok(self) -> bool:
        """Whether the MFA obligation, if any, is met for this session."""
        return self.mfa_satisfied or not self.mfa_required

    def attr(self, name: str) -> int | None:
        """Read the attribute an `OwnershipRule` names.

        Restricted to the three linking ids on purpose: an ownership rule must
        not be able to reach `is_email_verified` or `roles` and turn an
        identity flag into a row filter.
        """
        if name not in _LINK_ATTRS:
            raise ValueError(f"{name!r} is not a linkable principal attribute")
        return getattr(self, name)  # type: ignore[no-any-return]


_LINK_ATTRS = frozenset({"tourist_id", "driver_id", "provider_id", "user_id"})
