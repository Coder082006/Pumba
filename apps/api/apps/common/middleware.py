"""Request middleware — the lifecycle in SRS §8.3."""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers

from apps.common.context import reset_context, set_actor_id, set_request_id

logger = logging.getLogger(__name__)

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
    presentment currency. Catalogue reads consume both from Phase 3; §18.4's
    authoritative FX is a different mechanism entirely and does not read this.

    Two things this does beyond recording the headers, both of which are about
    what happens *downstream* of the request:

    **A malformed currency is ignored, not rejected.** `X-Currency: <script>`
    is a display preference the client got wrong, and failing a public
    catalogue read over it turns a cosmetic mistake into an outage. The
    response says what was actually used, so nothing is silent.

    **Both headers go into `Vary`.** Without it, any cache in front of this —
    a CDN, a reverse proxy, `django.middleware.cache` — will serve the first
    tourist's currency and language to everyone behind the same URL. That is
    the kind of defect that only appears under a cache, in production, as
    prices in a currency nobody asked for.
    """

    #: ISO 4217 alpha-3, structurally. The full register is not shipped here
    #: for the same reason `catalogue.domain.hierarchy` does not ship it: it
    #: changes, and an unknown-but-well-formed code should reach the rate port
    #: and come back `None` rather than be rejected by a stale list.
    _CURRENCY = re.compile(r"^[A-Za-z]{3}$")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.locale = (  # type: ignore[attr-defined]
            request.META.get("HTTP_ACCEPT_LANGUAGE", "en").split(",")[0].strip() or "en"
        )
        request.presentment_currency = self._currency(request)  # type: ignore[attr-defined]

        response = self.get_response(request)
        patch_vary_headers(response, ("Accept-Language", "X-Currency"))
        return response

    def _currency(self, request: HttpRequest) -> str | None:
        raw = str(request.META.get("HTTP_X_CURRENCY", "")).strip()
        if not raw:
            return None
        if not self._CURRENCY.match(raw):
            logger.info(
                "presentment_currency_ignored",
                extra={"reason": "malformed", "length": len(raw)},
            )
            return None
        return raw.upper()


class AuditContextMiddleware:
    """Bind the authenticated actor for audit writes (SRS §8.3, §30.12)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        user = getattr(request, "user", None)
        set_actor_id(user.id if user is not None and user.is_authenticated else None)
        return self.get_response(request)
