"""The SMTP email adapter — SRS §19.4, §34.8, rule 13.

The behaviours worth defending are the ones that only show up in production:
what happens when the address is refused, when the server is unreachable, and
whether the message that leaves is one a spam filter will accept. A test that
only asserted "it sent" would pass while every verification code went to junk.

`EMAIL_BACKEND` is swapped for Django's in-memory one, so these assert on the
message that *would* have gone out. Nothing here opens a socket.
"""

from __future__ import annotations

import smtplib
from unittest import mock

import pytest
from django.core import mail

from apps.notify.adapters.smtp import SmtpEmailAdapter
from ports.notification import DeliveryStatus

LOCMEM = "django.core.mail.backends.locmem.EmailBackend"


@pytest.fixture(autouse=True)
def _in_memory(settings) -> None:  # type: ignore[no-untyped-def]
    settings.EMAIL_BACKEND = LOCMEM
    settings.DEFAULT_FROM_EMAIL = "Pumba <no-reply@example.test>"
    mail.outbox.clear()


def _send(**overrides: object) -> object:
    payload: dict[str, object] = {
        "to": "ada@example.test",
        "subject": "Verify your email address",
        "html_body": "<p>Your verification code is <code>418209</code>.</p>",
        "template_id": "email_verification",
    }
    payload.update(overrides)
    return SmtpEmailAdapter().send(**payload)  # type: ignore[arg-type]


class TestASuccessfulSend:
    def test_it_reports_accepted(self) -> None:
        assert _send().status is DeliveryStatus.ACCEPTED  # type: ignore[attr-defined]

    def test_the_message_reaches_the_addressee(self) -> None:
        _send()
        assert [m.to for m in mail.outbox] == [["ada@example.test"]]

    def test_it_carries_a_message_id(self) -> None:
        """§19.4 wants a provider reference against every delivery. SMTP
        returns none, so the `Message-ID` Django generated is the closest true
        equivalent — it is the handle the receiving server logs."""
        result = _send()
        assert result.provider_message_id  # type: ignore[attr-defined]

    def test_it_sends_from_the_configured_address(self) -> None:
        _send()
        assert mail.outbox[0].from_email == "Pumba <no-reply@example.test>"


class TestTheMessageShape:
    def test_it_carries_a_plain_text_part(self) -> None:
        """Not decoration. A message with only an HTML alternative is scored
        as spam by most filters, and a verification code in the junk folder is
        the same as one never sent."""
        _send()
        assert mail.outbox[0].body.strip()

    def test_the_text_part_carries_the_code(self) -> None:
        """The tags are stripped, not the content. A text fallback that lost
        the code would be worse than none — it would look like a working
        message and be useless."""
        _send()
        assert "418209" in mail.outbox[0].body

    def test_the_html_is_attached_as_an_alternative(self) -> None:
        _send()
        alternatives = mail.outbox[0].alternatives
        assert alternatives is not None
        assert alternatives[0][1] == "text/html"
        assert "<code>418209</code>" in alternatives[0][0]

    def test_a_supplied_text_body_is_preferred_over_stripped_html(self) -> None:
        """The caller knows better than `strip_tags` what the plain version
        should read like; the fallback exists for callers that have none."""
        _send(text_body="Your code is 418209.")
        assert mail.outbox[0].body == "Your code is 418209."


class TestFailuresAreReportedNotRaised:
    """§19.4 needs the per-recipient outcome, and the caller sends from a
    `transaction.on_commit` hook — an exception there surfaces after the
    response has already gone, where nothing can act on it."""

    def test_a_refused_recipient_is_rejected(self) -> None:
        """Distinct from a transport fault on purpose: the address is wrong,
        retrying will not fix it, and a caller should stop rather than queue."""
        with mock.patch.object(
            mail.EmailMultiAlternatives,
            "send",
            side_effect=smtplib.SMTPRecipientsRefused({}),
        ):
            result = _send()
        assert result.status is DeliveryStatus.REJECTED  # type: ignore[attr-defined]

    def test_a_transport_failure_is_failed(self) -> None:
        with mock.patch.object(
            mail.EmailMultiAlternatives,
            "send",
            side_effect=smtplib.SMTPAuthenticationError(535, b"bad credentials"),
        ):
            result = _send()
        assert result.status is DeliveryStatus.FAILED  # type: ignore[attr-defined]

    def test_a_dead_socket_is_failed_rather_than_an_exception(self) -> None:
        """`OSError` is what an unreachable host raises before SMTP is even
        spoken, and it is not an `SMTPException`."""
        with mock.patch.object(
            mail.EmailMultiAlternatives, "send", side_effect=OSError("connection refused")
        ):
            result = _send()
        assert result.status is DeliveryStatus.FAILED  # type: ignore[attr-defined]

    def test_a_backend_that_delivers_nothing_is_failed(self) -> None:
        """Django reports zero delivered without raising when a backend
        declines quietly. Reporting ACCEPTED would record a successful send of
        a message that does not exist."""
        with mock.patch.object(mail.EmailMultiAlternatives, "send", return_value=0):
            result = _send()
        assert result.status is DeliveryStatus.FAILED  # type: ignore[attr-defined]

    def test_no_failure_escapes_as_an_exception(self) -> None:
        for boom in (
            smtplib.SMTPRecipientsRefused({}),
            smtplib.SMTPServerDisconnected("gone"),
            OSError("refused"),
        ):
            with mock.patch.object(mail.EmailMultiAlternatives, "send", side_effect=boom):
                assert _send() is not None


class TestItSatisfiesThePort:
    def test_the_registry_can_resolve_it(self, settings) -> None:  # type: ignore[no-untyped-def]
        """The wiring, not just the class. A dotted path that does not import
        is a failure at first send — long after deployment."""
        from apps.common.ports_registry import get_email_port, reset_ports

        settings.PORT_ADAPTERS = {"email": "apps.notify.adapters.smtp.SmtpEmailAdapter"}
        reset_ports()
        try:
            assert isinstance(get_email_port(), SmtpEmailAdapter)
        finally:
            reset_ports()

    def test_the_default_is_still_the_fake(self) -> None:
        """Sending is opt-in. The failure mode of a missing variable must be
        "no mail", never "mail from a half-configured host"."""
        from config.settings import base

        assert base.PORT_ADAPTERS["email"] == "fake"
