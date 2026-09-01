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

**This issues real secrets through the real path** rather than flipping a
column. It calls `resend_verification` and reads what the fake email port
recorded, so a code it prints is one the endpoint accepts — and the command
cannot keep working while the flow it exists to exercise is broken.

It prints **both** the six-digit code and the link, because the verification
email carries both and they are spent on different screens — the code in
§24.3's dialog, the link at `/verify-email`. Printing one would leave half the
flow untestable, which is the situation this command exists to end.

**It needs a registration in progress** (ADR 0021). Nothing is written to the
database until a code is verified, so there is no account to look up — the
details live in `pending` for the length of the TTL, and this reissues against
them. Register first; if the entry has expired, register again.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.common.config import get_setting
from apps.identity import pending, services
from apps.identity import repositories as repo


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
        entry = pending.get(email)
        if entry is None:
            if repo.find_user_by_email(email) is not None:
                self.stdout.write(f"{email} is already verified — sign in normally.")
                return
            raise CommandError(
                f"no registration in progress for {email!r} — register first, or the "
                "code has already expired"
            )

        # Reissued through the same staging call the API makes, so the printed
        # secrets are ones the endpoints accept — and the command cannot keep
        # working while the flow it exists to exercise is broken.
        #
        # The service hands them back rather than the command reading them out
        # of the email port: only `FakeEmail` records what it was given, so
        # scraping it would make this work *only* while no real provider was
        # configured, which is the opposite of useful.
        issued = services.reissue_verification(email)
        if issued is None:  # pragma: no cover - guarded above
            raise CommandError(f"nothing to reissue for {email!r}")
        code, token = issued

        minutes = int(get_setting("auth.email_verification_code_ttl_minutes"))
        self.stdout.write(f"code: {code}   (expires in {minutes} minutes)")
        self.stdout.write(f"link: {options['base_url']}/verify-email?token={token}")
