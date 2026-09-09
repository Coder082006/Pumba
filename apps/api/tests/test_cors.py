"""The browser's half of the contract — SRS §9.1.

**These exist because nothing else can catch this.** Django's test client is not
a browser and does not enforce CORS, so an endpoint can pass every functional
test it has and still be unreachable from the app that calls it: the preflight
answers `200`, the browser declines to send the real request, and the server log
shows an `OPTIONS` with nothing after it. There is no error, no status code and
no failing test — only a screen that says a thing could not be loaded.

That has now happened twice. `CORS_ALLOW_CREDENTIALS` defaulted to `False` and
broke registration, sign-in, password reset and refresh; then §9.1's
`X-Currency` was attached to every request by `apiFetch` and was not in the
allow-list, so choosing a currency broke the entire application at once.

So the tests below are written to fail for a *new* header nobody remembered,
rather than to check the two we know about: `test_every_request_header_the_api_reads_is_allowed`
reads the API's own source for what it consumes. A third header added to the
middleware and forgotten in settings fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.test import Client

from apps.common.middleware import REQUEST_ID_RESPONSE_HEADER

ORIGIN = "http://localhost:3000"

# The API reads custom request headers via `request.META["HTTP_X_..."]`. That
# spelling is Django's own and is what makes this derivable rather than a list
# somebody has to remember to extend.
_META_HEADER = re.compile(r"HTTP_(X_[A-Z0-9_]+)")

# `X-Forwarded-Proto` is set by the reverse proxy, never by a browser, so it
# needs no CORS permission. It is named rather than pattern-matched away: a
# reader should see that it was considered and excluded on purpose.
_SET_BY_INFRASTRUCTURE = {"X_FORWARDED_PROTO"}


def _preflight(client: Client, *, headers: str, method: str = "GET"):
    return client.options(
        "/api/v1/trips",
        HTTP_ORIGIN=ORIGIN,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD=method,
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS=headers,
    )


def _allowed(response) -> set[str]:
    raw = response.headers.get("access-control-allow-headers", "")
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


@pytest.mark.django_db
def test_the_preflight_allows_the_headers_every_request_carries() -> None:
    """`apiFetch` sends all three on a signed-in request; one missing blocks it.

    They are asserted together because that is how the browser evaluates them:
    a preflight that allows two of three is refused exactly like one that
    allows none, and the request never leaves the tab.
    """
    response = _preflight(Client(), headers="authorization,content-type,x-currency")

    assert response.status_code == 200
    assert {"authorization", "content-type", "x-currency"} <= _allowed(response)


@pytest.mark.django_db
def test_every_request_header_the_api_reads_is_allowed() -> None:
    """Derived from the source, so a new header cannot be forgotten here.

    An allow-list of known headers would pass for a header nobody added to it —
    which is the failure this file exists to prevent, not an example of it.
    """
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path(settings.BASE_DIR, "apps").rglob("*.py")
        if "tests" not in path.parts
    )
    consumed = {name for name in _META_HEADER.findall(source) if name not in _SET_BY_INFRASTRUCTURE}
    assert consumed, "The pattern matched nothing; it has stopped tracking what it claims to."

    allowed = _allowed(_preflight(Client(), headers=",".join(consumed)))
    missing = {name for name in consumed if name.replace("_", "-").lower() not in allowed}
    assert not missing, (
        f"{sorted(missing)} reach the API but no browser may send them. "
        "Add them to CORS_ALLOW_HEADERS in config/settings/base.py."
    )


@pytest.mark.django_db
def test_the_request_id_is_readable_by_the_client_that_reports_it() -> None:
    """A cross-origin response hides every header but four unless it says otherwise.

    All three clients read `X-Request-Id` off the response to show in an error.
    Unexposed it is `null` in the browser and correct in every test — and the
    field a tourist quotes to support is the one that finds the log line.
    """
    response = Client().get("/api/v1/config", HTTP_ORIGIN=ORIGIN)

    assert response.headers[REQUEST_ID_RESPONSE_HEADER]
    exposed = {
        part.strip().lower()
        for part in response.headers.get("access-control-expose-headers", "").split(",")
    }
    assert REQUEST_ID_RESPONSE_HEADER.lower() in exposed


def test_the_exposed_name_is_the_one_the_middleware_sends() -> None:
    """Settings cannot import the middleware, so this is what ties them together."""
    assert settings.CORS_EXPOSE_HEADERS == [REQUEST_ID_RESPONSE_HEADER]
