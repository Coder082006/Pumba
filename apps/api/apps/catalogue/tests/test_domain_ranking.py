"""Deterministic catalogue ranking — SRS §16.5, TC-902.

Two things carry this file. The direction tests, because `feature_rank ASC`
alongside `rating_avg DESC` is exactly the pair someone inverts while
"tidying up"; and the injectivity test, because the `id` tie-break is what makes
byte-identical repeated output possible and it is the line most likely to be
deleted as redundant.
"""

from __future__ import annotations

import itertools
from decimal import Decimal

import pytest

from apps.catalogue.domain.ranking import (
    DEFAULT_TERMS,
    RankInputs,
    SortOption,
    displayable_rating,
    order_terms,
    rank_key,
)

ZANZIBAR = 1
PEMBA = 2


def row(
    id: int = 1,
    *,
    destination_id: int = ZANZIBAR,
    tags: frozenset[str] = frozenset(),
    feature_rank: int = 100,
    rating_avg: Decimal | None = None,
    rating_count: int = 0,
    price: Decimal | None = Decimal("50.00"),
    duration_minutes: int | None = 240,
    distance_metres: int | None = 1000,
) -> RankInputs:
    return RankInputs(
        id=id,
        destination_id=destination_id,
        tags=tags,
        feature_rank=feature_rank,
        rating_avg=rating_avg,
        rating_count=rating_count,
        price=price,
        duration_minutes=duration_minutes,
        distance_metres=distance_metres,
    )


def order(rows: list[RankInputs], **kwargs: object) -> list[int]:
    """Sort by the expression, with the rating gate open unless a test closes it.

    `min_display_count=0` means "state every mean", which is the ordering these
    tests were written against and is still exactly what §16.5 says once a
    subject has enough reviews. `TestTheRatingGate` below closes it, because
    that is the behaviour ADR 0017 added and it deserves its own tests rather
    than being folded into every other one.
    """
    kwargs.setdefault("min_display_count", 0)  # type: ignore[attr-defined]
    return [r.id for r in sorted(rows, key=lambda r: rank_key(r, **kwargs))]  # type: ignore[arg-type]


class TestTheExpressionInOrder:
    def test_the_selected_destination_comes_first(self) -> None:
        rows = [row(1, destination_id=PEMBA), row(2, destination_id=ZANZIBAR)]
        assert order(rows, selected_destination_id=ZANZIBAR) == [2, 1]

    def test_destination_match_outranks_everything_below_it(self) -> None:
        # A perfectly curated, five-star, cheap listing in the wrong
        # destination still loses to an unremarkable one in the right place.
        elsewhere = row(
            1,
            destination_id=PEMBA,
            feature_rank=1,
            rating_avg=Decimal("5.0"),
            rating_count=900,
            price=Decimal("1.00"),
        )
        here = row(2, destination_id=ZANZIBAR, feature_rank=999, price=Decimal("999.00"))
        assert order([elsewhere, here], selected_destination_id=ZANZIBAR) == [2, 1]

    def test_tag_overlap_is_boolean_not_a_count(self) -> None:
        # §16.5 uses the && operator: any overlap, not how much. One matching
        # tag ties with three, and feature_rank breaks the tie.
        one = row(1, tags=frozenset({"nature"}), feature_rank=5)
        three = row(2, tags=frozenset({"nature", "heritage", "culture"}), feature_rank=6)
        got = order([three, one], interest_tags=["nature", "heritage", "culture"])
        assert got == [1, 2]

    def test_any_tag_overlap_beats_none(self) -> None:
        matching = row(1, tags=frozenset({"nature"}), feature_rank=900)
        unmatched = row(2, tags=frozenset({"diving"}), feature_rank=1)
        assert order([unmatched, matching], interest_tags=["nature"]) == [1, 2]

    def test_feature_rank_is_ascending(self) -> None:
        # 1 outranks 100. Inverting this hands the top of every list to the
        # least curated listings.
        assert order([row(1, feature_rank=100), row(2, feature_rank=1)]) == [2, 1]

    def test_rating_avg_is_descending(self) -> None:
        rows = [row(1, rating_avg=Decimal("3.0")), row(2, rating_avg=Decimal("4.8"))]
        assert order(rows) == [2, 1]

    def test_unrated_listings_sort_last_not_first(self) -> None:
        # PostgreSQL's default for DESC is NULLS FIRST, which would put every
        # brand-new listing at the top of every list.
        rows = [row(1, rating_avg=None), row(2, rating_avg=Decimal("2.0"))]
        assert order(rows) == [2, 1]

    def test_rating_count_breaks_ties_between_equal_averages(self) -> None:
        rows = [
            row(1, rating_avg=Decimal("4.5"), rating_count=3),
            row(2, rating_avg=Decimal("4.5"), rating_count=200),
        ]
        assert order(rows) == [2, 1]

    def test_price_is_ascending(self) -> None:
        rows = [row(1, price=Decimal("90.00")), row(2, price=Decimal("10.00"))]
        assert order(rows) == [2, 1]

    def test_id_breaks_a_total_tie(self) -> None:
        assert order([row(7), row(3)]) == [3, 7]

    def test_the_full_expression_end_to_end(self) -> None:
        rows = [
            row(1, destination_id=PEMBA, feature_rank=1),
            row(2, tags=frozenset({"nature"}), feature_rank=50),
            row(3, feature_rank=10),
            row(4, feature_rank=10, rating_avg=Decimal("4.9"), rating_count=10),
            row(5, feature_rank=10, rating_avg=Decimal("4.9"), rating_count=99),
        ]
        got = order(rows, selected_destination_id=ZANZIBAR, interest_tags=["nature"])
        assert got == [2, 5, 4, 3, 1]


