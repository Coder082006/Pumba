"""`POST /transport/quotes` and `GET /transport/corridors` — SRS §9.4.4, §9.3.4.

This file lives under `tests/` rather than `apps/trip/tests/` for the reason
`administrators.py` gives: §6.4 forbids `trip` from importing `identity`, and a
test needing a signed-in tourist spans both modules.

**The §30.3 assertion is the reason this file exists at all.** §9.4.4 carries
the trip in the *body*, which means the authorisation matrix's static checks —
all of which read the URL — cannot see it. `SCOPED_BY_A_BODY_IDENTIFIER`
records the claim and guards it three ways; this is where the claim is proven
behaviourally, and the strong form is asserted: a stranger's request and a
request for a trip that never existed produce the **same status and the same
body**. Only checking the status would pass for an implementation that leaked
existence through the message.

**Everything else here is about the three ways a leg can fail to price**, which
§24.16 renders differently and which a screen must be able to tell apart:
a route with no tariff (contact support), a metered leg that cannot be measured
(retry), and a party too large for any vehicle (a fact about the party).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.gis.geos import Point
from rest_framework.test import APIClient

from apps.catalogue.tests.factories import make_accommodation, make_destination, make_region
from apps.transport.models import TariffScope, TransferCorridor, TransferTariff, VehicleClass
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

QUOTES = "/api/v1/transport/quotes"
CORRIDORS = "/api/v1/transport/corridors"

START = dt.date(2027, 6, 1)
END = dt.date(2027, 6, 4)
DEPART_AT = dt.datetime(2027, 6, 2, 2, 45, tzinfo=dt.UTC)


@pytest.fixture
def classes() -> dict[str, VehicleClass]:
    """§12.4's four classes, in Appendix C's order."""
    spec = [("STANDARD", 4, 3, 1), ("COMFORT", 4, 3, 2), ("VAN", 7, 6, 3), ("MINIBUS", 14, 12, 4)]
    return {
        code: VehicleClass.objects.create(
            code=code, name=code.title(), seats=seats, luggage_capacity=bags, display_order=order
        )
        for code, seats, bags, order in spec
    }


@pytest.fixture
def gateway() -> Any:
    region = make_region()
    return make_destination(
        region,
        name="Bay Airport",
        slug="bay-airport",
        is_gateway=True,
        gateway_type="AIRPORT",
        gateway_code="BAY",
        centroid=Point(174.00, -35.20, srid=4326),
    )


@pytest.fixture
def resort(gateway: Any) -> Any:
    return make_destination(
        gateway.region,
        name="North Beach",
        slug="north-beach",
        centroid=Point(174.30, -35.00, srid=4326),
    )


@pytest.fixture
def tourist() -> APIClient:
    return signed_in_as()


def _trip(client: APIClient, destination_slug: str) -> Any:
    response = client.post(
        "/api/v1/trips",
        {
            "destination": destination_slug,
            "start_date": START.isoformat(),
            "end_date": END.isoformat(),
            "adults": 2,
        },
        format="json",
    )
    assert response.status_code == 201, response.content
    return response.json()["data"]


def _payload(trip_id: str, gateway: Any, resort: Any, **overrides: Any) -> dict[str, Any]:
    leg: dict[str, Any] = {
        "reference": "leg-1",
        "origin": {"destination": gateway.slug},
        "target": {"destination": resort.slug},
        "depart_at": DEPART_AT.isoformat(),
        "pax": 2,
        "luggage": 2,
    }
    leg.update(overrides.pop("leg", {}))
    body: dict[str, Any] = {"trip_id": trip_id, "legs": [leg]}
    body.update(overrides)
    return body


def _corridor(
    klass: VehicleClass, origin: Any, target: Any, price: str = "90000.00", **overrides: Any
) -> TransferCorridor:
    values: dict[str, Any] = {
        "origin_destination_id": origin.pk,
        "target_destination_id": target.pk,
        "vehicle_class": klass,
        "fixed_price": Decimal(price),
        "currency": "NZD",
        "valid_from": dt.date(2027, 1, 1),
    }
    values.update(overrides)
    return TransferCorridor.objects.create(**values)


def _tariff(klass: VehicleClass, region_id: int, **overrides: Any) -> TransferTariff:
    values: dict[str, Any] = {
        "scope": TariffScope.REGION,
        "region_id": region_id,
        "vehicle_class": klass,
        "base_fare": Decimal("12.00"),
        "per_km_rate": Decimal("0.4000"),
        "currency": "NZD",
        "valid_from": dt.date(2027, 1, 1),
    }
    values.update(overrides)
    return TransferTariff.objects.create(**values)


class TestAQuotedLeg:
    def test_a_corridor_prices_it(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)

        response = tourist.post(QUOTES, _payload(trip["public_id"], gateway, resort), format="json")

        assert response.status_code == 200, response.content
        (leg,) = response.json()["data"]
        assert leg["reference"] == "leg-1"
        assert [o["vehicle_class"] for o in leg["options"]] == ["STANDARD"]
        price = leg["options"][0]["price"]
        assert price["amount"] == "90000.00"
        assert price["currency"] == "NZD"
        # §24.1's conversion, absent because this request asked for no
        # currency. Null rather than missing — see `MoneySerializer.display`.
        assert price["display"] is None

    def test_the_leg_names_its_endpoints(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§24.16 shows the tourist where the driver is collecting them from.
        An answer that echoed only the client's own `reference` would leave the
        screen unable to caption anything it did not already know."""
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)
        (leg,) = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        ).json()["data"]
        assert leg["origin"] == gateway.name
        assert leg["target"] == resort.name

    def test_the_breakdown_sums_to_the_price(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§9.4.4's `breakdown`. A quote whose parts disagreed with its total
        would be the first thing a tourist queried."""
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)
        (leg,) = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        ).json()["data"]
        parts = leg["options"][0]["breakdown"]
        total = sum(Decimal(parts[k]) for k in ("base", "distance", "time", "surcharges"))
        assert total == Decimal(leg["options"][0]["price"]["amount"])

    def test_every_fitting_class_is_offered(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§12.4: capacity **filters**; it does not select. §24.16 renders the
        survivors as cards and the tourist chooses."""
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort, "90000.00")
        _corridor(classes["VAN"], gateway, resort, "130000.00")
        (leg,) = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        ).json()["data"]
        assert [o["vehicle_class"] for o in leg["options"]] == ["STANDARD", "VAN"]

    def test_a_class_too_small_for_the_party_is_not_offered(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)
        _corridor(classes["VAN"], gateway, resort, "130000.00")
        (leg,) = tourist.post(
            QUOTES,
            _payload(trip["public_id"], gateway, resort, leg={"pax": 6}),
            format="json",
        ).json()["data"]
        assert [o["vehicle_class"] for o in leg["options"]] == ["VAN"]

    def test_the_match_names_the_rule_and_the_rung(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§12.4 stores the matched rule id "so the price is reproducible
        forever", and §27.11's preview tool shows an administrator which rung
        answered."""
        trip = _trip(tourist, resort.slug)
        row = _corridor(classes["STANDARD"], gateway, resort)
        (leg,) = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        ).json()["data"]
        match = leg["options"][0]["match"]
        assert match["kind"] == "CORRIDOR"
        assert match["step"] == 1
        assert match["rule"] == str(row.public_id)


class TestSection126OnTheWire:
    def test_an_approximate_leg_is_badged(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """ADR 0019 and §24.17. No routing provider is configured, so every
        distance is a haversine estimate — and the client must be able to say
        so rather than presenting it as a measurement."""
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)
        (leg,) = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        ).json()["data"]
        assert leg["estimate_quality"] == "APPROXIMATE"
        assert leg["distance_m"] > 0

    def test_a_corridor_prices_anyway(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§12.6 forbids the haversine fallback only "for a priced corridor
        **without** a fixed corridor price". This leg has one, so an
        unmeasurable road does not stop it being sold — which is the whole
        reason §24.16 works at all today."""
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)
        response = tourist.post(QUOTES, _payload(trip["public_id"], gateway, resort), format="json")
        assert response.status_code == 200

    def test_a_metered_leg_refuses_with_502(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """The other half. With only a metered tariff configured and no
        measured route, §12.6 says 502 rather than a guessed price."""
        trip = _trip(tourist, resort.slug)
        _tariff(classes["STANDARD"], resort.region_id)
        response = tourist.post(QUOTES, _payload(trip["public_id"], gateway, resort), format="json")
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "ROUTING_UNAVAILABLE"
        assert response.json()["error"]["retryable"] is True

    def test_no_price_appears_in_the_refusal(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """The failure ADR 0019 calls quality laundering: an approximate
        distance turned into a number a tourist reads as a fare."""
        trip = _trip(tourist, resort.slug)
        _tariff(classes["STANDARD"], resort.region_id)
        response = tourist.post(QUOTES, _payload(trip["public_id"], gateway, resort), format="json")
        assert "amount" not in response.content.decode()


class TestWhenNothingIsConfigured:
    def test_it_is_422_and_not_a_guess(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§12.4: "No match -> 422 NO_TARIFF_CONFIGURED (never guess a price)".
        §24.16 renders it as "transfers to this location are not yet available
        — contact support"."""
        trip = _trip(tourist, resort.slug)
        response = tourist.post(QUOTES, _payload(trip["public_id"], gateway, resort), format="json")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NO_TARIFF_CONFIGURED"

    def test_the_two_failures_are_distinguishable(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§24.16 shows a retry for one and support for the other. A client
        that could not tell them apart would offer a retry that can never
        succeed."""
        trip = _trip(tourist, resort.slug)
        unconfigured = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        )
        _tariff(classes["STANDARD"], resort.region_id)
        unmeasurable = tourist.post(
            QUOTES, _payload(trip["public_id"], gateway, resort), format="json"
        )
        assert unconfigured.status_code != unmeasurable.status_code
        assert unconfigured.json()["error"]["code"] != unmeasurable.json()["error"]["code"]

    def test_a_party_too_large_is_an_empty_option_list(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """A third state again, and not an error: a fact about the party rather
        than about our configuration."""
        trip = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)
        response = tourist.post(
            QUOTES,
            _payload(trip["public_id"], gateway, resort, leg={"pax": 30}),
            format="json",
        )
        assert response.status_code == 200
        assert response.json()["data"][0]["options"] == []


class TestOwnership:
    def test_a_stranger_and_a_missing_trip_are_indistinguishable(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§30.3, in its strong form. The identifier is in the body, so no
        static check in the authorisation matrix can see this — which is why
        `SCOPED_BY_A_BODY_IDENTIFIER` names this test.
        """
        mine = _trip(tourist, resort.slug)
        _corridor(classes["STANDARD"], gateway, resort)

        stranger = signed_in_as()
        foreign = stranger.post(QUOTES, _payload(mine["public_id"], gateway, resort), format="json")
        absent = stranger.post(QUOTES, _payload(str(uuid.uuid4()), gateway, resort), format="json")

        assert foreign.status_code == 404
        assert foreign.status_code == absent.status_code
        assert foreign.json()["error"]["code"] == absent.json()["error"]["code"]
        assert foreign.json()["error"]["message"] == absent.json()["error"]["message"]

    def test_ownership_is_checked_before_anything_is_priced(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """A stranger naming an unconfigured route must get 404, not 422.

        Otherwise the error code itself becomes an oracle: a 422 says the trip
        was theirs to quote and only the tariff was missing.
        """
        mine = _trip(tourist, resort.slug)
        stranger = signed_in_as()
        response = stranger.post(
            QUOTES, _payload(mine["public_id"], gateway, resort), format="json"
        )
        assert response.status_code == 404

    def test_it_needs_a_principal_at_all(self, gateway: Any, resort: Any) -> None:
        assert APIClient().post(QUOTES, {}, format="json").status_code in (401, 403)


class TestTheRequestIsStrict:
    def test_an_endpoint_naming_nothing_is_refused(
        self, tourist: APIClient, gateway: Any, resort: Any
    ) -> None:
        trip = _trip(tourist, resort.slug)
        payload = _payload(trip["public_id"], gateway, resort)
        payload["legs"][0]["origin"] = {}
        assert tourist.post(QUOTES, payload, format="json").status_code == 422

    def test_an_endpoint_naming_two_things_is_refused(
        self, tourist: APIClient, gateway: Any, resort: Any
    ) -> None:
        """§12.2 binds an endpoint to one place. Two would make the tariff
        depend on which the server happened to read first."""
        trip = _trip(tourist, resort.slug)
        hotel = make_accommodation(resort)
        payload = _payload(trip["public_id"], gateway, resort)
        payload["legs"][0]["origin"] = {
            "destination": gateway.slug,
            "accommodation": hotel.slug,
        }
        assert tourist.post(QUOTES, payload, format="json").status_code == 422

    def test_half_a_coordinate_is_refused(
        self, tourist: APIClient, gateway: Any, resort: Any
    ) -> None:
        trip = _trip(tourist, resort.slug)
        payload = _payload(trip["public_id"], gateway, resort)
        payload["legs"][0]["origin"]["latitude"] = "-35.2"
        assert tourist.post(QUOTES, payload, format="json").status_code == 422

    def test_two_legs_may_not_share_a_reference(
        self, tourist: APIClient, gateway: Any, resort: Any
    ) -> None:
        """The client matches answers to rows by `reference`. Two legs sharing
        one would silently give a screen the wrong price for a leg."""
        trip = _trip(tourist, resort.slug)
        payload = _payload(trip["public_id"], gateway, resort)
        payload["legs"].append(dict(payload["legs"][0]))
        assert tourist.post(QUOTES, payload, format="json").status_code == 422

    def test_an_unknown_place_is_404(self, tourist: APIClient, gateway: Any, resort: Any) -> None:
        """§30.3 again: a withdrawn listing must be indistinguishable from one
        that never existed, so this is not a field-level 422."""
        trip = _trip(tourist, resort.slug)
        payload = _payload(trip["public_id"], gateway, resort)
        payload["legs"][0]["target"] = {"destination": "nowhere-at-all"}
        assert tourist.post(QUOTES, payload, format="json").status_code == 404

    def test_a_hotel_is_an_acceptable_endpoint(
        self, tourist: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§12.2's second binding, and §24.16's default: the arrival transfer
        goes to the property the tourist has already booked."""
        trip = _trip(tourist, resort.slug)
        hotel = make_accommodation(resort)
        _corridor(classes["STANDARD"], gateway, resort)
        payload = _payload(trip["public_id"], gateway, resort)
        payload["legs"][0]["target"] = {"accommodation": hotel.slug}
        response = tourist.post(QUOTES, payload, format="json")
        assert response.status_code == 200, response.content
        assert response.json()["data"][0]["options"][0]["vehicle_class"] == "STANDARD"


class TestTheCorridorList:
    def test_it_is_public(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        _corridor(classes["STANDARD"], gateway, resort)
        response = APIClient().get(CORRIDORS)
        assert response.status_code == 200
        assert len(response.json()["data"]) == 1

    def test_the_endpoints_are_named_not_numbered(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§7.2. `transport` publishes internal ids across the module boundary
        because ADR 0012 says a cross-module reference is an id; this endpoint
        is where they stop."""
        _corridor(classes["STANDARD"], gateway, resort)
        (row,) = APIClient().get(CORRIDORS).json()["data"]
        assert row["origin"] == {"slug": gateway.slug, "name": gateway.name}
        assert row["target"] == {"slug": resort.slug, "name": resort.name}
        assert "origin_destination_id" not in row

    def test_no_fare_is_published(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """A corridor's price depends on the class and the date, and this
        response is cacheable. One number here would be a price list that goes
        stale without anything happening."""
        _corridor(classes["STANDARD"], gateway, resort)
        (row,) = APIClient().get(CORRIDORS).json()["data"]
        assert set(row) == {"id", "origin", "target", "vehicle_class", "is_bidirectional"}

    def test_a_withdrawn_corridor_is_absent(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        row = _corridor(classes["STANDARD"], gateway, resort)
        row.is_active = False
        row.save(update_fields=["is_active"])
        assert APIClient().get(CORRIDORS).json()["data"] == []
