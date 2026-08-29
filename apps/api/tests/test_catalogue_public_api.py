"""The §9.3.2 public catalogue — SRS §9.1, §9.2, §9.3.2, §4.1, §7.7, §16.5, TC-902.

These are the only endpoints on the platform that answer an unauthenticated
request with rows. There is no principal to scope against, so §30.3's ownership
predicate has nothing to say here and the entire control is
`domain.visibility` — walked over the country → region → destination → listing
chain by `selectors.visible`.

**That is why `TestEveryPublicRouteFiltersByVisibility` is derived from the URL
conf and not written by hand.** `test_authorisation_matrix.py` exempts every
public catalogue route from the ownership check, correctly, and an exemption
that nothing re-proves is the hole the previous commit removed elsewhere. So
each of those routes must appear in `HIDDEN_ROW_CASES` below with a scenario
that builds a row which must not be visible, and the build fails for a route
that has none. A new public endpoint therefore cannot ship without somebody
answering "what does this hide, and how do you know".

The rest of the file is the two things pagination gets wrong quietly:

**A page boundary that skips or repeats a row.** Asserted by walking the whole
list one row at a time and comparing the concatenation against an unpaginated
read — the only assertion that catches an off-by-one in the keyset predicate,
because every individual page looks perfectly reasonable.

**A cursor honoured under an ordering it was not issued for.** A cursor from
`?sort=price_asc` describes a position in an ordering that no longer exists;
honouring it returns a page that looks entirely normal and is silently missing
rows. It is refused.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalogue.models import PropertyType
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
    make_market,
    make_media,
    make_tag,
)
from tests.test_authorisation_matrix import PUBLIC_CATALOGUE_ROUTES

pytestmark = pytest.mark.django_db

#: The public views evaluate visibility against today in UTC, so the fixtures
#: must too — a `launch_date` test pinned to a hard-coded date would start
#: failing on its own the day it went past.
TODAY = timezone.now().date()
TOMORROW = TODAY + dt.timedelta(days=1)


@pytest.fixture
def public() -> APIClient:
    """No credentials. §9.3.2: the catalogue is readable by anyone."""
    return APIClient()


def _ids(response: Any) -> list[str]:
    assert response.status_code == 200, response.data
    return [str(row["public_id"]) for row in response.data["data"]]


def _meta(response: Any) -> dict[str, Any]:
    return dict(response.data["meta"])


# ---------------------------------------------------------------------------
# Visibility — the whole authorisation story for these endpoints
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HiddenCase:
    """A row that must not be reachable, and the request that would reach it."""

    path: str
    row: Any
    params: dict[str, Any] = field(default_factory=dict)


def _hidden_destination() -> HiddenCase:
    return HiddenCase(
        "/api/v1/destinations",
        make_destination(slug="closed-market", name="Closed Market", is_active=False),
    )


def _unlaunched_destination() -> HiddenCase:
    return HiddenCase(
        "/api/v1/destinations",
        make_destination(slug="opens-tomorrow", name="Opens Tomorrow", launch_date=TOMORROW),
    )


def _attraction_under_hidden_destination() -> HiddenCase:
    destination = make_destination(slug="closed-market", is_active=False)
    return HiddenCase(
        "/api/v1/attractions", make_attraction(destination=destination, slug="hidden-ruins")
    )


def _activity_under_hidden_destination() -> HiddenCase:
    destination = make_destination(slug="closed-market", is_active=False)
    return HiddenCase(
        "/api/v1/activities", make_activity(destination=destination, slug="hidden-dive")
    )


def _accommodation_under_hidden_destination() -> HiddenCase:
    destination = make_destination(slug="closed-market", is_active=False)
    return HiddenCase(
        "/api/v1/accommodation", make_accommodation(destination=destination, slug="hidden-lodge")
    )


def _hidden_row_is_not_searchable() -> HiddenCase:
    """§24.7's box is a fifth way to reach every one of the four tables.

    A search that ignored visibility would publish an unlaunched market by
    name — the one disclosure the listing endpoints are careful about — and it
    would do it through the endpoint a tourist reaches first.
    """
    destination = make_destination(slug="closed-market", name="Sarabande", is_active=False)
    return HiddenCase("/api/v1/search", destination, {"q": "Sarabande"})


def _deactivated_market() -> HiddenCase:
    """A market, and the one state that hides it — which is *not* the state
    that hides a destination.

    An unlaunched destination is hidden; an unlaunched market is listed, with
    `is_open: false`, because §24.6's selector has to name a place the
    Platform is about to serve. So the row that must not be reachable here is
    the deactivated one — Pemba, which §4.1 defers outright.

    Getting this backwards in either direction is silent. Hide the announced
    market and the landing page loses the feature; publish the deactivated one
    and the Platform advertises a market nobody decided to open.
    """
    return HiddenCase(
        "/api/v1/markets",
        make_market(slug="deferred-island", name="Deferred Island", is_active=False),
    )


def _retired_tag() -> HiddenCase:
    """A tag has no parent, so it has no visibility chain — `deleted_at` and
    `is_active` are the whole of its lifecycle, and both must remove it from
    the chip strip or a retired interest keeps being offered."""
    tag = make_tag(slug="retired-interest", label="Retired Interest")
    tag.delete()
    return HiddenCase("/api/v1/tags", tag)


#: One scenario per public catalogue route: something that must not be visible,
#: and the request that would surface it. The list and detail routes for an
#: entity share a scenario — the row is built once and both are checked.
HIDDEN_ROW_CASES: dict[str, Any] = {
    "v1:catalogue:market-list": _deactivated_market,
    "v1:catalogue:market-detail": _deactivated_market,
    "v1:catalogue:destination-list": _hidden_destination,
    "v1:catalogue:destination-detail": _unlaunched_destination,
    "v1:catalogue:attraction-list": _attraction_under_hidden_destination,
    "v1:catalogue:attraction-detail": _attraction_under_hidden_destination,
    "v1:catalogue:activity-list": _activity_under_hidden_destination,
    "v1:catalogue:activity-detail": _activity_under_hidden_destination,
    "v1:catalogue:accommodation-list": _accommodation_under_hidden_destination,
    "v1:catalogue:accommodation-detail": _accommodation_under_hidden_destination,
    "v1:catalogue:search": _hidden_row_is_not_searchable,
    "v1:catalogue:tag-list": _retired_tag,
}


class TestEveryPublicRouteFiltersByVisibility:
    """The exemption in the authorisation matrix, re-proven behaviourally.

    §9.3.2's endpoints are exempt from the ownership check because they have no
    principal. That exemption is only honest if something else hides the rows,
    and this is that something else.
    """

    def test_every_public_catalogue_route_has_a_hidden_row_case(self) -> None:
        """Derived from the URL conf, so it cannot be satisfied by forgetting.

        A public catalogue endpoint added without a scenario here is an
        endpoint nobody has asked "what does this hide?" about — and the answer
        "nothing, it publishes unlaunched markets" looks identical in every
        other test.
        """
        assert PUBLIC_CATALOGUE_ROUTES, "no public catalogue routes were discovered"
        missing = PUBLIC_CATALOGUE_ROUTES - set(HIDDEN_ROW_CASES)
        assert not missing, (
            f"public catalogue routes with no visibility assertion: {sorted(missing)}. "
            "Add a scenario to HIDDEN_ROW_CASES that builds a row the endpoint must "
            "not publish."
        )

    def test_the_case_list_has_no_stale_entries(self) -> None:
        assert not set(HIDDEN_ROW_CASES) - PUBLIC_CATALOGUE_ROUTES

    @pytest.mark.parametrize("route", sorted(HIDDEN_ROW_CASES))
    def test_a_hidden_row_is_absent_from_its_endpoint(self, route: str, public: APIClient) -> None:
        case = HIDDEN_ROW_CASES[route]()
        if route.endswith("-detail"):
            # A hidden row and a missing one answer identically. Anything else
            # publishes the launch date of a market that has not opened.
            assert public.get(f"{case.path}/{case.row.public_id}").status_code == 404
            assert public.get(f"{case.path}/{case.row.slug}").status_code == 404
        else:
            assert str(case.row.public_id) not in _ids(public.get(case.path, case.params))

    def test_deactivating_a_destination_removes_its_listings_too(self, public: APIClient) -> None:
        """§4.1's Pemba switch, end to end over HTTP.

        One flag on one row, and the attractions beneath it leave the listing,
        the detail URL and — because commit 34 builds the sitemap from this
        payload — the sitemap together.
        """
        destination = make_destination(slug="open-market")
        attraction = make_attraction(destination=destination, slug="the-ruins")
        assert str(attraction.public_id) in _ids(public.get("/api/v1/attractions"))

        destination.is_active = False
        destination.save(update_fields=["is_active"])

        assert str(attraction.public_id) not in _ids(public.get("/api/v1/attractions"))
        assert public.get(f"/api/v1/attractions/{attraction.public_id}").status_code == 404

    def test_a_soft_deleted_row_leaves_the_public_surface(self, public: APIClient) -> None:
        """§7.7. The row survives for referential integrity; it is not public."""
        attraction = make_attraction(slug="retired-ruins")
        assert str(attraction.public_id) in _ids(public.get("/api/v1/attractions"))
        attraction.delete()
        assert str(attraction.public_id) not in _ids(public.get("/api/v1/attractions"))


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestKeysetPagination:
    @pytest.fixture
    def many(self) -> Any:
        """Rows whose ranking terms tie in places, so the keyset has to walk
        past the leading terms rather than being decided by the first one."""
        destination = make_destination(slug="one-market")
        for slug, rank in [(f"a{index}", 10 if index % 2 else 20) for index in range(25)]:
            make_attraction(destination=destination, slug=slug, feature_rank=rank)
        return destination

    def test_a_page_is_the_size_it_was_asked_for(self, many: Any, public: APIClient) -> None:
        response = public.get("/api/v1/attractions", {"limit": 10})
        assert len(_ids(response)) == 10
        assert _meta(response)["next_cursor"]

    def test_walking_every_page_yields_every_row_exactly_once(
        self, many: Any, public: APIClient
    ) -> None:
        """The assertion that catches an off-by-one in the keyset predicate.

        A boundary that skips one row or repeats one row produces pages that
        each look entirely reasonable; only the concatenation shows it. Walked
        three at a time so there are many boundaries to get wrong.
        """
        walked: list[str] = []
        cursor: str | None = None
        for _ in range(20):  # a bound, so a broken cursor cannot loop forever
            params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
            response = public.get("/api/v1/attractions", params)
            walked += _ids(response)
            cursor = _meta(response)["next_cursor"]
            if cursor is None:
                break
        assert cursor is None, "pagination did not terminate"

        whole = _ids(public.get("/api/v1/attractions", {"limit": 100}))
        assert walked == whole
        assert len(walked) == len(set(walked)) == 25

    def test_the_last_page_carries_no_cursor(self, many: Any, public: APIClient) -> None:
        """`None`, not an empty string — a client would send an empty string
        back, and a client that trusts a present cursor renders one empty
        final screen."""
        response = public.get("/api/v1/attractions", {"limit": 100})
        assert _meta(response)["next_cursor"] is None

    def test_an_exactly_full_page_is_the_last_page(self, many: Any, public: APIClient) -> None:
        """The classic off-by-one: 25 rows at 25 per page is one page, not two.

        Fetching `limit + 1` and discarding the extra is what makes this true
        without a second `COUNT(*)`.
        """
        assert _meta(public.get("/api/v1/attractions", {"limit": 25}))["next_cursor"] is None

    def test_a_cursor_from_a_different_ordering_is_refused(
        self, many: Any, public: APIClient
    ) -> None:
        """The bug this prevents is invisible: the response is a normal page
        of normal rows, and the ones it skipped are simply never seen."""
        first = public.get("/api/v1/attractions", {"limit": 5, "sort": "price_asc"})
        cursor = _meta(first)["next_cursor"]
        assert cursor

        replayed = public.get(
            "/api/v1/attractions", {"limit": 5, "sort": "recommended", "cursor": cursor}
        )
        assert replayed.status_code == 422
        assert replayed.json()["error"]["code"] == "INVALID_CURSOR"

    def test_a_cursor_for_a_different_entity_is_refused(self, many: Any, public: APIClient) -> None:
        """`feature_rank, id` is the whole ordering for both an attraction and
        an accommodation, so without the model in the fingerprint the two would
        be interchangeable."""
        # Under the same destination: `make_accommodation` builds a whole new
        # country by default, and `country.iso_code` is unique among live rows.
        make_accommodation(destination=many, slug="a-lodge")
        make_accommodation(destination=many, slug="b-lodge")
        cursor = _meta(public.get("/api/v1/attractions", {"limit": 1}))["next_cursor"]
        assert cursor
        assert (
            public.get("/api/v1/accommodation", {"limit": 1, "cursor": cursor}).status_code == 422
        )

    @pytest.mark.parametrize(
        "cursor",
        [
            "not-base64",
            "IiI",  # valid base64 of a JSON string, not the object expected
            "eyJ2Ijo5OTksIm8iOiJ4IiwiayI6W119",  # a future payload version
        ],
    )
    def test_a_malformed_cursor_is_a_422_not_a_500(self, cursor: str, public: APIClient) -> None:
        """Client input on an unauthenticated endpoint. A cursor that reached
        the database as a comparison value would be a 500 on a public URL, and
        a cheap one to trigger repeatedly."""
        assert public.get("/api/v1/attractions", {"cursor": cursor}).status_code == 422

    def test_an_empty_cursor_means_the_first_page(self, many: Any, public: APIClient) -> None:
        """`?cursor=` is absence, not a malformed value.

        DRF reads an empty query parameter on a non-required field as the
        parameter not being there, which is the right reading — a client
        building a URL from an unset variable means "no cursor", and answering
        422 would make the first page the hardest one to request.
        """
        response = public.get("/api/v1/attractions", {"cursor": "", "limit": 3})
        assert _ids(response) == _ids(public.get("/api/v1/attractions", {"limit": 3}))

    def test_the_limit_is_capped_rather_than_honoured(self, many: Any, public: APIClient) -> None:
        """`page.max_size` is a `system_setting` (rule 5), so an administrator
        can lower it during an incident. An unbounded limit is a way to ask for
        the whole catalogue, its ancestor chain and its galleries at once."""
        assert len(_ids(public.get("/api/v1/attractions", {"limit": 1000}))) == 25

    def test_a_page_is_a_constant_number_of_queries(
        self, many: Any, public: APIClient, django_assert_num_queries: Any
    ) -> None:
        """NFR-P01. The shape of the bug this catches is that it passes every
        functional test: one query per row for the ancestor chain or the
        gallery is correct, and unusable."""
        with django_assert_num_queries(2):
            public.get("/api/v1/attractions", {"limit": 25})


# ---------------------------------------------------------------------------
# What the payload says
# ---------------------------------------------------------------------------


class TestThePayload:
    def test_a_row_is_addressable_by_slug_and_by_identifier(self, public: APIClient) -> None:
        """§7.2 exchanges the UUID; §24.8 serves pages from slugs. Both resolve
        to the same row, so `/destinations/zanzibar` needs no lookup call."""
        destination = make_destination(slug="the-island", name="The Island")
        by_id = public.get(f"/api/v1/destinations/{destination.public_id}")
        by_slug = public.get("/api/v1/destinations/the-island")
        assert by_id.status_code == by_slug.status_code == 200
        assert by_id.data["data"] == by_slug.data["data"]

    def test_no_sequential_integer_appears_anywhere_in_a_payload(self, public: APIClient) -> None:
        """§7.2, over the nested tree: an attraction carries its destination,
        which carries its region, which carries its country."""
        make_attraction(slug="the-ruins")
        payload = public.get("/api/v1/attractions").data["data"]
        assert payload
        assert "id" not in _every_key(payload)

    def test_an_activity_with_too_few_reviews_states_no_mean(self, public: APIClient) -> None:
        """BR-127 at the boundary that matters.

        The domain rule is tested in isolation; this asserts the payload
        actually withholds the number, because a rule the serializer does not
        apply is a rule four clients will each have to remember.
        """
        make_activity(slug="the-new-one", rating_avg=Decimal("5.00"), rating_count=1)
        [row] = public.get("/api/v1/activities").data["data"]
        assert row["rating_avg"] is None
        # The count still travels — it is what a client renders "New" from.
        assert row["rating_count"] == 1

    def test_an_activity_with_enough_reviews_states_its_mean(self, public: APIClient) -> None:
        make_activity(slug="the-established-one", rating_avg=Decimal("4.50"), rating_count=12)
        [row] = public.get("/api/v1/activities").data["data"]
        assert row["rating_avg"] == "4.50"
        assert row["rating_count"] == 12

    def test_the_detail_route_applies_the_same_rule(self, public: APIClient) -> None:
        """The list and the detail payload are built by different call sites,
        and a rule applied to one of them is the kind of gap nobody sees until
        a single-activity page shows a rating the list page hides."""
        activity = make_activity(slug="the-quiet-one", rating_avg=Decimal("5.00"), rating_count=2)
        payload = public.get(f"/api/v1/activities/{activity.slug}").data["data"]
        assert payload["rating_avg"] is None

    def test_a_launch_day_catalogue_publishes_no_ratings_at_all(self, public: APIClient) -> None:
        """Every activity on day one has `rating_avg = 0.00` (SRS §7.5 makes
        the column NOT NULL DEFAULT 0.00, not nullable). Without BR-127 the
        catalogue would open publishing "0.0" for everything it lists."""
        make_activity(slug="opening-day")
        [row] = public.get("/api/v1/activities").data["data"]
        assert row["rating_avg"] is None
        assert row["rating_count"] == 0

    def test_money_leaves_as_a_string(self, public: APIClient) -> None:
        """§7.2 forbids float for money anywhere. A JSON number is parsed as an
        IEEE double by every client in the stack, which is that float."""
        make_activity(slug="the-dive", price_per_person=Decimal("120.50"), currency="USD")
        [row] = public.get("/api/v1/activities").data["data"]
        assert row["price_per_person"] == "120.50"
        assert row["currency"] == "USD"

    def test_an_accommodation_carries_no_commercial_field(self, public: APIClient) -> None:
        """ADR 0013 at the API boundary. There is no rate to suppress."""
        make_accommodation(slug="the-lodge", property_type=PropertyType.HOTEL)
        [row] = public.get("/api/v1/accommodation").data["data"]
        for absent in ("base_rate", "price", "availability", "cancellation_policy", "provider"):
            assert absent not in row

    def test_the_destination_timezone_travels_with_every_listing(self, public: APIClient) -> None:
        """§15.2 evaluates opening hours in it and §7.2 renders timestamps in
        it. A client holding the listing never has to ask a second endpoint."""
        destination = make_destination(slug="the-island", timezone="Africa/Dar_es_Salaam")
        make_attraction(destination=destination, slug="the-ruins")
        [row] = public.get("/api/v1/attractions").data["data"]
        assert row["destination"]["timezone"] == "Africa/Dar_es_Salaam"

    def test_a_gallery_is_ordered_primary_first(self, public: APIClient) -> None:
        attraction = make_attraction(slug="the-ruins")
        make_media(owner=attraction, file_key="second", sort_order=2, is_primary=False)
        make_media(owner=attraction, file_key="hero", sort_order=9, is_primary=True)
        [row] = public.get("/api/v1/attractions").data["data"]
        assert [item["file_key"] for item in row["media"]] == ["hero", "second"]


# ---------------------------------------------------------------------------
# Filters and ordering
# ---------------------------------------------------------------------------


class TestFiltersAndOrdering:
    def test_a_destination_filter_narrows_the_list(self, public: APIClient) -> None:
        here = make_destination(slug="here")
        there = make_destination(region=here.region, slug="there", name="There")
        mine = make_attraction(destination=here, slug="mine")
        theirs = make_attraction(destination=there, slug="theirs")

        found = _ids(public.get("/api/v1/attractions", {"destination": "here"}))
        assert str(mine.public_id) in found
        assert str(theirs.public_id) not in found

    def test_a_hidden_destination_slug_narrows_to_nothing_rather_than_reordering(
        self, public: APIClient
    ) -> None:
        """`_destination_id` resolves the §16.5 context term through `visible`.

        Otherwise a hidden market's slug is a way to reorder the public list,
        which also confirms that the market exists.
        """
        hidden = make_destination(slug="closed-market", is_active=False)
        make_activity(destination=hidden, slug="hidden-dive")
        assert _ids(public.get("/api/v1/activities", {"destination": "closed-market"})) == []

    def test_a_tag_filter_narrows_the_list(self, public: APIClient) -> None:
        destination = make_destination(slug="here")
        for slug in ("diving", "culture"):
            make_tag(slug=slug, label=slug.title())
        dive = make_attraction(destination=destination, slug="the-reef", tags=["diving"])
        ruin = make_attraction(destination=destination, slug="the-ruins", tags=["culture"])

        found = _ids(public.get("/api/v1/attractions", {"tags": ["diving"]}))
        assert found == [str(dive.public_id)]
        assert str(ruin.public_id) not in found

    def test_curation_order_is_what_comes_back(self, public: APIClient) -> None:
        """§16.5's `feature_rank ASC`: 1 is the most featured."""
        destination = make_destination(slug="here")
        last = make_activity(destination=destination, slug="last", feature_rank=90)
        first = make_activity(destination=destination, slug="first", feature_rank=1)
        assert _ids(public.get("/api/v1/activities")) == [
            str(first.public_id),
            str(last.public_id),
        ]

    def test_the_same_request_returns_the_same_order_every_time(self, public: APIClient) -> None:
        """TC-902. An ordering that is not total usually *looks* right the
        first time and reorders under a different query plan."""
        destination = make_destination(slug="here")
        for index in range(8):
            make_activity(destination=destination, slug=f"same-{index}", feature_rank=10)
        first = _ids(public.get("/api/v1/activities", {"limit": 100}))
        for _ in range(5):
            assert _ids(public.get("/api/v1/activities", {"limit": 100})) == first


