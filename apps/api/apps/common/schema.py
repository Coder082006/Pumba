"""OpenAPI extensions — SRS §36.2, §30.8.

drf-spectacular resolves each authentication class to a security scheme by
looking one up in a registry. `PrincipalJWTAuthentication` is ours, so the
mapping has to be declared or every authenticated operation is documented as
though it were public — which is worse than a missing document, because
§30.8 publishes this to partners.
"""

from __future__ import annotations

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension

__all__ = ["PrincipalJWTScheme"]


class PrincipalJWTScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    # The base class registers subclasses through an untyped
    # `__init_subclass__`, which strict mode flags at the class statement
    # itself. The extension is a handful of literals; there is nothing here
    # for the ignore to hide.
    target_class = "apps.common.authentication.PrincipalJWTAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, Any]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": (
                "Access token from POST /api/v1/auth/login. Valid for 15 minutes; "
                "refresh at POST /api/v1/auth/refresh."
            ),
        }
