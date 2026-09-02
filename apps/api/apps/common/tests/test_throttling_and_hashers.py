"""Tests for the §9.6 rate limits and the §30.2 hasher parameters."""

# ruff: noqa: ARG002

from __future__ import annotations

from typing import Any

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


class TestAThrottleActuallyThrottles:
    """The assertion this file was missing, and the defect that hid in the gap.

    Every test above checks a *part*: that a limit parses, that it matches
    §9.6's table, that a bucket key is a hash rather than an address. All of
    them passed while no limit in the platform did anything at all —
    `SettingsRateThrottle` set `num_requests` and `duration` per request but
    left `self.rate` at None, and `SimpleRateThrottle.allow_request` opens with
    `if self.rate is None: return True`. Login brute-force protection,
    registration limits and the catalogue's IP limit were all inert.

    It surfaced in Phase 5, when the quote endpoint became the first thing
    anybody asserted a 429 from over HTTP. So the missing assertion is the one
    below: not "the limit is configured" but "the eleventh request is
    refused".
    """

    def _throttle(self, limit: str) -> Any:
        from apps.common.throttling import SettingsRateThrottle

        class _Fixed(SettingsRateThrottle):
            setting_key = "ratelimit.catalogue_read"

            def _load(self) -> str:
                return limit

        return _Fixed()

    def _request(self, ip: str = "203.0.113.7") -> Any:
        class _Req:
            META = {"REMOTE_ADDR": ip}
            query_params: dict[str, str] = {}
            data: dict[str, str] = {}

        return _Req()

    def test_requests_within_the_limit_are_allowed(self) -> None:
        from django.core.cache import cache

        cache.clear()
        throttle = self._throttle("3/minute/ip")
        request = self._request()
        assert [throttle.allow_request(request, None) for _ in range(3)] == [True] * 3

    def test_the_request_past_the_limit_is_refused(self) -> None:
        """The whole point. Without `self.rate`, this returned True forever."""
        from django.core.cache import cache

        cache.clear()
        throttle = self._throttle("3/minute/ip")
        request = self._request()
        for _ in range(3):
            throttle.allow_request(request, None)
        assert throttle.allow_request(request, None) is False

    def test_a_different_bucket_is_unaffected(self) -> None:
        from django.core.cache import cache

        cache.clear()
        throttle = self._throttle("2/minute/ip")
        for _ in range(3):
            throttle.allow_request(self._request("198.51.100.4"), None)
        assert throttle.allow_request(self._request("203.0.113.9"), None) is True

    def test_the_rate_is_set_from_the_setting_on_every_request(self) -> None:
        """An administrator's change takes effect without a restart, which is
        why the rate is resolved per request rather than at import — and the
        reason `self.rate` was left None in the first place."""
        from django.core.cache import cache

        cache.clear()
        throttle = self._throttle("5/hour/ip")
        throttle.allow_request(self._request(), None)
        assert throttle.rate == "5/hour/ip"
        assert (throttle.num_requests, throttle.duration) == (5, 3600)
