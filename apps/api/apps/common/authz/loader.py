"""How the shared kernel obtains a `Principal` without importing `identity`.

`identity` owns `get_principal()` (SRS §6.4), but `apps.common` is a leaf and
`apps.catalogue` and friends have no dependency on `identity` either — so the
authentication class that needs to build a principal on every request cannot
call it directly. Same shape as the settings read port (S1) and the audit
write port (Q2), resolved the same way: a registry here, populated by
`identity` at startup.

Nothing falls back. An unregistered loader raises, because "authorisation
could not determine who you are" must never quietly become "authorisation
treated you as nobody in particular" — the failure mode of a silent `None`
here is an unauthenticated request that reaches a view expecting a principal.
"""

from __future__ import annotations

from typing import Protocol

from apps.common.authz.principal import Principal

__all__ = [
    "PrincipalLoader",
    "register_principal_loader",
    "load_principal",
    "PrincipalLoaderMissingError",
]


class PrincipalLoader(Protocol):
    """`identity.services.get_principal`, stated as a type.

    A Protocol rather than `Callable[..., Principal | None]` so the keyword
    names are part of the contract — the registry is the one place the two
    modules agree on a signature, and `...` would let them drift.
    """

    def __call__(self, *, user_id: int, mfa_satisfied: bool = False) -> Principal | None: ...


class PrincipalLoaderMissingError(RuntimeError):
    """No module registered a principal loader."""


_loader: PrincipalLoader | None = None


def register_principal_loader(loader: PrincipalLoader) -> None:
    """Called by `identity` at startup. Signature: (user_id, mfa_satisfied)."""
    global _loader
    _loader = loader


def load_principal(*, user_id: int, mfa_satisfied: bool = False) -> Principal | None:
    if _loader is None:
        raise PrincipalLoaderMissingError(
            "No principal loader registered. `apps.identity` installs one in its "
            "AppConfig.ready(); check it is in INSTALLED_APPS."
        )
    return _loader(user_id=user_id, mfa_satisfied=mfa_satisfied)
