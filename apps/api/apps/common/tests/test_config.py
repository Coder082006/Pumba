"""Settings read port tests — NFR-M07, SRS Appendix B."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.common import config
from apps.common.config import SETTINGS_REGISTER, UnknownSettingError, get_setting


@pytest.fixture(autouse=True)
def _clear_provider():
    config._provider = None
    yield
    config._provider = None


class TestRegister:
    def test_all_thirty_appendix_b_keys_are_declared(self) -> None:
        assert len(SETTINGS_REGISTER) == 30

    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("quote.ttl_minutes", 20),
            ("payment.window_minutes", 30),
            ("platform_fee_rate", Decimal("0.05")),
            ("commission.default_percent", Decimal("15")),
            ("dispatch.lead_hours", 72),
            ("geofence.pickup_m", 300),
            ("trip.max_days", 30),
            ("location.retention_days", 30),
        ],
    )
    def test_defaults_match_the_srs(self, key: str, expected: object) -> None:
        assert get_setting(key) == expected

    def test_money_defaults_are_decimal_not_float(self) -> None:
        for key in ("platform_fee_rate", "commission.default_percent", "fx.markup_percent"):
            assert isinstance(get_setting(key), Decimal), f"{key} must be Decimal"

    def test_dispatch_weights_sum_to_one(self) -> None:
        """SRS §11.6 / Appendix B: 0.40/0.25/0.20/0.10/0.05.

        A weight vector that does not sum to 1.0 silently changes the meaning
        of every driver score.
        """
        assert sum(get_setting("dispatch.weights").values()) == Decimal("1.00")


class TestResolution:
    def test_unknown_key_raises_rather_than_returning_none(self) -> None:
        """A typo must not silently disable a business rule."""
        with pytest.raises(UnknownSettingError, match="not in the settings register"):
            get_setting("quote.ttl_minutes_typo")

    def test_registered_provider_overrides_the_default(self) -> None:
        config.register_provider(lambda key: 5 if key == "quote.ttl_minutes" else (_ for _ in ()))
        assert get_setting("quote.ttl_minutes") == 5

    def test_provider_lookup_error_falls_back_to_the_default(self) -> None:
        def provider(key: str) -> object:
            raise LookupError(key)

        config.register_provider(provider)
        assert get_setting("quote.ttl_minutes") == 20

    def test_provider_failure_falls_back_rather_than_propagating(self) -> None:
        """A settings-store outage must not take down request handling."""

        def provider(key: str) -> object:
            raise RuntimeError("redis down")

        config.register_provider(provider)
        assert get_setting("quote.ttl_minutes") == 20
