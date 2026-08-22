"""Building the actor an administrative test needs — and nothing more.

§27.8 and §41.12 both start from "an administrator", and getting one is not
one call. §30.2 makes TOTP *mandatory* for every administrative role, and
`identity.services.authenticate` enforces that at the door: a CATALOGUE_ADMIN
who has never enrolled cannot obtain a token at all, whatever their password
is. So the sequence below is the real one — register, verify the address, hold
the role, enrol in TOTP, confirm it, and only then sign in with a code.

Kept in one place because two suites need it and because it is exactly the
sequence a real administrator is onboarded through. A test that faked a
principal instead would pass while proving that a principal nobody could
actually obtain is permitted to do something.

It lives under `tests/` rather than under `apps/catalogue/tests/` for a reason
the import contracts enforce: §6.4 forbids `catalogue` from importing
`identity`, and `apps.catalogue.tests` is inside `apps.catalogue`. A test that
spans the two modules belongs outside both.
"""

from __future__ import annotations

import uuid

from django.utils import timezone
from rest_framework.test import APIClient

from apps.common.authz import Role
from apps.identity import repositories as identity_repo
from apps.identity import services as identity_services
from apps.identity.domain.mfa import base32_to_secret, totp_code

__all__ = ["PASSWORD", "signed_in_as"]

PASSWORD = "correct-horse-battery-staple-42"


def signed_in_as(*role: Role, email: str | None = None) -> APIClient:
    """A verified account holding `role`, signed in, with MFA satisfied.

    Passing no role gives a plain tourist — which is the useful case for
    asserting that an ordinary principal is refused an administrative
    endpoint.

    MFA is enrolled and satisfied only where the roles oblige it (§30.2). A
    tourist who was made to enrol would not be the principal the refusal tests
    are about.
    """
    address = email or f"actor-{uuid.uuid4().hex[:10]}@example.com"
    identity_services.register_tourist(
        email=address, password=PASSWORD, first_name="Test", last_name="Actor"
    )
    user = identity_repo.find_user_by_email(address)
    assert user is not None

    identity_repo.mark_email_verified(user, now=timezone.now())
    for granted in role:
        identity_repo.grant_role(user, granted)
    user.refresh_from_db()

    code: str | None = None
    principal = identity_services.get_principal(user_id=user.pk)
    assert principal is not None
    if principal.mfa_required:
        secret = base32_to_secret(
            identity_services.begin_mfa_enrolment(principal=principal)["secret"]
        )
        # The same code twice: enrolment consumes one and the sign-in that
        # follows needs another, and nothing in §30.2 makes a code single-use.
        # If that ever changes, this is where it will fail, loudly.
        code = totp_code(secret, at=timezone.now())
        identity_services.confirm_mfa_enrolment(principal=principal, code=code)

    result = identity_services.authenticate(email=address, password=PASSWORD, mfa_code=code)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {result.tokens.access_token}")
    return client
