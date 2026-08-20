"""Display conversion, and the wall between it and the money path.

SRS §18.4 gives the authoritative conversion its own mechanism: `fx_rate` rows
frozen at `priced_at`, recorded with rate, source and timestamp, so §20.6's
"there are no untraceable conversions" holds. §9.1's `X-Currency` is a
different thing entirely — a figure on a page, so a tourist can compare a stay
total against prices at home.

The requirement is that the two stay *visibly* apart, and not by convention.
So the greater part of this file asserts what an indicative amount refuses to
do. Every one of those refusals is a mistake somebody would otherwise make
once, ship, and discover in a reconciliation.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

import pytest

from apps.common.display_money import (
    IndicativeAmount,
    IndicativeAmountError,
    convert_for_display,
)
from apps.common.money import Money
from ports.exchange_rate import IndicativeRate
from ports.fakes import FakeExchangeRates

AS_OF = dt.datetime(2027, 1, 1, tzinfo=dt.UTC)


def rate(base: str = "TZS", quote: str = "USD", value: str = "0.0004") -> IndicativeRate:
    return IndicativeRate(base=base, quote=quote, rate=Decimal(value), as_of=AS_OF, source="test")


def amount() -> IndicativeAmount:
    return convert_for_display(Money(Decimal("250000.00"), "TZS"), rate=rate())


class TestTheIndicativeBlockIsNeverAnInputToATotal:
    """The condition attached to Q3, asserted rather than documented."""

    def test_two_indicative_amounts_do_not_add(self) -> None:
        with pytest.raises(IndicativeAmountError, match="cannot be used in arithmetic"):
            amount() + amount()  # type: ignore[operator]

    def test_they_cannot_be_summed(self) -> None:
        """`sum()` starts from 0 and calls `__radd__`, which is the path a
        line-total loop would actually take."""
        with pytest.raises(IndicativeAmountError):
            sum([amount(), amount()])  # type: ignore[arg-type]

    def test_money_plus_indicative_is_refused(self) -> None:
        """The subtotal case, and the one that nearly got through.

        `Money._check` compared currencies and nothing else, so an
        `IndicativeAmount` — which carries `.amount` and `.currency` by design
        — added by duck typing and came back out a `Money`. In the same
        currency it did not even raise. A display figure became a line total,
        silently, which is the precise thing §18.4 forbids.
        """
        with pytest.raises(TypeError, match="only Money is money"):
            Money(Decimal("100.00"), "USD") + amount()  # type: ignore[operator]

    def test_money_minus_indicative_is_refused(self) -> None:
        with pytest.raises(TypeError, match="only Money is money"):
            Money(Decimal("100.00"), "USD") - amount()  # type: ignore[operator]

    def test_money_refuses_any_lookalike(self) -> None:
        """The check is on the type, not on this one class, so the next thing
        that grows an `.amount` and a `.currency` is refused too."""

        @dataclass(frozen=True)
        class NotMoney:
            amount: Decimal
            currency: str

        with pytest.raises(TypeError, match="only Money is money"):
            Money(Decimal("1.00"), "USD") + NotMoney(Decimal("1.00"), "USD")  # type: ignore[operator]

    def test_indicative_plus_money_is_refused(self) -> None:
        with pytest.raises(IndicativeAmountError):
            amount() + Money(Decimal("10.00"), "USD")  # type: ignore[operator]

    def test_it_cannot_be_scaled_by_a_quantity(self) -> None:
        """Three nights at the converted nightly rate is the §24.11 mistake:
        the stay total is computed in the listing currency and converted once,
        never converted and then multiplied."""
        with pytest.raises(IndicativeAmountError):
            amount() * 3  # type: ignore[operator]

    def test_it_cannot_be_divided(self) -> None:
        """The nightly average is derived from the total, in the listing
        currency, before any conversion happens."""
        with pytest.raises(IndicativeAmountError):
            amount() / 3  # type: ignore[operator]

    def test_it_cannot_be_subtracted_or_negated(self) -> None:
        with pytest.raises(IndicativeAmountError):
            amount() - amount()  # type: ignore[operator]
        with pytest.raises(IndicativeAmountError):
            -amount()  # type: ignore[operator]

    def test_the_refusal_says_why(self) -> None:
        """A bare `TypeError` reads like a missing operator somebody should
        add, which is the exact mistake this type prevents."""
        with pytest.raises(IndicativeAmountError) as exc:
            amount() + amount()  # type: ignore[operator]
        message = str(exc.value)
        assert "fx_rate" in message
        assert "priced_at" in message

    def test_it_is_not_a_money(self) -> None:
        assert not isinstance(amount(), Money)

    def test_the_display_module_reaches_nothing_in_finance(self) -> None:
        """§18.4's path is `finance.fx_rate`. This module must not know it
        exists, or the separation is one import away from collapsing."""
        import inspect

        from apps.common import display_money

        source = inspect.getsource(display_money)
        assert "apps.finance" not in source


class TestConversion:
    def test_it_converts_and_rounds_once(self) -> None:
        converted = convert_for_display(Money(Decimal("250000.00"), "TZS"), rate=rate())
        assert converted.amount == Decimal("100.00")
        assert converted.currency == "USD"

    def test_it_rounds_to_the_target_currencys_minor_unit(self) -> None:
        """§18.5: 2 for USD and for TZS "as used by the PSP", 0 for currencies
        without minor units. Yen is the clean case for the second."""
        converted = convert_for_display(
            Money(Decimal("100.00"), "USD"), rate=rate("USD", "JPY", "157.25")
        )
        assert converted.amount == Decimal("15725")
        assert converted.amount.as_tuple().exponent == 0

    def test_tzs_keeps_two_places_because_the_psp_does(self) -> None:
        converted = convert_for_display(
            Money(Decimal("100.00"), "USD"), rate=rate("USD", "TZS", "2500")
        )
        assert converted.amount == Decimal("250000.00")

    def test_it_rounds_half_up(self) -> None:
        converted = convert_for_display(
            Money(Decimal("1.00"), "USD"), rate=rate("USD", "EUR", "0.925")
        )
        assert converted.amount == Decimal("0.93")

    def test_the_source_price_is_kept_alongside(self) -> None:
        """§24.11 shows both, and the listing currency is what is charged."""
        converted = amount()
        assert converted.source == Money(Decimal("250000.00"), "TZS")

    def test_the_rate_travels_with_the_figure(self) -> None:
        """§20.6, applied to a page: a caller cannot get the number without
        also holding where it came from and when."""
        converted = amount()
        assert converted.rate.source == "test"
        assert converted.rate.as_of == AS_OF

    def test_converting_a_price_the_rate_does_not_cover_is_refused(self) -> None:
        with pytest.raises(IndicativeAmountError, match="converts from"):
            convert_for_display(Money(Decimal("10.00"), "EUR"), rate=rate("TZS", "USD"))

    def test_zero_converts_to_zero(self) -> None:
        assert convert_for_display(Money(Decimal("0.00"), "TZS"), rate=rate()).amount == 0


class TestTheRateValueObject:
    def test_a_float_rate_is_refused(self) -> None:
        """Every upstream feed publishes JSON numbers, so this is where a
        float would get in."""
        with pytest.raises(TypeError, match="must be a Decimal"):
            IndicativeRate(base="TZS", quote="USD", rate=0.0004, as_of=AS_OF, source="x")  # type: ignore[arg-type]

    def test_a_naive_timestamp_is_refused(self) -> None:
        """§7.2. "As of when" is the whole value of the field."""
        with pytest.raises(ValueError, match="timezone-aware"):
            IndicativeRate(
                base="TZS",
                quote="USD",
                rate=Decimal("0.0004"),
                as_of=dt.datetime(2027, 1, 1),  # noqa: DTZ001
                source="x",
            )

    def test_a_zero_or_negative_rate_is_refused(self) -> None:
        for value in ("0", "-1"):
            with pytest.raises(ValueError, match="positive"):
                IndicativeRate(
                    base="TZS", quote="USD", rate=Decimal(value), as_of=AS_OF, source="x"
                )

    def test_a_rate_between_one_currency_and_itself_is_refused(self) -> None:
        with pytest.raises(ValueError, match="two different currencies"):
            IndicativeRate(base="USD", quote="USD", rate=Decimal("1"), as_of=AS_OF, source="x")

    def test_an_unsourced_rate_is_refused(self) -> None:
        with pytest.raises(ValueError, match="name its source"):
            IndicativeRate(base="TZS", quote="USD", rate=Decimal("0.0004"), as_of=AS_OF, source="")


class TestTheFake:
    def test_it_is_deterministic(self) -> None:
        """TC-902's byte-identity applies to any figure on a page."""
        first = FakeExchangeRates().indicative_rate(base="TZS", quote="USD")
        second = FakeExchangeRates().indicative_rate(base="TZS", quote="USD")
        assert first == second

    def test_an_unknown_currency_returns_none_rather_than_guessing(self) -> None:
        """The §12.6 discipline applied to money: never fabricate a figure a
        tourist could mistake for fact."""
        assert FakeExchangeRates().indicative_rate(base="TZS", quote="XYZ") is None

    def test_the_same_currency_returns_none(self) -> None:
        """Not an error: the caller already has the figure asked for."""
        assert FakeExchangeRates().indicative_rate(base="USD", quote="USD") is None

    def test_it_crosses_through_usd_both_ways(self) -> None:
        port = FakeExchangeRates()
        there = port.indicative_rate(base="USD", quote="TZS")
        back = port.indicative_rate(base="TZS", quote="USD")
        assert there is not None and back is not None
        assert there.rate * back.rate == Decimal(1)

    def test_it_returns_decimals(self) -> None:
        found = FakeExchangeRates().indicative_rate(base="EUR", quote="GBP")
        assert found is not None
        assert isinstance(found.rate, Decimal)

    def test_the_registry_resolves_it_by_default(self) -> None:
        from apps.common.ports_registry import get_exchange_rate_port, reset_ports

        reset_ports()
        try:
            assert isinstance(get_exchange_rate_port(), FakeExchangeRates)
        finally:
            reset_ports()