class TestLaunchDayWithNoReviews:
    """Phase 3 has no reviews at all. Every rating is None; every count is 0."""

    def test_ordering_is_still_total_with_no_ratings(self) -> None:
        rows = [row(i, feature_rank=10) for i in (5, 3, 9, 1)]
        assert order(rows) == [1, 3, 5, 9]

    def test_feature_rank_still_governs_with_no_ratings(self) -> None:
        rows = [row(1, feature_rank=50), row(2, feature_rank=5)]
        assert order(rows) == [2, 1]

    def test_price_still_governs_with_no_ratings(self) -> None:
        rows = [
            row(1, feature_rank=10, price=Decimal("80.00")),
            row(2, feature_rank=10, price=Decimal("20.00")),
        ]
        assert order(rows) == [2, 1]

    def test_a_zero_count_does_not_outrank_a_real_one(self) -> None:
        rows = [
            row(1, rating_avg=None, rating_count=0),
            row(2, rating_avg=Decimal("1.0"), rating_count=1),
        ]
        assert order(rows) == [2, 1]


class TestInjectivity:
    """TC-902 needs byte-identical repeated output, which needs a total order."""

    def test_rank_key_is_injective_over_rows_differing_only_by_id(self) -> None:
        rows = [row(i) for i in range(1, 51)]
        keys = {rank_key(r, min_display_count=0) for r in rows}
        assert len(keys) == len(rows)

    def test_rank_key_is_injective_across_a_generated_cross_product(self) -> None:
        combos = itertools.product(
            [ZANZIBAR, PEMBA],
            [frozenset(), frozenset({"nature"})],
            [1, 100],
            [None, Decimal("4.0")],
            [0, 9],
            [Decimal("10.00"), None],
        )
        rows = [
            row(
                i, destination_id=d, tags=t, feature_rank=f, rating_avg=ra, rating_count=rc, price=p
            )
            for i, (d, t, f, ra, rc, p) in enumerate(combos, start=1)
        ]
        keys = {
            rank_key(
                r, min_display_count=0, selected_destination_id=ZANZIBAR, interest_tags=["nature"]
            )
            for r in rows
        }
        assert len(keys) == len(rows)

    def test_repeated_sorting_is_stable_regardless_of_input_order(self) -> None:
        rows = [row(i, feature_rank=10) for i in range(1, 21)]
        first = order(rows)
        for shift in range(1, 6):
            rotated = rows[shift:] + rows[:shift]
            assert order(rotated) == first

    def test_removing_the_id_tie_break_would_break_injectivity(self) -> None:
        # Guards the reasoning rather than the code: if id is ever dropped from
        # DEFAULT_TERMS, this is the test that explains why it was there.
        assert DEFAULT_TERMS[-1].expression == "id"
        assert DEFAULT_TERMS[-1].descending is False


