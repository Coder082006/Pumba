"""Cursor pagination — SRS §9.1.

    "Cursor-based: ?limit=&cursor=; response returns next_cursor"

Cursor rather than offset because the catalogue and booking lists are read
while rows are being inserted, and offset pagination silently skips or
duplicates rows under concurrent writes.

The cursor is opaque to clients (SRS §30.8: the API discloses no internal
identifiers) but is deliberately *not* encrypted — it encodes only an
ordering position, never authorisation. It is *validated*, though: a cursor
that does not decode, or that was issued under a different ordering, is
refused rather than approximated.

**Two mechanisms, and which one to reach for.**

`CursorPagination` is DRF's, kept as the project default. It keys the cursor on
a single ordering field, which is fine for a list ordered by `-created_at, -id`
and is what most endpoints want.

`Page` and the codec below are for an ordering DRF's cannot express. SRS §16.5
orders the catalogue by seven terms with mixed directions and `NULLS LAST` on
three of them; resuming inside that needs the whole tuple, so the cursor
carries the whole tuple. `apps.catalogue.selectors` builds the predicate,
because the ordering is compiled there and a cursor built anywhere else could
drift from it.

**The ordering fingerprint is not decoration.** A cursor taken from a
`?sort=price_asc` listing and replayed against `?sort=recommended` describes a
position in an ordering that no longer exists, and the rows it would skip or
repeat are silently wrong — the worst shape of pagination bug, because the
response looks like a page. So the ordering is hashed into the cursor and
checked on the way back in.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Generic, TypeVar

from rest_framework.pagination import CursorPagination as DRFCursorPagination
from rest_framework.response import Response

from apps.common.errors import ValidationError

__all__ = [
    "CursorPagination",
    "Page",
    "InvalidCursorError",
    "encode_cursor",
    "decode_cursor",
]

T = TypeVar("T")


class InvalidCursorError(ValidationError):
    """A cursor did not decode, or belongs to a different ordering.

    A `ValidationError`, so it surfaces as 422 with no view having to catch it.
    Refusing is the only safe answer: a cursor that cannot be trusted to mean a
    position can still be *used* as one, and the page it produces would look
    entirely normal.
    """

    code = "INVALID_CURSOR"
    default_message = "That page cursor is not valid for this request."


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """One page of results, and how to ask for the next.

    `next_cursor` is `None` on the last page — not an empty string, which a
    client would happily send back. The distinction is the whole loop
    condition, so it is a type difference rather than a value convention.

    Iterable and sized, because almost every caller wants the items and the
    ceremony of `.items` on each one earns nothing.
    """

    items: tuple[T, ...]
    next_cursor: str | None = None

    def __iter__(self) -> Iterator[T]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)


#: Bumped if the payload shape changes. An old cursor then fails closed rather
#: than being reinterpreted under new rules, which is the failure mode that
#: silently skips rows.
_CURSOR_VERSION = 1


def encode_cursor(values: Sequence[object], *, ordering: str) -> str:
    """The position of one row in `ordering`, as an opaque string.

    Values are tagged with their type rather than inferred on the way back.
    An untagged `"120.00"` is a string that has to be guessed into a `Decimal`
    by whatever the receiving column happens to be, and a guess that lands on
    `float` would compare wrongly against money — §7.2 forbids float for money
    anywhere, and a comparison is not an exception.
    """
    payload = {"v": _CURSOR_VERSION, "o": ordering, "k": [_tag(value) for value in values]}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str, *, ordering: str) -> tuple[object, ...]:
    """The values `encode_cursor` was given, or `InvalidCursorError`.

    Every failure is the same error and the same message. A cursor is client
    input on a public endpoint, and distinguishing "not base64" from "wrong
    ordering" tells a prober about the internals for no benefit to a caller,
    who can do exactly one thing about any of them: start from the first page.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise InvalidCursorError() from exc

    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise InvalidCursorError()
    if payload.get("o") != ordering:
        raise InvalidCursorError()
    keys = payload.get("k")
    if not isinstance(keys, list):
        raise InvalidCursorError()
    return tuple(_untag(item) for item in keys)


def _tag(value: object) -> list[Any]:
    # `bool` before `int`: it is a subclass, and an untagged `True` would come
    # back as `1` and compare against a boolean column as an integer.
    if value is None:
        return ["n", None]
    if isinstance(value, bool):
        return ["b", value]
    if isinstance(value, int):
        return ["i", value]
    if isinstance(value, Decimal):
        return ["d", str(value)]
    if isinstance(value, str):
        return ["s", value]
    raise TypeError(f"{type(value).__name__} cannot be carried in a cursor")


def _untag(item: object) -> object:
    if not isinstance(item, list) or len(item) != 2:
        raise InvalidCursorError()
    kind, value = item
    if kind == "n" and value is None:
        return None
    if kind == "b" and isinstance(value, bool):
        return value
    if kind == "i" and isinstance(value, int) and not isinstance(value, bool):
        return value
    if kind == "s" and isinstance(value, str):
        return value
    if kind == "d" and isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise InvalidCursorError() from exc
    raise InvalidCursorError()


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
