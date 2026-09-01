"""The development aid that unblocks registration — no SRS section, by design.

`verification_link` exists because no email provider need be configured for a
developer to finish an account: with `EMAIL_ADAPTER` unset, `get_email_port()`
resolves to `FakeEmail`, which records the message in memory and surfaces it
nowhere. Without this, a local registration could be started and never
completed.

Two properties are worth defending, and one is a security boundary:

* it refuses to run with `DEBUG` off, because handing a verification secret to
  whoever can reach a shell defeats the only thing verification proves;
* it goes through `resend_verification` — the same call the API makes — so the
  code and link it prints are ones the endpoints accept. A command that reached
  into the cache and rebuilt them itself would keep working while the flow it
  exists to exercise was broken.

Since ADR 0021 it operates on a **registration in progress**, not an account:
nothing is written to the database until a code is verified.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.common.errors import ValidationError
from apps.identity import pending, services
from apps.identity import repositories as repo

pytestmark = pytest.mark.django_db

EMAIL = "ada@example.com"
PASSWORD = "Str0ng-Passw0rd!x"


def _register(email: str = EMAIL) -> None:
    services.register_tourist(
        email=email, password=PASSWORD, first_name="Ada", last_name="Lovelace"
    )


def _run(*args: str) -> str:
    out = StringIO()
    call_command("verification_link", *args, stdout=out)
    return out.getvalue().strip()


def _code_from(output: str) -> str:
    """The digits off the `code:` line, ignoring the expiry note beside them."""
    line = next(row for row in output.splitlines() if row.startswith("code:"))
    return line.split()[1]


class TestInDevelopment:
    """`settings` is pytest-django's fixture; it restores the value after each
    test, which `override_settings` cannot do as a plain-class decorator."""

    @pytest.fixture(autouse=True)
    def _debug_on(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.DEBUG = True

    def test_it_prints_a_code_the_dialog_endpoint_accepts(self) -> None:
        """The property that makes this worth having rather than a shortcut:
        the code is real, so the command cannot pass while the flow it exists
        to exercise is broken."""
        _register()
        code = _code_from(_run(EMAIL))

        assert services.verify_email_code(EMAIL, code).email_verified

    def test_it_prints_a_link_the_verify_endpoint_accepts(self) -> None:
        _register()
        token = _run(EMAIL).rsplit("token=", 1)[1]

        assert services.verify_email(token).email_verified

    def test_the_code_is_six_digits(self) -> None:
        _register()
        code = _code_from(_run(EMAIL))
        assert len(code) == 6
        assert code.isdigit()

    def test_the_link_points_at_the_web_client(self) -> None:
        _register()
        assert "http://localhost:3000/verify-email?token=" in _run(EMAIL)

    def test_the_base_url_is_configurable(self) -> None:
        """A different port, or a tunnel, is the ordinary case for testing a
        link on a phone."""
        _register()
        assert "http://192.168.1.4:3000/verify-email?token=" in _run(
            EMAIL, "--base-url", "http://192.168.1.4:3000"
        )

    def test_it_supersedes_the_previous_code(self) -> None:
        """It reissues rather than reading back what was sent, so the code it
        prints is the only live one — printing a stale code would be worse
        than printing none."""
        _register()
        first = _code_from(_run(EMAIL))
        second = _code_from(_run(EMAIL))

        assert first != second
        with pytest.raises(ValidationError):
            services.verify_email_code(EMAIL, first)

    def test_an_address_with_a_real_account_is_told_so(self) -> None:
        """Not an error, and no secret. There is nothing to verify."""
        repo.create_tourist(
            email=EMAIL,
            password_hash=make_password(PASSWORD),
            first_name="Ada",
            last_name="Lovelace",
        )
        assert "already verified" in _run(EMAIL)

    def test_an_address_with_no_registration_is_an_error_not_a_secret(self) -> None:
        """ADR 0021: there is no account to look up and no entry to reissue
        against. The message says to register first rather than inventing a
        registration nobody asked for."""
        with pytest.raises(CommandError, match="no registration in progress"):
            _run("nobody@example.com")

    def test_it_creates_no_account_of_its_own(self) -> None:
        from apps.identity.models import User

        _register()
        _run(EMAIL)
        assert User.objects.count() == 0


class TestOutsideDevelopment:
    @pytest.fixture(autouse=True)
    def _debug_off(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.DEBUG = False

    def test_it_refuses_to_run(self) -> None:
        """The security boundary. Verification proves the registrant can read
        the mailbox they claimed; a command that hands out secrets on demand
        proves nothing at all."""
        _register()
        with pytest.raises(CommandError, match="DEBUG"):
            _run(EMAIL)

    def test_the_refusal_issues_nothing(self) -> None:
        """A refusal that had already superseded the live code would be worse
        than no refusal, because it would look safe while locking the person
        out of the code they were sent."""
        _register()
        before = pending.get(EMAIL)
        with pytest.raises(CommandError):
            _run(EMAIL)
        after = pending.get(EMAIL)

        assert before is not None and after is not None
        assert before.code_hash == after.code_hash
