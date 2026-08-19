"""Unified catalogue search — SRS §7.6, §9.3.2, §24.7.

`GET /search` spans four tables — destination, attraction, activity,
accommodation — and §7.6 indexes them with
`GIN(to_tsvector(name || description))`. Two problems follow, and they are the
whole of this module.

**Turning a tourist's typing into a query safely.** The input is arbitrary text
from a public, unauthenticated endpoint. It must not reach `to_tsquery`, whose
operator syntax raises a database error on a stray `&` or an unbalanced
bracket — a 500 on `GET /search?q=fish & chips` is both a bug and a small
denial-of-service. PostgreSQL's `websearch_to_tsquery` is the right target: it
accepts what a person types, including quotes and `or`, and never raises. This
module normalises the input for it and enforces §24.7's *"requires two
characters"*.

**Ordering four result sets into one list, reproducibly.** TC-902's byte
identity applies here as much as to `/activities`. Relevance rank alone is not
a total order — two rows from different tables routinely tie — so `merge_ranked`
falls through rank, then a fixed kind precedence, then id. The kind precedence
is deliberate rather than alphabetical: a tourist searching "Nungwi" wants the
destination above the seventeen activities inside it, because the destination
is the page that contains them all.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "SearchKind",
    "Hit",
    "SearchQueryError",
    "normalise_query",
    "to_websearch_query",
    "merge_ranked",
    "KIND_PRECEDENCE",
]


class SearchQueryError(ValueError):
    """The query is unusable. Surfaces as 422, never as a database error."""


class SearchKind(StrEnum):
    DESTINATION = "destination"
    ATTRACTION = "attraction"
    ACTIVITY = "activity"
    ACCOMMODATION = "accommodation"


#: Ties break toward the container. Somebody searching "Nungwi" wants the
#: destination page above the activities inside it.
KIND_PRECEDENCE: tuple[SearchKind, ...] = (
    SearchKind.DESTINATION,
    SearchKind.ATTRACTION,
    SearchKind.ACTIVITY,
    SearchKind.ACCOMMODATION,
)

_WHITESPACE = re.compile(r"\s+")
#: Control characters have no meaning in a search box and NUL terminates a C
#: string; both are stripped rather than escaped.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class Hit:
    """One row from one of the four searched tables."""

    kind: SearchKind
    id: int
    rank: Decimal


def normalise_query(raw: str, *, min_length: int, max_length: int) -> str:
    """Trim, collapse and bound the raw input. §24.7.

    Length is measured in code points, so a two-character query in any script
    is two characters. Counting bytes would make the minimum three ASCII
    letters or one Chinese one, which is a different rule for different
    tourists.
    """
    if min_length < 1:
        raise ValueError("min_length must be at least 1")
    if max_length < min_length:
        raise ValueError("max_length cannot be below min_length")

    cleaned = _WHITESPACE.sub(" ", _CONTROL.sub("", raw)).strip()
    if len(cleaned) < min_length:
        raise SearchQueryError(f"search needs at least {min_length} characters")
    return cleaned[:max_length]


def to_websearch_query(query: str) -> str:
    """Prepare `query` for PostgreSQL's `websearch_to_tsquery`.

    `websearch_to_tsquery` is chosen over `to_tsquery` and `plainto_tsquery`
    because it is the only one that both accepts human input without raising
    and honours quoted phrases — a tourist searching `"stone town"` means the
    phrase.

    The only transformation needed is balancing quotes: an odd number makes
    PostgreSQL treat the trailing fragment as an unterminated phrase and
    quietly return nothing, which reads to the tourist as "no results" rather
    than as a typo.
    """
    if query.count('"') % 2:
        query = query.replace('"', " ")
        query = _WHITESPACE.sub(" ", query).strip()
    return query


def merge_ranked(groups: Mapping[SearchKind, Sequence[Hit]]) -> tuple[Hit, ...]:
    """One ordered list from four. Total, so TC-902 holds across tables.

    Rank descending, then `KIND_PRECEDENCE`, then id ascending. Rank alone ties
    constantly between tables, and a tie broken by dictionary iteration order
    is a result list that reorders itself between two identical requests.
    """
    precedence = {kind: index for index, kind in enumerate(KIND_PRECEDENCE)}
    unknown = set(groups) - set(precedence)
    if unknown:
        raise ValueError(f"no precedence declared for {sorted(unknown)}")

    hits = [hit for kind in groups for hit in groups[kind]]
    return tuple(sorted(hits, key=lambda hit: (-hit.rank, precedence[hit.kind], hit.id)))