class TestTheQueryStringIsClosed:
    def test_a_mistyped_filter_is_refused_rather_than_ignored(self, public: APIClient) -> None:
        """`?tag=` instead of `?tags=` would otherwise return the full,
        unfiltered list with a 200 — a filter that silently does not apply, and
        every layer downstream reporting success."""
        response = public.get("/api/v1/attractions", {"tag": "diving"})
        assert response.status_code == 422
        assert any(detail["field"] == "tag" for detail in response.json()["error"]["details"])

    def test_an_unknown_sort_is_refused(self, public: APIClient) -> None:
        """Silently falling back to the default would make a provider's
        placement dispute unanswerable: nobody could say which ordering
        actually ran."""
        assert public.get("/api/v1/activities", {"sort": "cheapest"}).status_code == 422

    def test_a_zero_limit_is_refused(self, public: APIClient) -> None:
        assert public.get("/api/v1/attractions", {"limit": 0}).status_code == 422


class TestAuthenticationIsNotRequired:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/destinations",
            "/api/v1/attractions",
            "/api/v1/activities",
            "/api/v1/accommodation",
        ],
    )
    def test_an_anonymous_request_is_served(self, path: str, public: APIClient) -> None:
        """§9.3.2. The catalogue is what a tourist reads before deciding to
        sign up, and what Google indexes."""
        assert public.get(path).status_code == 200

    def test_a_missing_row_is_a_404_with_the_platform_error_shape(self, public: APIClient) -> None:
        response = public.get("/api/v1/destinations/no-such-place")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestSearch:
    """§24.7's box, and §7.6's index behind it.

    Two properties dominate, and both are about what arbitrary public text can
    do. `to_tsquery` raises a database error on a stray `&` or an unbalanced
    quote — a 500 on an unauthenticated URL, and cheap to trigger in a loop —
    so nothing reaches it; `websearch_to_tsquery` accepts what a person types.
    And relevance alone ties constantly across four tables, so the merge falls
    through to a fixed kind precedence and then to id, because TC-902 requires
    two identical requests to return identical bytes.
    """

    @pytest.fixture(autouse=True)
    def _corpus(self) -> Any:
        destination = make_destination(slug="nungwi", name="Nungwi")
        make_attraction(
            destination=destination, slug="turtle-sanctuary", name="Nungwi Turtle Sanctuary"
        )
        make_activity(destination=destination, slug="sunset-dhow", name="Nungwi Sunset Dhow")
        make_accommodation(destination=destination, slug="beach-lodge", name="Nungwi Beach Lodge")
        return destination

    def test_it_searches_all_four_tables_at_once(self, public: APIClient) -> None:
        """One box, four tables. A tourist typing a place name does not know
        which of them holds what they are looking for."""
        response = public.get("/api/v1/search", {"q": "Nungwi"})
        assert response.status_code == 200
        assert {row["kind"] for row in response.data["data"]} == {
            "destination",
            "attraction",
            "activity",
            "accommodation",
        }

    def test_the_container_leads_on_a_tie(self, public: APIClient) -> None:
        """`KIND_PRECEDENCE`. Somebody searching "Nungwi" wants the page that
        contains the others, not the seventeenth activity inside it."""
        [first, *_] = public.get("/api/v1/search", {"q": "Nungwi"}).data["data"]
        assert first["kind"] == "destination"

    def test_a_kind_filter_narrows_the_search(self, public: APIClient) -> None:
        response = public.get("/api/v1/search", {"q": "Nungwi", "kind": ["activity"]})
        assert {row["kind"] for row in response.data["data"]} == {"activity"}

    def test_an_unknown_kind_is_refused(self, public: APIClient) -> None:
        assert public.get("/api/v1/search", {"q": "Nungwi", "kind": ["hotel"]}).status_code == 422

    @pytest.mark.parametrize(
        "query",
        [
            "fish & chips",
            '"Nungwi',
            "Nungwi | ",
            "!!!",
            "a:*",
            "(((",
            "Nungwi <-> beach",
        ],
    )
    def test_operator_characters_never_reach_the_database_as_operators(
        self, query: str, public: APIClient
    ) -> None:
        """Every one of these is a `to_tsquery` syntax error, which PostgreSQL
        raises as a database error — a 500 on a public URL. `websearch_to_
        tsquery` treats them as text, so the worst outcome is no results."""
        assert public.get("/api/v1/search", {"q": query}).status_code == 200

    def test_an_unbalanced_quote_still_finds_things(self, public: APIClient) -> None:
        """An odd quote makes PostgreSQL read the tail as an unterminated
        phrase and return nothing, which the tourist reads as "no results"
        rather than as their own typo."""
        assert public.get("/api/v1/search", {"q": '"Nungwi'}).data["data"]

    def test_a_one_character_query_is_refused_before_the_database(self, public: APIClient) -> None:
        """§24.7: *"requires two characters"*. A 422 naming `q`, not a scan of
        every row in the catalogue — and the threshold is
        `search.min_length` in `system_setting`, not a literal."""
        response = public.get("/api/v1/search", {"q": "N"})
        assert response.status_code == 422
        assert any(detail["field"] == "q" for detail in response.json()["error"]["details"])

    def test_a_missing_query_is_refused(self, public: APIClient) -> None:
        assert public.get("/api/v1/search").status_code == 422

    def test_an_absurdly_long_query_is_bounded_rather_than_refused(self, public: APIClient) -> None:
        """`search.max_length` truncates; it does not reject.

        Both answers close the same hole — an unbounded string is a way to
        make the parser do arbitrary work before the index is consulted, and
        nothing past the ceiling reaches PostgreSQL either way. Truncating is
        the kinder of the two: somebody who pastes a paragraph into a search
        box gets results for the start of it rather than an error telling them
        their query was too long.

        Asserted against the explicitly truncated form, so this is a statement
        about the bound being applied rather than about a long string happening
        to work.
        """
        ceiling = 64  # `search.max_length`
        # Chosen so the cut lands on a token boundary — "Nungwi Beach " is
        # thirteen characters and sixty-four is a whole number of repeats plus
        # a whole "Nungwi Beach". Otherwise the truncated tail is half a word,
        # which matches nothing, and the equality below would hold vacuously
        # with both sides empty.
        padded = "Nungwi Beach " * 500
        long_query = public.get("/api/v1/search", {"q": padded})
        assert long_query.status_code == 200
        assert _ids(long_query) == _ids(public.get("/api/v1/search", {"q": padded[:ceiling]}))
        assert _ids(long_query), "the truncated prefix should still match"

    def test_the_same_query_returns_the_same_order_every_time(self, public: APIClient) -> None:
        """TC-902, across four tables. A tie broken by whichever query
        returned first is a result list that reorders itself between two
        identical requests."""
        first = _ids(public.get("/api/v1/search", {"q": "Nungwi"}))
        for _ in range(5):
            assert _ids(public.get("/api/v1/search", {"q": "Nungwi"})) == first

    def test_a_hit_carries_enough_to_build_a_link_and_nothing_more(self, public: APIClient) -> None:
        """A hit that carried the whole entity would fan out four
        `select_related` trees to render a line of text."""
        [row, *_] = public.get("/api/v1/search", {"q": "Nungwi"}).data["data"]
        assert set(row) == {"kind", "public_id", "name", "slug", "destination_slug", "rank"}

    def test_a_listing_hit_carries_the_destination_it_belongs_to(self, public: APIClient) -> None:
        """So the result can be linked without a second request. A destination
        hit has none, which is why the field is nullable rather than blank."""
        rows = {
            row["kind"]: row for row in public.get("/api/v1/search", {"q": "Nungwi"}).data["data"]
        }
        assert rows["attraction"]["destination_slug"] == "nungwi"
        assert rows["destination"]["destination_slug"] is None

    def test_a_query_that_matches_nothing_is_an_empty_list_not_a_404(
        self, public: APIClient
    ) -> None:
        """Nothing was not found; nothing matched. A 404 would make the search
        box look broken for a perfectly ordinary query."""
        response = public.get("/api/v1/search", {"q": "hippopotamus"})
        assert response.status_code == 200
        assert response.data["data"] == []


