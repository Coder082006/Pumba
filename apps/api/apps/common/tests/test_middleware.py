"""Request middleware tests — SRS §8.3, §9.1, §32.6."""

from __future__ import annotations

import contextlib

from django.http import HttpResponse
from django.test import RequestFactory

from apps.common.context import get_request_id
from apps.common.middleware import LocaleMiddleware, RequestIdMiddleware


def _ok(request) -> HttpResponse:
    return HttpResponse("ok")


class TestRequestId:
    def test_generates_an_id_when_absent(self) -> None:
        response = RequestIdMiddleware(_ok)(RequestFactory().get("/"))
        assert response["X-Request-Id"]

    def test_propagates_a_supplied_id(self) -> None:
        """SRS §32.6: support takes an id from a screenshot and reconstructs
        the whole causal chain, so a client-supplied id must survive."""
        request = RequestFactory().get("/", HTTP_X_REQUEST_ID="client-abc")
        assert RequestIdMiddleware(_ok)(request)["X-Request-Id"] == "client-abc"

    def test_truncates_an_oversized_id(self) -> None:
        request = RequestFactory().get("/", HTTP_X_REQUEST_ID="x" * 500)
        assert len(RequestIdMiddleware(_ok)(request)["X-Request-Id"]) == 128

    def test_context_is_cleared_after_the_response(self) -> None:
        """Under ASGI one thread serves many requests; a leaked id would
        mislabel another request's logs."""
        RequestIdMiddleware(_ok)(RequestFactory().get("/"))
        assert get_request_id() is None

    def test_context_is_cleared_even_when_the_view_raises(self) -> None:
        def boom(request):
            raise RuntimeError

        with contextlib.suppress(RuntimeError):
            RequestIdMiddleware(boom)(RequestFactory().get("/"))
        assert get_request_id() is None


class TestLocale:
    def test_defaults_to_english_and_no_currency(self) -> None:
        request = RequestFactory().get("/")
        LocaleMiddleware(_ok)(request)
        assert request.locale == "en"
        assert request.presentment_currency is None

    def test_reads_accept_language_and_x_currency(self) -> None:
        """SRS §9.1: Accept-Language selects locale, X-Currency selects
        presentment currency."""
        request = RequestFactory().get(
            "/", HTTP_ACCEPT_LANGUAGE="de-DE,de;q=0.9", HTTP_X_CURRENCY="tzs"
        )
        LocaleMiddleware(_ok)(request)
        assert request.locale == "de-DE"
        assert request.presentment_currency == "TZS"
