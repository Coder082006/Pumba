"""Model-layer validators — SRS §7.2, §7.5.3.

The rule itself lives in `apps.common.money`, which owns what a currency
means. This is the `ValidationError`-raising wrapper that puts it on the
`Model.full_clean` path, so an administrator writing a provider through the
console is held to the same rule as a service call.

Shaped exactly like `apps.transport.validators`, and for the same reason: the
admin provider console writes through `full_clean`, so the rule has to be on the
model and not only in a service.
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
