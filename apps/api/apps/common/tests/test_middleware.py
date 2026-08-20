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


class TestPresentmentCurrencyIsValidated:
    """§9.1's header arrives from a public, unauthenticated endpoint."""

    def test_a_malformed_currency_is_ignored_rather_than_rejected(self) -> None:
        """A display preference the client got wrong must not take down a
        catalogue read. The page falls back to the listing currency."""
        request = RequestFactory().get("/", HTTP_X_CURRENCY="<script>alert(1)</script>")
        LocaleMiddleware(_ok)(request)
        assert request.presentment_currency is None

    def test_a_two_letter_code_is_ignored(self) -> None:
        request = RequestFactory().get("/", HTTP_X_CURRENCY="US")
        LocaleMiddleware(_ok)(request)
        assert request.presentment_currency is None

    def test_a_code_with_digits_is_ignored(self) -> None:
        request = RequestFactory().get("/", HTTP_X_CURRENCY="US1")
        LocaleMiddleware(_ok)(request)
        assert request.presentment_currency is None

    def test_an_unrecognised_but_wellformed_code_is_kept(self) -> None:
        """Structural only. A currency the register gained last year should
        reach the rate port and come back `None`, not be rejected by a stale
        list compiled into the middleware."""
        request = RequestFactory().get("/", HTTP_X_CURRENCY="xbt")
        LocaleMiddleware(_ok)(request)
        assert request.presentment_currency == "XBT"

    def test_whitespace_is_trimmed(self) -> None:
        request = RequestFactory().get("/", HTTP_X_CURRENCY="  eur  ")
        LocaleMiddleware(_ok)(request)
        assert request.presentment_currency == "EUR"


class TestVary:
    """Without this, a cache serves one tourist's currency to the next."""

    def test_both_negotiation_headers_are_declared(self) -> None:
        response = LocaleMiddleware(_ok)(RequestFactory().get("/"))
        vary = {v.strip().lower() for v in response.headers["Vary"].split(",")}
        assert {"accept-language", "x-currency"} <= vary

    def test_it_is_declared_even_when_no_header_was_sent(self) -> None:
        """The response for a request without `X-Currency` is still specific to
        not having one, and must not be reused for a request that has it."""
        response = LocaleMiddleware(_ok)(RequestFactory().get("/"))
        assert "X-Currency" in response.headers["Vary"]

    def test_an_existing_vary_is_extended_not_replaced(self) -> None:
        def with_vary(request) -> HttpResponse:
            response = HttpResponse("ok")
            response.headers["Vary"] = "Origin"
            return response

        response = LocaleMiddleware(with_vary)(RequestFactory().get("/"))
        vary = {v.strip().lower() for v in response.headers["Vary"].split(",")}
        assert {"origin", "accept-language", "x-currency"} <= vary
