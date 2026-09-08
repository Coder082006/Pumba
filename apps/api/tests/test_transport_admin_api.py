"""§27.11's tariff console over HTTP — SRS §27.11, §12.4, §41.13.

    §27.11: "CRUD over transfer corridors and tariffs, with a quote-preview
    tool for any origin-destination-class combination".

**The preview is the interesting endpoint and the reason §27.11 asks for one.**
A corridor either prices a route or it does not, and both look the same from
outside: a fare comes back either way. What an administrator needs to know
before saving a change is *which rung answered* — a price that fell through to
the country default when a corridor was expected is a misconfiguration
indistinguishable from a correct answer. `TestThePreviewExplainsItself` is that.

It also runs the tourist's own code path, and `test_it_agrees_with_the_tourist_
facing_quote` is what stops the two drifting apart. A second implementation
would eventually disagree, and the disagreement would surface as an
administrator insisting a price is right while somebody is charged otherwise.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.contrib.gis.geos import Point
from rest_framework.test import APIClient

from apps.catalogue.tests.factories import make_destination, make_region
from apps.common.authz import Role
from apps.transport.models import TariffScope, TransferCorridor, TransferTariff, VehicleClass
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

#: A fixed "today" rather than the clock's. `date.today()` reads the
#: server's zone, and every trip these open is bounded by the destination's —
#: a test that straddled midnight in one and not the other would fail once a
#: day for reasons nobody could reproduce.
TODAY = dt.datetime.now(tz=dt.UTC).date()

CORRIDORS = "/api/v1/admin/transport/corridors"
TARIFFS = "/api/v1/admin/transport/tariffs"
PREVIEW = "/api/v1/admin/transport/quote-preview"


@pytest.fixture
def classes() -> dict[str, VehicleClass]:
    spec = [("STANDARD", 4, 3, 1), ("VAN", 7, 6, 2)]
    return {
        code: VehicleClass.objects.create(
            code=code, name=code.title(), seats=seats, luggage_capacity=bags, display_order=order
        )
        for code, seats, bags, order in spec
    }


@pytest.fixture
def gateway() -> Any:
    return make_destination(
        make_region(),
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


def _iso(destination: Any) -> str:
    """The country the fixture chain built, by the code an administrator types."""
    return str(destination.region.country.iso_code)


@pytest.fixture
def admin() -> APIClient:
    return signed_in_as(Role.CATALOGUE_ADMIN)


def _corridor_body(gateway: Any, resort: Any, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "origin_destination": gateway.slug,
        "target_destination": resort.slug,
        "vehicle_class": "STANDARD",
        "fixed_price": "90000.00",
        "currency": "TZS",
        "is_bidirectional": True,
        "valid_from": "2026-01-01",
    }
    body.update(overrides)
    return body


def _tariff_body(iso: str, **overrides: Any) -> dict[str, Any]:
    """§12.4's step 4, the country default.

    The ISO code comes from the fixture rather than being written out: the
    catalogue factories build their own country, and a literal here would tie
    this file to whichever one they happen to use — the destination-specific
    coupling §4.2 exists to prevent, in a test.
    """
    body: dict[str, Any] = {
        "scope": "COUNTRY",
        "country": iso,
        "vehicle_class": "STANDARD",
        "base_fare": "30000.00",
        "per_km_rate": "1620.0000",
        "currency": "TZS",
        "valid_from": "2026-01-01",
    }
    body.update(overrides)
    return body


class TestCorridorManagement:
    def test_an_administrator_creates_one(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        response = admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json")
        assert response.status_code == 201, response.content
        assert TransferCorridor.objects.count() == 1

    def test_the_response_names_the_destinations(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§7.2. The table stores ids because ADR 0012 makes a cross-module
        reference an id; the console reads slugs, because that is what an
        administrator typed and what they can check."""
        data = admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json").json()["data"]
        assert data["origin_destination"] == gateway.slug
        assert data["target_destination"] == resort.slug
        assert "origin_destination_id" not in data

    def test_an_unknown_destination_is_named_in_the_refusal(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """A 422 rather than a 404: the request is malformed in a field, and an
        administrator fixing it needs to know which one."""
        response = admin.post(
            CORRIDORS, _corridor_body(gateway, resort, target_destination="nowhere"), format="json"
        )
        assert response.status_code == 422
        assert "nowhere" in str(response.content)

    def test_an_unknown_vehicle_class_is_refused(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        response = admin.post(
            CORRIDORS, _corridor_body(gateway, resort, vehicle_class="HELICOPTER"), format="json"
        )
        assert response.status_code == 422

    def test_a_price_is_amended_in_place(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        created = admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json").json()[
            "data"
        ]
        response = admin.patch(
            f"{CORRIDORS}/{created['id']}", {"fixed_price": "120000.00"}, format="json"
        )
        assert response.status_code == 200, response.content
        assert TransferCorridor.objects.get().fixed_price == Decimal("120000.00")

    def test_a_corridor_is_retired_by_the_same_call(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§27.11 has no separate deactivate action and needs none: withdrawing
        a price is `is_active` moving, so there is no second path that could
        record the change differently or not at all."""
        created = admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json").json()[
            "data"
        ]
        admin.patch(f"{CORRIDORS}/{created['id']}", {"is_active": False}, format="json")
        assert TransferCorridor.objects.get().is_active is False

    def test_an_unknown_corridor_is_404(self, admin: APIClient) -> None:
        import uuid

        assert (
            admin.patch(
                f"{CORRIDORS}/{uuid.uuid4()}", {"is_active": False}, format="json"
            ).status_code
            == 404
        )

    def test_an_unrecognised_field_is_refused(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§30.6, through `StrictSerializer`. A typo on a pricing form must not
        silently do nothing, which on a console looks exactly like success."""
        response = admin.post(
            CORRIDORS, _corridor_body(gateway, resort, price="90000.00"), format="json"
        )
        assert response.status_code == 422


class TestTariffManagement:
    def test_a_country_tariff_is_created(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any
    ) -> None:
        response = admin.post(TARIFFS, _tariff_body(_iso(gateway)), format="json")
        assert response.status_code == 201, response.content
        assert TransferTariff.objects.get().scope == TariffScope.COUNTRY

    def test_a_region_tariff_is_created(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any
    ) -> None:
        response = admin.post(
            TARIFFS,
            _tariff_body("", scope="REGION", country=None, region=gateway.region.slug),
            format="json",
        )
        assert response.status_code == 201, response.content
        assert TransferTariff.objects.get().region_id == gateway.region_id

    def test_an_unknown_country_is_refused(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any
    ) -> None:
        response = admin.post(TARIFFS, _tariff_body("ZZ"), format="json")
        assert response.status_code == 422

    def test_an_incoherent_scope_is_refused_by_the_database(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any
    ) -> None:
        """A row claiming both a region and a country would answer on whichever
        rung the ladder tried first, which is not a decision anybody made.

        Asserted through the service rather than over HTTP: an `IntegrityError`
        is not an `APIException`, so §9.2's handler does not shape it and what
        a client sees is a 500. That is the right outcome — a CHECK firing means
        something got past validation that should not have — but it makes the
        constraint's own name unreadable from a response body, and the
        constraint's name is the assertion worth making.
        """
        from apps.administration import services

        with pytest.raises(Exception, match="scope_columns_coherent"):
            services.create_tariff(
                fields={
                    **_tariff_body(_iso(gateway)),
                    "region": gateway.region.slug,
                    "valid_from": dt.date(2026, 1, 1),
                },
                principal=None,
                ip=None,
            )

    def test_a_rate_is_amended(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any
    ) -> None:
        created = admin.post(TARIFFS, _tariff_body(_iso(gateway)), format="json").json()["data"]
        admin.patch(f"{TARIFFS}/{created['id']}", {"per_km_rate": "2000.0000"}, format="json")
        assert TransferTariff.objects.get().per_km_rate == Decimal("2000.0000")


class TestThePreviewExplainsItself:
    def test_it_names_the_rung_that_answered(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """The whole reason §27.11 asks for the tool."""
        created = admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json").json()[
            "data"
        ]
        response = admin.post(
            PREVIEW,
            {"origin_destination": gateway.slug, "target_destination": resort.slug},
            format="json",
        )
        assert response.status_code == 200, response.content
        (option, *_) = response.json()["data"]["options"]
        assert option["matched_step"] == 1
        assert option["matched_kind"] == "CORRIDOR"
        assert option["matched_rule"] == created["id"]

    def test_a_fallback_says_it_fell_back(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """Step 4 with no corridor configured. Without the step, this answer
        and the one above are the same number with the same shape."""
        admin.post(TARIFFS, _tariff_body(_iso(gateway)), format="json")
        response = admin.post(
            PREVIEW,
            {
                "origin_destination": gateway.slug,
                "target_destination": resort.slug,
                "distance_m": 40000,
                "travel_seconds": 3000,
            },
            format="json",
        )
        assert response.status_code == 200, response.content
        (option, *_) = response.json()["data"]["options"]
        assert option["matched_step"] == 4
        assert option["matched_kind"] == "TARIFF"

    def test_the_breakdown_adds_up(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        admin.post(TARIFFS, _tariff_body(_iso(gateway)), format="json")
        (option, *_) = admin.post(
            PREVIEW,
            {
                "origin_destination": gateway.slug,
                "target_destination": resort.slug,
                "distance_m": 40000,
                "travel_seconds": 3000,
            },
            format="json",
        ).json()["data"]["options"]
        parts = option["breakdown"]
        total = sum(Decimal(parts[key]) for key in ("base", "distance", "time", "surcharges"))
        assert total == Decimal(option["price"]["amount"])

    def test_an_unconfigured_route_previews_as_a_refusal(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """The most useful thing the tool can say. An administrator checking
        whether a route is covered wants the same answer a tourist would get,
        not an empty list they have to interpret."""
        response = admin.post(
            PREVIEW,
            {"origin_destination": gateway.slug, "target_destination": resort.slug},
            format="json",
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "NO_TARIFF_CONFIGURED"

    def test_it_agrees_with_the_tourist_facing_quote(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """One code path, proved rather than asserted in a docstring.

        A preview computed separately would drift, and the drift would surface
        as an administrator insisting a price is right while a tourist is
        charged something else.
        """
        admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json")

        preview = admin.post(
            PREVIEW,
            {"origin_destination": gateway.slug, "target_destination": resort.slug},
            format="json",
        ).json()["data"]

        tourist = signed_in_as()
        trip = tourist.post(
            "/api/v1/trips",
            {
                "destination": resort.slug,
                "start_date": (TODAY + dt.timedelta(days=30)).isoformat(),
                "end_date": (TODAY + dt.timedelta(days=33)).isoformat(),
                "adults": 2,
            },
            format="json",
        ).json()["data"]

        quoted = tourist.post(
            "/api/v1/transport/quotes",
            {
                "trip_id": trip["public_id"],
                "legs": [
                    {
                        "reference": "leg-1",
                        "origin": {"destination": gateway.slug},
                        "target": {"destination": resort.slug},
                        "depart_at": dt.datetime.now(tz=dt.UTC).isoformat(),
                        "pax": 2,
                        "luggage": 2,
                    }
                ],
            },
            format="json",
        ).json()["data"][0]

        assert [o["price"]["amount"] for o in preview["options"]] == [
            o["price"]["amount"] for o in quoted["options"]
        ]


class TestOnlyAnAdministratorReachesIt:
    def test_a_tourist_is_refused(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§5.2: `CATALOGUE_ADMIN` manages tariffs. A tourist holds no such
        permission, and a 403 here is right rather than §30.3's 404 — the
        endpoint's existence is not a secret, only its use is."""
        tourist = signed_in_as()
        assert (
            tourist.post(CORRIDORS, _corridor_body(gateway, resort), format="json").status_code
            == 403
        )

    def test_an_anonymous_client_is_refused(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        assert APIClient().post(
            CORRIDORS, _corridor_body(gateway, resort), format="json"
        ).status_code in (401, 403)

    def test_the_preview_is_administrator_only(
        self, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """The fare is what is administrator-only, not the destinations —
        anybody may read those through §9.3.2."""
        tourist = signed_in_as()
        assert (
            tourist.post(
                PREVIEW,
                {"origin_destination": gateway.slug, "target_destination": resort.slug},
                format="json",
            ).status_code
            == 403
        )


class TestItIsAudited:
    def test_a_created_corridor_leaves_a_record(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§41.13: an entry per administrative action. A hundred and fifty
        pricing rows with no record of who wrote them is the state somebody is
        in when one of them turns out to be wrong."""
        from apps.administration.models import AuditLog

        admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json")
        assert AuditLog.objects.filter(entity_type="transfer_corridor").exists()

    def test_an_amendment_leaves_one_too(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        from apps.administration.models import AuditLog

        created = admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json").json()[
            "data"
        ]
        admin.patch(f"{CORRIDORS}/{created['id']}", {"fixed_price": "99000.00"}, format="json")
        assert (
            AuditLog.objects.filter(
                entity_type="transfer_corridor", action="catalogue.updated"
            ).count()
            == 1
        )

    def test_a_preview_leaves_none(
        self, admin: APIClient, classes: dict[str, VehicleClass], gateway: Any, resort: Any
    ) -> None:
        """§41.13 records administrative *actions*, and looking at a price is
        not one. An audit log that filled with reads would bury the writes."""
        from apps.administration.models import AuditLog

        admin.post(CORRIDORS, _corridor_body(gateway, resort), format="json")
        before = AuditLog.objects.count()
        admin.post(
            PREVIEW,
            {"origin_destination": gateway.slug, "target_destination": resort.slug},
            format="json",
        )
        assert AuditLog.objects.count() == before
