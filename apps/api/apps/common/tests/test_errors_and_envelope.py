"""Error hierarchy and envelope tests — SRS §8.7, §9.2, §32."""

from __future__ import annotations

import json

import pytest
from rest_framework import exceptions as drf_exc
from rest_framework.test import APIClient, APIRequestFactory

from apps.common.context import set_request_id
from apps.common.envelope import error_envelope, success_envelope
from apps.common.errors import (
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    InternalError,
    InventoryUnavailableError,
    NotFoundError,
    PermissionDeniedError,
    PlatformError,
    RateLimitedError,
    ValidationError,
)
from apps.common.exception_handler import platform_exception_handler


class TestHierarchyStatusMapping:
    """SRS §8.7 maps each class to exactly one HTTP status."""

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (ValidationError(), 422),
            (AuthenticationError(), 401),
            (PermissionDeniedError(), 403),
            (NotFoundError(), 404),
            (ConflictError(), 409),
            (InventoryUnavailableError(), 409),
            (RateLimitedError(), 429),
            (ExternalServiceError(), 502),
            (InternalError(), 500),
        ],
    )
    def test_status_codes(self, error: PlatformError, status: int) -> None:
        assert error.status_code == status

    def test_validation_error_is_422_not_400(self) -> None:
        """SRS §32.2 reserves 400 for malformed requests that fail *before*
        validation; semantic failures are 422."""
        assert ValidationError().status_code == 422

    def test_inventory_unavailable_is_a_conflict_subtype(self) -> None:
        assert isinstance(InventoryUnavailableError(), ConflictError)
        assert InventoryUnavailableError().code == "INVENTORY_UNAVAILABLE"

    def test_external_and_rate_limit_errors_are_retryable(self) -> None:
        assert ExternalServiceError().retryable
        assert RateLimitedError().retryable

    def test_conflicts_are_not_retryable_by_default(self) -> None:
        """SRS §32.3 marks the booking-path conflicts "No" — a blind retry
        would just fail again, or double-charge."""
        assert not ConflictError().retryable
        assert not InventoryUnavailableError().retryable

    def test_code_and_message_are_overridable(self) -> None:
        error = ConflictError("Quote has expired.", code="QUOTE_EXPIRED")
        assert error.code == "QUOTE_EXPIRED"
        assert error.message == "Quote has expired."


class TestEnvelopeShape:
    def test_success_envelope(self) -> None:
        set_request_id("req-123")
        assert success_envelope({"id": 1}) == {
            "data": {"id": 1},
            "meta": {"request_id": "req-123"},
        }

    def test_success_envelope_merges_meta(self) -> None:
        set_request_id("req-123")
        envelope = success_envelope([], {"next_cursor": "abc"})
        assert envelope["meta"] == {"request_id": "req-123", "next_cursor": "abc"}

    def test_error_envelope_matches_srs_9_2(self) -> None:
        set_request_id("0f0a")
        envelope = error_envelope(
            "INVENTORY_UNAVAILABLE",
            "This departure is no longer available.",
            details=[{"field": "items[1].departure_id", "issue": "SOLD_OUT"}],
        )
        assert envelope == {
            "error": {
                "code": "INVENTORY_UNAVAILABLE",
                "message": "This departure is no longer available.",
                "details": [{"field": "items[1].departure_id", "issue": "SOLD_OUT"}],
                "request_id": "0f0a",
                "retryable": False,
            }
        }


class TestExceptionHandler:
    def _handle(self, exc: Exception):
        request = APIRequestFactory().get("/api/v1/health")
        return platform_exception_handler(exc, {"request": request, "view": None})

    def test_platform_error_becomes_the_envelope(self) -> None:
        set_request_id("rid")
        response = self._handle(ConflictError("Quote expired.", code="QUOTE_EXPIRED"))
        assert response is not None
        assert response.status_code == 409
        assert response.data["error"]["code"] == "QUOTE_EXPIRED"
        assert response.data["error"]["request_id"] == "rid"

    def test_django_http404_becomes_not_found(self) -> None:
        from django.http import Http404

        response = self._handle(Http404())
        assert response is not None and response.status_code == 404
        assert response.data["error"]["code"] == "NOT_FOUND"

    def test_drf_validation_error_is_flattened_to_details(self) -> None:
        """SRS §9.2: details[].field uses JSON-pointer-like paths."""
        response = self._handle(
            drf_exc.ValidationError({"items": [{"departure_id": ["SOLD_OUT"]}]})
        )
        assert response is not None and response.status_code == 422
        assert {"field": "items[0].departure_id", "issue": "SOLD_OUT"} in response.data["error"][
            "details"
        ]

    def test_throttled_sets_retry_after(self) -> None:
        """SRS §32.2: clients back off using Retry-After."""
        response = self._handle(drf_exc.Throttled(wait=30))
        assert response is not None and response.status_code == 429
        assert response["Retry-After"] == "30"
        assert response.data["error"]["retryable"] is True

    def test_unexpected_exception_does_not_leak_internals(self) -> None:
        response = self._handle(RuntimeError("psycopg: password authentication failed for user"))
        assert response is not None and response.status_code == 500
        body = str(response.data)
        assert "password" not in body, "internal detail leaked to the client"
        assert response.data["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.django_db
class TestTheEnvelopeIsAppliedStructurally:
    """`EnvelopeJSONRenderer` existed, was unit-tested, and was wired to
    nothing.

    Its own docstring said it "wraps every response body", and DRF had no
    `DEFAULT_RENDERER_CLASSES`, so the only thing enveloping responses was each
    view remembering to call `success_envelope`. Every catalogue view did.
    `/health` did not, and returned a bare object — which both web clients
    unwrap to `.data`, getting `undefined`. In the console that surfaced as
    react-query's "Query data cannot be undefined".

    The class of defect is the one this project keeps finding: a mechanism that
    exists, is tested in isolation, is documented as global, and is connected
    to nothing. So the assertion below is made against a *rendered* response
    rather than against the renderer, because testing the renderer directly is
    what passed while the endpoint was broken.
    """

    def test_health_is_enveloped_like_everything_else(self) -> None:
        response = APIClient().get("/api/v1/health")
        assert response.status_code == 200

        body = json.loads(response.content)
        assert set(body) == {"data", "meta"}, body
        assert body["data"]["status"] == "ok"
        assert "request_id" in body["meta"]

    def test_a_view_that_envelopes_itself_is_not_wrapped_twice(self) -> None:
        """`ConfigView` calls `success_envelope` directly. The renderer must
        pass it through, or every such view would serve `data.data`."""
        body = json.loads(APIClient().get("/api/v1/config").content)
        assert set(body) == {"data", "meta"}
        assert "data" not in body["data"]
        assert body["data"]["min_supported_version"]

    def test_the_renderer_is_actually_configured(self) -> None:
        """Guards the wiring itself, not the class.

        Both tests above would still pass if a view were changed to envelope
        by hand, which would quietly restore the "remember to call it" rule
        this replaces.
        """
        from django.conf import settings

        assert (
            "apps.common.envelope.EnvelopeJSONRenderer"
            in (settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"])
        )
