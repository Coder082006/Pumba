"""`/markets` — the destination selector's endpoint. SRS §24.6, §4.2 (v1.5), ADR 0018.

`test_catalogue_public_api.py` already proves the thing every public route
must prove: a row that is not public is not reachable. This file is about the
one way `/markets` is deliberately *unlike* every other public route, which
that file cannot express because it only knows how to assert absence.

**An announced market is present and closed at the same time.** It appears in
the list, it resolves by slug, and everything beneath it 404s. Three
properties that have to hold together — and each one, alone, looks like a bug:

* present but closed, read alone, looks like a leak;
* closed and absent, read alone, looks like the feature is missing;
* present and open-by-implication is the actual defect, and produces no error
  at all — a tile linking into a catalogue that answers 404.

So they are asserted together, against one market, in
`TestAnAnnouncedMarketIsListedAndClosed`.

The second thing here is the asymmetry with `get_destination`. That returns
`None` for an unlaunched row because a distinguishable "exists but hidden"
would publish a launch date nobody announced. `get_market` returns the row,
because for a market the launch *is* the announcement. Two endpoints, opposite
answers, same underlying rule — which is exactly the kind of pair that gets
"simplified" into one behaviour by somebody who reads only one of them.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.catalogue.tests.factories import (
    make_attraction,
    make_destination,
    make_market,
    make_region,
)

pytestmark = pytest.mark.django_db

#: Evaluated against today in UTC, as the views are — a hard-coded date would
#: start failing on its own the day it went past.
TODAY = timezone.now().date()
TOMORROW = TODAY + dt.timedelta(days=1)


@pytest.fixture
def public() -> APIClient:
    """No credentials. §9.3.2."""
    return APIClient()


def _rows(response: Any) -> list[dict[str, Any]]:
    assert response.status_code == 200, response.data
    return [dict(row) for row in response.data["data"]]


def _slugs(response: Any) -> list[str]:
    return [row["slug"] for row in _rows(response)]


def _subtree(market: Any) -> dict[str, Any]:
    region = make_region(country=market.country, market=market)
    destination = make_destination(region=region, slug="under-market", is_active=True)
    return {
        "destination": destination,
        "attraction": make_attraction(destination=destination, slug="under-market-ruins"),
    }


class TestAnAnnouncedMarketIsListedAndClosed:
    """The three properties, together, on one market."""

    @staticmethod
    def _announced() -> Any:
        return make_market(slug="arusha", name="Arusha", is_active=True, launch_date=TOMORROW)

    def test_it_appears_in_the_selector_flagged_closed(self, public: APIClient) -> None:
        self._announced()
        [row] = [r for r in _rows(public.get("/api/v1/markets")) if r["slug"] == "arusha"]
        assert row["is_open"] is False
        assert row["name"] == "Arusha"

    def test_its_own_page_resolves_rather_than_404ing(self, public: APIClient) -> None:
        """The difference from every other detail endpoint. A 404 here would
        make the tile on the landing page lead nowhere."""
        self._announced()
        response = public.get("/api/v1/markets/arusha")
        assert response.status_code == 200, response.data
        assert response.data["data"]["is_open"] is False

    def test_nothing_beneath_it_is_reachable(self, public: APIClient) -> None:
        """The half that makes the other two safe."""
        rows = _subtree(self._announced())
        assert "under-market" not in _slugs(public.get("/api/v1/destinations"))
        assert "under-market-ruins" not in _slugs(public.get("/api/v1/attractions"))
        assert public.get("/api/v1/destinations/under-market").status_code == 404
        assert public.get("/api/v1/attractions/under-market-ruins").status_code == 404
        # Built, so the absence above is a filter doing its job rather than a
        # fixture that never created anything.
        assert rows["destination"].is_active is True

    def test_all_of_it_opens_on_the_launch_date_with_no_deployment(self, public: APIClient) -> None:
        """§4.1's promise, end to end. The same rows, the same code, a
        different answer because the date moved."""
        market = self._announced()
        _subtree(market)
        market.launch_date = TODAY
        market.save(update_fields=["launch_date"])

        assert public.get("/api/v1/markets/arusha").data["data"]["is_open"] is True
        assert "under-market" in _slugs(public.get("/api/v1/destinations"))
        assert public.get("/api/v1/attractions/under-market-ruins").status_code == 200


class TestADeactivatedMarketIsNotAnnouncedAtAll:
    """Pemba. §4.1 defers it, which is a different decision from scheduling
    it, and the API has to tell them apart."""

    def test_it_is_absent_from_the_selector(self, public: APIClient) -> None:
        make_market(slug="pemba", name="Pemba", is_active=False)
        assert "pemba" not in _slugs(public.get("/api/v1/markets"))

    def test_its_page_is_a_404(self, public: APIClient) -> None:
        make_market(slug="pemba", name="Pemba", is_active=False)
        assert public.get("/api/v1/markets/pemba").status_code == 404

    def test_a_soft_deleted_market_is_gone_too(self, public: APIClient) -> None:
        """§7.7. `is_listed` is looser about `launch_date` and about nothing
        else."""
        market = make_market(slug="withdrawn", name="Withdrawn", is_active=True)
        market.delete()
        assert "withdrawn" not in _slugs(public.get("/api/v1/markets"))
        assert public.get("/api/v1/markets/withdrawn").status_code == 404


class TestTheShapeOfTheResponse:
    def test_a_market_carries_what_the_selector_tile_needs(self, public: APIClient) -> None:
        make_market(slug="zanzibar", name="Zanzibar", summary="Unguja.", is_active=True)
        [row] = _rows(public.get("/api/v1/markets"))
        assert set(row) == {"public_id", "name", "slug", "summary", "is_open", "country"}
        assert row["country"]["iso_code"]

    def test_it_resolves_by_public_id_as_well_as_by_slug(self, public: APIClient) -> None:
        """§7.2 exchanges the UUID; §24.6 puts the slug in the path. Both
        address the same row, as everywhere else in the catalogue."""
        market = make_market(slug="zanzibar", name="Zanzibar", is_active=True)
        by_slug = public.get("/api/v1/markets/zanzibar")
        by_id = public.get(f"/api/v1/markets/{market.public_id}")
        assert by_slug.status_code == by_id.status_code == 200
        assert by_slug.data["data"] == by_id.data["data"]

    def test_an_unknown_query_parameter_is_refused_rather_than_ignored(
        self, public: APIClient
    ) -> None:
        """A 200 that quietly ignored `?is_open=true` would be worse than an
        error: the caller believes a filter was applied."""
        make_market(slug="zanzibar", is_active=True)
        assert public.get("/api/v1/markets", {"is_open": "true"}).status_code == 422

    def test_the_list_needs_no_credentials(self, public: APIClient) -> None:
        assert public.get("/api/v1/markets").status_code == 200

    def test_markets_are_ordered_by_name(self, public: APIClient) -> None:
        """The tile grid has no ranking signal, and a grid that reorders
        between visits is one a returning visitor has to re-read."""
        country = make_market(slug="a", name="Aardvark", is_active=True).country
        make_market(country=country, slug="z", name="Zulu", is_active=True)
        make_market(country=country, slug="m", name="Mike", is_active=True)
        assert _slugs(public.get("/api/v1/markets")) == ["a", "m", "z"]
