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


def _hidden_destination() -> tuple[str, Any]:
    destination = make_destination(slug="closed-market", name="Closed Market", is_active=False)
    return "/api/v1/destinations", destination


def _unlaunched_destination() -> tuple[str, Any]:
    destination = make_destination(
        slug="opens-tomorrow", name="Opens Tomorrow", launch_date=TOMORROW
    )
    return "/api/v1/destinations", destination


def _attraction_under_hidden_destination() -> tuple[str, Any]:
    destination = make_destination(slug="closed-market", is_active=False)
    return "/api/v1/attractions", make_attraction(destination=destination, slug="hidden-ruins")


def _activity_under_hidden_destination() -> tuple[str, Any]:
    destination = make_destination(slug="closed-market", is_active=False)
    return "/api/v1/activities", make_activity(destination=destination, slug="hidden-dive")


def _accommodation_under_hidden_destination() -> tuple[str, Any]:
    destination = make_destination(slug="closed-market", is_active=False)
    return "/api/v1/accommodation", make_accommodation(destination=destination, slug="hidden-lodge")


#: One scenario per public catalogue route: something that must not be visible,
#: and where to look for it. The list and detail routes for an entity share a
#: scenario — the row is built once and both are checked against it.
HIDDEN_ROW_CASES: dict[str, Any] = {
    "v1:catalogue:destination-list": _hidden_destination,
    "v1:catalogue:destination-detail": _unlaunched_destination,
    "v1:catalogue:attraction-list": _attraction_under_hidden_destination,
    "v1:catalogue:attraction-detail": _attraction_under_hidden_destination,
    "v1:catalogue:activity-list": _activity_under_hidden_destination,
    "v1:catalogue:activity-detail": _activity_under_hidden_destination,
    "v1:catalogue:accommodation-list": _accommodation_under_hidden_destination,
    "v1:catalogue:accommodation-detail": _accommodation_under_hidden_destination,
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
        path, row = HIDDEN_ROW_CASES[route]()
        if route.endswith("-detail"):
            # A hidden row and a missing one answer identically. Anything else
            # publishes the launch date of a market that has not opened.
            assert public.get(f"{path}/{row.public_id}").status_code == 404
            assert public.get(f"{path}/{row.slug}").status_code == 404
        else:
            assert str(row.public_id) not in _ids(public.get(path))

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
