"""Money tests — SRS §7.2, §18.5.

Money correctness is the highest-consequence code in Phase 1: an error here
misprices a basket and shows up as a reconciliation exception rather than a
crash. These tests pin the two rules that are easy to get wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.common.money import (
    CurrencyMismatchError,
    InvalidCurrencyError,
    Money,
    minor_unit_exponent,
    validate_currency_code,
)


class TestConstruction:
    def test_rejects_float(self) -> None:
        """SRS §7.2 and brief rule 6: never float. Not even once."""
        with pytest.raises(TypeError, match="rejects float"):
            Money(38.00, "USD")  # type: ignore[arg-type]

    def test_accepts_decimal_and_str(self) -> None:
        assert Money(Decimal("38.00"), "USD").amount == Decimal("38.00")
        assert Money("38.00", "USD").amount == Decimal("38.00")  # type: ignore[arg-type]

    def test_normalises_currency_case(self) -> None:
        assert Money(Decimal(1), "usd").currency == "USD"

    @pytest.mark.parametrize("code", ["US", "USDD", "US1", ""])
    def test_rejects_non_iso_currency(self, code: str) -> None:
        with pytest.raises(ValueError, match="ISO 4217"):
            Money(Decimal(1), code)

    def test_rejects_non_finite(self) -> None:
        with pytest.raises(ValueError, match="finite"):
            Money(Decimal("NaN"), "USD")

    def test_is_immutable(self) -> None:
        money = Money(Decimal("10"), "USD")
        with pytest.raises(Exception):  # noqa: B017 — frozen dataclass raises FrozenInstanceError
            money.amount = Decimal("20")  # type: ignore[misc]


class TestCurrencyIsolation:
    """SRS §18.4: conversion is explicit and rate-stamped, never implicit."""

    def test_addition_across_currencies_raises(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            Money(Decimal(1), "USD") + Money(Decimal(1), "TZS")

    def test_comparison_across_currencies_raises(self) -> None:
        with pytest.raises(CurrencyMismatchError):
            _ = Money(Decimal(1), "USD") < Money(Decimal(2), "TZS")

    def test_error_names_both_currencies(self) -> None:
        with pytest.raises(CurrencyMismatchError) as exc:
            Money(Decimal(1), "USD") - Money(Decimal(1), "EUR")
        assert exc.value.left == "USD"
        assert exc.value.right == "EUR"


class TestArithmetic:
    def test_add_and_subtract(self) -> None:
        assert (Money(Decimal("10.50"), "USD") + Money(Decimal("4.50"), "USD")).amount == Decimal(
            "15.00"
        )
        assert (Money(Decimal("10.50"), "USD") - Money(Decimal("0.50"), "USD")).amount == Decimal(
            "10.00"
        )

    def test_multiply_by_decimal(self) -> None:
        # The platform fee of SRS Appendix B: 5% of 795.00.
        assert (Money(Decimal("795.00"), "USD") * Decimal("0.05")).amount == Decimal("39.7500")

    def test_multiply_by_float_raises(self) -> None:
        with pytest.raises(TypeError, match="float"):
            Money(Decimal("100"), "USD") * 0.05  # type: ignore[operator]

    def test_negation(self) -> None:
        """Reversing ledger entries depend on this (SRS §22.3, principle A2)."""
        assert (-Money(Decimal("38.00"), "USD")).amount == Decimal("-38.00")


class TestRounding:
    """SRS §18.5 — the clause most likely to be implemented wrongly."""

    def test_does_not_quantize_implicitly(self) -> None:
        """Intermediate values retain full precision.

        If arithmetic quantized eagerly, `total = subtotal + fee + tax` would
        drift and the invariant SRS §18.5 asserts before writing a trip would
        fail for reasons unrelated to the data.
        """
        product = Money(Decimal("795.00"), "USD") * Decimal("0.05")
        assert product.amount == Decimal("39.7500"), "arithmetic must not round"

    def test_quantize_is_half_up_not_bankers(self) -> None:
        """Python's Decimal default is ROUND_HALF_EVEN; the SRS requires HALF_UP."""
        assert Money(Decimal("0.125"), "USD").quantize().amount == Decimal("0.13")
        assert Money(Decimal("0.135"), "USD").quantize().amount == Decimal("0.14")

    def test_quantize_respects_zero_decimal_currencies(self) -> None:
        """SRS §18.5: "0 for currencies without minor units".

        A fixed 2dp quantize would produce JPY 1234.50, which does not exist.
        """
        assert Money(Decimal("1234.56"), "JPY").quantize().amount == Decimal("1235")
        assert Money(Decimal("1234.44"), "JPY").quantize().amount == Decimal("1234")

    def test_quantize_respects_three_decimal_currencies(self) -> None:
        assert Money(Decimal("1.2345"), "KWD").quantize().amount == Decimal("1.235")

    @pytest.mark.parametrize(
        ("currency", "expected"),
        [("USD", 2), ("TZS", 2), ("EUR", 2), ("JPY", 0), ("UGX", 0), ("KWD", 3), ("ZZZ", 2)],
    )
    def test_exponents(self, currency: str, expected: int) -> None:
        # TZS is 2 per SRS §18.5 ("as used by the PSP"); unknown codes default to 2.
        assert minor_unit_exponent(currency) == expected


