"""Rate limits — SRS §9.6, §30.7.

    §30.7: "implemented as Redis token buckets keyed by principal where
     authenticated and by IP otherwise"

Every limit is a `system_setting` row (`ratelimit.*`, ADR 0006) in the form
`"N/period/scope"`, so an administrator can retune one during an incident
without a deployment — which is the point of NFR-M07 and the moment it
matters most.

`scope` decides the bucket key, and getting it wrong is the usual way a rate
limit becomes useless:

* `ip` — the peer address, for unauthenticated traffic.
* `principal` — the authenticated user.
* `email` — the address *being attempted*, not the caller's. §9.6 limits login
  to "10 / hour / IP **and** 5 / hour / email", and only the second stops a
  distributed attack on one account from a thousand addresses.

The address is hashed into the key rather than stored in it, so the cache does
not become a list of every address anyone has tried to sign in as.
"""

from __future__ import annotations

import hashlib
from typing import Any

from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle
from rest_framework.views import APIView

from apps.common.authentication import principal_from_request
from apps.common.config import get_setting

__all__ = [
    "SettingsRateThrottle",
    "LoginIpThrottle",
    "LoginEmailThrottle",
    "RegistrationThrottle",
    "AuthenticatedReadThrottle",
    "CatalogueReadThrottle",
    "parse_limit",
]

_PERIOD_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


def parse_limit(value: str) -> tuple[int, int, str]:
    """`"10/hour/ip"` -> `(10, 3600, "ip")`.

    Raises on anything else. A malformed limit must not silently degrade to
    "no limit" — an unparseable rate is a configuration error, and the safe
    reading of one is not "allow everything".
    """
    parts = value.split("/")
    if len(parts) != 3:
        raise ValueError(f"rate limit {value!r} must be 'count/period/scope'")
    count, period, scope = parts
    if period not in _PERIOD_SECONDS:
        raise ValueError(f"unknown period {period!r} in rate limit {value!r}")
    return int(count), _PERIOD_SECONDS[period], scope


class SettingsRateThrottle(SimpleRateThrottle):
    """A throttle whose rate comes from `system_setting`, read per request."""

    setting_key: str = ""
    scope = "settings"

    def __init__(self) -> None:
        # `SimpleRateThrottle.__init__` resolves the rate once, at import
        # time. Reading it per request is what makes an administrator's
        # change take effect without a restart.
        self.rate = None
        self.num_requests = 0
        self.duration = 0

    def _load(self) -> str:
        return str(get_setting(self.setting_key))

    def allow_request(self, request: Request, view: APIView) -> bool:
        self.num_requests, self.duration, self._scope_kind = parse_limit(self._load())
        return super().allow_request(request, view)

    def get_cache_key(self, request: Request, view: APIView) -> str | None:
        ident = self._identity(request)
        if ident is None:
            return None
        return f"throttle:{self.setting_key}:{ident}"

    def _identity(self, request: Request) -> str | None:
        kind = getattr(self, "_scope_kind", "ip")
        if kind == "principal":
            principal = principal_from_request(request)
            return None if principal is None else f"u{principal.user_id}"
        if kind == "email":
            return self._hashed_email(request)
        return self.get_ident(request)

    @staticmethod
    def _hashed_email(request: Request) -> str | None:
        """Bucket by the address being attempted, hashed.

        Storing it in clear would turn the cache into a list of every address
        anyone has tried to sign in as — a disclosure worth having for an
        attacker who reaches the cache but not the database.
        """
        data: Any = getattr(request, "data", None)
        if not isinstance(data, dict):
            return None
        email = data.get("email")
        if not isinstance(email, str) or not email.strip():
            return None
        digest = hashlib.sha256(email.strip().casefold().encode()).hexdigest()
        return f"e{digest[:32]}"


class LoginIpThrottle(SettingsRateThrottle):
    """§9.6: 10 / hour / IP."""

    setting_key = "ratelimit.auth_login_ip"


class LoginEmailThrottle(SettingsRateThrottle):
    """§9.6: 5 / hour / email — the half that stops a distributed attack."""

    setting_key = "ratelimit.auth_login_email"


class RegistrationThrottle(SettingsRateThrottle):
    """§9.6: 5 / hour / IP for registration and password reset."""

    setting_key = "ratelimit.auth_register"


class AuthenticatedReadThrottle(SettingsRateThrottle):
    """§9.6: 300 / minute / principal."""

    setting_key = "ratelimit.authenticated_read"


class CatalogueReadThrottle(SettingsRateThrottle):
    """§9.6: 60 / minute / IP for the unauthenticated catalogue.

    Keyed by address because there is no principal — these are the §9.3.2
    endpoints a tourist reaches before signing in. They are also the only
    unauthenticated endpoints that run a full-text query and a seven-term
    ordering, which makes them the cheapest thing on the platform to point a
    script at.
    """

    setting_key = "ratelimit.catalogue_read"