class TestExplicitSortOverrides:
    def test_price_asc_leads_with_price(self) -> None:
        rows = [
            row(1, feature_rank=1, price=Decimal("99.00")),
            row(2, feature_rank=900, price=Decimal("9.00")),
        ]
        assert order(rows, sort=SortOption.PRICE_ASC) == [2, 1]

    def test_price_desc_leads_with_price(self) -> None:
        rows = [
            row(1, feature_rank=900, price=Decimal("99.00")),
            row(2, feature_rank=1, price=Decimal("9.00")),
        ]
        assert order(rows, sort=SortOption.PRICE_DESC) == [1, 2]

    def test_an_explicit_sort_still_ends_on_the_tie_break(self) -> None:
        assert order([row(9), row(4)], sort=SortOption.PRICE_ASC) == [4, 9]

    def test_an_explicit_sort_still_prefers_the_selected_destination(self) -> None:
        # Equal price, different destination: the tourist's chosen destination
        # still wins, because they asked to sort by price, not to leave.
        rows = [row(1, destination_id=PEMBA), row(2, destination_id=ZANZIBAR)]
        assert order(rows, sort=SortOption.PRICE_ASC, selected_destination_id=ZANZIBAR) == [2, 1]

    def test_duration_sort(self) -> None:
        rows = [row(1, duration_minutes=480), row(2, duration_minutes=60)]
        assert order(rows, sort=SortOption.DURATION) == [2, 1]

    def test_distance_sort(self) -> None:
        rows = [row(1, distance_metres=9000), row(2, distance_metres=100)]
        assert order(rows, sort=SortOption.DISTANCE) == [2, 1]

    def test_rating_sort_puts_unrated_last(self) -> None:
        rows = [row(1, rating_avg=None), row(2, rating_avg=Decimal("1.0"))]
        assert order(rows, sort=SortOption.RATING) == [2, 1]

    @pytest.mark.parametrize("sort", list(SortOption))
    def test_every_sort_option_produces_a_total_order(self, sort: SortOption) -> None:
        rows = [row(i, feature_rank=10) for i in range(1, 11)]
        keys = {rank_key(r, min_display_count=0, sort=sort) for r in rows}
        assert len(keys) == len(rows)

    @pytest.mark.parametrize("sort", list(SortOption))
    def test_every_sort_option_ends_on_id(self, sort: SortOption) -> None:
        assert order_terms(sort)[-1].expression == "id"


class TestOrderTermsDeclaration:
    def test_the_default_expression_matches_the_srs_line_for_line(self) -> None:
        terms = order_terms(selected_destination_id=ZANZIBAR, interest_tags=["nature"])
        assert [(t.expression, t.descending) for t in terms] == [
            ("matches_selected_destination", True),
            ("matches_interest_tags", True),
            ("feature_rank", False),
            ("rating_avg", True),
            ("rating_count", True),
            ("price", False),
            ("id", False),
        ]

    def test_rating_avg_declares_nulls_last(self) -> None:
        term = next(t for t in DEFAULT_TERMS if t.expression == "rating_avg")
        assert term.descending is True
        assert term.nulls_last is True

    def test_the_destination_term_is_dropped_when_none_is_selected(self) -> None:
        names = [t.expression for t in order_terms(interest_tags=["nature"])]
        assert "matches_selected_destination" not in names
        assert "matches_interest_tags" in names

    def test_the_tag_term_is_dropped_when_no_tags_are_requested(self) -> None:
        names = [t.expression for t in order_terms(selected_destination_id=ZANZIBAR)]
        assert "matches_interest_tags" not in names
        assert "matches_selected_destination" in names

    def test_both_context_terms_drop_on_an_unfiltered_list(self) -> None:
        names = [t.expression for t in order_terms()]
        assert names == ["feature_rank", "rating_avg", "rating_count", "price", "id"]

    def test_dropping_a_context_term_does_not_change_the_resulting_order(self) -> None:
        # The dropped terms are constants for the request, so removing them is
        # an optimisation and must not be a behaviour change.
        rows = [row(i, feature_rank=(i * 7) % 5 + 1) for i in range(1, 11)]
        with_context = [
            r.id
            for r in sorted(
                rows, key=lambda r: rank_key(r, min_display_count=0, selected_destination_id=None)
            )
        ]
        assert with_context == order(rows)


