"""A tourist reads a price in their own currency — SRS §9.1, §24.1, ADR 0024.

Every price this platform stores is in the destination's currency, which for
the seeded catalogue is Tanzanian shillings. The tourists it is built for
arrive from Germany, Britain and America and cannot tell whether `TZS 94,500`
is cheap.

**The load-bearing assertion is that the charged figure never moves.** A
conversion that quietly replaced the price would be a different and much worse
product: `trip.currency` stays the destination's, BR-016's lock is untouched,
and the converted figure arrives beside it carrying the rate that produced it.
`TestTheChargedPriceIsUntouched` is that guarantee, and it is the one that must
still hold when Phase 8 makes the *charged* currency configurable.

The rate comes from `ports.fakes.FakeExchangeRates`, whose table is fixed so
these assertions are exact. Nothing prices from it — §18.4 gives the charged
conversion `finance.fx_rate`, frozen at `priced_at`, which does not exist yet.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any
from unittest import mock

import pytest
from rest_framework.test import APIClient

from apps.catalogue.tests.factories import make_activity, make_destination
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

#: A fixed "today" rather than the clock's. `date.today()` reads the
#: server's zone, and every trip these open is bounded by the destination's —
#: a test that straddled midnight in one and not the other would fail once a
#: day for reasons nobody could reproduce.
TODAY = dt.datetime.now(tz=dt.UTC).date()

#: `FakeExchangeRates.PER_USD`. One USD is 0.92 EUR and 2500 TZS, so one TZS is
#: 0.92 / 2500 EUR — the arithmetic every expectation below is derived from.
EUR_PER_TZS = Decimal("0.92") / Decimal("2500")


@pytest.fixture
def activity() -> Any:
    destination = make_destination(default_currency="TZS")
    return make_activity(destination, price_per_person=Decimal("45000.00"), currency="TZS")


def _get(path: str, currency: str | None = None) -> Any:
    headers = {"HTTP_X_CURRENCY": currency} if currency else {}
    return APIClient().get(path, **headers)


class TestTheCatalogueConverts:
    def test_a_price_is_shown_in_the_requested_currency(self, activity: Any) -> None:
        response = _get(f"/api/v1/activities/{activity.slug}", "EUR")
        assert response.status_code == 200, response.content
        display = response.json()["data"]["price_per_person_display"]
        assert display["currency"] == "EUR"
        assert Decimal(display["amount"]) == (Decimal("45000.00") * EUR_PER_TZS).quantize(
            Decimal("0.01")
        )

    def test_the_rate_travels_with_the_figure(self, activity: Any) -> None:
        """§20.6: "there are no untraceable conversions". A converted number
        with no rate beside it is indistinguishable from a price, which is the
        confusion ADR 0024 exists to prevent."""
        display = _get(f"/api/v1/activities/{activity.slug}", "EUR").json()["data"][
            "price_per_person_display"
        ]
        assert Decimal(display["rate"]) > 0
        assert display["source"]
        assert dt.datetime.fromisoformat(display["as_of"])

    def test_asking_for_nothing_converts_nothing(self, activity: Any) -> None:
        """The ordinary case, and it must not carry an empty object a client
        would render as a blank price."""
        data = _get(f"/api/v1/activities/{activity.slug}").json()["data"]
        assert data["price_per_person_display"] is None

    def test_asking_for_the_currency_it_is_already_in_converts_nothing(self, activity: Any) -> None:
        data = _get(f"/api/v1/activities/{activity.slug}", "TZS").json()["data"]
        assert data["price_per_person_display"] is None

    def test_a_currency_the_platform_cannot_show_is_ignored_not_rejected(
        self, activity: Any
    ) -> None:
        """§9.1's header is a display preference. Failing a public catalogue
        read because a browser remembered a currency an administrator has since
        retired would turn a cosmetic mistake into an outage."""
        response = _get(f"/api/v1/activities/{activity.slug}", "XYZ")
        assert response.status_code == 200
        assert response.json()["data"]["price_per_person_display"] is None

    def test_a_malformed_currency_is_ignored_too(self, activity: Any) -> None:
        response = _get(f"/api/v1/activities/{activity.slug}", "<script>")
        assert response.status_code == 200

    def test_the_response_varies_on_the_header(self, activity: Any) -> None:
        """Without this a CDN serves the first tourist's currency to everyone
        behind the same URL — a defect that only appears under a cache, in
        production, as prices in a currency nobody asked for."""
        response = _get(f"/api/v1/activities/{activity.slug}", "EUR")
        assert "X-Currency" in response.headers.get("Vary", "")


class TestTheChargedPriceIsUntouched:
    """ADR 0024's whole line, asserted rather than described."""

    def test_the_listing_price_does_not_move(self, activity: Any) -> None:
        data = _get(f"/api/v1/activities/{activity.slug}", "EUR").json()["data"]
        assert data["price_per_person"] == "45000.00"
        assert data["currency"] == "TZS"

    def test_the_trip_currency_does_not_move(self, activity: Any) -> None:
        """BR-010 and BR-016: a trip has one currency, from the destination,
        locked at first pricing. §9.1's header changes what is *shown* and
        nothing else."""
        tourist = signed_in_as()
        response = tourist.post(
            "/api/v1/trips",
            {
                "destination": activity.destination.slug,
                "start_date": (TODAY + dt.timedelta(days=30)).isoformat(),
                "end_date": (TODAY + dt.timedelta(days=33)).isoformat(),
                "adults": 2,
            },
            format="json",
            HTTP_X_CURRENCY="EUR",
        )
        assert response.status_code == 201, response.content
        assert response.json()["data"]["currency"] == "TZS"

    def test_a_trip_total_is_converted_beside_itself(self, activity: Any) -> None:
        tourist = signed_in_as()
        created = tourist.post(
            "/api/v1/trips",
            {
                "destination": activity.destination.slug,
                "start_date": (TODAY + dt.timedelta(days=30)).isoformat(),
                "end_date": (TODAY + dt.timedelta(days=33)).isoformat(),
                "adults": 2,
            },
            format="json",
        ).json()["data"]

        data = tourist.get(f"/api/v1/trips/{created['public_id']}", HTTP_X_CURRENCY="EUR").json()[
            "data"
        ]

        assert data["currency"] == "TZS"
        assert data["total_amount_display"]["currency"] == "EUR"