class TestTags:
    def test_the_vocabulary_is_curated_order_not_alphabetical(self, public: APIClient) -> None:
        """§24.7's chip strip is editorial. `sort_order` is the curator's, and
        §16.5's determinism applies to it as much as to results."""
        make_tag(slug="diving", label="Diving", sort_order=2)
        make_tag(slug="culture", label="Culture", sort_order=1)
        assert [row["slug"] for row in public.get("/api/v1/tags").data["data"]] == [
            "culture",
            "diving",
        ]

    def test_a_new_interest_needs_no_deployment(self, public: APIClient) -> None:
        """The whole reason the vocabulary is rows. §4.2 forbids the words
        themselves appearing in application code, so adding one has to be a
        write, not a release."""
        assert public.get("/api/v1/tags").data["data"] == []
        make_tag(slug="birdwatching", label="Birdwatching")
        assert [row["slug"] for row in public.get("/api/v1/tags").data["data"]] == ["birdwatching"]

    def test_a_deactivated_tag_leaves_the_strip(self, public: APIClient) -> None:
        tag = make_tag(slug="diving", label="Diving")
        tag.is_active = False
        tag.save(update_fields=["is_active"])
        assert public.get("/api/v1/tags").data["data"] == []

    def test_a_tag_is_addressed_by_identifier_not_by_slug(self, public: APIClient) -> None:
        """§7.2. The slug is what the chip is called and is free to change,
        which makes it the wrong thing for the console to edit the row by."""
        make_tag(slug="diving", label="Diving")
        [row] = public.get("/api/v1/tags").data["data"]
        assert uuid.UUID(row["public_id"])

    def test_an_unknown_query_parameter_is_refused(self, public: APIClient) -> None:
        """`/tags` takes nothing, and "nothing" is still a shape. Ignoring
        `?is_active=false` would be a 200 that quietly did not do what was
        asked."""
        assert public.get("/api/v1/tags", {"is_active": "false"}).status_code == 422

    def test_the_strip_is_one_response_rather_than_a_cursor_walk(self, public: APIClient) -> None:
        """Deliberate, and asserted so it is not "fixed" into pagination
        later: a front end should not loop to draw a row of chips."""
        for index in range(30):
            make_tag(slug=f"interest-{index:02d}", label=f"Interest {index}", sort_order=index)
        response = public.get("/api/v1/tags")
        assert len(response.data["data"]) == 30
        assert "next_cursor" not in response.data["meta"]


def _every_key(payload: Any) -> set[str]:
    found: set[str] = set()
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            found |= set(item)
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return found
