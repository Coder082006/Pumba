"""Print a working email-verification link — local development only.

**Why this exists.** Registration issues a single-use token and hands it to
`get_email_port()`. No provider is selected yet (the brief forbids one), so
that resolves to `ports.fakes.FakeEmail`, which records the message in memory
and surfaces it nowhere. Locally the result is an account that can be created
and can never sign in: `POST /auth/login` answers `EMAIL_NOT_VERIFIED`, and the
link that would clear it went into a fake.

The alternative — having the fake log the message body — was rejected. It puts
a live single-use credential into the log stream of whichever environment is
running without a configured adapter, and a misconfigured production is exactly
the environment where that is worst.

**This issues a real token through the real path** rather than flipping a
column. Marking `email_verified` directly would let this command keep working
while the token path was broken, which is the one thing it must not do.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.common.config import get_setting
from apps.identity import repositories as repo
from apps.identity.models import TokenPurpose


class Command(BaseCommand):
    help = "Print an email-verification link for a registered address (DEBUG only)."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("email")
        parser.add_argument(
            "--base-url",
            default="http://localhost:3000",
            help="Where the tourist web app is served from.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        # Refused outside development, and not as a formality. Issuing a
        # verification token to whoever can reach a shell defeats the only
        # thing verification proves — that the person registering can read
        # the mailbox they claimed.
        if not settings.DEBUG:
            raise CommandError(
                "verification_link is a development aid and refuses to run with DEBUG off. "
                "Configure a real email adapter in PORT_ADAPTERS instead."
            )

        email = options["email"]
        user = repo.find_user_by_email(email)
        if user is None:
            raise CommandError(f"no account for {email!r}")

        if user.email_verified_at is not None:
            self.stdout.write(f"{email} is already verified — sign in normally.")
            return

        raw = repo.issue_one_time_token(
            user,
            TokenPurpose.EMAIL_VERIFICATION,
            ttl=timedelta(hours=int(get_setting("auth.email_verification_ttl_hours"))),
            now=timezone.now(),
        )
        self.stdout.write(f"{options['base_url']}/verify-email?token={raw}")
