"""PostgreSQL returns exactly what `rank_key` says it should — SRS §16.5, TC-902.

`domain.ranking` describes §16.5's ordering twice on purpose: `rank_key` as a
pure sort key that can be tested exhaustively with no database, and
`order_terms` as a declaration that `selectors.apply_order` compiles into ORM
ordering. That is two implementations of one commercially published rule, and
§16.5 commits to publishing it: *"so that providers understand exactly how
placement is earned"*. Providers will read it, and some will dispute their
placement.

This file is what stops the two drifting. Every test here sorts a fixture with
`rank_key` and asserts the database returns that exact sequence. When the ORM
translation is wrong the failure lands here, naming the ordering, rather than in
TC-902 six weeks later as "the same request returned different bytes".

Two properties get their own tests because they are the ones that break
silently:

**`NULLS LAST` on a descending term.** PostgreSQL defaults `DESC` to
`NULLS FIRST`, so an unrated listing sorts above a five-star one unless the
translation says otherwise. Every listing is unrated in Phase 3 — there are no
reviews — so this is launch-day behaviour, not an edge case.

**A column the model does not have.** Since ADR 0013 `accommodation` has no
price and no rating, and `apply_order` annotates constants rather than dropping
the terms. Dropping them would rank accommodation by a quietly different
expression, and nothing downstream would notice.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.catalogue.domain.ranking import RankInputs, SortOption, rank_key
from apps.catalogue.models import Accommodation, Activity
from apps.catalogue.selectors import apply_order, visible
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_destination,
    make_tag,
)

TODAY = dt.date(2027, 8, 12)

pytestmark = pytest.mark.django_db


def _activity_inputs(row: Activity) -> RankInputs:
    return RankInputs(
        id=row.id,
        destination_id=row.destination_id,
        tags=frozenset(row.tags),
        feature_rank=row.feature_rank,
        rating_avg=row.rating_avg,
        rating_count=row.rating_count,
        price=row.price_per_person,
        duration_minutes=row.duration_minutes,
    )


def _accommodation_inputs(row: Accommodation) -> RankInputs:
    """ADR 0013: no rating and no price, so the domain is fed the absence.

    `rating_count=0` rather than `None` mirrors `_RANK_SOURCES`, which
    annotates `Value(0)`: a location record has zero reviews, which is a
    number, where its price is genuinely unknown.
    """
    return RankInputs(
        id=row.id,
        destination_id=row.destination_id,
        tags=frozenset(),
        feature_rank=row.feature_rank,
        rating_avg=None,
        rating_count=0,
        price=None,
        duration_minutes=None,
    )


def _expected(rows: list[RankInputs], **kwargs: object) -> list[int]:
    return [row.id for row in sorted(rows, key=lambda r: rank_key(r, **kwargs))]  # type: ignore[arg-type]


def _actual(queryset: object, **kwargs: object) -> list[int]:
    return list(apply_order(queryset, **kwargs).values_list("id", flat=True))  # type: ignore[arg-type,attr-defined]


@pytest.fixture
def spread() -> tuple[object, list[Activity]]:
    """Rows chosen so that every term in the expression decides at least once.

    Deliberately includes a pair identical on all six preceding fields, so the
    `id` tie-break is load-bearing rather than decorative — that pair is the
    reason §16.5's expression ends where it does.
    """
    destination = make_destination()
    # `assert_known_tags` refuses a slug with no `tag` row, so the vocabulary
    # exists before anything is tagged with it. That trigger is the reason
    # `tags text[]` is safe to filter on without a join table.
    for slug in ("diving", "culture"):
        make_tag(slug=slug, label=slug.title())
    rows = [
        make_activity(
            destination=destination,
            slug="a",
            feature_rank=10,
            rating_avg=Decimal("4.80"),
            rating_count=40,
            price_per_person=Decimal("120.00"),
            duration_minutes=180,
            tags=["diving"],
        ),
        make_activity(
            destination=destination,
            slug="b",
            feature_rank=10,
            rating_avg=Decimal("4.80"),
            rating_count=12,
            price_per_person=Decimal("120.00"),
            duration_minutes=90,
            tags=["culture"],
        ),
        make_activity(
            destination=destination,
            slug="c",
            feature_rank=1,
            rating_avg=Decimal("0.00"),
            rating_count=0,
            price_per_person=Decimal("300.00"),
            duration_minutes=240,
            tags=[],
        ),
        # The tie-break pair: identical on everything the expression reads
        # before `id`.
        make_activity(
            destination=destination,
            slug="d",
            feature_rank=50,
            rating_avg=Decimal("3.00"),
            rating_count=5,
            price_per_person=Decimal("60.00"),
            duration_minutes=60,
            tags=["culture"],
        ),
        make_activity(
            destination=destination,
            slug="e",
            feature_rank=50,
            rating_avg=Decimal("3.00"),
            rating_count=5,
            price_per_person=Decimal("60.00"),
            duration_minutes=60,
            tags=["culture"],
        ),
    ]
    return destination, rows


class TestTheDatabaseAgreesWithTheDomain:
    def test_the_default_ordering_matches(self, spread: tuple[object, list[Activity]]) -> None:
        _, rows = spread
        assert _actual(visible(Activity.objects.all(), today=TODAY)) == _expected(
            [_activity_inputs(row) for row in rows]
        )

    @pytest.mark.parametrize(
        "sort",
        [
            SortOption.PRICE_ASC,
            SortOption.PRICE_DESC,
            SortOption.RATING,
            SortOption.DURATION,
        ],
    )
    def test_every_explicit_sort_matches(
        self, spread: tuple[object, list[Activity]], sort: SortOption
    ) -> None:
        """§16.5: *"The tourist may override with an explicit sort parameter"*.
        Each override still falls back to the default expression and still ends
        on `id`, so each is still total."""
        _, rows = spread
        assert _actual(visible(Activity.objects.all(), today=TODAY), sort=sort) == _expected(
            [_activity_inputs(row) for row in rows], sort=sort
        )

    def test_the_interest_tag_term_matches(self, spread: tuple[object, list[Activity]]) -> None:
        _, rows = spread
        tags = ["culture"]
        assert _actual(
            visible(Activity.objects.all(), today=TODAY), interest_tags=tags
        ) == _expected([_activity_inputs(row) for row in rows], interest_tags=tags)

    def test_the_selected_destination_term_matches(
        self, spread: tuple[object, list[Activity]]
    ) -> None:
        """Two destinations, so the term actually decides something."""
        destination, rows = spread
        elsewhere = make_destination(
            region=destination.region,
            slug="stone-town",
            name="Stone Town",  # type: ignore[attr-defined]
        )
        rows = [
            *rows,
            make_activity(destination=elsewhere, slug="f", feature_rank=1),
            make_activity(destination=elsewhere, slug="g", feature_rank=200),
        ]
        selected = elsewhere.pk
        assert _actual(
            visible(Activity.objects.all(), today=TODAY), selected_destination_id=selected
        ) == _expected([_activity_inputs(row) for row in rows], selected_destination_id=selected)

    def test_it_is_stable_across_repeated_identical_queries(
        self, spread: tuple[object, list[Activity]]
    ) -> None:
        """TC-902's byte identity, at the layer that decides the order.

        Repeated rather than asserted once: an ordering that is not total
        usually *looks* right the first time and reorders under a different
        plan.
        """
        first = _actual(visible(Activity.objects.all(), today=TODAY))
        for _ in range(5):
            assert _actual(visible(Activity.objects.all(), today=TODAY)) == first


class TestNullsSortLast:
    def test_an_unrated_listing_does_not_lead_the_page(self) -> None:
        """The launch-day case. Phase 3 has no reviews at all, so `rating_avg`
        is at its default for every row; a listing with a real rating must
        still outrank one without."""
        destination = make_destination()
        unrated = make_activity(
            destination=destination, slug="unrated", feature_rank=10, rating_count=0
        )
        rated = make_activity(
            destination=destination,
            slug="rated",
            feature_rank=10,
            rating_avg=Decimal("4.90"),
            rating_count=30,
        )
        order = _actual(visible(Activity.objects.all(), today=TODAY), sort=SortOption.RATING)
        assert order.index(rated.pk) < order.index(unrated.pk)

    def test_a_null_price_term_does_not_disturb_the_ordering(self) -> None:
        """`accommodation` has no price at all since ADR 0013, so every row
        ties on that term and `feature_rank` decides. The bug this catches is
        the null sorting *first* and inverting the curation order."""
        destination = make_destination()
        ranked_last = make_accommodation(destination=destination, slug="last", feature_rank=10)
        ranked_first = make_accommodation(destination=destination, slug="first", feature_rank=1)
        order = _actual(visible(Accommodation.objects.all(), today=TODAY))
        assert order == [ranked_first.pk, ranked_last.pk]


class TestAccommodationRanksByTheSameExpression:
    """ADR 0013: three of the six terms are constants, and it is still §16.5."""

    def test_the_ordering_matches_the_domain_fed_the_same_absence(self) -> None:
        destination = make_destination()
        rows = [
            make_accommodation(destination=destination, slug="p1", feature_rank=50),
            make_accommodation(destination=destination, slug="p2", feature_rank=1),
            make_accommodation(destination=destination, slug="p3", feature_rank=50),
        ]
        assert _actual(visible(Accommodation.objects.all(), today=TODAY)) == _expected(
            [_accommodation_inputs(row) for row in rows]
        )

    def test_equal_rank_falls_through_to_the_total_order(self) -> None:
        """Without `id` these two have no defined relative order, and
        PostgreSQL is free to return either first depending on the plan."""
        destination = make_destination()
        first = make_accommodation(destination=destination, slug="same-a", feature_rank=100)
        second = make_accommodation(destination=destination, slug="same-b", feature_rank=100)
        assert _actual(visible(Accommodation.objects.all(), today=TODAY)) == [
            first.pk,
            second.pk,
        ]
