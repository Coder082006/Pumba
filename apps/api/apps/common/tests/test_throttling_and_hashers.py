"""Tests for the §9.6 rate limits and the §30.2 hasher parameters."""

from __future__ import annotations

import pytest

from apps.common.config import SETTINGS_REGISTER
from apps.common.hashers import (
    MEMORY_COST_KIB,
    PARALLELISM,
    TIME_COST,
    PlatformArgon2PasswordHasher,
)
from apps.common.throttling import (
    LoginEmailThrottle,
    LoginIpThrottle,
    RegistrationThrottle,
    parse_limit,
)


class TestArgon2Parameters:
    """§30.2: "Argon2id (memory 64 MiB, time cost 3, parallelism 4)"."""

    def test_memory_is_sixty_four_mebibytes(self) -> None:
        assert MEMORY_COST_KIB == 65536
        assert PlatformArgon2PasswordHasher.memory_cost == 65536

    def test_time_cost_is_three(self) -> None:
        assert TIME_COST == 3
        assert PlatformArgon2PasswordHasher.time_cost == 3

    def test_parallelism_is_four(self) -> None:
        assert PARALLELISM == 4
        assert PlatformArgon2PasswordHasher.parallelism == 4

    def test_the_parameters_differ_from_the_django_defaults(self) -> None:
        """If they ever coincide, this class is redundant — but a test that
        silently passes because Django changed its defaults would hide a
        change to the work factor protecting every password."""
        from django.contrib.auth.hashers import Argon2PasswordHasher

        assert (
            PlatformArgon2PasswordHasher.memory_cost,
            PlatformArgon2PasswordHasher.time_cost,
            PlatformArgon2PasswordHasher.parallelism,
        ) != (
            Argon2PasswordHasher.memory_cost,
            Argon2PasswordHasher.time_cost,
            Argon2PasswordHasher.parallelism,
        )

    def test_it_produces_an_argon2id_hash(self) -> None:
        encoded = PlatformArgon2PasswordHasher().encode("a-passphrase", "somesaltvalue")
        assert "argon2id" in encoded
        assert "m=65536" in encoded
        assert "t=3" in encoded
        assert "p=4" in encoded


class TestParseLimit:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("10/hour/ip", (10, 3600, "ip")),
            ("5/hour/email", (5, 3600, "email")),
            ("300/minute/principal", (300, 60, "principal")),
            ("120/minute/driver", (120, 60, "driver")),
            ("1/second/ip", (1, 1, "ip")),
        ],
    )
    def test_parses_the_register_format(self, value: str, expected: tuple[int, int, str]) -> None:
        assert parse_limit(value) == expected

    @pytest.mark.parametrize("value", ["10/hour", "10", "", "10/fortnight/ip", "ten/hour/ip"])
    def test_a_malformed_limit_raises_rather_than_allowing_everything(self, value: str) -> None:
        """The safe reading of an unparseable rate is not "no limit"."""
        with pytest.raises(ValueError):
            parse_limit(value)


class TestEveryRegisteredLimitIsParseable:
    """A limit that cannot be parsed is a 500 on the endpoint it guards."""

    @pytest.mark.parametrize(
        "key", sorted(k for k in SETTINGS_REGISTER if k.startswith("ratelimit."))
    )
    def test_limit_parses(self, key: str) -> None:
        count, seconds, scope = parse_limit(str(SETTINGS_REGISTER[key].default))
        assert count > 0
        assert seconds > 0
        assert scope


class TestTheLimitsMatchSection96:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("ratelimit.auth_login_ip", (10, 3600)),
            ("ratelimit.auth_login_email", (5, 3600)),
            ("ratelimit.auth_register", (5, 3600)),
            ("ratelimit.authenticated_read", (300, 60)),
            ("ratelimit.catalogue_read", (60, 60)),
            ("ratelimit.trip_quote", (20, 3600)),
            ("ratelimit.payment_intent", (10, 3600)),
            ("ratelimit.driver_location", (120, 60)),
            ("ratelimit.messaging_send", (60, 3600)),
        ],
    )
    def test_value(self, key: str, expected: tuple[int, int]) -> None:
        count, seconds, _ = parse_limit(str(SETTINGS_REGISTER[key].default))
        assert (count, seconds) == expected


class TestLoginIsLimitedBothWays:
    """§9.6 limits login by IP *and* by email, and one without the other is
    defeated by turning the attack sideways."""

    def test_the_per_ip_throttle_reads_the_ip_limit(self) -> None:
        assert LoginIpThrottle.setting_key == "ratelimit.auth_login_ip"
        assert parse_limit(str(SETTINGS_REGISTER[LoginIpThrottle.setting_key].default))[2] == "ip"

    def test_the_per_email_throttle_buckets_by_the_attempted_address(self) -> None:
        key = LoginEmailThrottle.setting_key
        assert parse_limit(str(SETTINGS_REGISTER[key].default))[2] == "email"

    def test_registration_and_reset_share_the_register_limit(self) -> None:
        assert RegistrationThrottle.setting_key == "ratelimit.auth_register"


class TestTheEmailBucketDoesNotStoreAddresses:
    def test_the_key_is_a_hash_not_the_address(self) -> None:
        """Otherwise the cache becomes a list of every address anyone has
        tried to sign in as."""

        class _Req:
            data = {"email": "alice@example.com"}

        ident = LoginEmailThrottle._hashed_email(_Req())  # type: ignore[arg-type]
        assert ident is not None
        assert "alice" not in ident
        assert "example.com" not in ident

    def test_case_and_whitespace_do_not_create_separate_buckets(self) -> None:
        class _A:
            data = {"email": "  Alice@Example.COM "}

        class _B:
            data = {"email": "alice@example.com"}

        assert LoginEmailThrottle._hashed_email(_A()) == LoginEmailThrottle._hashed_email(_B())  # type: ignore[arg-type]

    def test_a_missing_address_yields_no_bucket(self) -> None:
        class _Req:
            data: dict[str, str] = {}

        assert LoginEmailThrottle._hashed_email(_Req()) is None  # type: ignore[arg-type]
