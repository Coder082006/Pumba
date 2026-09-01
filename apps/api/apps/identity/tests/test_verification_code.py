"""Six-digit email verification — SRS §24.3, §24.4, §30.3.

A six-digit code is one of a million. That is a perfectly good secret *only*
while two things hold: it dies quickly, and the guesses are counted. Neither is
visible on screen, and the second was silently absent the first time this was
written — `verify_email_code` was `@transaction.atomic`, so the increment that
counted a wrong guess was rolled back by the exception that reported it. Five
failures left `attempts` at zero and the code still live.

That is why the tests below assert the counter *and* what happens after it runs
out, rather than only that a good code works.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.common.errors import ValidationError
from apps.identity import repositories as repo
from apps.identity import services
from apps.identity.models import OneTimeToken, TokenPurpose, User

pytestmark = pytest.mark.django_db

EMAIL = "ada@example.com"
OTHER = "grace@example.com"
PASSWORD = "Str0ng-Passw0rd!x"
MAX_ATTEMPTS = 5


def _register(email: str = EMAIL) -> User:
    services.register_tourist(
        email=email, password=PASSWORD, first_name="Ada", last_name="Lovelace"
    )
    return User.objects.get(email=email)


def _row(user: User) -> OneTimeToken:
    return OneTimeToken.objects.filter(
        user=user, purpose=TokenPurpose.EMAIL_VERIFICATION_CODE
    ).latest("created_at")


def _code_of(user: User) -> str:
    """Recover the issued code by searching its own space.

    Only a test may do this, and only because the space is small by design. It
    is the honest way to get the plaintext: the service hands the code to the
    email port and keeps nothing but a hash, which is the property being relied
    on everywhere else.
    """
    stored = _row(user).token_hash
    for candidate in range(1_000_000):
        code = f"{candidate:06d}"
        if repo.hash_code(user, code) == stored:
            return code
    raise AssertionError("the issued code hashes to nothing in the six-digit space")


class TestTheHappyPath:
    def test_a_correct_code_activates_the_account(self) -> None:
        user = _register()
        assert services.verify_email_code(EMAIL, _code_of(user)).email_verified

    def test_registration_issues_a_code_and_a_link(self) -> None:
        """Both, in one email. The link is for the device that registered; the
        code is for the phone the mail was read on."""
        user = _register()
        purposes = set(OneTimeToken.objects.filter(user=user).values_list("purpose", flat=True))
        assert purposes == {
            TokenPurpose.EMAIL_VERIFICATION,
            TokenPurpose.EMAIL_VERIFICATION_CODE,
        }

    def test_a_code_is_six_digits_including_its_leading_zeros(self) -> None:
        """A code rendered without leading zeros is a shorter code, and the
        ones beginning `0` would be the weakest of the set."""
        for _ in range(50):
            code = repo.new_verification_code()
            assert len(code) == 6
            assert code.isdigit()

    def test_the_link_still_works_on_its_own(self) -> None:
        """The two secrets do not consume one another. Somebody who ignores the
        code and opens the link must not be told it has been used."""
        user = _register()
        raw = repo.issue_one_time_token(
            user,
            TokenPurpose.EMAIL_VERIFICATION,
            ttl=timedelta(hours=24),
            now=timezone.now(),
        )
        assert services.verify_email(raw).email_verified


class TestTheAttemptLimit:
    """The defect this file exists for."""

    def test_a_wrong_guess_is_counted(self) -> None:
        user = _register()
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, "000000")
        assert _row(user).attempts == 1

    def test_the_count_survives_the_failure_that_reported_it(self) -> None:
        """The regression, stated exactly. Wrapping the use case in a
        transaction rolled the increment back with the exception that raised,
        so any number of failures left the counter at zero."""
        user = _register()
        for _ in range(3):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")
        assert _row(user).attempts == 3

    def test_the_code_is_burned_on_the_last_allowed_failure(self) -> None:
        """Not left to expire. A code that has absorbed the whole attempt
        budget is one an attacker has already spent their guesses on; leaving
        it alive until the TTL hands the budget back on the next request."""
        user = _register()
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")
        assert _row(user).consumed_at is not None

    def test_the_correct_code_is_refused_after_the_limit(self) -> None:
        """What a person would actually notice, and what was false: the right
        code still worked after five wrong ones."""
        user = _register()
        code = _code_of(user)
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")

        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)
        user.refresh_from_db()
        assert user.email_verified_at is None


class TestExpiry:
    def test_an_expired_code_is_refused(self) -> None:
        user = _register()
        code = _code_of(user)
        OneTimeToken.objects.filter(purpose=TokenPurpose.EMAIL_VERIFICATION_CODE).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)

    def test_a_code_cannot_be_spent_twice(self) -> None:
        user = _register()
        code = _code_of(user)
        services.verify_email_code(EMAIL, code)
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)


class TestItDoesNotEnumerateAccounts:
    """§30.3's reasoning, applied to an endpoint anyone may call."""

    def test_an_unknown_address_reads_the_same_as_a_wrong_code(self) -> None:
        _register()
        with pytest.raises(ValidationError) as wrong:
            services.verify_email_code(EMAIL, "000000")
        with pytest.raises(ValidationError) as unknown:
            services.verify_email_code("nobody@example.com", "000000")
        assert str(wrong.value) == str(unknown.value)

    def test_an_expired_code_reads_the_same_as_a_wrong_one(self) -> None:
        """Telling them apart would say whether it is worth guessing again."""
        user = _register()
        with pytest.raises(ValidationError) as wrong:
            services.verify_email_code(EMAIL, "000000")

        OneTimeToken.objects.filter(user=user).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with pytest.raises(ValidationError) as expired:
            services.verify_email_code(EMAIL, "111111")
        assert str(wrong.value) == str(expired.value)

    def test_resend_is_silent_for_an_address_with_no_account(self) -> None:
        """No exception, no signal. §24.5 states the rule for password reset,
        and this endpoint answers the same question."""
        services.resend_verification("nobody@example.com")

    def test_resend_is_silent_for_an_already_verified_account(self) -> None:
        user = _register()
        services.verify_email_code(EMAIL, _code_of(user))
        before = OneTimeToken.objects.count()
        services.resend_verification(EMAIL)
        assert OneTimeToken.objects.count() == before


