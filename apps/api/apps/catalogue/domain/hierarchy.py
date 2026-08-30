"""Geography hierarchy invariants — SRS §4.1, §4.2, §7.5.6.

§4.2 is blunt about what this module exists to prevent:

    PROHIBITED                         REQUIRED INSTEAD
    if destination == "Zanzibar":      destination = Destination.objects.get(...)
    ZANZIBAR_AIRPORT_ID = 1            destination.is_gateway == True
    if region in ("Unguja","Pemba"):   region.country.code / region.is_active
    hard-coded currency "TZS"          Currency resolved from destination.country

So the flags carry the behaviour and there are no names in code. What is left
to enforce is that the flags are *coherent*, because a destination with
`is_gateway = true` and no `gateway_code` breaks flight capture, and one with an
invalid IANA zone breaks every opening-hours table in it — and both are one
mistyped console field away.

The three validated formats are all external standards with fixed shapes:
ISO 3166-1 alpha-2, ISO 4217 alpha-3, and an IANA time zone. Only the first two
are checked structurally here; a zone is checked against the system's tz
database, because the set is large, versioned, and not something to hard-code.
`Africa/Dar_es_Salaam` and `Africa/Arusha` must both work without a code
change — which is precisely the §41.12 acceptance test.

**Gateway coherence is `if and only if`**, not `if`. A destination with a
`gateway_code` but `is_gateway = false` is just as broken as the reverse: the
partial unique index in §7.5.6 is `UNIQUE(gateway_code) WHERE is_gateway`, so
the orphaned code escapes uniqueness and a second gateway can later claim it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apps.common import money

__all__ = [
    "GatewayType",
    "HierarchyError",
    "validate_country_code",
    "validate_currency_code",
    "validate_timezone",
    "validate_gateway",
    "DestinationFlags",
]


class HierarchyError(ValueError):
    """A geography record whose flags do not cohere."""


class GatewayType(StrEnum):
    """§4.1: "AIRPORT | SEAPORT | LAND_BORDER"."""

    AIRPORT = "AIRPORT"
    SEAPORT = "SEAPORT"
    LAND_BORDER = "LAND_BORDER"


def validate_country_code(code: str) -> str:
    """ISO 3166-1 alpha-2, upper-cased. §7.5.6 stores `CHAR(2)`.

    Structural only. Validating against the full ISO list would mean shipping
    and maintaining that list, and the register changes — a market opening in a
    newly recognised country must not need a release.
    """
    normalised = code.strip().upper()
    if len(normalised) != 2 or not normalised.isascii() or not normalised.isalpha():
        raise HierarchyError(f"country code must be two ASCII letters, got {code!r}")
    return normalised


def validate_currency_code(code: str) -> str:
    """ISO 4217 alpha-3, upper-cased. Same reasoning as the country code.

    Delegates to `common.money`, which owns what a currency is. `trip` needs
    the identical rule for `trip.currency` and may not import catalogue's
    internals, so the definition moved to the shared kernel rather than being
    written twice. The `HierarchyError` translation stays here because every
    caller in this module catches that type.
    """
    try:
        return money.validate_currency_code(code)
    except money.InvalidCurrencyError as exc:
        raise HierarchyError(str(exc)) from exc


def validate_timezone(name: str) -> str:
    """An IANA zone the running system can actually resolve.

    Checked against the tz database rather than a pattern, because a name that
    merely *looks* like a zone — `Africa/Zanzibar`, which does not exist — would
    pass a regex and then fail at render time inside every opening-hours table
    in that destination.
    """
    candidate = name.strip()
    if not candidate:
        raise HierarchyError("timezone is required")
    try:
        ZoneInfo(candidate)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HierarchyError(f"{name!r} is not a known IANA time zone") from exc
    return candidate


def validate_gateway(
    *, is_gateway: bool, gateway_type: str | None, gateway_code: str | None
) -> tuple[GatewayType | None, str | None]:
    """§7.5.6's gateway columns, coherent in both directions.

    Required when `is_gateway`; forbidden when not. The reverse case matters
    because §7.5.6 indexes `UNIQUE(gateway_code) WHERE is_gateway` — a code on
    a non-gateway row sits outside that index and a second gateway can later
    claim the same one.
    """
    if not is_gateway:
        if gateway_type or gateway_code:
            raise HierarchyError(
                "gateway_type and gateway_code are only meaningful when is_gateway is true"
            )
        return None, None

    if not gateway_type:
        raise HierarchyError("a gateway needs a gateway_type")
    if not gateway_code or not gateway_code.strip():
        raise HierarchyError("a gateway needs a gateway_code")

    try:
        parsed = GatewayType(gateway_type.strip().upper())
    except ValueError as exc:
        raise HierarchyError(
            f"gateway_type must be one of {[t.value for t in GatewayType]}, got {gateway_type!r}"
        ) from exc

    code = gateway_code.strip().upper()
    if len(code) > 10 or not code.isascii() or not code.isalnum():
        # §7.5.6 gives the column VARCHAR(10); "ZNZ" is an IATA code, but a
        # seaport or land border may use something longer and non-standard.
        raise HierarchyError(f"gateway_code must be up to 10 alphanumeric characters, got {code!r}")
    return parsed, code


@dataclass(frozen=True, slots=True)
class DestinationFlags:
    """The §4.1 flags that drive behaviour, validated together.

    Constructed on every §27.8 write, so an incoherent destination is rejected
    at the form rather than discovered by a tourist whose flight cannot be
    captured.
    """

    timezone: str
    default_currency: str
    is_gateway: bool = False
    gateway_type: GatewayType | None = None
    gateway_code: str | None = None
    feature_rank: int = 100

    @classmethod
    def build(
        cls,
        *,
        timezone: str,
        default_currency: str,
        is_gateway: bool = False,
        gateway_type: str | None = None,
        gateway_code: str | None = None,
        feature_rank: int = 100,
    ) -> DestinationFlags:
        if feature_rank < 1:
            # §7.5.6 defaults it to 100 and `ranking` sorts it ascending, so 0
            # or a negative would place a destination above every curated one —
            # silently, and permanently.
            raise HierarchyError("feature_rank must be a positive integer")
        parsed_type, parsed_code = validate_gateway(
            is_gateway=is_gateway, gateway_type=gateway_type, gateway_code=gateway_code
        )
        return cls(
            timezone=validate_timezone(timezone),
            default_currency=validate_currency_code(default_currency),
            is_gateway=is_gateway,
            gateway_type=parsed_type,
            gateway_code=parsed_code,
            feature_rank=feature_rank,
        )
