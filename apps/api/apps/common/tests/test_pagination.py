"""Cursor pagination tests — SRS §9.1, principle A7."""

from __future__ import annotations

from apps.common.pagination import CursorPagination


class TestOrdering:
    def test_default_ordering_has_a_stable_tie_break(self) -> None:
        """SRS principle A7: "Any ordering exposed to a user must have a total
        order and a stable tie-break."

        `-created_at` alone is not a total order — two rows inserted in the
        same transaction can share a timestamp, and cursor pagination over a
        non-total order silently skips or repeats rows.
        """
        assert CursorPagination.ordering == ("-created_at", "-id")

    def test_page_size_is_capped(self) -> None:
        """An uncapped limit is a denial-of-service vector on a list endpoint."""
        assert CursorPagination.max_page_size == 100
        assert CursorPagination.page_size <= CursorPagination.max_page_size

    def test_query_parameters_match_the_srs(self) -> None:
        # SRS §9.1: "Cursor-based: ?limit=&cursor=".
        assert CursorPagination.page_size_query_param == "limit"
        assert CursorPagination.cursor_query_param == "cursor"


class TestCursorExtraction:
    def test_pulls_the_cursor_out_of_a_next_link(self) -> None:
        paginator = CursorPagination()
        url = "https://api.example.com/api/v1/activities?limit=20&cursor=eyJpZCI6NDJ9"
        assert paginator._cursor_from_url(url) == "eyJpZCI6NDJ9"

    def test_returns_none_on_the_last_page(self) -> None:
        assert CursorPagination()._cursor_from_url(None) is None

    def test_returns_none_when_the_link_carries_no_cursor(self) -> None:
        assert CursorPagination()._cursor_from_url("https://api.example.com/x?limit=20") is None


class TestSchema:
    def test_documents_the_enveloped_shape(self) -> None:
        """The committed OpenAPI must describe what clients actually receive.

        DRF's default paginated schema describes {count, next, previous,
        results}, which this API never returns — the renderer lifts the cursor
        into `meta` per SRS §9.2. Publishing the default would make the
        committed contract a lie.
        """
        schema = CursorPagination().get_paginated_response_schema(
            {"type": "array", "items": {"type": "object"}}
        )

        assert set(schema["required"]) == {"data", "meta"}
        assert schema["properties"]["data"]["type"] == "array"
        assert "next_cursor" in schema["properties"]["meta"]["properties"]
        assert "request_id" in schema["properties"]["meta"]["properties"]
        assert "count" not in schema["properties"]
