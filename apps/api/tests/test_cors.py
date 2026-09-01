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

    def test_production_denies_every_origin_by_default(self) -> None:
        """`prod.py` reads the list from the environment with an empty default,
        so a missing variable denies every origin rather than allowing one. A
        default of `["*"]` would fail *open*, and invisibly — which is the
        combination that matters now that credentials are allowed.

        **The environment is supplied here rather than inherited.** `prod.py`
        reads `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS` with no default,
        on purpose — it must fail fast rather than boot on a fallback secret.
        The first version of this test imported the module bare, which worked
        under `docker compose` because the `api` service sets both, and failed
        on CI where the backend job sets neither. That is the difference
        between two surfaces being used as an explanation instead of a
        hypothesis, and it turned the build red for three commits.

        So the two required variables are injected for the length of the
        import, and `CORS_ALLOWED_ORIGINS` is removed — which is the condition
        actually under test.
        """
        import importlib
        import os
        from unittest import mock

        with mock.patch.dict(
            os.environ,
            {
                "DJANGO_SECRET_KEY": "supplied-only-for-this-import",
                "DJANGO_ALLOWED_HOSTS": "example.test",
            },
        ):
            # Inside the patch, so the original value is restored on exit.
            os.environ.pop("CORS_ALLOWED_ORIGINS", None)
            prod = importlib.reload(importlib.import_module("config.settings.prod"))

        assert prod.CORS_ALLOWED_ORIGINS == []
        assert prod.DEBUG is False


@pytest.mark.django_db
class TestWhatTheBrowserActuallyReceives:
    """The headers on the wire, not the settings that should produce them.

    **The allow-list is set here rather than inherited**, and for the reason
    the class above records. `CORS_ALLOWED_ORIGINS` lives in `dev.py` and
    `prod.py`; `base.py` and `ci.py` never define it. So these tests passed
    under `docker compose`, which runs `config.settings.dev`, and failed on CI,
    which runs `config.settings.ci` with no allow-list at all — no origin
    allowed, no headers emitted, three assertions red.

    Declaring it makes the tests say what they depend on and pass under any
    settings module. It also fixes `test_an_unknown_origin_is_not_echoed`,
    which passed on CI for the wrong reason: with no allow-list, *nothing* is
    echoed, so it could not have caught an over-permissive one.
    """

    @pytest.fixture(autouse=True)
    def _allow_the_web_client(self, settings) -> None:  # type: ignore[no-untyped-def]
        settings.CORS_ALLOWED_ORIGINS = [ORIGIN]

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
