"""Model-layer validators for the trip schema — SRS §7.2, §7.5.10.

The same arrangement `catalogue.validators` uses, and for the same reason: the
rule lives in a pure module that is covered to 95%, and the validator is a
`ValidationError`-raising wrapper that puts it on the `Model.full_clean` path
the admin console writes through.

There is one rule here rather than catalogue's five, because a trip stores one
externally-standardised format: the presentment currency of §7.5.10. It comes
from `common.money`, which owns what a currency is — `catalogue` delegates to
the same function, so a trip and a destination can never disagree about what a
valid code looks like.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError

from apps.common import money

__all__ = ["validate_iso_currency_code"]


def validate_iso_currency_code(value: str) -> None:
    """ISO 4217 alpha-3, structurally. §7.2 pairs one with every money column."""
    try:
        money.validate_currency_code(value)
    except money.InvalidCurrencyError as exc:
        raise ValidationError(str(exc), code="invalid_currency_code") from exc
