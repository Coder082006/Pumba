"""Tests for the §30.2 / §9.4.1 password policy."""

from __future__ import annotations

import pytest

from apps.identity.domain.passwords import (
    PasswordViolationCode,
    email_local_part,
    validate_password,
)

EMAIL = "alice@example.com"


def codes(password: str, *, email: str = EMAIL, min_length: int = 12, breached: bool = False):  # type: ignore[no-untyped-def]
    return {
        v.code
        for v in validate_password(
            password, email=email, min_length=min_length, is_breached=breached
        )
    }


class TestLength:
    def test_a_compliant_password_passes(self) -> None:
        assert codes("correct horse battery") == set()

    def test_eleven_characters_is_too_short(self) -> None:
        assert PasswordViolationCode.TOO_SHORT in codes("a" * 11)

    def test_twelve_characters_is_the_boundary_and_passes(self) -> None:
        assert PasswordViolationCode.TOO_SHORT not in codes("a" * 12)

    def test_an_empty_password_is_too_short(self) -> None:
        assert PasswordViolationCode.TOO_SHORT in codes("")

    def test_length_is_counted_in_code_points_not_bytes(self) -> None:
        """Twelve non-Latin characters is twelve characters.

        Counting UTF-8 bytes would hold some users to a weaker rule than
        others — 12 bytes is only 4 CJK characters.
        """
        assert PasswordViolationCode.TOO_SHORT not in codes("パスワードパスワードパス")
        assert len("パスワードパスワードパス") == 12

    def test_the_minimum_is_a_parameter_not_a_constant(self) -> None:
        """It is a system_setting value; the domain must not assume 12."""
        assert PasswordViolationCode.TOO_SHORT in codes("a" * 12, min_length=16)


class TestBreachCheck:
    def test_a_breached_password_is_rejected(self) -> None:
        assert PasswordViolationCode.BREACHED in codes("password1234", breached=True)

    def test_an_unbreached_password_is_accepted(self) -> None:
        assert PasswordViolationCode.BREACHED not in codes("password1234", breached=False)

    def test_tc_003_a_long_but_breached_password_still_fails(self) -> None:
        """TC-003: "password1234" is 12 characters — length alone lets it in."""
        result = codes("password1234", breached=True)
        assert result == {PasswordViolationCode.BREACHED}


class TestEmailLocalPart:
    def test_a_password_equal_to_the_local_part_is_rejected(self) -> None:
        assert PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART in codes("alice")

    def test_the_comparison_ignores_case(self) -> None:
        assert PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART in codes("ALICE")

    def test_a_password_merely_containing_it_is_allowed(self) -> None:
        """§9.4.1 says "not equal to", not "does not contain"."""
        assert PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART not in codes("alice in wonderland")

    def test_an_email_without_an_at_sign_does_not_crash(self) -> None:
        assert PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART in codes("bob", email="bob")

    def test_an_empty_local_part_never_matches(self) -> None:
        """Otherwise an empty password would 'equal' it."""
        assert PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART not in codes("", email="@example.com")

    @pytest.mark.parametrize(
        ("email", "expected"),
        [
            ("Alice@Example.COM", "alice"),
            ("  alice@example.com  ", "alice"),
            ('"weird@address"@example.com', '"weird'),
        ],
    )
    def test_extraction(self, email: str, expected: str) -> None:
        """Splits on the first @: a quoted local part may legally contain one,
        and splitting on the last would compare against the wrong string."""
        assert email_local_part(email) == expected


class TestAllViolationsAreReported:
    def test_several_failures_are_returned_together(self) -> None:
        """§24.3 maps errors to fields by details[].field — one exception
        carrying one message cannot express two simultaneous failures."""
        assert codes("alice", breached=True) == {
            PasswordViolationCode.TOO_SHORT,
            PasswordViolationCode.BREACHED,
            PasswordViolationCode.EQUALS_EMAIL_LOCAL_PART,
        }

    def test_the_result_is_an_immutable_tuple(self) -> None:
        result = validate_password("a", email=EMAIL, min_length=12, is_breached=False)
        assert isinstance(result, tuple)

    def test_every_violation_carries_a_human_message(self) -> None:
        for v in validate_password("alice", email=EMAIL, min_length=12, is_breached=True):
            assert v.message.strip()
            assert v.message.endswith(".")
