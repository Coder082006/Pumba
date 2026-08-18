"""Authorisation vocabulary and mechanism — SRS §5.2, §30.3.

**Why this is in `common` and not in `identity`.**

SRS §30.3 makes authorisation a control that *every* module applies, in the
same way, on every request. But §6.4 gives `identity` only two dependants —
`provider` depends on it, and nothing else does. `catalogue` depends on
`location` alone, `inventory` on `catalogue` alone. So `apps.catalogue.views`
cannot import `apps.identity` without breaking the module contracts, and
`apps.common` cannot either, because `common` is a leaf.

That leaves exactly one place a control used by all fourteen modules can live:
the shared kernel. This is the same shape as issue S1, where `system_setting`
is owned by `administration` but read through a port in `common`, and it is
resolved the same way — by separating the *mechanism* from the *ownership*:

    common.authz    the vocabulary (Role, Permission, Resource), the rule
                    table, and the pure decision functions. Depends on
                    nothing.

    common.scoping  the one place a `Filter` becomes a Django `Q`.

    identity        the `role` and `user_role` *tables*, principal
                    *construction* from a token (`get_principal()` in the
                    §6.4 interface), and authentication.

`roles`, `principal` and `ownership` are held to the domain-layer purity
contract in `.importlinter` and to the 95% coverage gate, exactly as if they
sat under an `apps/*/domain/` path — because that is what they are.

Recorded as ADR 0005.
"""

from __future__ import annotations

from apps.common.authz.ownership import (
    OWNERSHIP,
    Filter,
    FilterKind,
    OwnershipRule,
    Resource,
    Scope,
    ownership_filter,
)
from apps.common.authz.principal import Principal
from apps.common.authz.roles import (
    MFA_MANDATORY_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    mfa_mandatory,
    permissions_for,
)

__all__ = [
    "Role",
    "Permission",
    "ROLE_PERMISSIONS",
    "MFA_MANDATORY_ROLES",
    "permissions_for",
    "mfa_mandatory",
    "Principal",
    "Resource",
    "Scope",
    "OwnershipRule",
    "OWNERSHIP",
    "Filter",
    "FilterKind",
    "ownership_filter",
]
