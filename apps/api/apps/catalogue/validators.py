"""Model-layer validators for the geography hierarchy — SRS §4.1, §7.5.6.

`apps.catalogue.domain.hierarchy` already refuses an incoherent country code,
currency or IANA zone, and every service path goes through it. That is not
enough on its own: §27.8 requires an administrator to create a destination
"with no code change and no deployment", and the console writes through
`Model.full_clean`, not through a service. A validator on the field is what
puts the domain rule on that path.

The zone matters more than the other two. `Africa/Zanzibar` looks exactly like
an IANA name and is not one; a regex passes it, the row saves, and the failure
surfaces later as a broken opening-hours table in every attraction in that
destination — far from the console field that caused it.

Validators are `ValidationError`-raising wrappers and nothing more. The rule
itself stays in `domain/`, which owns it and is covered to 95%.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.catalogue.domain import cancellation, hierarchy, requirements

__all__ = [
    "validate_iana_timezone",
    "validate_iso_country_code",
    "validate_iso_currency_code",
    "validate_cancellation_tiers",
    "validate_activity_requirements",
]


def validate_iana_timezone(value: str) -> None:
    """The zone must exist in the running system's tz database."""
    try:
        hierarchy.validate_timezone(value)
    except hierarchy.HierarchyError as exc:
        raise ValidationError(str(exc), code="invalid_timezone") from exc


def validate_iso_country_code(value: str) -> None:
    """ISO 3166-1 alpha-2, structurally. §7.5.6 stores `CHAR(2)`."""
    try:
        hierarchy.validate_country_code(value)
    except hierarchy.HierarchyError as exc:
        raise ValidationError(str(exc), code="invalid_country_code") from exc


def validate_iso_currency_code(value: str) -> None:
    """ISO 4217 alpha-3, structurally. §7.2 pairs it with every money column."""
    try:
        hierarchy.validate_currency_code(value)
    except hierarchy.HierarchyError as exc:
        raise ValidationError(str(exc), code="invalid_currency_code") from exc


def validate_cancellation_tiers(value: object) -> None:
    """§14.6's ordered `{hours_before, refund_percent}` list.

    `domain.cancellation.validate_tiers` rejects rather than repairs: tiers out
    of order, duplicated, or with a refund that rises as the cancellation gets
    later. A repaired policy is a policy nobody wrote, and the tourist is
    refunded by it.

    The JSONB column can only be CHECKed as far as "is an array". The shape
    inside it is checked here, on the write path an administrator uses.
    """
    if not isinstance(value, list):
        raise ValidationError("cancellation tiers must be a list", code="invalid_tiers")
    try:
        cancellation.parse_tiers(value)
    except cancellation.CancellationPolicyError as exc:
        raise ValidationError(str(exc), code="invalid_tiers") from exc


def validate_activity_requirements(value: object) -> None:
    """§16.4's structured restrictions.

    These are safety controls before they are copy: `min_age` and
    `swimming_ability_required` feed VR-15 and the booking guards. An unknown
    key is rejected rather than ignored, because typing `minimum_age` would
    otherwise create a listing whose age restriction silently does not exist.
    """
    if value is None:
        return
    if not isinstance(value, dict):
        raise ValidationError("requirements must be an object", code="invalid_requirements")
    try:
        requirements.parse_requirements(value)
    except requirements.RequirementsError as exc:
        raise ValidationError(str(exc), code="invalid_requirements") from exc
