"""Cross-origin access for the browser clients — SRS §30.4, ADR 0008.

**A defect no other test in this repository could have found.** Every auth
endpoint had a passing suite: register returned 201, login returned tokens,
refresh rotated them. None of it worked from a browser, because
`CORS_ALLOW_CREDENTIALS` was never set and django-cors-headers defaults it to
False.

ADR 0008 puts the refresh token in an HttpOnly cookie scoped to
`/api/v1/auth`, so `web-tourist/src/lib/auth.ts` sends every one of those calls
with `credentials: 'include'`. The CORS specification says a browser must
discard the response to a credentialed request unless it carries
`Access-Control-Allow-Credentials: true` — before it becomes a status, before
any body is read. The user sees a bare "Failed to fetch" with no message and no
request id, and the server log shows a perfectly ordinary 201.

Django's test client is not a browser and enforces nothing, which is precisely
why the header has to be asserted rather than assumed. These tests read the
response headers the middleware actually emits.
"""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

from config.settings import base

ORIGIN = "http://localhost:3000"


class TestTheSettingItself:
    def test_credentials_are_allowed(self) -> None:
        """Without this every call in `lib/auth.ts` fails in the browser."""
        assert base.CORS_ALLOW_CREDENTIALS is True

    def test_no_environment_opens_every_origin(self) -> None:
        """The one combination that would make the setting above dangerous.

        `CORS_ALLOW_ALL_ORIGINS` with credentials lets any site on the internet
        make authenticated calls with the user's cookie. django-cors-headers
        refuses the pair, but a reader should not have to know that to see that
        it is not being attempted.
        """
        assert getattr(base, "CORS_ALLOW_ALL_ORIGINS", False) is False

    def test_production_denies_by_default(self) -> None:
        """`prod.py` reads the list from the environment with an empty default,
        so a missing variable denies every origin rather than allowing one. A
        default of `["*"]` fails closed in the other direction — invisibly."""
        from config.settings import prod

        assert isinstance(prod.CORS_ALLOWED_ORIGINS, list)


@pytest.mark.django_db
class TestWhatTheBrowserActuallyReceives:
    """The headers on the wire, not the settings that should produce them."""

    def _register(self, client: APIClient, email: str) -> object:
        return client.post(
            "/api/v1/auth/register",
            {
                "email": email,
                "password": "Str0ng-Passw0rd!x",
                "first_name": "Ada",
                "last_name": "Lovelace",
            },
            format="json",
            HTTP_ORIGIN=ORIGIN,
        )

    def test_the_preflight_allows_credentials(self) -> None:
        client = APIClient()
        response = client.options(
            "/api/v1/auth/register",
            HTTP_ORIGIN=ORIGIN,
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
            HTTP_ACCESS_CONTROL_REQUEST_HEADERS="content-type",
        )
        assert response.headers["access-control-allow-origin"] == ORIGIN
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_the_response_itself_allows_credentials(self) -> None:
        """The preflight is not enough — the browser checks the real response
        too, and a configuration that satisfied one and not the other would
        fail after the request had already been made."""
        response = self._register(APIClient(), "ada@example.com")
        assert response.status_code == 201
        assert response.headers["access-control-allow-credentials"] == "true"

    def test_an_unknown_origin_is_not_echoed(self) -> None:
        """The allowlist still applies. With credentials enabled, echoing an
        arbitrary origin would be the whole vulnerability."""
        client = APIClient()
        response = client.options(
            "/api/v1/auth/register",
            HTTP_ORIGIN="https://not-ours.example",
            HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST",
        )
        assert "access-control-allow-origin" not in response.headers

    def test_the_refresh_cookie_is_set_on_login(self) -> None:
        """The reason credentials are needed at all (ADR 0008). If this stops
        being true the setting above becomes unnecessary rather than wrong, and
        the next reader should find out here."""
        from django.utils import timezone

        from apps.identity import repositories as identity_repo
        from apps.identity.views import REFRESH_COOKIE

        client = APIClient()
        self._register(client, "grace@example.com")

        # Verified through the repository rather than by updating columns: the
        # field names are the module's business, and a test that reaches past
        # its own boundary breaks on a rename that broke nothing.
        user = identity_repo.find_user_by_email("grace@example.com")
        assert user is not None
        identity_repo.mark_email_verified(user, now=timezone.now())

        response = client.post(
            "/api/v1/auth/login",
            {"email": "grace@example.com", "password": "Str0ng-Passw0rd!x"},
            format="json",
            HTTP_ORIGIN=ORIGIN,
        )
        assert response.status_code == 200
        assert REFRESH_COOKIE in response.cookies