class TestThePreference:
    def test_a_currency_outside_the_enabled_set_is_refused_at_registration(self) -> None:
        """§9.4.1 says "preferred_currency in the enabled set", and the rule was
        written down and never checked until now — so a tourist could store a
        preference every page would then silently ignore."""
        response = APIClient().post(
            "/api/v1/auth/register",
            {
                "email": "nobody@example.com",
                "password": "a-long-enough-passphrase",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "preferred_currency": "XYZ",
            },
            format="json",
        )
        assert response.status_code == 422
        assert "preferred_currency" in str(response.content)

    def test_an_enabled_currency_is_accepted(self) -> None:
        """The control. Without it the check could reject everything."""
        response = APIClient().post(
            "/api/v1/auth/register",
            {
                "email": "somebody@example.com",
                "password": "a-long-enough-passphrase",
                "first_name": "Ada",
                "last_name": "Lovelace",
                "preferred_currency": "eur",
            },
            format="json",
        )
        assert response.status_code == 202, response.content

    def test_a_tourist_can_change_it(self) -> None:
        """§24.28: "Language, presentment currency"."""
        tourist = signed_in_as()
        response = tourist.patch("/api/v1/me", {"preferred_currency": "GBP"}, format="json")
        assert response.status_code == 200, response.content
        assert response.json()["data"]["profile"]["preferred_currency"] == "GBP"

    def test_the_change_is_validated_too(self) -> None:
        tourist = signed_in_as()
        assert (
            tourist.patch("/api/v1/me", {"preferred_currency": "XYZ"}, format="json").status_code
            == 422
        )

    def test_an_empty_patch_is_not_an_error(self) -> None:
        """What an idempotent client sends when nothing changed. A 422 here
        would make a retry look like a bug."""
        tourist = signed_in_as()
        assert tourist.patch("/api/v1/me", {}, format="json").status_code == 200

    def test_an_unknown_field_is_refused(self) -> None:
        """§30.6, through `StrictSerializer`. A typo must not silently do
        nothing — which on a settings screen looks exactly like success."""
        tourist = signed_in_as()
        assert tourist.patch("/api/v1/me", {"currency": "GBP"}, format="json").status_code == 422


