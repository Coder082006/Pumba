"""Record references — SRS §7.5.10, §7.5.12.

The property worth defending is the one that is invisible when it breaks: a
reference must not disclose how many records exist. A sequential generator
passes every functional test in this file and fails the only one that matters,
which is why `test_two_references_are_not_adjacent` exists and why it is
written as a distribution check rather than a single comparison.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

import pytest

from apps.common.reference import (
    DIGITS,
    REFERENCE_RE,
    InvalidPrefixError,
    new_reference,
    parse_reference,
)


class TestFormat:
    def test_it_matches_the_specified_shape(self) -> None:
        """§7.5.10: `TRP-YYYY-NNNNNNN`."""
        assert REFERENCE_RE.match(new_reference("TRP", year=2027))

    def test_the_number_is_zero_padded(self) -> None:
        """A variable-width number would make two references of different
        lengths for the same year, which breaks column alignment everywhere a
        human reads a list of them."""
        for _ in range(50):
            number = new_reference("TRP", year=2027).rsplit("-", 1)[1]
            assert len(number) == DIGITS

    def test_the_year_is_carried(self) -> None:
        assert new_reference("BKG", year=2031).startswith("BKG-2031-")

    def test_the_year_defaults_to_utc(self) -> None:
        """§7.2 keeps every instant in UTC. A reference issued at 23:30 in
        Zanzibar must not carry a different year from the `created_at` beside
        it."""
        reference = new_reference("TRP")
        assert reference.split("-")[1] == str(datetime.now(UTC).year)

    @pytest.mark.parametrize("prefix", ["trp", "TR", "TRIP", "TR1", "TR-", "", "TRÉ"])
    def test_a_bad_prefix_is_refused(self, prefix: str) -> None:
        """Not silently normalised. A lower-case prefix that was upper-cased
        here would let two call sites disagree about the format while both
        appearing to work."""
        with pytest.raises(InvalidPrefixError):
            new_reference(prefix, year=2027)


class TestUnguessability:
    def test_two_references_are_not_adjacent(self) -> None:
        """The property this module exists for.

        A sequential generator passes every other test here. This one asks
        whether consecutive calls produce consecutive numbers — because if
        they do, one reference in a confirmation email discloses roughly how
        many trips the platform has taken, and a range of them is enumerable.

        Written over many samples rather than two: a single pair could be
        adjacent by chance, one time in five million.
        """
        numbers = [int(new_reference("TRP", year=2027).rsplit("-", 1)[1]) for _ in range(200)]
        adjacent = sum(1 for a, b in pairwise(numbers) if b == a + 1)
        assert adjacent == 0

    def test_the_numbers_are_spread_across_the_space(self) -> None:
        """A generator that always returned 42 would also be non-adjacent."""
        numbers = [int(new_reference("TRP", year=2027).rsplit("-", 1)[1]) for _ in range(200)]
        assert len(set(numbers)) > 190
        assert max(numbers) - min(numbers) > 10**DIGITS // 2

    def test_it_does_not_pre_check_uniqueness(self) -> None:
        """Deliberately absent, and worth pinning so nobody adds it.

        A check followed by an insert is a race that shows up exactly when two
        people book at once. The column carries a UNIQUE constraint and the
        caller retries on violation, which is correct under concurrency and
        needs no lock. This asserts the function needs no database at all —
        note the absence of `pytest.mark.django_db` on this module.
        """
        assert new_reference("TRP", year=2027)


class TestParsing:
    def test_it_reads_back_what_it_wrote(self) -> None:
        reference = new_reference("TRP", year=2027)
        parsed = parse_reference(reference)
        assert parsed is not None
        prefix, year, number = parsed
        assert prefix == "TRP"
        assert year == 2027
        assert 0 <= number < 10**DIGITS

    @pytest.mark.parametrize(
        "value",
        [
            "TRP-2027-000004",  # six digits
            "TRP-2027-00000412",  # eight
            "TRP-27-0000041",
            "TRIP-2027-0000041",
            "0d1b2c3d-4e5f-6789-abcd-ef0123456789",
            "",
            "TRP-2027-0000041 OR 1=1",
        ],
    )
    def test_it_returns_none_for_anything_else(self, value: str) -> None:
        """`None` rather than raising: the caller is deciding whether a path
        parameter is a reference or a UUID, and that is a question rather than
        a failure. The injection-shaped string is there because this is the
        function that decides whether user input is a reference at all."""
        assert parse_reference(value) is None

    def test_it_is_anchored_at_both_ends(self) -> None:
        """A pattern matching a substring would accept a reference with
        something appended, which is how a lookup starts trusting input."""
        assert parse_reference("XTRP-2027-0000041") is None
        assert parse_reference("TRP-2027-0000041X") is None

    def test_it_tolerates_how_a_human_types_it(self) -> None:
        """A tourist reading a reference off an email will paste it with
        whitespace, and may type it in lower case."""
        assert parse_reference("  trp-2027-0000041  ") == ("TRP", 2027, 41)
