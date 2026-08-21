"""The read path — SRS §7.2, §9.3.2, §24.7, §29 NFR-P01, TC-902.

What the list and detail selectors return, and what they cost.

**No sequential integer leaves.** §7.2. Asserted on the DTOs directly rather
than trusted to the serializer, because the serializer is the second place it
could leak and the DTO is the first.

**A hidden row is indistinguishable from a missing one.** `get_*` returns
`None` for both. The same reason §30.3 returns 404 rather than 403: a
distinguishable "exists but hidden" publishes the launch date of a market that
has not opened, and the sitemap is a public list of exactly which rows exist.

**Query count does not move with row count.** §29's NFR-P01 budget does not
survive one query per row for the destination chain or the gallery, and the
shape of that bug is that it passes every functional test. Parameterised over
1, 10 and 50 rows with the count asserted identical.

**Search never reaches `to_tsquery`.** A stray `&` there is a database error,
which on a public unauthenticated endpoint is a 500 and a cheap one to trigger.
`websearch_to_tsquery` accepts what a person types.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.catalogue import selectors
from apps.catalogue.domain.search import SearchKind, SearchQueryError
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
    make_media,
    make_tag,
)

TODAY = dt.date(2027, 8, 12)
TOMORROW = TODAY + dt.timedelta(days=1)

pytestmark = pytest.mark.django_db


class TestWhatTheDtoCarries:
    def test_no_dto_carries_a_database_id(self) -> None:
        """§7.2: *"Sequential integers are never returned to clients."*"""
        destination = make_destination()
        make_attraction(destination=destination)
        make_activity(destination=destination)
        make_accommodation(destination=destination)

        dtos = [
            *selectors.list_destinations(today=TODAY),
            *selectors.list_attractions(today=TODAY),
            *selectors.list_activities(today=TODAY),
            *selectors.list_accommodation(today=TODAY),
        ]
        assert dtos
        for dto in dtos:
            assert not hasattr(dto, "id")
            assert dto.public_id is not None

    def test_a_coordinate_arrives_as_the_domain_value_object(self) -> None:
        """§13.1. The type refuses an out-of-range or over-precise coordinate,
        so a bad seed row fails at the boundary rather than becoming a transfer
        quote."""
        destination = make_destination()
        make_accommodation(destination=destination)
        [stay] = selectors.list_accommodation(today=TODAY)
        assert isinstance(stay.coordinates.lat, Decimal)
        assert -90 <= stay.coordinates.lat <= 90

    def test_an_accommodation_dto_has_no_commercial_fields(self) -> None:
        """ADR 0013, on the read side.

        There is no branch suppressing a rate; there is no field to suppress.
        That is the difference between deferring a subsystem and hiding one.
        """
        destination = make_destination()
        make_accommodation(destination=destination)
        [stay] = selectors.list_accommodation(today=TODAY)
        for absent in ("base_rate", "price", "availability", "cancellation_policy", "provider"):
            assert not hasattr(stay, absent)

    def test_the_destination_timezone_travels_with_every_listing(self) -> None:
        """§15.2 evaluates opening hours in it and §7.2 renders timestamps in
        it. A client holding the listing never has to ask a second endpoint."""
        destination = make_destination(timezone="Africa/Dar_es_Salaam")
        make_attraction(destination=destination)
        [attraction] = selectors.list_attractions(today=TODAY)
        assert attraction.destination.timezone == "Africa/Dar_es_Salaam"

    def test_an_image_without_dimensions_is_dropped_rather_than_published(self) -> None:
        """`next/image` cannot reserve space without them, and the reflow is a
        §24 Lighthouse CLS failure on every page that shows it."""
        destination = make_destination()
        attraction = make_attraction(destination=destination)
        make_media(owner=attraction, width=None, height=None, file_key="broken")
        make_media(owner=attraction, width=1600, height=900, file_key="good")

        [dto] = selectors.list_attractions(today=TODAY)
        assert [item.file_key for item in dto.media] == ["good"]

    def test_the_gallery_is_ordered_primary_first(self) -> None:
        """`domain.media.order_media`. A gallery whose images swap between page
        loads shifts under the reader."""
        destination = make_destination()
        attraction = make_attraction(destination=destination)
        make_media(owner=attraction, file_key="second", sort_order=2, is_primary=False)
        make_media(owner=attraction, file_key="hero", sort_order=9, is_primary=True)

        [dto] = selectors.list_attractions(today=TODAY)
        assert [item.file_key for item in dto.media] == ["hero", "second"]


class TestHiddenAndMissingAreIndistinguishable:
    def test_a_hidden_destination_reads_as_absent(self) -> None:
        hidden = make_destination(is_active=False)
        assert selectors.get_destination(public_id=hidden.public_id, today=TODAY) is None

    def test_an_unlaunched_destination_reads_as_absent_until_its_date(self) -> None:
        launching = make_destination(is_active=True, launch_date=TOMORROW)
        assert selectors.get_destination(public_id=launching.public_id, today=TODAY) is None
        assert selectors.get_destination(public_id=launching.public_id, today=TOMORROW)

    def test_a_listing_under_a_hidden_destination_reads_as_absent(self) -> None:
        destination = make_destination(is_active=False)
        stay = make_accommodation(destination=destination)
        assert selectors.get_accommodation(public_id=stay.public_id, today=TODAY) is None

    def test_filtering_by_a_hidden_destination_does_not_promote_its_listings(self) -> None:
        """`_destination_id` resolves the §16.5 context term through `visible`.

        Otherwise a hidden market's slug is a way to reorder the public list,
        which also confirms that the market exists.
        """
        hidden = make_destination(is_active=False, slug="pemba-north", name="Pemba North")
        make_activity(destination=hidden, slug="pemba-dive")
        assert selectors.list_activities(today=TODAY, destination_slug="pemba-north") == ()


class TestQueryCountDoesNotMoveWithRowCount:
    @pytest.mark.parametrize("count", [1, 10, 50])
    def test_listing_accommodation_is_a_constant_number_of_queries(
        self, count: int, django_assert_num_queries: object
    ) -> None:
        destination = make_destination()
        for index in range(count):
            make_accommodation(destination=destination, slug=f"stay-{index}")

        # Two: the page of rows with its ancestor chain joined, and one gallery
        # query for the whole page. Not three, and never `count + 2`.
        with django_assert_num_queries(2):  # type: ignore[operator]
            rows = selectors.list_accommodation(today=TODAY)
        assert len(rows) == count

    @pytest.mark.parametrize("count", [1, 10])
    def test_listing_activities_is_a_constant_number_of_queries(
        self, count: int, django_assert_num_queries: object
    ) -> None:
        destination = make_destination()
        for index in range(count):
            make_activity(destination=destination, slug=f"activity-{index}")

        with django_assert_num_queries(2):  # type: ignore[operator]
            rows = selectors.list_activities(today=TODAY)
        assert len(rows) == count


class TestSearch:
    @pytest.fixture(autouse=True)
    def _catalogue(self) -> object:
        destination = make_destination(name="Nungwi", slug="nungwi")
        make_attraction(
            destination=destination, name="Nungwi Turtle Sanctuary", slug="turtle-sanctuary"
        )
        make_activity(destination=destination, name="Nungwi Sunset Dhow", slug="sunset-dhow")
        make_accommodation(destination=destination, name="Nungwi Beach Lodge", slug="beach-lodge")
        return destination

    def test_it_finds_across_all_four_tables(self) -> None:
        hits = selectors.search("Nungwi", today=TODAY, min_length=2, max_length=64)
        assert {hit.kind for hit in hits} == {kind.value for kind in SearchKind}

    def test_the_destination_leads_on_a_tie(self) -> None:
        """`KIND_PRECEDENCE`. Somebody searching "Nungwi" wants the page that
        contains the others, not the seventeenth activity inside it."""
        hits = selectors.search("Nungwi", today=TODAY, min_length=2, max_length=64)
        assert hits[0].kind == SearchKind.DESTINATION.value

    def test_operator_characters_do_not_reach_the_database_as_operators(self) -> None:
        """`to_tsquery` raises a database error on this; `websearch_to_tsquery`
        does not. On a public unauthenticated endpoint that difference is a 500
        and a cheap denial of service."""
        assert selectors.search("fish & chips", today=TODAY, min_length=2, max_length=64) == ()

    def test_an_unbalanced_quote_still_returns_results(self) -> None:
        """An odd quote makes PostgreSQL treat the tail as an unterminated
        phrase and return nothing, which reads to the tourist as "no results"
        rather than as a typo."""
        hits = selectors.search('"Nungwi', today=TODAY, min_length=2, max_length=64)
        assert hits

    def test_a_one_character_query_is_refused_before_the_database(self) -> None:
        """§24.7 *"requires two characters"*. Surfaces as 422, not as a scan of
        every row in the catalogue."""
        with pytest.raises(SearchQueryError):
            selectors.search("N", today=TODAY, min_length=2, max_length=64)

    def test_a_hidden_row_is_not_searchable(self, _catalogue: object) -> None:
        # Same region, so the shared country is reused: `make_destination`
        # builds a whole country by default and `country.iso_code` is unique
        # among live rows.
        hidden = make_destination(
            region=_catalogue.region,  # type: ignore[attr-defined]
            name="Pemba",
            slug="pemba",
            is_active=False,
        )
        make_attraction(destination=hidden, name="Pemba Reef", slug="pemba-reef")
        hits = selectors.search("Pemba", today=TODAY, min_length=2, max_length=64)
        assert hits == ()

    def test_the_same_query_returns_the_same_order_every_time(self) -> None:
        """TC-902, across four tables. Relevance alone ties constantly between
        them, and a tie broken by which query returned first is a result list
        that reorders itself between two identical requests."""
        first = selectors.search("Nungwi", today=TODAY, min_length=2, max_length=64)
        for _ in range(5):
            assert selectors.search("Nungwi", today=TODAY, min_length=2, max_length=64) == first


class TestTags:
    def test_the_chip_vocabulary_is_ordered_and_excludes_retired_tags(self) -> None:
        """§24.7. Rows, so a new interest needs no deployment — and a retired
        one leaves the chips without one either."""
        make_tag(slug="diving", label="Diving", sort_order=2)
        make_tag(slug="culture", label="Culture", sort_order=1)
        retired = make_tag(slug="retired", label="Retired", sort_order=0)
        retired.delete()

        assert [tag.slug for tag in selectors.list_tags()] == ["culture", "diving"]