class TestBr127:
    """A mean nobody may display is a mean nobody is handed.

    The rule is enforced by returning `None`, not by returning the value with a
    flag beside it — see ADR 0015. A flag is a rule in every client; an absent
    value is a rule in one place.
    """

    def test_a_subject_below_the_threshold_states_no_mean(self) -> None:
        assert displayable_rating(Decimal("5.00"), 1, min_display_count=3) is None

    def test_the_threshold_is_inclusive(self) -> None:
        """ "fewer than 3" means 3 qualifies. Off by one here is a rule that
        either hides a legitimate mean or publishes a forbidden one."""
        assert displayable_rating(Decimal("4.50"), 3, min_display_count=3) == Decimal("4.50")

    def test_an_unrated_subject_states_no_mean(self) -> None:
        """Launch day, for every activity in the catalogue. `rating_avg` is
        `0.00` rather than NULL (SRS §7.5), so without this rule the API would
        publish "0.0" for everything the platform has ever listed."""
        assert displayable_rating(Decimal("0.00"), 0, min_display_count=3) is None

    def test_the_threshold_is_a_parameter_not_a_constant(self) -> None:
        """Rule 5. A market with thinner supply may set it lower, and BR-127's
        "3" is a default rather than a law."""
        assert displayable_rating(Decimal("4.90"), 1, min_display_count=1) == Decimal("4.90")

    def test_a_threshold_of_zero_states_every_mean(self) -> None:
        assert displayable_rating(Decimal("0.00"), 0, min_display_count=0) == Decimal("0.00")

    def test_a_negative_threshold_is_refused(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            displayable_rating(Decimal("4.00"), 10, min_display_count=-1)

    def test_a_thin_five_star_does_not_outrank_an_established_four_eight(self) -> None:
        """ADR 0017, and the inverse of what this test asserted before it.

        It used to pin the opposite — that §16.5 ranked on the raw mean while
        BR-127 suppressed the display, so one five-star review bought top
        placement on a page that showed "New". That was recorded as a Product
        Owner decision and has now been taken: ranking uses the *displayable*
        mean, so a subject with too few reviews ranks as unrated.

        The test is kept rather than deleted because the pair of them is the
        record. It was doing its job when it failed — it is what said, in the
        commit that changed this, that a published ordering was moving.
        """
        loud = RankInputs(
            id=1,
            destination_id=1,
            tags=frozenset(),
            feature_rank=100,
            rating_avg=Decimal("5.00"),
            rating_count=1,
            price=Decimal("100.00"),
        )
        established = RankInputs(
            id=2,
            destination_id=1,
            tags=frozenset(),
            feature_rank=100,
            rating_avg=Decimal("4.80"),
            rating_count=50,
            price=Decimal("100.00"),
        )
        ranked = sorted([established, loud], key=lambda r: rank_key(r, min_display_count=3))
        assert [row.id for row in ranked] == [2, 1], "the thin five-star must not lead"

    def test_ranking_and_display_are_the_same_rule(self) -> None:
        """The property that makes this unviolatable rather than documented.

        A subject ranks on exactly the value it is allowed to show. If those
        two ever diverge again it is because somebody changed one of them, and
        this is the test that says so.
        """
        for count in (0, 1, 2, 3, 50):
            row = RankInputs(
                id=count + 1,
                destination_id=1,
                tags=frozenset(),
                feature_rank=100,
                rating_avg=Decimal("4.20"),
                rating_count=count,
                price=Decimal("10.00"),
            )
            shown = displayable_rating(row.rating_avg, row.rating_count, min_display_count=3)
            # With no destination and no tags selected, both context terms are
            # dropped, so the key is (feature_rank, rating_avg, rating_count,
            # price, id) and the rating term is at index 1. Each element is
            # `(null_rank, magnitude)`; a null_rank of 1 is the NULLS LAST
            # sentinel.
            null_rank, _ = rank_key(row, min_display_count=3)[1]
            assert (null_rank == 1) == (shown is None), f"count={count}"
