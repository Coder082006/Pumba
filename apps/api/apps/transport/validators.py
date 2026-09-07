"""Model-layer validators — SRS §7.2, §12.4.

The rule itself lives in `apps.common.money`, which owns what a currency
means. This is the `ValidationError`-raising wrapper that puts it on the
`Model.full_clean` path, so an administrator writing a tariff through the
console is held to the same rule as a service call.

Shaped exactly like `apps.catalogue.validators`, and for the same reason: §27.11
requires an administrator to configure a tariff "with no code change", and the
console writes through `full_clean`, not through `services.py`.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.common.money import InvalidCurrencyError, validate_currency_code

__all__ = ["validate_iso_currency_code"]


def validate_iso_currency_code(value: str) -> None:
    try:
        validate_currency_code(value)
    except InvalidCurrencyError as exc:
        raise ValidationError(str(exc), code="invalid_currency") from exc
