"""SMTP, for `ports.notification.EmailPort` — SRS §19.4, §34.8.

**Why SMTP rather than a provider SDK.** §34.8 puts all three channels behind
ports because "SMS providers in particular are frequently changed", and the
same argument applies here in reverse: SMTP is the one interface every provider
speaks. Gmail, a self-hosted server, SendGrid, Mailgun and SES all expose one,
so this single adapter reaches any of them with a change of four environment
variables and no change to the manifest. Rule 13 confines vendor SDKs to
`adapters/`; the cheapest way to honour it is not to need one.

**Django's mail layer does the transport.** It is not a vendor SDK — it is the
framework already in the stack — and it owns TLS negotiation, connection reuse
and header encoding, none of which is worth reimplementing on `smtplib`. It
also means a test can swap `EMAIL_BACKEND` for the in-memory one and assert on
what would have been sent, which is how the tests beside this file work.

**Nothing raises.** §19.4 requires per-recipient delivery auditing, so a
refused recipient comes back as a `DeliveryResult` with `REJECTED` and a
transport failure as `FAILED`. An exception here would lose the outcome, and —
because `_send_verification_email` sends from a `transaction.on_commit` hook —
would surface as an unhandled error after the response had already gone.

**No credential is logged.** The error string from `smtplib` can carry the
username; it is recorded, so an operator can diagnose a bad password, but the
password itself never enters a log line because it is never in the message.
"""

from __future__ import annotations

import logging
import smtplib
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import strip_tags

from ports.notification import DeliveryResult, DeliveryStatus

__all__ = ["SmtpEmailAdapter"]

logger = logging.getLogger(__name__)


class SmtpEmailAdapter:
    """Send through whatever SMTP server `EMAIL_HOST` names."""

    def send(
        self,
        *,
        to: str,
        subject: str,
        html_body: str,
        text_body: str | None = None,
        template_id: str | None = None,
        context: dict[str, str] | None = None,
    ) -> DeliveryResult:
        # `template_id` and `context` are the provider-template path — SES and
        # SendGrid both render server-side from an id. SMTP has no such notion,
        # so the fully-rendered `html_body` the caller already built is what
        # goes on the wire. They stay in the signature because the port
        # declares them and a provider adapter will use them.
        message = EmailMultiAlternatives(
            subject=subject,
            # A text part, always. A message with only an HTML alternative is
            # scored as spam by most filters, and a verification code that
            # lands in junk is the same as one never sent.
            body=text_body or strip_tags(html_body),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to],
        )
        message.attach_alternative(html_body, "text/html")

        try:
            # `fail_silently=False`: the exception is what tells the difference
            # between a refused address and a dead connection, and both are
            # reported rather than swallowed.
            sent = message.send(fail_silently=False)
        except smtplib.SMTPRecipientsRefused as refused:
            # The address itself is wrong. Not retryable, and distinct from a
            # transport fault so a caller can stop rather than queue.
            logger.warning("email_recipient_refused", extra={"template_id": template_id})
            return DeliveryResult(status=DeliveryStatus.REJECTED, error=str(refused))
        except (smtplib.SMTPException, OSError) as failure:
            # Authentication, TLS, DNS, a closed socket. Retryable in
            # principle, which is why it is FAILED rather than REJECTED.
            logger.error("email_send_failed", extra={"template_id": template_id})
            return DeliveryResult(status=DeliveryStatus.FAILED, error=str(failure))

        if not sent:
            # Django reports zero delivered without raising when a backend
            # declines quietly. Reporting ACCEPTED here would record a
            # successful send of a message that does not exist.
            return DeliveryResult(
                status=DeliveryStatus.FAILED, error="the mail backend accepted no recipients"
            )

        return DeliveryResult(
            status=DeliveryStatus.ACCEPTED, provider_message_id=_message_id(message)
        )


def _message_id(message: Any) -> str | None:
    """The `Message-ID` header, which is the only handle SMTP gives back.

    §19.4 wants a provider reference against each delivery. An API adapter gets
    one in the response; SMTP does not, so the header Django generated is the
    closest true equivalent — it is what appears in the receiving server's logs.
    """
    try:
        return str(message.message()["Message-ID"])
    except Exception:  # pragma: no cover - a malformed message cannot reach here
        return None
