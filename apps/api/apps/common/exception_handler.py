"""The single DRF exception handler — SRS §32.4.

Converts the §8.7 exception hierarchy into the §9.2 error envelope, attaches
the request id, and logs at the level the SRS prescribes:

    INFO     expected client errors
    WARNING  conflicts and external-dependency failures
    ERROR    unhandled exceptions, with a stack trace to the log store only

Unhandled exceptions inside a transaction always roll back; there is no
partial write path.
"""

from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.http import Http404
from rest_framework import exceptions as drf_exc
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_handler

from apps.common.envelope import error_envelope
from apps.common.errors import (
    AuthenticationError,
    InternalError,
    NotFoundError,
    PermissionDeniedError,
    PlatformError,
    RateLimitedError,
    ValidationError,
)

logger = logging.getLogger(__name__)

__all__ = ["platform_exception_handler"]


def _log_level_for(status_code: int) -> int:
    if status_code >= 500:
        return logging.ERROR
    if status_code in (409, 429):
        return logging.WARNING
    return logging.INFO


def _flatten_drf_detail(detail: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Turn DRF's nested error structure into SRS §9.2 `details[]`.

    `field` uses JSON-pointer-like paths into the request body, e.g.
    `items[1].departure_id`.
    """
    out: list[dict[str, Any]] = []

    if isinstance(detail, dict):
        for key, value in detail.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_flatten_drf_detail(value, path))
    elif isinstance(detail, list):
        for index, value in enumerate(detail):
            # A list of plain messages belongs to `prefix` itself; a list of
            # structures is an indexed collection.
            if isinstance(value, dict | list):
                out.extend(_flatten_drf_detail(value, f"{prefix}[{index}]"))
            else:
                out.append({"field": prefix, "issue": str(value)})
    else:
        out.append({"field": prefix, "issue": str(detail)})

    return out


def _translate(exc: Exception) -> PlatformError:
    """Map framework and Django exceptions onto the platform hierarchy."""
    if isinstance(exc, PlatformError):
        return exc

    if isinstance(exc, Http404):
        return NotFoundError()

    if isinstance(exc, DjangoPermissionDenied):
        return PermissionDeniedError()

    if isinstance(exc, drf_exc.ValidationError):
        return ValidationError(details=_flatten_drf_detail(exc.detail))

    if isinstance(exc, DjangoValidationError):
        # §8.6 tier 2 reaching HTTP. `Model.full_clean()` is the one validation
        # tier that is *not* a serializer — it is what the console, the seed
        # loader and the admin API all share — so its failures must land as 422
        # with the field named, exactly like a serializer's. Untranslated they
        # fall through to `InternalError`, and an administrator typing an
        # invalid timezone would see a 500 with nothing to act on.
        # `message_dict` is only available when the error carries field
        # mapping; a model-wide `ValidationError` — a failed CHECK-mirroring
        # `clean()` — has messages and no fields, and is reported against the
        # body rather than invented onto one.
        if hasattr(exc, "error_dict"):
            return ValidationError(details=_flatten_drf_detail(exc.message_dict))
        return ValidationError(details=_flatten_drf_detail(list(exc.messages)))

    if isinstance(exc, drf_exc.NotAuthenticated | drf_exc.AuthenticationFailed):
        return AuthenticationError(str(exc.detail))

    if isinstance(exc, drf_exc.PermissionDenied):
        return PermissionDeniedError(str(exc.detail))

    if isinstance(exc, drf_exc.NotFound):
        return NotFoundError(str(exc.detail))

    if isinstance(exc, drf_exc.Throttled):
        # `wait` is absent from the DRF stubs but is part of the public API.
        wait = getattr(exc, "wait", None)
        return RateLimitedError(
            str(exc.detail), retry_after=int(wait) if wait is not None else None
        )

    if isinstance(exc, drf_exc.MethodNotAllowed | drf_exc.UnsupportedMediaType):
        err = ValidationError(str(exc.detail))
        err.status_code = exc.status_code
        err.code = "METHOD_NOT_ALLOWED"
        return err

    return InternalError()


def platform_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    # Let DRF handle anything it recognises that we have not translated, so
    # that unexpected framework behaviour is never silently swallowed.
    error = _translate(exc)

    is_unhandled = isinstance(error, InternalError) and not isinstance(exc, PlatformError)

    logger.log(
        _log_level_for(error.status_code),
        "request_failed",
        extra={
            "error_code": error.code,
            "status_code": error.status_code,
            "exception_class": type(exc).__name__,
        },
        # Stack traces go to the log store only, never to the client.
        exc_info=is_unhandled,
    )

    if is_unhandled:
        # Preserve DRF's own handling for exceptions it owns and we did not map.
        fallback = drf_default_handler(exc, context)
        if fallback is not None:
            return fallback

    headers: dict[str, str] = {}
    if isinstance(error, RateLimitedError) and error.retry_after is not None:
        headers["Retry-After"] = str(error.retry_after)

    return Response(
        error_envelope(
            error.code,
            error.message,
            details=error.details,
            retryable=error.retryable,
        ),
        status=error.status_code,
        headers=headers or None,
    )
