"""The keyset cursor codec — SRS §9.1, §30.8.

Pure: no database, no Django. The codec is the one piece of the pagination
machinery that reads attacker-controlled input, and it reads it on
unauthenticated endpoints, so its failure paths matter more than its success
path and get more tests than it.

Three properties, and each one is a bug that does not look like a bug:

**A cursor round-trips exactly, including its types.** An untagged `"120.00"`
would have to be guessed back into a `Decimal` by whatever column received it,
and a guess landing on `float` would compare wrongly against money — §7.2
forbids float for money anywhere, and a comparison is not an exception.

**A cursor from a different ordering is refused.** Honouring one returns a page
of ordinary-looking rows with an arbitrary set missing. Nothing downstream can
detect that, which is why it is detected here.

**Nothing a client can send produces anything but `InvalidCursorError`.** A
cursor that reached the database as a comparison value would be a 500 on a
public URL and cheap to trigger repeatedly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.common.pagination import (
    InvalidCursorError,
    Page,
    decode_cursor,
    encode_cursor,
)

ORDERING = "a1b2c3d4a1b2c3d4"


class TestRoundTrip:
    @pytest.mark.parametrize(
        "values",
        [
            (1,),
            (True, False, 42),
            (Decimal("120.50"), None, 7),
            (None, None, None, 1),
            ("a-slug", 3),
            (Decimal("-0.01"), Decimal("0"), 99999999),
        ],
    )
    def test_values_survive_encoding_unchanged(self, values: tuple[object, ...]) -> None:
        assert decode_cursor(encode_cursor(values, ordering=ORDERING), ordering=ORDERING) == values

    def test_a_decimal_comes_back_a_decimal_and_not_a_float(self) -> None:
        """The whole reason values are tagged.

        `Decimal("0.1")` and `float("0.1")` compare differently against a
        `NUMERIC` column, and the rows that fall between them are the ones a
        page would silently skip.
        """
        [value] = decode_cursor(
            encode_cursor([Decimal("0.1")], ordering=ORDERING), ordering=ORDERING
        )
        assert isinstance(value, Decimal)
        assert not isinstance(value, float)

    def test_a_bool_comes_back_a_bool_and_not_an_int(self) -> None:
        """`bool` is a subclass of `int`, so an order-of-checks mistake in the
        tagger turns `True` into `1` — which compares against a boolean column
        as an integer and orders the two §16.5 context terms wrongly."""
        decoded = decode_cursor(encode_cursor([True, 1], ordering=ORDERING), ordering=ORDERING)
        assert [type(item) for item in decoded] == [bool, int]

    def test_the_cursor_is_url_safe_and_unpadded(self) -> None:
        """It travels in a query string. `+`, `/` and `=` all mean something
        else there, and a client that does not escape them sends a different
        cursor than the one it was given."""
        cursor = encode_cursor([Decimal("1.5"), None, 2**40], ordering=ORDERING)
        assert set(cursor) <= set(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        )

    def test_an_empty_position_round_trips(self) -> None:
        """Degenerate but reachable: a model whose ordering compiled to
        nothing. Better to round-trip than to be a special case somewhere."""
        assert decode_cursor(encode_cursor([], ordering=ORDERING), ordering=ORDERING) == ()


class TestTheOrderingIsCheckedNotAssumed:
    def test_a_cursor_from_another_ordering_is_refused(self) -> None:
        cursor = encode_cursor([1, 2], ordering=ORDERING)
        with pytest.raises(InvalidCursorError):
            decode_cursor(cursor, ordering="ffffffffffffffff")

    def test_the_ordering_is_part_of_the_payload_not_a_side_channel(self) -> None:
        """Two cursors at the same position under different orderings must not
        be the same string, or the check above could never fire."""
        assert encode_cursor([1], ordering=ORDERING) != encode_cursor([1], ordering="0" * 16)


class TestNothingAClientSendsBecomesAnythingElse:
    @pytest.mark.parametrize(
        "cursor",
        [
            "",
            "!!!not base64!!!",
            "____",
            "IiI",  # base64 of `""` — valid JSON, not the object expected
            "W10",  # base64 of `[]` — a list, not the object expected
            "bnVsbA",  # base64 of `null`
            "eyJ2IjoxLCJvIjoiYTFiMmMzZDRhMWIyYzNkNCJ9",  # right version, no "k"
            "eyJ2Ijo5OTksIm8iOiJhMWIyYzNkNGExYjJjM2Q0IiwiayI6W119",  # future version
        ],
    )
    def test_a_malformed_cursor_raises_the_one_error(self, cursor: str) -> None:
        with pytest.raises(InvalidCursorError):
            decode_cursor(cursor, ordering=ORDERING)

    @pytest.mark.parametrize(
        "keys",
        [
            [["z", 1]],  # unknown type tag
            [["i", "1"]],  # tag and value disagree
            [["i", True]],  # a bool smuggled in as an int
            [["d", "not-a-number"]],  # a Decimal that will not parse
            [["d", 1.5]],  # a float where the string form belongs
            [["n", 1]],  # null tag with a value
            [[1, 2, 3]],  # wrong arity
            ["notalist"],
        ],
    )
    def test_a_tampered_payload_raises_rather_than_reaching_a_query(
        self, keys: list[object]
    ) -> None:
        """Well-formed base64, well-formed JSON, right ordering, wrong contents.

        This is what a prober actually sends. Each of these would otherwise
        arrive at the database as a comparison value of the wrong type, which
        is a 500 rather than a 422.
        """
        import base64
        import json

        payload = {"v": 1, "o": ORDERING, "k": keys}
        raw = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        with pytest.raises(InvalidCursorError):
            decode_cursor(raw, ordering=ORDERING)

    def test_the_error_is_a_validation_error_so_it_surfaces_as_422(self) -> None:
        """Not a 500 and not a 400. §8.7 puts semantic failures at 422, and no
        view should have to catch this to get there."""
        from apps.common.errors import ValidationError

        assert issubclass(InvalidCursorError, ValidationError)
        assert InvalidCursorError().status_code == 422
        assert InvalidCursorError().code == "INVALID_CURSOR"

    def test_every_failure_says_the_same_thing(self) -> None:
        """A cursor is client input on a public endpoint. Distinguishing "not
        base64" from "wrong ordering" tells a prober about the internals for no
        benefit to a caller, who can do exactly one thing about any of them:
        start from the first page.
        """
        messages = set()
        for cursor in ("!!!", "IiI", encode_cursor([1], ordering="0" * 16)):
            try:
                decode_cursor(cursor, ordering=ORDERING)
            except InvalidCursorError as exc:
                messages.add(exc.message)
        assert len(messages) == 1


class TestWhatCannotBeCarried:
    def test_an_unsupported_type_is_refused_at_encoding_time(self) -> None:
        """Loudly, and on our side of the wire.

        A term whose value is a date or a point would otherwise be silently
        stringified and come back as something that compares differently — a
        pagination bug introduced by adding an ordering term, discovered by a
        customer.
        """
        import datetime as dt

        with pytest.raises(TypeError, match="cannot be carried in a cursor"):
            encode_cursor([dt.date(2027, 8, 12)], ordering=ORDERING)


class TestPage:
    def test_a_page_is_iterable_and_sized(self) -> None:
        page: Page[int] = Page((1, 2, 3), "next")
        assert list(page) == [1, 2, 3]
        assert len(page) == 3

    def test_the_last_page_carries_none_rather_than_an_empty_string(self) -> None:
        """A client would send an empty string back, and the loop would never
        end. The distinction is the whole loop condition, so it is a type
        difference rather than a value convention."""
        assert Page(()).next_cursor is None

    def test_a_page_is_frozen(self) -> None:
        page: Page[int] = Page((1,))
        with pytest.raises(AttributeError):
            page.next_cursor = "tampered"  # type: ignore[misc]
