"""Tests for the TOTP implementation, against the RFC 6238 vectors.

The vectors are the reason this is stdlib rather than a dependency: they turn
"we used a popular library" into "this produces the bytes the RFC says it
should".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.identity.domain.mfa import (
    base32_to_secret,
    provisioning_uri,
    secret_to_base32,
    totp_code,
    verify_totp,
)

#: RFC 6238 Appendix B. The seeds are the ASCII string "12345678901234567890"
#: repeated to the length each HMAC variant requires.
SEED_SHA1 = b"12345678901234567890"
SEED_SHA256 = b"12345678901234567890123456789012"
SEED_SHA512 = b"1234567890123456789012345678901234567890123456789012345678901234"

#: (unix time, expected 8-digit code, algorithm) — RFC 6238 Appendix B table.
RFC6238_VECTORS = [
    (59, "94287082", "SHA1"),
    (59, "46119246", "SHA256"),
    (59, "90693936", "SHA512"),
    (1111111109, "07081804", "SHA1"),
    (1111111109, "68084774", "SHA256"),
    (1111111109, "25091201", "SHA512"),
    (1111111111, "14050471", "SHA1"),
    (1111111111, "67062674", "SHA256"),
    (1111111111, "99943326", "SHA512"),
    (1234567890, "89005924", "SHA1"),
    (1234567890, "91819424", "SHA256"),
    (1234567890, "93441116", "SHA512"),
    (2000000000, "69279037", "SHA1"),
    (2000000000, "90698825", "SHA256"),
    (2000000000, "38618901", "SHA512"),
    (20000000000, "65353130", "SHA1"),
    (20000000000, "77737706", "SHA256"),
    (20000000000, "47863826", "SHA512"),
]

SEEDS = {"SHA1": SEED_SHA1, "SHA256": SEED_SHA256, "SHA512": SEED_SHA512}

T0 = datetime(2027, 8, 10, 12, 0, 0, tzinfo=UTC)
SECRET = b"a-test-secret-16"


class TestRfc6238Vectors:
    @pytest.mark.parametrize(("unix_time", "expected", "algorithm"), RFC6238_VECTORS)
    def test_published_vector(self, unix_time: int, expected: str, algorithm: str) -> None:
        at = datetime.fromtimestamp(unix_time, tz=UTC)
        assert totp_code(SEEDS[algorithm], at=at, digits=8, algorithm=algorithm) == expected

    @pytest.mark.parametrize(("unix_time", "expected", "algorithm"), RFC6238_VECTORS)
    def test_the_vector_verifies(self, unix_time: int, expected: str, algorithm: str) -> None:
        at = datetime.fromtimestamp(unix_time, tz=UTC)
        assert verify_totp(
            SEEDS[algorithm], expected, at=at, digits=8, algorithm=algorithm, drift_steps=0
        )


class TestStepBehaviour:
    def test_the_code_is_stable_within_a_step(self) -> None:
        base = datetime(2027, 8, 10, 12, 0, 0, tzinfo=UTC)
        assert totp_code(SECRET, at=base) == totp_code(SECRET, at=base + timedelta(seconds=29))

    def test_the_code_changes_between_steps(self) -> None:
        base = datetime(2027, 8, 10, 12, 0, 0, tzinfo=UTC)
        assert totp_code(SECRET, at=base) != totp_code(SECRET, at=base + timedelta(seconds=30))

    def test_the_step_length_is_configurable(self) -> None:
        base = datetime(2027, 8, 10, 12, 0, 0, tzinfo=UTC)
        a = totp_code(SECRET, at=base + timedelta(seconds=45), step_seconds=30)
        b = totp_code(SECRET, at=base + timedelta(seconds=45), step_seconds=60)
        assert a != b


class TestDrift:
    def test_the_current_step_verifies(self) -> None:
        assert verify_totp(SECRET, totp_code(SECRET, at=T0), at=T0)

    def test_one_step_late_is_accepted(self) -> None:
        """A phone thirty seconds behind still logs in."""
        stale = totp_code(SECRET, at=T0 - timedelta(seconds=30))
        assert verify_totp(SECRET, stale, at=T0)

    def test_one_step_early_is_accepted(self) -> None:
        early = totp_code(SECRET, at=T0 + timedelta(seconds=30))
        assert verify_totp(SECRET, early, at=T0)

    def test_two_steps_out_is_refused(self) -> None:
        far = totp_code(SECRET, at=T0 - timedelta(seconds=60))
        assert not verify_totp(SECRET, far, at=T0)

    def test_drift_can_be_disabled(self) -> None:
        stale = totp_code(SECRET, at=T0 - timedelta(seconds=30))
        assert not verify_totp(SECRET, stale, at=T0, drift_steps=0)

    def test_drift_is_symmetric(self) -> None:
        for offset in (-60, -30, 0, 30, 60):
            code = totp_code(SECRET, at=T0 + timedelta(seconds=offset))
            assert verify_totp(SECRET, code, at=T0, drift_steps=2)

    def test_negative_drift_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="not be negative"):
            verify_totp(SECRET, "000000", at=T0, drift_steps=-1)


class TestVerificationRejectsJunk:
    @pytest.mark.parametrize("code", ["", "  ", "12345", "1234567", "abcdef", "12 34 56", None])
    def test_malformed_codes_are_refused(self, code: str | None) -> None:
        assert not verify_totp(SECRET, code, at=T0)  # type: ignore[arg-type]

    def test_a_wrong_code_of_the_right_shape_is_refused(self) -> None:
        right = totp_code(SECRET, at=T0)
        wrong = "000000" if right != "000000" else "111111"
        assert not verify_totp(SECRET, wrong, at=T0)

    def test_a_code_for_a_different_secret_is_refused(self) -> None:
        other = totp_code(b"another-secret16", at=T0)
        assert not verify_totp(SECRET, other, at=T0)

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        """Users paste codes with a stray space; that is not an attack."""
        assert verify_totp(SECRET, f"  {totp_code(SECRET, at=T0)} ", at=T0)


class TestInputValidation:
    def test_a_naive_datetime_is_rejected(self) -> None:
        """SRS §7.2 forbids naive datetimes; silently assuming UTC here would
        make a code valid at the wrong minute for half the world."""
        with pytest.raises(ValueError, match="timezone-aware"):
            totp_code(SECRET, at=datetime(2027, 8, 10, 12, 0, 0))  # noqa: DTZ001

    def test_an_empty_secret_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            totp_code(b"", at=T0)

    @pytest.mark.parametrize("digits", [5, 11])
    def test_absurd_digit_counts_are_rejected(self, digits: int) -> None:
        with pytest.raises(ValueError, match="between 6 and 10"):
            totp_code(SECRET, at=T0, digits=digits)

    def test_a_non_positive_step_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be positive"):
            totp_code(SECRET, at=T0, step_seconds=0)

    def test_an_unknown_algorithm_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="unsupported algorithm"):
            totp_code(SECRET, at=T0, algorithm="MD5")


class TestBase32Round:
    def test_round_trips(self) -> None:
        assert base32_to_secret(secret_to_base32(SECRET)) == SECRET

    def test_encoding_is_unpadded(self) -> None:
        """Authenticator apps reject the '=' padding."""
        assert "=" not in secret_to_base32(b"12345")

    def test_decoding_tolerates_spaces_and_lower_case(self) -> None:
        encoded = secret_to_base32(SECRET)
        spaced = " ".join(encoded[i : i + 4] for i in range(0, len(encoded), 4)).lower()
        assert base32_to_secret(spaced) == SECRET


class TestProvisioningUri:
    def test_contains_the_secret_and_issuer(self) -> None:
        uri = provisioning_uri(secret=SECRET, account="alice@example.com", issuer="Pumba")
        assert uri.startswith("otpauth://totp/")
        assert secret_to_base32(SECRET) in uri
        assert "issuer=Pumba" in uri

    def test_the_label_is_escaped(self) -> None:
        """An unescaped ':' or '/' in an account name breaks the URI."""
        uri = provisioning_uri(secret=SECRET, account="a/b:c", issuer="Pumba")
        assert "a/b:c" not in uri
        assert "Pumba%3Aa%2Fb%3Ac" in uri

    def test_parameters_match_the_verification_defaults(self) -> None:
        """A URI that provisions different parameters than the server verifies
        produces codes that never match."""
        uri = provisioning_uri(secret=SECRET, account="alice", issuer="Pumba")
        assert "digits=6" in uri
        assert "period=30" in uri
        assert "algorithm=SHA1" in uri