class TestResend:
    def test_the_previous_code_stops_working(self) -> None:
        """A user who asks again because the first mail did not arrive must not
        leave the first code live — and an attacker who provoked a send must
        not keep a working one after the real user requests theirs."""
        user = _register()
        first = _code_of(user)
        services.resend_verification(EMAIL)

        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, first)

    def test_the_new_code_works(self) -> None:
        user = _register()
        services.resend_verification(EMAIL)
        assert services.verify_email_code(EMAIL, _code_of(user)).email_verified

    def test_a_resent_code_starts_with_a_fresh_attempt_budget(self) -> None:
        user = _register()
        for _ in range(3):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")
        services.resend_verification(EMAIL)
        assert _row(user).attempts == 0


class TestTheHashIsBoundToTheAccount:
    def test_two_accounts_holding_the_same_code_do_not_collide(self) -> None:
        """`token_hash` is UNIQUE. An unbound hash of six digits would make two
        users holding the same code a failed registration roughly once in a
        million — and would make every stored hash a table anyone can
        precompute in a second."""
        first = _register()
        second = _register(OTHER)
        assert repo.hash_code(first, "123456") != repo.hash_code(second, "123456")

    def test_a_code_issued_for_one_account_does_not_verify_another(self) -> None:
        first = _register()
        code = _code_of(first)
        _register(OTHER)

        with pytest.raises(ValidationError):
            services.verify_email_code(OTHER, code)
