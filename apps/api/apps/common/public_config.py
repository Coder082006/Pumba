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

**The test for admitting a value.** `stay.max_nights` is a business constant,
which is precisely what the paragraph above says must never escape — so the
rule that lets it through has to be written down, rather than inferred later
from this one precedent by somebody arguing that their threshold is similar.
A registered setting may be added to `PUBLIC_SETTINGS` only when *both* hold:

1. **A screen the SRS specifies cannot do its job without it.** §24.11 requires
   the stay length to be validated *before* submission, so the client has to
   know the bound. An alternative that serves nothing — hardcoding 30 in the
   front end — is the NFR-M07 violation this whole module presupposes, and no
   allow-list would ever have caught it.
2. **Knowing it in advance confers no advantage.** The bound is enforced
   server-side regardless, and a tourist meets it the moment they pick dates.
   A §11.6 dispatch weight fails this: a provider who knows it can game
   allocation. A §30.14 fraud threshold fails it harder: knowing it is the
   whole attack. Neither will ever pass, which is the point of stating it.

`test_the_public_set_is_exactly_this` pins the outcome, so a sixth entry is a
deliberate edit to a test whose docstring is this paragraph.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
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
    # BR-101 / §24.11, admitted under the two-part rule in this module's
    # docstring. The stay screen refuses an over-long stay before submitting
    # it, so the client needs the number; the server enforces the same bound
    # through `catalogue.domain.pricing.stay_nights` either way.
    "stay_max_nights": "stay.max_nights",
}

#: Wire name -> coercion, for the values whose registered type is not already
#: what a client should receive. `get_setting` returns the Appendix B default
#: today, but an administrator-set row arrives as whatever the column holds —
#: and `stay_max_nights` reaching a browser as the string "30" would compare
#: lexically against a number, so a 5-night stay would fail and a 100-night one
#: would pass. Same defence as `bool()` on the flags below.
_WIRE_TYPES: Mapping[str, Callable[[Any], Any]] = {
    "stay_max_nights": int,
}

#: §35: "the flag set is served to clients through GET /config". Any registered
#: setting under this prefix is a flag and is public *by construction* — which
#: is safe only because the prefix means "client-visible switch" and nothing
#: else. A threshold must never be named `feature.*`, and
#: `test_no_flag_carries_a_business_value` is what enforces that.
FEATURE_FLAG_PREFIX = "feature."


def _identity(value: Any) -> Any:
    return value


def feature_flag_keys() -> tuple[str, ...]:
    """Registered flags, sorted. Derived, so a new flag needs no code change."""
    return tuple(sorted(k for k in SETTINGS_REGISTER if k.startswith(FEATURE_FLAG_PREFIX)))


def public_config() -> dict[str, Any]:
    """The whole payload. The only function that reads settings for this route.

    A single choke point on purpose: a view that reached for `get_setting`
    directly could serve anything, and the allow-list would become advisory.
    """
    payload: dict[str, Any] = {
        name: _WIRE_TYPES.get(name, _identity)(get_setting(key))
        for name, key in sorted(PUBLIC_SETTINGS.items())
    }
    payload["features"] = {
        key.removeprefix(FEATURE_FLAG_PREFIX): bool(get_setting(key)) for key in feature_flag_keys()
    }
    return payload
