"""Response envelope — SRS §9.2.

Success:
    {"data": {...}, "meta": {"request_id": "...", "next_cursor": "..."}}

Error:
    {"error": {"code", "message", "details", "request_id", "retryable"}}

Applied by a DRF renderer so no view has to remember it. A view returns its
resource; the renderer wraps it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from rest_framework.renderers import JSONRenderer

from apps.common.context import get_request_id

__all__ = ["EnvelopeJSONRenderer", "success_envelope", "error_envelope"]


def success_envelope(data: Any, meta: dict[str, Any] | None = None) -> dict[str, Any]:
    envelope_meta: dict[str, Any] = {"request_id": get_request_id()}
    if meta:
        envelope_meta.update(meta)
    return {"data": data, "meta": envelope_meta}


def error_envelope(
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    retryable: bool = False,
) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "request_id": get_request_id(),
            "retryable": retryable,
        }
    }


class EnvelopeJSONRenderer(JSONRenderer):
    """Wraps every response body in the SRS §9.2 envelope.

    Passes through anything already enveloped — the exception handler builds
    its own error envelope, and the OpenAPI schema view must not be wrapped.
    """

    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> bytes:
        if isinstance(data, dict) and ("error" in data or "data" in data):
            return cast(bytes, super().render(data, accepted_media_type, renderer_context))

        meta: dict[str, Any] = {}
        # Cursor pagination injects its cursor into meta, not into the body.
        if isinstance(data, dict) and "next_cursor" in data and "results" in data:
            meta["next_cursor"] = data["next_cursor"]
            data = data["results"]

        return cast(
            bytes,
            super().render(
                success_envelope(data, meta or None), accepted_media_type, renderer_context
            ),
        )
