"""Business configuration read port.

NFR-M07 and brief rule 5: "No business constant is hard-coded; all thresholds,
rates, weights and TTLs live in `system_setting`." The full register is
SRS Appendix B.

**Why this lives in `common` and not in `administration`.**
SRS §6.4 assigns the `system_setting` table to the `administration` module and
lists administration's dependencies as "all". But every module must read
settings — pricing reads `platform_fee_rate`, transport reads
`dispatch.weights`, inventory reads `quote.ttl_minutes`. That makes
`administration -> all` and `all -> administration` simultaneously, which is a
cycle and cannot be expressed in the import-linter contracts of SRS §6.5.

The resolution (issue S1 in docs/IMPLEMENTATION-PLAN.md) is to split read from
write. This module is the *read* port and lives in `common`, which is a leaf
every module may import. The `administration` module keeps the table, the
write path, the audit trail (SRS §30.12) and the console UI, and registers a
database-backed provider here at startup.

Until `administration` has models (Phase 2+), reads resolve to the Appendix B
defaults declared below. That is deliberate: it means no module ever hard-codes
a constant, even before the table exists.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "Setting",
    "SETTINGS_REGISTER",
    "get_setting",
    "register_provider",
    "UnknownSettingError",
]


class UnknownSettingError(KeyError):
    """A key was requested that is not in the register.

    Deliberately fatal rather than returning None: a typo in a settings key
    must not silently disable a business rule.
    """


@dataclass(frozen=True, slots=True)
class Setting:
    key: str
    default: Any
    description: str


def _d(value: str) -> Decimal:
    return Decimal(value)


# SRS Appendix B — System Settings Register, p. 144.
# "Every value below is a system_setting row, editable by an administrator,
#  with no deployment required."
SETTINGS_REGISTER: dict[str, Setting] = {
    s.key: s
    for s in [
        # -- Scheduling buffers --
        Setting("buffer.arrival_processing_minutes", 45, "Delay between flight arrival and pickup"),
        Setting(
            "buffer.airport_departure_minutes", 180, "Required arrival before departure flight"
        ),
        Setting("buffer.activity_minutes", 15, "Slack before an activity start"),
        # -- Quote and payment windows --
        Setting("quote.ttl_minutes", 20, "Quote and hold validity"),
        Setting("payment.window_minutes", 30, "Extended hold during payment"),
        # -- Dispatch --
        Setting("dispatch.lead_hours", 72, "When scheduled dispatch begins"),
        Setting("offer.ttl_seconds.scheduled", 3600, "Offer validity for future work"),
        Setting("offer.ttl_seconds.imminent", 90, "Offer validity within 24 hours"),
        Setting("assignment.disclosure_hours", 24, "Deadline to disclose driver details"),
        Setting(
            "dispatch.weights",
            {
                "proximity": _d("0.40"),
                "rating": _d("0.25"),
                "acceptance": _d("0.20"),
                "experience": _d("0.10"),
                "utilisation": _d("0.05"),
            },
            "Driver candidate scoring weights; must sum to 1.0",
        ),
        Setting("dispatch.max_radius_m", 60000, "Proximity normalisation ceiling"),
        # -- Geofencing and waiting --
        Setting("geofence.pickup_m", 300, "Arrival geofence"),
        Setting("geofence.approach_m", 1500, "Nearby notification"),
        Setting("wait.airport_minutes", 60, "No-show threshold at a gateway"),
        Setting("wait.standard_minutes", 15, "No-show threshold elsewhere"),
        # -- Money --
        Setting("platform_fee_rate", _d("0.05"), "Tourist-facing service fee"),
        Setting("commission.default_percent", _d("15"), "Global fallback commission"),
        Setting("fx.markup_percent", _d("2.0"), "Conversion protection margin"),
        Setting("settlement_hold_days", 2, "Delay before balance becomes available"),
        Setting(
            "payout.minimum",
            _d("50"),
            "USD equivalent; threshold below which balance rolls forward",
        ),
        Setting(
            "refund.auto_approve_limit",
            _d("200"),
            "USD equivalent; above this, finance approval required",
        ),
        # -- Itinerary limits --
        Setting("limit.items_per_day", 5, "Warning threshold VR-13"),
        Setting("limit.travel_minutes_per_day", 240, "Warning threshold VR-14"),
        Setting("trip.max_days", 30, "Maximum trip length"),
        Setting("stay.max_nights", 30, "Maximum accommodation stay"),
        # -- Horizons and retention --
        Setting("availability.horizon_days", 400, "Calendar auto-extension"),
        Setting("departures.horizon_days", 180, "Schedule materialisation window"),
        Setting("location.retention_days", 30, "Raw point retention"),
        Setting("provider_response_hours", 24, "On-request activity response window"),
        Setting("review.window_days", 30, "Review submission window"),
    ]
}

# Populated by `administration` once its models exist. Signature: (key) -> value,
# raising LookupError when the key has no row so the default applies.
_provider: Callable[[str], Any] | None = None


def register_provider(provider: Callable[[str], Any]) -> None:
    """Install the database-backed reader. Called by `administration` at startup."""
    global _provider
    _provider = provider


def get_setting(key: str) -> Any:
    """Resolve a business constant.

    Order: administrator-set row, then the Appendix B default. Raises
    `UnknownSettingError` for a key that is not registered at all.
    """
    if key not in SETTINGS_REGISTER:
        raise UnknownSettingError(
            f"{key!r} is not in the settings register. Add it to SETTINGS_REGISTER "
            "with its SRS Appendix B default before use."
        )

    if _provider is not None:
        try:
            return _provider(key)
        except LookupError:
            pass
        except Exception:
            # A settings-store failure must not take down request handling;
            # fall back to the declared default and alert.
            logger.exception("system_setting_read_failed", extra={"setting_key": key})

    return SETTINGS_REGISTER[key].default
