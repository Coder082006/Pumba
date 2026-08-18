"""Request middleware — the lifecycle in SRS §8.3.

Rate limiting and JWT authentication are Phase 2 concerns and are not here
yet; the ordering in `config.settings.base.MIDDLEWARE` leaves their slots.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.common.context import reset_context, set_actor_id, set_request_id

__all__ = ["RequestIdMiddleware", "LocaleMiddleware", "AuditContextMiddleware"]

REQUEST_ID_HEADER = "HTTP_X_REQUEST_ID"
REQUEST_ID_RESPONSE_HEADER = "X-Request-Id"

# Guard against a client sending an unbounded or malformed correlation id.
_MAX_REQUEST_ID_LENGTH = 128


class RequestIdMiddleware:
    """Generate or propagate X-Request-Id and echo it on every response.

    SRS §9.1: "X-Request-Id echoed in every response."
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.META.get(REQUEST_ID_HEADER, "").strip()
        request_id = incoming[:_MAX_REQUEST_ID_LENGTH] if incoming else uuid.uuid4().hex

        request.request_id = request_id  # type: ignore[attr-defined]
        set_request_id(request_id)
        try:
            response = self.get_response(request)
            response[REQUEST_ID_RESPONSE_HEADER] = request_id
            return response
        finally:
            reset_context()


class LocaleMiddleware:
    """Resolve content locale and presentment currency.

    SRS §9.1: `Accept-Language` selects content locale, `X-Currency` selects
    presentment currency. Phase 1 records them on the request; catalogue
    localisation (Phase 3) and FX (Phase 8) consume them.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.locale = (  # type: ignore[attr-defined]
            request.META.get("HTTP_ACCEPT_LANGUAGE", "en").split(",")[0].strip() or "en"
        )
        request.presentment_currency = (  # type: ignore[attr-defined]
            request.META.get("HTTP_X_CURRENCY", "").strip().upper() or None
        )
        return self.get_response(request)


class AuditContextMiddleware:
    """Bind the authenticated actor for audit writes (SRS §8.3, §30.12)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        set_actor_id(user.id if user is not None and user.is_authenticated else None)
        return self.get_response(request)
