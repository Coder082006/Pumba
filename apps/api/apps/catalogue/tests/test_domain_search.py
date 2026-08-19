"""Unified search and media ordering — SRS §7.6, §24.7, §35.7, TC-902."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalogue.domain.media import (
    RESPONSIVE_WIDTHS,
    ImageFormat,
    MediaItem,
    order_media,
    primary_of,
    srcset,
    variant_url,
)
from apps.catalogue.domain.search import (
    KIND_PRECEDENCE,
    Hit,
    SearchKind,
    SearchQueryError,
    merge_ranked,
    normalise_query,
    to_websearch_query,
)

MIN, MAX = 2, 120


class TestNormaliseQuery:
    def test_it_passes_an_ordinary_query_through(self) -> None:
        assert normalise_query("stone town", min_length=MIN, max_length=MAX) == "stone town"

    def test_it_trims(self) -> None:
        assert normalise_query("  nungwi  ", min_length=MIN, max_length=MAX) == "nungwi"

    def test_it_collapses_internal_whitespace(self) -> None:
        assert normalise_query("stone    town", min_length=MIN, max_length=MAX) == "stone town"

    def test_it_collapses_newlines_and_tabs(self) -> None:
        assert normalise_query("stone\n\ttown", min_length=MIN, max_length=MAX) == "stone town"

    def test_a_one_character_query_is_rejected(self) -> None:
        # §24.7: "requires two characters".
        with pytest.raises(SearchQueryError, match="at least 2 characters"):
            normalise_query("n", min_length=MIN, max_length=MAX)

    def test_an_empty_query_is_rejected(self) -> None:
        with pytest.raises(SearchQueryError):
            normalise_query("   ", min_length=MIN, max_length=MAX)

    def test_two_characters_is_enough(self) -> None:
        assert normalise_query("nu", min_length=MIN, max_length=MAX) == "nu"

    def test_length_is_measured_in_code_points_not_bytes(self) -> None:
        # Counting bytes would make the minimum three ASCII letters or one
        # Chinese character — a different rule for different tourists.
        assert normalise_query("中国", min_length=MIN, max_length=MAX) == "中国"

    def test_a_long_query_is_truncated_not_rejected(self) -> None:
        got = normalise_query("a" * 500, min_length=MIN, max_length=MAX)
        assert len(got) == MAX

    def test_control_characters_are_stripped(self) -> None:
        # NUL terminates a C string on the way to the database.
        assert normalise_query("stone\x00town", min_length=MIN, max_length=MAX) == "stonetown"

    def test_the_bounds_are_parameters(self) -> None:
        assert normalise_query("abc", min_length=3, max_length=3) == "abc"

    def test_an_invalid_minimum_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_length must be at least 1"):
            normalise_query("abc", min_length=0, max_length=10)

    def test_a_maximum_below_the_minimum_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be below min_length"):
            normalise_query("abc", min_length=5, max_length=2)


class TestToWebsearchQuery:
    def test_ordinary_text_is_unchanged(self) -> None:
        assert to_websearch_query("stone town") == "stone town"

    def test_an_ampersand_survives_rather_than_raising(self) -> None:
        # to_tsquery would raise a syntax error here, and a 500 on
        # /search?q=fish %26 chips is both a bug and a small denial of service.
        assert to_websearch_query("fish & chips") == "fish & chips"

    def test_balanced_quotes_are_kept_so_a_phrase_stays_a_phrase(self) -> None:
        assert to_websearch_query('"stone town"') == '"stone town"'

    def test_an_odd_quote_is_removed_rather_than_left_dangling(self) -> None:
        # An unterminated phrase makes PostgreSQL return nothing, which reads
        # to the tourist as "no results" rather than as a typo.
        assert to_websearch_query('"stone town') == "stone town"

    def test_removing_quotes_also_tidies_the_whitespace(self) -> None:
        assert to_websearch_query('stone " town') == "stone town"

    def test_websearch_operators_are_left_for_postgres_to_interpret(self) -> None:
        assert to_websearch_query("nungwi or kendwa") == "nungwi or kendwa"

    def test_it_is_deterministic(self) -> None:
        assert to_websearch_query('"stone town') == to_websearch_query('"stone town')


def hit(kind: SearchKind, id: int, rank: str) -> Hit:
    return Hit(kind=kind, id=id, rank=Decimal(rank))


class TestMergeRanked:
    def test_higher_rank_comes_first(self) -> None:
        groups = {
            SearchKind.ACTIVITY: [hit(SearchKind.ACTIVITY, 1, "0.2")],
            SearchKind.DESTINATION: [hit(SearchKind.DESTINATION, 2, "0.9")],
        }
        assert [h.id for h in merge_ranked(groups)] == [2, 1]

    def test_a_rank_tie_breaks_toward_the_container(self) -> None:
        # Somebody searching "Nungwi" wants the destination page above the
        # seventeen activities inside it.
        groups = {
            SearchKind.ACTIVITY: [hit(SearchKind.ACTIVITY, 1, "0.5")],
            SearchKind.ACCOMMODATION: [hit(SearchKind.ACCOMMODATION, 2, "0.5")],
            SearchKind.DESTINATION: [hit(SearchKind.DESTINATION, 3, "0.5")],
            SearchKind.ATTRACTION: [hit(SearchKind.ATTRACTION, 4, "0.5")],
        }
        assert [h.kind for h in merge_ranked(groups)] == list(KIND_PRECEDENCE)

    def test_id_breaks_a_tie_within_one_kind(self) -> None:
        groups = {
            SearchKind.ACTIVITY: [
                hit(SearchKind.ACTIVITY, 9, "0.5"),
                hit(SearchKind.ACTIVITY, 3, "0.5"),
            ]
        }
        assert [h.id for h in merge_ranked(groups)] == [3, 9]

    def test_the_ordering_is_total(self) -> None:
        # TC-902 across four tables: identical requests must produce identical
        # bytes, and rank alone ties constantly between tables.
        groups = {kind: [hit(kind, n, "0.5") for n in range(1, 6)] for kind in SearchKind}
        first = merge_ranked(groups)
        for _ in range(5):
            assert merge_ranked(groups) == first

    def test_input_order_does_not_affect_output(self) -> None:
        # A tie broken by dictionary iteration order is a result list that
        # reorders itself between two identical requests.
        a = {
            SearchKind.DESTINATION: [hit(SearchKind.DESTINATION, 1, "0.5")],
            SearchKind.ACTIVITY: [hit(SearchKind.ACTIVITY, 2, "0.5")],
        }
        b = {
            SearchKind.ACTIVITY: [hit(SearchKind.ACTIVITY, 2, "0.5")],
            SearchKind.DESTINATION: [hit(SearchKind.DESTINATION, 1, "0.5")],
        }
        assert merge_ranked(a) == merge_ranked(b)

    def test_an_empty_result_set_is_empty(self) -> None:
        assert merge_ranked({}) == ()

    def test_empty_groups_contribute_nothing(self) -> None:
        groups = {
            SearchKind.DESTINATION: [],
            SearchKind.ACTIVITY: [hit(SearchKind.ACTIVITY, 1, "0.5")],
        }
        assert [h.id for h in merge_ranked(groups)] == [1]

    def test_every_kind_has_a_declared_precedence(self) -> None:
        # A new SearchKind without a precedence entry would silently sort by
        # whatever the dict gave it.
        assert set(KIND_PRECEDENCE) == set(SearchKind)


class TestMediaOrdering:
    def test_the_primary_comes_first(self) -> None:
        items = [
            MediaItem(id=1, file_key="a", sort_order=0),
            MediaItem(id=2, file_key="b", sort_order=5, is_primary=True),
        ]
        assert [m.id for m in order_media(items)] == [2, 1]

    def test_sort_order_governs_the_rest(self) -> None:
        items = [
            MediaItem(id=1, file_key="a", sort_order=2),
            MediaItem(id=2, file_key="b", sort_order=1),
        ]
        assert [m.id for m in order_media(items)] == [2, 1]

    def test_id_breaks_a_sort_order_tie(self) -> None:
        # A gallery whose second and third images swap between page loads
        # shifts under the reader, which is a CLS failure as well as an
        # irritation.
        items = [MediaItem(id=9, file_key="a"), MediaItem(id=3, file_key="b")]
        assert [m.id for m in order_media(items)] == [3, 9]

    def test_two_primaries_are_ordered_rather_than_rejected(self) -> None:
        # The console prevents this; a bulk import or a race can still produce
        # it, and a gallery that raises is worse than one that picks the lower
        # id deterministically.
        items = [
            MediaItem(id=7, file_key="a", is_primary=True),
            MediaItem(id=2, file_key="b", is_primary=True),
        ]
        assert [m.id for m in order_media(items)] == [2, 7]

    def test_ordering_is_stable_across_input_permutations(self) -> None:
        items = [MediaItem(id=n, file_key=str(n)) for n in (4, 1, 3, 2)]
        assert order_media(items) == order_media(list(reversed(items)))

    def test_primary_of_returns_the_flagged_item(self) -> None:
        items = [
            MediaItem(id=1, file_key="a"),
            MediaItem(id=2, file_key="b", is_primary=True),
        ]
        primary = primary_of(items)
        assert primary is not None and primary.id == 2

    def test_primary_of_falls_back_to_the_first_ordered_item(self) -> None:
        # Every §24.8-24.12 screen leads with a hero; no hero is an empty space
        # at the top of the page.
        items = [MediaItem(id=5, file_key="a"), MediaItem(id=2, file_key="b")]
        primary = primary_of(items)
        assert primary is not None and primary.id == 2

    def test_primary_of_is_none_when_there_is_no_media(self) -> None:
        assert primary_of([]) is None

    def test_intrinsic_size_is_reported(self) -> None:
        # An <img> without width and height reserves no space and the page
        # reflows when it loads — most of the CLS budget.
        assert MediaItem(id=1, file_key="a", width=1600, height=900).has_intrinsic_size is True
        assert MediaItem(id=1, file_key="a").has_intrinsic_size is False
        assert MediaItem(id=1, file_key="a", width=1600).has_intrinsic_size is False


class TestVariantUrls:
    BASE = "https://cdn.example.test/media"

    def test_it_builds_a_url(self) -> None:
        got = variant_url(base_url=self.BASE, file_key="ab/cd/hash", width=640)
        assert got == "https://cdn.example.test/media/ab/cd/hash/640.webp"

    def test_the_format_is_selectable(self) -> None:
        got = variant_url(
            base_url=self.BASE, file_key="ab/cd/hash", width=640, fmt=ImageFormat.AVIF
        )
        assert got.endswith("/640.avif")

    def test_a_trailing_slash_on_the_base_is_tolerated(self) -> None:
        got = variant_url(base_url=self.BASE + "/", file_key="k", width=320)
        assert "//" not in got.removeprefix("https://")

    def test_it_is_pure_in_its_inputs(self) -> None:
        # No timestamp, no counter, no cache-busting parameter: §35.7's long
        # cache lifetime depends on the content hash being the only thing that
        # changes when the image does.
        a = variant_url(base_url=self.BASE, file_key="k", width=320)
        b = variant_url(base_url=self.BASE, file_key="k", width=320)
        assert a == b
        assert "?" not in a

    def test_an_unconfigured_width_is_rejected(self) -> None:
        # Every distinct width is a separate CDN transform and cache entry; an
        # open set turns the CDN into an image-resizing service for anyone who
        # can edit a URL.
        with pytest.raises(ValueError, match="not a configured responsive width"):
            variant_url(base_url=self.BASE, file_key="k", width=333)

    def test_an_absolute_file_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="relative object key"):
            variant_url(base_url=self.BASE, file_key="/etc/passwd", width=320)

    def test_an_empty_file_key_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="relative object key"):
            variant_url(base_url=self.BASE, file_key="", width=320)

    def test_srcset_covers_every_configured_width(self) -> None:
        got = srcset(base_url=self.BASE, file_key="k")
        for width in RESPONSIVE_WIDTHS:
            assert f"{width}w" in got
        assert got.count(",") == len(RESPONSIVE_WIDTHS) - 1

    def test_srcset_is_deterministic(self) -> None:
        assert srcset(base_url=self.BASE, file_key="k") == srcset(base_url=self.BASE, file_key="k")
