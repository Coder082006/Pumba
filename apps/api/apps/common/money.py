"""Money value object.

Pure — no Django, no ORM, no I/O. Layer 3 (SRS §8.2).

Two rules from the specification drive every decision here:

* **SRS §7.2** — "Every money column is accompanied by a currency CHAR(3)
  column. Never store money without its currency." So currency is part of the
  value, not context, and arithmetic across currencies raises.

* **SRS §18.5** — "All money is Decimal, ROUND_HALF_UP, at the currency's
  minor-unit precision (2 for USD, 2 for TZS as used by the PSP, **0 for
  currencies without minor units**). Rounding is applied once per line total
  and once per aggregate; intermediate values retain full precision."

That last clause is why `Money` does not quantize on construction or after
every operation. Quantizing eagerly would round intermediate values and break
the `total = subtotal + fee + tax` invariant the SRS asserts before a trip is
written. Call `.quantize()` deliberately, at line totals and aggregates.

Never a float. Constructing from a float raises.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

__all__ = ["Money", "CurrencyMismatchError", "minor_unit_exponent"]


# ISO 4217 minor-unit exponents. The default is 2; only the exceptions are
# listed. Sourced from ISO 4217 Table A.1.
_EXPONENT_OVERRIDES: Final[dict[str, int]] = {
    # No minor unit
    "BIF": 0,
    "CLP": 0,
    "DJF": 0,
    "GNF": 0,
    "ISK": 0,
    "JPY": 0,
    "KMF": 0,
    "KRW": 0,
    "PYG": 0,
    "RWF": 0,
    "UGX": 0,
    "UYI": 0,
    "VND": 0,
    "VUV": 0,
    "XAF": 0,
    "XOF": 0,
    "XPF": 0,
    # Three minor digits
    "BHD": 3,
    "IQD": 3,
    "JOD": 3,
    "KWD": 3,
    "LYD": 3,
    "OMR": 3,
    "TND": 3,
    # Four minor digits
    "CLF": 4,
    "UYW": 4,
}

_DEFAULT_EXPONENT: Final[int] = 2


class CurrencyMismatchError(ValueError):
    """Raised when an operation mixes two currencies.

    Never caught to "convert on the fly" — conversion is an explicit,
    rate-stamped operation (SRS §18.4: the FX rate is frozen at `priced_at`).
    """

    def __init__(self, left: str, right: str) -> None:
        super().__init__(f"Cannot combine {left} and {right} without an explicit FX conversion")
        self.left = left
        self.right = right


def minor_unit_exponent(currency: str) -> int:
    """Number of decimal places this currency is expressed in."""
    return _EXPONENT_OVERRIDES.get(currency.upper(), _DEFAULT_EXPONENT)


@dataclass(frozen=True, slots=True, order=False)
class Money:
    """An exact amount in a specific currency."""

    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount, float):
            raise TypeError(
                "Money rejects float. Use Decimal or str: Money(Decimal('38.00'), 'USD')."
            )
        if not isinstance(self.amount, Decimal):
            object.__setattr__(self, "amount", Decimal(str(self.amount)))
        if not self.amount.is_finite():
            raise ValueError(f"Money amount must be finite, got {self.amount}")

        code = self.currency.upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError(f"Currency must be an ISO 4217 alpha-3 code, got {self.currency!r}")
        object.__setattr__(self, "currency", code)

    # -- constructors ------------------------------------------------------

    @classmethod
    def zero(cls, currency: str) -> Money:
        return cls(Decimal(0), currency)

    @classmethod
    def parse(cls, amount: str, currency: str) -> Money:
        """Build from the API wire format (SRS §9.1: decimal string, never a float)."""
        return cls(Decimal(amount), currency)

    # -- invariants --------------------------------------------------------

    def _check(self, other: Money) -> None:
        if not isinstance(other, Money):
            # Not pedantry: anything carrying `.amount` and `.currency` would
            # otherwise add here by duck typing and come back out a `Money`.
            # `apps.common.display_money.IndicativeAmount` carries both by
            # design, and §18.4 forbids a display conversion becoming an input
            # to a total. An explicit type check is what stops a figure from a
            # page turning into a figure on an invoice.
            raise TypeError(
                f"only Money is money; {type(other).__name__} cannot take part in "
                "money arithmetic"
            )
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)

    @property
    def exponent(self) -> int:
        return minor_unit_exponent(self.currency)

    # -- arithmetic --------------------------------------------------------

    def __add__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._check(other)
        return Money(self.amount - other.amount, self.currency)

    def __mul__(self, factor: Decimal | int) -> Money:
        if isinstance(factor, float):
            raise TypeError("Money cannot be multiplied by float. Use Decimal.")
        return Money(self.amount * Decimal(factor), self.currency)

    __rmul__ = __mul__

    def __neg__(self) -> Money:
        return Money(-self.amount, self.currency)

    def quantize(self) -> Money:
        """Round to this currency's minor unit, ROUND_HALF_UP (SRS §18.5).

        Apply once per line total and once per aggregate — not after every
        intermediate operation.
        """
        step = Decimal(1).scaleb(-self.exponent)
        return Money(self.amount.quantize(step, rounding=ROUND_HALF_UP), self.currency)

    # -- comparison --------------------------------------------------------

    def __lt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount < other.amount

    def __le__(self, other: Money) -> bool:
        self._check(other)
        return self.amount <= other.amount

    def __gt__(self, other: Money) -> bool:
        self._check(other)
        return self.amount > other.amount

    def __ge__(self, other: Money) -> bool:
        self._check(other)
        return self.amount >= other.amount

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    # -- representation ----------------------------------------------------

    def as_dict(self) -> dict[str, str]:
        """The API money representation (SRS §9.1)."""
        q = self.quantize()
        return {"amount": f"{q.amount:.{q.exponent}f}", "currency": q.currency}

    def __str__(self) -> str:
        q = self.quantize()
        return f"{q.currency} {q.amount:.{q.exponent}f}"

    def __repr__(self) -> str:
        return f"Money({self.amount!r}, {self.currency!r})"
