"""Six-digit email verification — SRS §24.3, §24.4, §30.3, ADR 0021.

Two properties are load-bearing and neither is visible on screen.

**The code is one of a million**, so it is safe only while it dies quickly and
the guesses are counted. The counter was silently absent when this was first
written — `verify_email_code` was `@transaction.atomic`, so the increment that
counted a wrong guess was rolled back by the exception reporting it, and five
failures left the count at zero.

**Nothing reaches the database until the code is right** (ADR 0021). That is
what these tests are mostly checking now: an unverified registration is a cache
entry with a TTL, and the `user` table gains a row at exactly one moment.
"""

from __future__ import annotations

import pytest
from django.contrib.auth.hashers import check_password, make_password

from apps.common.errors import ValidationError
from apps.common.ports_registry import get_email_port
from apps.identity import pending, services
from apps.identity import repositories as repo
from apps.identity.models import TouristProfile, User

pytestmark = pytest.mark.django_db

EMAIL = "ada@example.com"
OTHER = "grace@example.com"
PASSWORD = "Str0ng-Passw0rd!x"
MAX_ATTEMPTS = 5


def _register(email: str = EMAIL) -> None:
    services.register_tourist(
        email=email, password=PASSWORD, first_name="Ada", last_name="Lovelace"
    )


def _code(email: str = EMAIL) -> str:
    """The plaintext code, off the email the fake port recorded.

    Read from the message rather than recovered from the hash, because the
    message is where a real person reads it. The hash is one-way by design and
    a test that brute-forced it would be asserting on the wrong artefact.
    """
    return str(get_email_port().sent[-1]["context"]["code"])


def _token(email: str = EMAIL) -> str:
    return str(get_email_port().sent[-1]["context"]["token"])


class TestNothingIsStoredUntilItIsProved:
    """ADR 0021, which is the reason this file changed shape."""

    def test_registering_creates_no_user(self) -> None:
        _register()
        assert User.objects.count() == 0

    def test_registering_creates_no_profile(self) -> None:
        _register()
        assert TouristProfile.objects.count() == 0

    def test_the_account_appears_only_when_the_code_is_right(self) -> None:
        _register()
        assert User.objects.count() == 0

        services.verify_email_code(EMAIL, _code())
        assert User.objects.count() == 1

    def test_a_wrong_code_creates_nothing(self) -> None:
        _register()
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, "000000")
        assert User.objects.count() == 0

    def test_an_abandoned_registration_leaves_no_trace(self) -> None:
        """The whole point. Somebody who registers and never opens the email
        has left nothing behind — not a row, not a name, not an address."""
        _register("abandoned@example.com")
        pending.drop("abandoned@example.com")

        assert User.objects.count() == 0
        assert repo.find_user_by_email("abandoned@example.com") is None


class TestTheHappyPath:
    def test_a_correct_code_creates_a_verified_account(self) -> None:
        _register()
        dto = services.verify_email_code(EMAIL, _code())
        assert dto.email_verified

    def test_the_account_is_active_immediately(self) -> None:
        """Created verified rather than PENDING-then-updated: it has just been
        verified, and a row that went in PENDING and was corrected a line later
        would describe a state nobody was ever in."""
        _register()
        services.verify_email_code(EMAIL, _code())
        assert User.objects.get(email=EMAIL).status == "ACTIVE"

    def test_the_profile_carries_what_was_registered(self) -> None:
        """The details survived the wait in the cache."""
        _register()
        services.verify_email_code(EMAIL, _code())
        assert TouristProfile.objects.get().first_name == "Ada"

    def test_the_password_registered_is_the_password_that_works(self) -> None:
        """Hashed at registration, carried through the cache, stored on the
        account. A break anywhere in that chain locks the person out of an
        account they just made."""
        _register()
        services.verify_email_code(EMAIL, _code())
        assert check_password(PASSWORD, User.objects.get(email=EMAIL).password)

    def test_registration_issues_a_code_and_a_link(self) -> None:
        """Both, in one email. The link is for the device that registered; the
        code is for the phone the mail was read on."""
        _register()
        context = get_email_port().sent[-1]["context"]
        assert context["code"] and context["token"]

    def test_a_code_is_six_digits_including_its_leading_zeros(self) -> None:
        """A code rendered without leading zeros is a shorter code, and the
        ones beginning `0` would be the weakest of the set."""
        for _ in range(50):
            code = repo.new_verification_code()
            assert len(code) == 6
            assert code.isdigit()

    def test_the_link_works_on_its_own(self) -> None:
        """Someone who ignores the code and opens the link gets the same
        account. The two secrets are alternatives, not a sequence."""
        _register()
        assert services.verify_email(_token()).email_verified
        assert User.objects.count() == 1


