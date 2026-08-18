"""Platform exception hierarchy.

Mirrors SRS §8.7 exactly. Every error carries a stable, machine-readable
`code` from the catalogue in SRS §32.3, an end-user-safe `message`, and a
`retryable` flag that clients use to decide whether to offer a retry.

Pure — no Django, no DRF. The mapping to HTTP lives in `exception_handler`.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "PlatformError",
    "ValidationError",
    "AuthenticationError",
    "PermissionDeniedError",
    "NotFoundError",
    "ConflictError",
    "InventoryUnavailableError",
    "RateLimitedError",
    "ExternalServiceError",
    "InternalError",
]


class PlatformError(Exception):
    """Root of the hierarchy. Never raised directly."""

    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    retryable: bool = False
    default_message: str = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: list[dict[str, Any]] | None = None,
        retryable: bool | None = None,
    ) -> None:
        self.message = message or self.default_message
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable
        self.details = details or []
        super().__init__(self.message)


class ValidationError(PlatformError):
    """Semantic validation failure — SRS §8.6 tier 2. Maps to 422, not 400."""

    status_code = 422
    code = "VALIDATION_ERROR"
    default_message = "The request could not be processed."


class AuthenticationError(PlatformError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    default_message = "Authentication is required."


class PermissionDeniedError(PlatformError):
    """Role forbids the operation.

    SRS §32.2: never used for ownership failures — those return 404 so the
    API does not disclose the existence of another principal's resource.
    """

    status_code = 403
    code = "PERMISSION_DENIED"
    default_message = "You do not have permission to perform this action."


class NotFoundError(PlatformError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "The requested resource was not found."


class ConflictError(PlatformError):
    """State conflict: illegal transition, version conflict, expired quote/hold."""

    status_code = 409
    code = "CONFLICT"
    default_message = "The request conflicts with the current state of the resource."


class InventoryUnavailableError(ConflictError):
    """Capacity is gone.

    SRS §32.5 singles this out: it is one of the conflicts that costs money,
    so `details` carries per-item alternatives where the catalogue offers them.
    """

    code = "INVENTORY_UNAVAILABLE"
    default_message = "The selected item is no longer available."


class RateLimitedError(PlatformError):
    status_code = 429
    code = "RATE_LIMITED"
    retryable = True
    default_message = "Too many requests. Please retry shortly."

    def __init__(self, *args: Any, retry_after: int | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ExternalServiceError(PlatformError):
    """An external port failed. Retryable by default — the caller may try again."""

    status_code = 502
    code = "EXTERNAL_SERVICE_ERROR"
    retryable = True
    default_message = "An upstream service is currently unavailable."


class InternalError(PlatformError):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "An unexpected error occurred."
