"""The development aid that unblocks registration — no SRS section, by design.

`verification_link` exists because no email provider is selected yet, so
`get_email_port()` resolves to `FakeEmail` and the token registration issues is
recorded in memory and surfaced nowhere. Locally that made an account that
could be created and never signed into.

Two properties are worth defending, and one of them is a security boundary:

* it refuses to run with `DEBUG` off, because issuing a verification token to
  whoever can reach a shell defeats the only thing verification proves;
* it goes through `issue_one_time_token`, so a token it prints is one
  `verify_email` accepts. A command that flipped `email_verified_at` directly
  would keep working while the token path was broken, which is exactly the
  failure it must not hide.
"""

from __future__ import annotations

from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.identity import services
from apps.identity.models import User

pytestmark = pytest.mark.django_db

EMAIL = "ada@example.com"
PASSWORD = "Str0ng-Passw0rd!x"


def _register() -> User:
    services.register_tourist(
        email=EMAIL, password=PASSWORD, first_name="Ada", last_name="Lovelace"
    )
    user = User.objects.get(email=EMAIL)
    return user


def _run(*args: str) -> str:
    out = StringIO()
    call_command("verification_link", *args, stdout=out)
    return out.getvalue().strip()


class TestInDevelopment:
    """`settings` is pytest-django's fixture; it restores the value after each
    test, which `override_settings` cannot do as a plain-class decorator."""

    @pytest.fixture(autouse=True)
    def _debug_on(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.DEBUG = True

    def test_it_prints_a_link_the_verify_endpoint_accepts(self) -> None:
        """The property that makes this worth having rather than a shortcut:
        the token is real, so the command cannot pass while the flow it exists
        to exercise is broken."""
        _register()
        token = _run(EMAIL).rsplit("token=", 1)[1]

        dto = services.verify_email(token)
        assert dto.email_verified

    def test_the_link_points_at_the_web_client(self) -> None:
        _register()
        assert _run(EMAIL).startswith("http://localhost:3000/verify-email?token=")

    def test_the_base_url_is_configurable(self) -> None:
        """A different port, or a tunnel, is the ordinary case for testing a
        link on a phone."""
        _register()
        assert _run(EMAIL, "--base-url", "http://192.168.1.4:3000").startswith(
            "http://192.168.1.4:3000/verify-email?token="
        )

    def test_an_already_verified_account_is_told_so_rather_than_reissued(self) -> None:
        """A fresh token for an account that does not need one is a live
        credential nobody asked for."""
        user = _register()
        token = _run(EMAIL).rsplit("token=", 1)[1]
        services.verify_email(token)
        user.refresh_from_db()

        assert "already verified" in _run(EMAIL)

    def test_an_unknown_address_is_an_error_not_a_token(self) -> None:
        with pytest.raises(CommandError, match="no account"):
            _run("nobody@example.com")


class TestOutsideDevelopment:
    @pytest.fixture(autouse=True)
    def _debug_off(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.DEBUG = False

    def test_it_refuses_to_run(self) -> None:
        """The security boundary. Verification proves the registrant can read
        the mailbox they claimed; a command that issues tokens on demand proves
        nothing at all."""
        _register()
        with pytest.raises(CommandError, match="DEBUG"):
            _run(EMAIL)

    def test_the_refusal_issues_no_token(self) -> None:
        """A refusal that had already written the row would be worse than no
        refusal, because it would look safe."""
        from apps.identity.models import OneTimeToken

        _register()
        before = OneTimeToken.objects.count()
        with pytest.raises(CommandError):
            _run(EMAIL)
        assert OneTimeToken.objects.count() == before