class TestRepresentation:
    def test_as_dict_matches_the_api_wire_format(self) -> None:
        """SRS §9.1: {"amount": "465.00", "currency": "USD"} — decimal string."""
        assert Money(Decimal("465"), "USD").as_dict() == {"amount": "465.00", "currency": "USD"}

    def test_as_dict_for_zero_decimal_currency(self) -> None:
        assert Money(Decimal("1235"), "JPY").as_dict() == {"amount": "1235", "currency": "JPY"}

    def test_str_is_human_readable(self) -> None:
        assert str(Money(Decimal("834.75"), "USD")) == "USD 834.75"

    def test_repr_shows_the_unrounded_amount(self) -> None:
        # repr must not quantize: when debugging a rounding dispute, the full
        # intermediate precision is the whole point.
        assert repr(Money(Decimal("39.7500"), "USD")) == "Money(Decimal('39.7500'), 'USD')"


class TestComparison:
    """Used for real thresholds: the refund ceiling of SRS §32.3
    REFUND_EXCEEDS_CAPTURED and the payout.minimum of Appendix B."""

    def test_orders_by_amount(self) -> None:
        small = Money(Decimal("49.99"), "USD")
        large = Money(Decimal("50.00"), "USD")

        assert small < large
        assert small <= large
        assert large > small
        assert large >= small

    def test_equal_amounts_compare_both_ways(self) -> None:
        a = Money(Decimal("50.00"), "USD")
        b = Money(Decimal("50.00"), "USD")

        assert a <= b
        assert a >= b
        assert not a < b
        assert not a > b

    def test_trailing_zeros_do_not_affect_ordering(self) -> None:
        # Decimal("50.0") and Decimal("50.00") differ in representation but
        # not in value; a payout threshold must not depend on which one the
        # database happened to return.
        assert not Money(Decimal("50.0"), "USD") < Money(Decimal("50.00"), "USD")

    @pytest.mark.parametrize("operation", ["lt", "le", "gt", "ge"])
    def test_every_comparison_refuses_a_currency_mismatch(self, operation: str) -> None:
        import operator

        with pytest.raises(CurrencyMismatchError):
            getattr(operator, operation)(Money(Decimal("1"), "USD"), Money(Decimal("1"), "TZS"))


class TestPredicates:
    def test_zero_constructor(self) -> None:
        assert Money.zero("USD").is_zero

    def test_parse_from_wire(self) -> None:
        assert Money.parse("465.00", "USD").amount == Decimal("465.00")

    def test_is_negative(self) -> None:
        assert Money(Decimal("-1"), "USD").is_negative
        assert not Money(Decimal("0"), "USD").is_negative

    def test_equality_includes_currency(self) -> None:
        assert Money(Decimal("1.00"), "USD") != Money(Decimal("1.00"), "TZS")


class TestCurrencyCode:
    """The structural ISO 4217 rule, now shared.

    It lived in `catalogue.domain.hierarchy` until `trip` needed the identical
    check for `trip.currency` and could not import catalogue's internals. The
    catalogue tests still exercise it through the delegating wrapper, so both
    call sites stay covered and there is only one definition to be wrong.
    """

    def test_normalises_to_upper_case(self) -> None:
        assert validate_currency_code("tzs") == "TZS"
        assert validate_currency_code("  usd  ") == "USD"

    @pytest.mark.parametrize("code", ["US", "USDD", "US1", "US$", "", "   ", "ÜSD"])
    def test_rejects_anything_that_is_not_three_ascii_letters(self, code: str) -> None:
        with pytest.raises(InvalidCurrencyError):
            validate_currency_code(code)

    def test_accepts_a_code_not_in_the_exponent_table(self) -> None:
        """Structural, not a membership test.

        `_EXPONENT_OVERRIDES` lists only the exceptions, so a currency absent
        from it is ordinary rather than unknown. Validating against a fixed
        list would reject a currency the platform had been configured to
        present — the failure ISO 4217's churn guarantees eventually.
        """
        assert validate_currency_code("gbp") == "GBP"
        assert minor_unit_exponent("GBP") == 2