class TestTheDisplayFigureRefusesToBeMoney:
    def test_indicative_amounts_cannot_be_summed(self) -> None:
        """The guard ADR 0024 rests on, asserted directly so that anybody who
        "fixes" it by adding `__add__` fails the suite rather than shipping."""
        from apps.common.display_money import IndicativeAmountError, convert_for_display
        from apps.common.money import Money
        from ports.fakes import FakeExchangeRates

        rate = FakeExchangeRates().indicative_rate(base="TZS", quote="EUR")
        assert rate is not None
        one = convert_for_display(Money(Decimal("1000.00"), "TZS"), rate=rate)
        two = convert_for_display(Money(Decimal("2000.00"), "TZS"), rate=rate)

        with pytest.raises(IndicativeAmountError):
            _ = one + two  # type: ignore[operator]
        with pytest.raises(IndicativeAmountError):
            sum([one, two])  # type: ignore[list-item]

    def test_money_refuses_to_add_one(self) -> None:
        """The other direction. `IndicativeAmount` carries `.amount` and
        `.currency`, so duck typing would have let it into a subtotal."""
        from apps.common.display_money import convert_for_display
        from apps.common.money import Money
        from ports.fakes import FakeExchangeRates

        rate = FakeExchangeRates().indicative_rate(base="TZS", quote="EUR")
        assert rate is not None
        indicative = convert_for_display(Money(Decimal("1000.00"), "TZS"), rate=rate)

        with pytest.raises(TypeError):
            _ = Money(Decimal("1.00"), "EUR") + indicative  # type: ignore[operator]


class TestTheRateIsFetchedOncePerRequest:
    """A response carries many prices and at most a handful of pairs.

    Free against the in-memory fake and one HTTP request each against the feed
    that eventually replaces it — the N+1 that only appears in production, on
    the endpoint a tourist reloads most.
    """

    def test_one_call_serves_every_price_on_a_page(self, activity: Any) -> None:
        from apps.common import presentment
        from apps.common.context import set_display_currency
        from ports.fakes import FakeExchangeRates

        fake = FakeExchangeRates()
        presentment.reset_rate_cache()
        set_display_currency("EUR")
        try:
            with mock.patch("apps.common.presentment.get_exchange_rate_port", return_value=fake):
                for _ in range(5):
                    assert presentment.display_of(Decimal("1000.00"), "TZS") is not None
            assert fake.calls == [("TZS", "EUR")]
        finally:
            presentment.reset_rate_cache()
            set_display_currency(None)

    def test_an_unavailable_pair_is_not_retried_per_price(self, activity: Any) -> None:
        """A `None` is cached as deliberately as a rate. Asking again once per
        price would turn one upstream failure into forty."""
        from apps.common import presentment
        from apps.common.context import set_display_currency

        class Silent:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def indicative_rate(self, *, base: str, quote: str) -> None:
                self.calls.append((base, quote))
                return None

        port = Silent()
        presentment.reset_rate_cache()
        set_display_currency("EUR")
        try:
            with mock.patch("apps.common.presentment.get_exchange_rate_port", return_value=port):
                for _ in range(5):
                    assert presentment.display_of(Decimal("1000.00"), "TZS") is None
            assert len(port.calls) == 1
        finally:
            presentment.reset_rate_cache()
            set_display_currency(None)

    def test_the_cache_does_not_outlive_the_request(self) -> None:
        """§18.4 gives the charged conversion its own frozen rate; a display
        rate that outlived the response it was fetched for would be the stale
        figure ADR 0024 refuses to show."""
        from apps.common import presentment
        from apps.common.context import reset_context, set_display_currency

        set_display_currency("EUR")
        assert presentment.display_of(Decimal("1000.00"), "TZS") is not None
        reset_context()
        assert presentment._rates.get() is None
