"""Turn a bearer token into a `Principal` — SRS §30.2, §30.3.

Wraps SimpleJWT rather than replacing it: the signature verification and
expiry handling are its job, and the `Principal` construction is ours. The
principal is attached to the request once, here, so no view rebuilds it and
no view can build a different one.
"""

from __future__ import annotations

from typing import Any

from rest_framework.request import Request
from rest_framework_simplejwt.authentication import JWTAuthentication

from apps.common.authz import Principal
from apps.common.authz.loader import load_principal
from apps.common.context import set_actor_id

__all__ = ["PrincipalJWTAuthentication", "principal_from_request"]


class PrincipalJWTAuthentication(JWTAuthentication):
    """Authenticate, then attach `request.principal`.

    The `mfa` claim is read from the token rather than from the database: it
    records whether the obligation was met *for this session*, which is a
    property of the login, not of the account. Reading `user.has_mfa` instead
    would let an enrolled user who signed in without a code pass the check.
    """

    def authenticate(self, request: Request) -> tuple[Any, Any] | None:
        result = super().authenticate(request)
        if result is None:
            return None

        user, validated_token = result
        principal = load_principal(
            user_id=user.pk,
            mfa_satisfied=bool(validated_token.get("mfa", False)),
        )
        request.principal = principal  # type: ignore[attr-defined]
        if principal is not None:
            set_actor_id(principal.user_id)
        return user, validated_token


def principal_from_request(request: Any) -> Principal | None:
    """The principal, or `None` for an anonymous request.

    Never raises and never invents one. A view that requires a principal
    says so with a permission class; a helper that fabricated an empty
    principal would turn a missing permission class into a silent grant of
    whatever an empty role set happens to allow.
    """
    return getattr(request, "principal", None)
