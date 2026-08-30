"""Human-facing record references — SRS §7.5.10, §7.5.12.

Pure. No Django, no ORM, no I/O. `trip` needs `TRP-YYYY-NNNNNNN` now and
`booking` needs `BKG-YYYY-NNNNNNN` on the same shape in Phase 7, so the format
lives in the shared kernel rather than in whichever module happened to need it
first — the argument that already moved the ISO 4217 rule and `geo` here.

**The seven digits are random, not sequential, and that is a decision.**

A reference is not an internal key. It goes in confirmation emails, a tourist
reads it down the phone to support, and it appears in any correspondence about
the trip. If `TRP-2027-0000041` implied that `TRP-2027-0000042` exists, then a
single reference would disclose roughly how many trips the platform has taken
this year, and a range of them would be enumerable by anyone who could turn a
reference into a lookup. That is an information disclosure with no upside:
nothing in the specification wants references to be ordered, and §7.2 already
provides `public_id` for identity and `created_at` for ordering.

So the number is drawn from `secrets`, not `random` — a predictable generator
seeded from the clock would give back the guessability this exists to remove.

**Uniqueness is the database's job, not this module's.** There is no
`exists()` check here, because a check followed by an insert is a race that
becomes visible exactly when two people book at once. The column carries a
UNIQUE constraint; the caller inserts, and retries with a fresh candidate on a
unique violation. That is correct under concurrency and needs no lock.

**The collision arithmetic, since "random" invites the question.** The space is
ten million *per prefix per year*. At a hundred thousand trips in a year, the
chance that any one insert collides is about one per cent, and a handful of
retries makes the chance of failing to place a reference negligible. It
degrades gracefully rather than suddenly: the day the platform outgrows seven
digits, the retry rate rises long before anything breaks, and `VARCHAR(20)`
leaves room to widen the format.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

__all__ = [
    "REFERENCE_RE",
    "DIGITS",
    "InvalidPrefixError",
    "new_reference",
    "parse_reference",
]

#: §7.5.10's `NNNNNNN`.
DIGITS = 7

#: The whole format, for a serializer or a test that needs to recognise one.
#: Anchored: a pattern that matches a substring would accept a reference with
#: something appended, which is how a lookup starts trusting user input.
REFERENCE_RE = re.compile(r"^(?P<prefix>[A-Z]{3})-(?P<year>\d{4})-(?P<number>\d{7})$")

_PREFIX_RE = re.compile(r"^[A-Z]{3}$")
_UPPER_BOUND = 10**DIGITS


class InvalidPrefixError(ValueError):
    """A prefix that is not three upper-case ASCII letters."""


def new_reference(prefix: str, *, year: int | None = None) -> str:
    """One candidate reference. The caller inserts it and retries on conflict.

    `year` defaults to the current UTC year rather than the server's local one.
    §7.2 keeps every instant in UTC, and a reference issued at 23:30 in
    Zanzibar should not carry a different year from the `created_at` beside it.
    """
    if not _PREFIX_RE.match(prefix):
        raise InvalidPrefixError(f"prefix must be three upper-case ASCII letters, got {prefix!r}")
    stamp = year if year is not None else datetime.now(UTC).year
    return f"{prefix}-{stamp:04d}-{secrets.randbelow(_UPPER_BOUND):0{DIGITS}d}"


def parse_reference(value: str) -> tuple[str, int, int] | None:
    """`(prefix, year, number)`, or `None` if this is not a reference.

    `None` rather than an exception: the caller is usually deciding whether a
    path parameter is a reference or a UUID (`selectors.reference_q` does
    exactly this in `catalogue`), and that is a question, not a failure.
    """
    match = REFERENCE_RE.match(value.strip().upper())
    if match is None:
        return None
    return match["prefix"], int(match["year"]), int(match["number"])