class TestTheAttemptLimit:
    """The defect this file was written for."""

    def test_a_wrong_guess_is_counted(self) -> None:
        _register()
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, "000000")

        entry = pending.get(EMAIL)
        assert entry is not None
        assert entry.attempts == 1

    def test_the_count_survives_the_failure_that_reported_it(self) -> None:
        """The regression, stated exactly. Wrapping the use case in a
        transaction rolled the increment back with the exception that raised,
        so any number of failures left the counter at zero."""
        _register()
        for _ in range(3):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")

        entry = pending.get(EMAIL)
        assert entry is not None
        assert entry.attempts == 3

    def test_the_registration_is_dropped_on_the_last_allowed_failure(self) -> None:
        """Burned rather than left to expire. A code that has absorbed the
        whole attempt budget is one an attacker has already spent their guesses
        on; leaving it alive hands the budget back on the next request."""
        _register()
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")
        assert pending.get(EMAIL) is None

    def test_the_correct_code_is_refused_after_the_limit(self) -> None:
        """What a person would actually notice, and what was false: the right
        code still worked after five wrong ones."""
        _register()
        code = _code()
        for _ in range(MAX_ATTEMPTS):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")

        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)
        assert User.objects.count() == 0


class TestExpiry:
    def test_an_expired_registration_is_refused(self) -> None:
        """Expiry is the cache's TTL — ADR 0021 leaves nothing in the database
        to age, and dropping the entry is what the TTL does when it fires."""
        _register()
        code = _code()
        pending.drop(EMAIL)
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)

    def test_a_code_cannot_be_spent_twice(self) -> None:
        _register()
        code = _code()
        services.verify_email_code(EMAIL, code)
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)

    def test_a_second_use_creates_no_second_account(self) -> None:
        _register()
        code = _code()
        services.verify_email_code(EMAIL, code)
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, code)
        assert User.objects.count() == 1


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
        _register()
        with pytest.raises(ValidationError) as wrong:
            services.verify_email_code(EMAIL, "000000")

        pending.drop(EMAIL)
        with pytest.raises(ValidationError) as expired:
            services.verify_email_code(EMAIL, "111111")
        assert str(wrong.value) == str(expired.value)

    def test_resend_is_silent_for_an_address_with_no_registration(self) -> None:
        """No exception, no signal. §24.5 states the rule for password reset,
        and this endpoint answers the same question."""
        services.resend_verification("nobody@example.com")

    def test_resend_is_silent_for_an_address_that_already_has_an_account(self) -> None:
        repo.create_tourist(
            email=EMAIL,
            password_hash=make_password(PASSWORD),
            first_name="Ada",
            last_name="Lovelace",
        )
        before = len(get_email_port().sent)
        services.resend_verification(EMAIL)
        assert len(get_email_port().sent) == before


class TestResend:
    def test_the_previous_code_stops_working(self) -> None:
        """A user who asks again because the first mail did not arrive must not
        leave the first code live — and an attacker who provoked a send must
        not keep a working one after the real user requests theirs."""
        _register()
        first = _code()
        services.resend_verification(EMAIL)

        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, first)

    def test_the_new_code_works(self) -> None:
        _register()
        services.resend_verification(EMAIL)
        assert services.verify_email_code(EMAIL, _code()).email_verified

    def test_a_resent_code_starts_with_a_fresh_attempt_budget(self) -> None:
        _register()
        for _ in range(3):
            with pytest.raises(ValidationError):
                services.verify_email_code(EMAIL, "000000")
        services.resend_verification(EMAIL)

        entry = pending.get(EMAIL)
        assert entry is not None
        assert entry.attempts == 0

    def test_it_keeps_the_details_that_were_registered(self) -> None:
        """A resend must not lose the name or the password — there is no form
        behind it to re-supply them."""
        _register()
        services.resend_verification(EMAIL)
        services.verify_email_code(EMAIL, _code())

        assert TouristProfile.objects.get().first_name == "Ada"
        assert check_password(PASSWORD, User.objects.get(email=EMAIL).password)


class TestTheCodeIsBoundToTheAddress:
    def test_a_code_issued_for_one_address_does_not_verify_another(self) -> None:
        """The hash covers `email:code`, so a code observed for one
        registration is worthless against another — and the stored hashes are
        not a table of a million entries anybody can precompute."""
        _register()
        stolen = _code()
        _register(OTHER)

        with pytest.raises(ValidationError):
            services.verify_email_code(OTHER, stolen)

    def test_two_addresses_holding_the_same_code_hash_differently(self) -> None:
        assert pending.hash_code(EMAIL, "123456") != pending.hash_code(OTHER, "123456")
