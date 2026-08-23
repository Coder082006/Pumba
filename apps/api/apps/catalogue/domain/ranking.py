"""Deterministic catalogue ranking — SRS §16.5.

    ORDER BY
      (a.destination_id = :selected_destination) DESC,   -- exact destination first
      (a.tags && :interest_tags) DESC,                   -- tag overlap, boolean
      a.feature_rank ASC,                                -- administrator curation
      agg.rating_avg DESC NULLS LAST,                    -- published reviews
      agg.rating_count DESC,
      a.price_per_person ASC,
      a.id ASC                                           -- total order tie-break

§16.5 is explicit that this is *"an explicit, reproducible expression — never a
learned model"*, and §3.5 makes that a release gate. It is also a commercial
commitment: the default ordering is published in the help centre *"so that
providers understand exactly how placement is earned — an obligation that a
learned ranker could not meet"*. Providers will read it, and some will dispute
their placement. The expression has to be defensible line by line.

**Why this exists twice.** `rank_key` is the expression as a pure sort key, so
the ordering can be tested exhaustively with no database at all. `order_terms`
is the same expression as a declarative description that `selectors.py`
translates into ORM ordering. Two implementations of one rule is a liability
unless something pins them together, so `tests/test_catalogue_ranking_db.py`
sorts a fixture with `rank_key` and asserts PostgreSQL returns that exact
sequence. If the ORM translation drifts, that test fails — not TC-902 six weeks
later.

**Why `id` is not redundant.** Without it, two rows equal on all six preceding
fields have no defined relative order, and PostgreSQL is free to return them in
whichever order the plan happens to produce — which changes with row count,
statistics and parallelism. TC-902 requires byte-identical output across
repeated identical requests, so the key must be *injective*: distinct rows,
distinct keys. `test_rank_key_is_injective` asserts exactly that, and it is the
test that stops someone deleting the tie-break as noise.

**NULL ratings are the launch-day case, not the edge case.** Phase 3 has no
reviews at all, so every `rating_avg` is `None` and every `rating_count` is 0.
A ranking that only behaves once ratings exist is a ranking that is wrong on the
day the platform opens.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal

__all__ = [
    "SortOption",
    "RankInputs",
    "OrderTerm",
    "rank_key",
    "order_terms",
    "DEFAULT_TERMS",
    "displayable_rating",
]


class SortOption(StrEnum):
    """§16.5: *"The tourist may override with an explicit sort parameter"*."""

    DEFAULT = "recommended"
    PRICE_ASC = "price_asc"
    PRICE_DESC = "price_desc"
    RATING = "rating"
    DURATION = "duration"
    DISTANCE = "distance"


@dataclass(frozen=True, slots=True)
class RankInputs:
    """One candidate row, reduced to the fields the expression reads.

    A value object rather than the model, because the domain layer may not
    import the ORM and because it makes the ranking testable against a list
    literal.
    """

    id: int
    destination_id: int
    tags: frozenset[str]
    feature_rank: int
    rating_avg: Decimal | None
    rating_count: int
    price: Decimal | None
    duration_minutes: int | None = None
    distance_metres: int | None = None


@dataclass(frozen=True, slots=True)
class OrderTerm:
    """One ORDER BY clause, named by what `selectors.py` must produce.

    `expression` is a symbolic name, not SQL and not an ORM object — the domain
    describes the ordering, the data-access layer builds it. `nulls_last` is
    carried explicitly because it is the difference between an unrated listing
    sorting first and sorting last, and PostgreSQL's default for `DESC` is
    `NULLS FIRST`, which is the wrong one.
    """

    expression: str
    descending: bool = False
    nulls_last: bool = True


#: The tie-break. Present in every ordering, always last, never optional.
_TOTAL_ORDER = OrderTerm("id", descending=False)

DEFAULT_TERMS: tuple[OrderTerm, ...] = (
    OrderTerm("matches_selected_destination", descending=True),
    OrderTerm("matches_interest_tags", descending=True),
    OrderTerm("feature_rank", descending=False),
    OrderTerm("rating_avg", descending=True, nulls_last=True),
    OrderTerm("rating_count", descending=True),
    OrderTerm("price", descending=False, nulls_last=True),
    _TOTAL_ORDER,
)

_EXPLICIT_LEAD: dict[SortOption, tuple[OrderTerm, ...]] = {
    SortOption.PRICE_ASC: (OrderTerm("price", descending=False, nulls_last=True),),
    SortOption.PRICE_DESC: (OrderTerm("price", descending=True, nulls_last=True),),
    SortOption.RATING: (
        OrderTerm("rating_avg", descending=True, nulls_last=True),
        OrderTerm("rating_count", descending=True),
    ),
    SortOption.DURATION: (OrderTerm("duration_minutes", descending=False, nulls_last=True),),
    SortOption.DISTANCE: (OrderTerm("distance_metres", descending=False, nulls_last=True),),
}


def order_terms(
    sort: SortOption = SortOption.DEFAULT,
    *,
    selected_destination_id: int | None = None,
    interest_tags: Collection[str] = (),
) -> tuple[OrderTerm, ...]:
    """The ordering for `sort`, as a declaration `selectors.py` compiles.

    An explicit sort leads with what the tourist asked for and then falls back
    to the default expression, so a price sort still puts the selected
    destination's equally-priced listings above another destination's — and
    still ends on `id`, so it is still total.
    """
    if sort is SortOption.DEFAULT:
        return _apply_context(DEFAULT_TERMS, selected_destination_id, interest_tags)
    lead = _EXPLICIT_LEAD[sort]
    tail = tuple(t for t in DEFAULT_TERMS if t.expression not in {x.expression for x in lead})
    return _apply_context(lead + tail, selected_destination_id, interest_tags)


def _apply_context(
    terms: Sequence[OrderTerm],
    selected_destination_id: int | None,
    interest_tags: Collection[str],
) -> tuple[OrderTerm, ...]:
    """Drop the two context terms when there is no context for them.

    Ordering by `destination_id = NULL` or by overlap with an empty tag set is
    a constant, and a constant in an ORDER BY is noise the database still has
    to evaluate. Dropping it also keeps the emitted SQL honest about what the
    request actually asked for.
    """
    drop: set[str] = set()
    if selected_destination_id is None:
        drop.add("matches_selected_destination")
    if not interest_tags:
        drop.add("matches_interest_tags")
    return tuple(t for t in terms if t.expression not in drop)


def rank_key(
    row: RankInputs,
    *,
    sort: SortOption = SortOption.DEFAULT,
    selected_destination_id: int | None = None,
    interest_tags: Collection[str] = (),
) -> tuple[object, ...]:
    """The sort key for `row`, ascending on every element.

    Descending fields are negated rather than reversed, because Python cannot
    sort a tuple with mixed directions and reversing the whole list would
    invert the tie-break too. `None` is mapped to a sentinel that sorts last in
    the direction the term declares, which is how `NULLS LAST` is honoured
    identically here and in SQL.
    """
    wanted = frozenset(interest_tags)
    values: dict[str, object] = {
        "matches_selected_destination": row.destination_id == selected_destination_id,
        "matches_interest_tags": bool(row.tags & wanted),
        "feature_rank": row.feature_rank,
        "rating_avg": row.rating_avg,
        "rating_count": row.rating_count,
        "price": row.price,
        "duration_minutes": row.duration_minutes,
        "distance_metres": row.distance_metres,
        "id": row.id,
    }
    return tuple(
        _sortable(values[term.expression], descending=term.descending, nulls_last=term.nulls_last)
        for term in order_terms(
            sort,
            selected_destination_id=selected_destination_id,
            interest_tags=interest_tags,
        )
    )


#: Sorts after every real value in an ascending comparison.
_NULLS_LAST: Literal[1] = 1
_PRESENT: Literal[0] = 0


def _sortable(
    value: object, *, descending: bool, nulls_last: bool
) -> tuple[int, bool | Decimal | int]:
    """`(null_rank, magnitude)`, both ascending.

    The leading element carries nullness so that `NULLS LAST` survives the
    negation used for descending order — negating a sentinel would move it to
    the front, which is the bug this shape prevents.
    """
    if value is None:
        return (_NULLS_LAST if nulls_last else -1, Decimal(0))
    if isinstance(value, bool):
        return (_PRESENT, not value if descending else value)
    if isinstance(value, Decimal):
        return (_PRESENT, -value if descending else value)
    if isinstance(value, int):
        return (_PRESENT, -value if descending else value)
    raise TypeError(f"unrankable value: {value!r}")


def displayable_rating(
    rating_avg: Decimal, rating_count: int, *, min_display_count: int
) -> Decimal | None:
    """BR-127's mean, or `None` when there are too few reviews to state one.

        "a subject with fewer than 3 published reviews displays 'New' rather
         than a mean"

    **This is a display rule, and it deliberately disagrees with the ordering
    above.** §16.5 ranks on the raw `rating_avg`, so a subject with one
    five-star review outranks one with fifty averaging 4.8 and shows "New"
    while doing it. That tension is real, is recorded in ADR 0015, and belongs
    to whoever owns `review` — it is not silently resolved by rounding it away
    here, because ranking is a published commitment and this is not.

    Returning `None` rather than a flag is what makes the rule enforceable. A
    payload carrying the mean plus "you may not show this" is a rule in every
    client; a payload carrying no mean is a rule in one place. `rating_count`
    still travels, so a client renders "New" without a second call.

    `min_display_count` is `review.min_display_count` (rule 5). It is a
    judgement about statistical confidence, and a market with thinner supply
    may want it lower.
    """
    if min_display_count < 0:
        raise ValueError("min_display_count must not be negative")
    if rating_count < min_display_count:
        return None
    return rating_avg
