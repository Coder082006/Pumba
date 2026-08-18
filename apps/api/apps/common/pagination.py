"""Cursor pagination — SRS §9.1.

    "Cursor-based: ?limit=&cursor=; response returns next_cursor"

Cursor rather than offset because the catalogue and booking lists are read
while rows are being inserted, and offset pagination silently skips or
duplicates rows under concurrent writes.

The cursor is opaque to clients (SRS §30.8: the API discloses no internal
identifiers) but is deliberately *not* encrypted — it encodes only an
ordering position, never authorisation.
"""

from __future__ import annotations

from typing import Any

from rest_framework.pagination import CursorPagination as DRFCursorPagination
from rest_framework.response import Response

__all__ = ["CursorPagination"]


class CursorPagination(DRFCursorPagination):
    """Default pagination for every list endpoint."""

    page_size = 20
    page_size_query_param = "limit"
    max_page_size = 100
    cursor_query_param = "cursor"

    # Stable total order. SRS principle A7: "Any ordering exposed to a user
    # must have a total order and a stable tie-break." `-created_at` alone is
    # not a total order — two rows can share a timestamp — so `-id` breaks
    # the tie. Individual views override this with their own stable ordering.
    ordering = ("-created_at", "-id")

    def get_paginated_response(self, data: Any) -> Response:
        """Return a shape the envelope renderer lifts `next_cursor` out of.

        The renderer moves `next_cursor` into `meta` per SRS §9.2, leaving
        `data` as the bare array.
        """
        return Response(
            {
                "results": data,
                "next_cursor": self._cursor_from_url(self.get_next_link()),
            }
        )

    def _cursor_from_url(self, url: str | None) -> str | None:
        if not url:
            return None
        from urllib.parse import parse_qs, urlparse

        values = parse_qs(urlparse(url).query).get(self.cursor_query_param)
        return values[0] if values else None

    def get_paginated_response_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Describe the *enveloped* shape so the committed OpenAPI is truthful."""
        return {
            "type": "object",
            "properties": {
                "data": schema,
                "meta": {
                    "type": "object",
                    "properties": {
                        "request_id": {"type": "string"},
                        "next_cursor": {"type": "string", "nullable": True},
                    },
                },
            },
            "required": ["data", "meta"],
        }
