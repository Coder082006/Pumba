"""The ownership predicate — the second of the two checks in SRS §30.3.

    ROLE CHECK      does this principal's role permit this operation class?
    OWNERSHIP CHECK does this principal own, or have a granted relationship
                    to, this specific row?
                    (queryset filtered by principal at the repository layer —
                     never "fetch then compare", which leaks existence)

This module is the whole of the second check, expressed as data. It declares
*what* a principal may reach; `apps.common.scoping` turns that declaration
into a queryset filter. The split is what keeps this layer pure and what makes
the rule table reviewable as a table rather than as fourteen scattered `if`
statements.

Four semantics, each one a place this class of control usually goes wrong:

1.  **Fail closed.** An unmapped `(role, resource)` pair grants nothing. The
    default is denial, and `test_domain_ownership.py` asserts the map is total
    over `Role x Resource`, so a new resource cannot ship unprotected.

2.  **Multi-role principals union, they do not intersect.** A user who is both
    a tourist and a driver reaches their own trips *and* their own
    assignments. Intersecting is the safe-looking wrong answer that breaks
    real accounts.

3.  **`GLOBAL_READ` degrades to denial on write.** That is the entire
    difference between SUPPORT_AGENT and FINANCE_OFFICER in §5.2. Encoding it
    as a flag on the lookup rather than as two maps keeps it visible.

4.  **Denial becomes `queryset.none()`, which becomes 404.** This is how "a
    foreign principal receives 404, not 403" is achieved structurally. There
    is no code path in which a foreign principal reaches a row and is then
    rejected, because the row is never loaded.

The resource set grows one phase at a time. Only resources whose models exist
are listed — declaring `BOOKING` before `booking` has models would be
future-phase scaffolding, and the totality test is what makes adding one later
safe rather than risky.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Literal

from apps.common.authz.principal import Principal
from apps.common.authz.roles import Role

__all__ = [
    "Resource",
    "Scope",
    "OwnershipRule",
    "OWNERSHIP",
    "Filter",
    "FilterKind",
    "ownership_filter",
]


class Resource(StrEnum):
    """A protected row class. One member per owning table, added per phase."""

    USER = "USER"
    TOURIST_PROFILE = "TOURIST_PROFILE"
    SESSION = "SESSION"
    DEVICE = "DEVICE"


class Scope(StrEnum):
    OWNED = "OWNED"
    """Restricted to rows linked to the principal."""

    GLOBAL_READ = "GLOBAL_READ"
    """Every row, but reads only (§5.2 SUPPORT_AGENT: "Global read, scoped write")."""

    GLOBAL = "GLOBAL"
    """Every row, reads and writes."""

    NONE = "NONE"
    """No access. Stated explicitly so the table is total and readable."""


@dataclass(frozen=True, slots=True)
class OwnershipRule:
    """One cell of the "Ownership predicate" column of SRS §5.2."""

    scope: Scope
    principal_attr: str | None = None
    row_field: str | None = None

    def __post_init__(self) -> None:
        if self.scope is Scope.OWNED:
            if not self.principal_attr or not self.row_field:
                raise ValueError("an OWNED rule must name both a principal attribute and a column")
        elif self.principal_attr or self.row_field:
            raise ValueError(f"a {self.scope} rule must not name a column")


_NONE = OwnershipRule(Scope.NONE)
_GLOBAL = OwnershipRule(Scope.GLOBAL)
_GLOBAL_READ = OwnershipRule(Scope.GLOBAL_READ)


def _own(principal_attr: str, row_field: str) -> OwnershipRule:
    return OwnershipRule(Scope.OWNED, principal_attr=principal_attr, row_field=row_field)


#: Every (role, resource) pair, stated. See the totality test.
OWNERSHIP: Mapping[tuple[Role, Resource], OwnershipRule] = MappingProxyType(
    {
        # --- USER -----------------------------------------------------------
        # A principal reaches their own user row; support and compliance read
        # every user (§5.2 USER_READ_ALL); SUPER_ADMIN manages accounts.
        (Role.TOURIST, Resource.USER): _own("user_id", "id"),
        (Role.DRIVER, Resource.USER): _own("user_id", "id"),
        (Role.PROVIDER_OWNER, Resource.USER): _own("user_id", "id"),
        (Role.PROVIDER_STAFF, Resource.USER): _own("user_id", "id"),
        (Role.SUPPORT_AGENT, Resource.USER): _GLOBAL_READ,
        (Role.FINANCE_OFFICER, Resource.USER): _own("user_id", "id"),
        (Role.CATALOGUE_ADMIN, Resource.USER): _own("user_id", "id"),
        # COMPLIANCE_ADMIN suspends accounts (§5.2), so it needs write, not read.
        (Role.COMPLIANCE_ADMIN, Resource.USER): _GLOBAL,
        (Role.SUPER_ADMIN, Resource.USER): _GLOBAL,
        # --- TOURIST_PROFILE -------------------------------------------------
        (Role.TOURIST, Resource.TOURIST_PROFILE): _own("tourist_id", "id"),
        (Role.DRIVER, Resource.TOURIST_PROFILE): _NONE,
        (Role.PROVIDER_OWNER, Resource.TOURIST_PROFILE): _NONE,
        (Role.PROVIDER_STAFF, Resource.TOURIST_PROFILE): _NONE,
        (Role.SUPPORT_AGENT, Resource.TOURIST_PROFILE): _GLOBAL_READ,
        (Role.FINANCE_OFFICER, Resource.TOURIST_PROFILE): _NONE,
        (Role.CATALOGUE_ADMIN, Resource.TOURIST_PROFILE): _NONE,
        (Role.COMPLIANCE_ADMIN, Resource.TOURIST_PROFILE): _GLOBAL_READ,
        (Role.SUPER_ADMIN, Resource.TOURIST_PROFILE): _GLOBAL,
        # --- SESSION ---------------------------------------------------------
        # Nobody reads another principal's sessions, not even support: a
        # session row is a live credential handle. SUPER_ADMIN may revoke.
        (Role.TOURIST, Resource.SESSION): _own("user_id", "user_id"),
        (Role.DRIVER, Resource.SESSION): _own("user_id", "user_id"),
        (Role.PROVIDER_OWNER, Resource.SESSION): _own("user_id", "user_id"),
        (Role.PROVIDER_STAFF, Resource.SESSION): _own("user_id", "user_id"),
        (Role.SUPPORT_AGENT, Resource.SESSION): _NONE,
        (Role.FINANCE_OFFICER, Resource.SESSION): _own("user_id", "user_id"),
        (Role.CATALOGUE_ADMIN, Resource.SESSION): _own("user_id", "user_id"),
        (Role.COMPLIANCE_ADMIN, Resource.SESSION): _NONE,
        (Role.SUPER_ADMIN, Resource.SESSION): _GLOBAL,
        # --- DEVICE ----------------------------------------------------------
        # A push token is a delivery address for private content; same rule.
        (Role.TOURIST, Resource.DEVICE): _own("user_id", "user_id"),
        (Role.DRIVER, Resource.DEVICE): _own("user_id", "user_id"),
        (Role.PROVIDER_OWNER, Resource.DEVICE): _own("user_id", "user_id"),
        (Role.PROVIDER_STAFF, Resource.DEVICE): _own("user_id", "user_id"),
        (Role.SUPPORT_AGENT, Resource.DEVICE): _NONE,
        (Role.FINANCE_OFFICER, Resource.DEVICE): _own("user_id", "user_id"),
        (Role.CATALOGUE_ADMIN, Resource.DEVICE): _own("user_id", "user_id"),
        (Role.COMPLIANCE_ADMIN, Resource.DEVICE): _NONE,
        (Role.SUPER_ADMIN, Resource.DEVICE): _GLOBAL,
    }
)


FilterKind = Literal["ALLOW_ALL", "DENY_ALL", "EQUALS", "ANY_OF"]


@dataclass(frozen=True, slots=True)
class Filter:
    """A row restriction, as a value.

    The domain must not touch a `QuerySet`, so it produces this instead and
    `apps.common.scoping` translates it. That indirection is also what lets
    the whole authorisation table be tested with no database at all.
    """

    kind: FilterKind
    row_field: str | None = None
    value: object | None = None
    branches: tuple[Filter, ...] = field(default_factory=tuple)

    @classmethod
    def allow_all(cls) -> Filter:
        return cls("ALLOW_ALL")

    @classmethod
    def deny_all(cls) -> Filter:
        return cls("DENY_ALL")

    @classmethod
    def equals(cls, row_field: str, value: object) -> Filter:
        return cls("EQUALS", row_field=row_field, value=value)

    @classmethod
    def any_of(cls, *branches: Filter) -> Filter:
        """Union.

        Absorbs the identities and drops duplicates, so callers need no
        special cases and the generated SQL does not accumulate one redundant
        `OR` per role. Two roles that scope the same resource by the same
        column — a tourist who also drives, reaching their own devices — is
        the common case, not an edge case.
        """
        if any(b.kind == "ALLOW_ALL" for b in branches):
            return cls.allow_all()
        seen: dict[Filter, None] = {}
        for b in branches:
            if b.kind != "DENY_ALL":
                seen.setdefault(b, None)
        real = tuple(seen)
        if not real:
            return cls.deny_all()
        if len(real) == 1:
            return real[0]
        return cls("ANY_OF", branches=real)

    @property
    def is_deny_all(self) -> bool:
        return self.kind == "DENY_ALL"


def ownership_filter(principal: Principal, resource: Resource, *, write: bool = False) -> Filter:
    """The rows this principal may reach on this resource.

    Returns `DENY_ALL` when nothing grants access — including when the
    principal holds a qualifying role but has no linked row for it, which is
    the case for a user granted DRIVER before their driver record exists.
    """
    branches: list[Filter] = []

    for role in sorted(principal.roles):
        rule = OWNERSHIP.get((role, resource))
        if rule is None or rule.scope is Scope.NONE:
            # Unmapped is denial, never permission. See semantic 1.
            continue

        if rule.scope is Scope.GLOBAL:
            return Filter.allow_all()

        if rule.scope is Scope.GLOBAL_READ:
            if write:
                # Degrades to denial rather than to OWNED: a global reader has
                # no linking id to fall back on. See semantic 3.
                continue
            return Filter.allow_all()

        assert rule.principal_attr is not None and rule.row_field is not None
        value = principal.attr(rule.principal_attr)
        if value is None:
            continue
        branches.append(Filter.equals(rule.row_field, value))

    return Filter.any_of(*branches)
