"""What `GET /config` is allowed to say — SRS §23.13, §24.1, §35.

    §23.13: "a `min_supported_version` returned by the API forces an upgrade
    prompt when a client falls below the floor; feature flags delivered
    through `GET /config` allow server-side control of client features without
    a store release."

    §24.1 (Splash): "API `GET /config` (min version, feature flags, enabled
    currencies)".

The endpoint reads `system_setting`, and `system_setting` is where every
business constant in Appendix B lives: dispatch weights, fraud thresholds,
commission percentages, quote TTLs, lockout policy. Serving that table to an
unauthenticated caller would publish the platform's entire operating model,
including the §11.6 dispatch weights a provider could game and the §30.14 fraud
thresholds an attacker could stay under.

So this module exists to make the safe set **explicit and closed**. Nothing is
served because of what it is named or where it sits; it is served because it
appears below. A row added to the register tomorrow is private until somebody
edits this file, and `tests/test_public_config.py` fails the build if anything
outside these two rules reaches the payload.

Two rules, not one, because feature flags are open-ended by design — the whole
point of §35's "ship dark" is that a flag can be added without ceremony — while
everything else is a fixed, small list.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from apps.common.config import SETTINGS_REGISTER, get_setting

__all__ = [
    "PUBLIC_SETTINGS",
    "FEATURE_FLAG_PREFIX",
    "feature_flag_keys",
    "public_config",
]

#: Wire name -> settings key. The wire name is deliberately not the settings
#: key: `map.tile_url` is an internal register path, and a client should not
#: have to know the register's shape to read a URL.
PUBLIC_SETTINGS: Mapping[str, str] = {
    # §24.1's three, verbatim.
    "min_supported_version": "client.min_supported_version",
    "enabled_currencies": "currency.enabled",
    # ADR 0016 / Appendix D9. Serving these here is what lets the web client
    # stop keeping its own copy: the settings row becomes the single source of
    # truth again, so changing tile provider is an administrator action rather
    # than a redeployment of the front end.
    "map_tile_url": "map.tile_url",
    "map_tile_attribution": "map.tile_attribution",
}

#: §35: "the flag set is served to clients through GET /config". Any registered
#: setting under this prefix is a flag and is public *by construction* — which
#: is safe only because the prefix means "client-visible switch" and nothing
#: else. A threshold must never be named `feature.*`, and
#: `test_no_flag_carries_a_business_value` is what enforces that.
FEATURE_FLAG_PREFIX = "feature."


def feature_flag_keys() -> tuple[str, ...]:
    """Registered flags, sorted. Derived, so a new flag needs no code change."""
    return tuple(sorted(k for k in SETTINGS_REGISTER if k.startswith(FEATURE_FLAG_PREFIX)))


def public_config() -> dict[str, Any]:
    """The whole payload. The only function that reads settings for this route.

    A single choke point on purpose: a view that reached for `get_setting`
    directly could serve anything, and the allow-list would become advisory.
    """
    payload: dict[str, Any] = {
        name: get_setting(key) for name, key in sorted(PUBLIC_SETTINGS.items())
    }
    payload["features"] = {
        key.removeprefix(FEATURE_FLAG_PREFIX): bool(get_setting(key)) for key in feature_flag_keys()
    }
    return payload
