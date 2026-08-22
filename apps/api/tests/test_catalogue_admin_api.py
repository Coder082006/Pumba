"""The §27.8 catalogue console, over HTTP — SRS §27.8, §30.3, §30.6, §41.13.

    §27.8: "the catalogue console creates a country, a region and a
    destination with no code change and no deployment".

    §41.13: "Every administrative action writes an audit entry with before and
    after state, actor, role, IP and request id."

`tests/test_destination_independence.py` asserts that the console can open a
market. This file asserts the four properties that make that safe, each of
which fails silently if it is wrong:

**Only a catalogue administrator reaches it.** §5.2 gives `CATALOGUE_MANAGE`
to CATALOGUE_ADMIN and, by composition, to SUPER_ADMIN — *"cannot alter
payments or catalogue"* is the sentence that keeps SUPPORT_AGENT off this
surface, and it is asserted rather than assumed, because a support agent who
could edit a listing is exactly the insider-risk case §30.1 separates roles to
prevent.

**A row nobody may reach is a 404, never a 403.** §30.3. Asserted by comparing
a nonexistent identifier against a real one belonging to nobody in particular:
the two answers must be indistinguishable down to the error code.

**Every write leaves an audit entry, and every failed write leaves none.** The
second half is the one that rots. An entry written outside the transaction
survives a rollback and then describes a change that never happened, which is
worse than no entry at all — an investigator trusts it.

**The shape of the request is closed.** §30.6. An unknown field is a 422
naming it, and ADR 0013's absent commercial fields are unknown fields: posting
`base_rate` to an accommodation is refused, not dropped.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.administration.models import AuditLog
from apps.catalogue.models import Accommodation, Country, Destination
from apps.common.audit import AuditAction
from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

TANZANIA = {
    "iso_code": "TZ",
    "name": "Tanzania",
    "default_currency": "TZS",
    "default_timezone": "Africa/Dar_es_Salaam",
}

ARUSHA = {
    "name": "Arusha",
    "slug": "arusha",
    "latitude": "-3.3869",
    "longitude": "36.6830",
    "timezone": "Africa/Dar_es_Salaam",
    "default_currency": "TZS",
}


@pytest.fixture
def admin() -> APIClient:
    return signed_in_as(Role.CATALOGUE_ADMIN)


def _post(client: APIClient, path: str, body: dict[str, Any]) -> Any:
    return client.post(path, body, format="json")


def _country(client: APIClient) -> dict[str, Any]:
    response = _post(client, "/api/v1/admin/countries", TANZANIA)
    assert response.status_code == 201, response.data
    return dict(response.data["data"])


def _region(client: APIClient, country: dict[str, Any]) -> dict[str, Any]:
    response = _post(
        client,
        "/api/v1/admin/regions",
        {"country": country["public_id"], "name": "Arusha Region", "slug": "arusha-region"},
    )
    assert response.status_code == 201, response.data
    return dict(response.data["data"])


def _destination(client: APIClient, region: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    response = _post(
        client, "/api/v1/admin/destinations", {"region": region["public_id"], **ARUSHA, **overrides}
    )
    assert response.status_code == 201, response.data
    return dict(response.data["data"])


def _entries(action: AuditAction, entity_type: str) -> list[AuditLog]:
    return list(AuditLog.objects.filter(action=str(action), entity_type=entity_type))


# ---------------------------------------------------------------------------
# Who may reach it
# ---------------------------------------------------------------------------


class TestOnlyACatalogueAdministratorReachesTheConsole:
    def test_an_anonymous_request_is_refused(self) -> None:
        assert _post(APIClient(), "/api/v1/admin/countries", TANZANIA).status_code == 401

    def test_a_forged_token_is_refused(self) -> None:
        client = APIClient()
        client.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
        assert _post(client, "/api/v1/admin/countries", TANZANIA).status_code == 401

    def test_a_tourist_is_refused(self) -> None:
        """A role failure, so 403 and not 404 — §32.2.

        The distinction matters: 404 is reserved for a row this principal may
        not reach, and using it here would tell an administrator debugging a
        permissions problem that the endpoint does not exist.
        """
        response = _post(signed_in_as(), "/api/v1/admin/countries", TANZANIA)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    def test_a_support_agent_is_refused(self) -> None:
        """§5.2: SUPPORT_AGENT "cannot alter payments or catalogue".

        Support holds global *read* over catalogue rows in the ownership table
        and no `CATALOGUE_MANAGE` permission, and it is the permission that
        gates this surface. A support agent who could retire a destination is
        the insider-risk case §30.1 separates roles to prevent.
        """
        assert (
            _post(signed_in_as(Role.SUPPORT_AGENT), "/api/v1/admin/countries", TANZANIA).status_code
            == 403
        )

    def test_a_super_admin_is_permitted(self) -> None:
        """§5.2 composes SUPER_ADMIN from every other role's permissions, so
        this must follow from the composition rather than from a second list
        somebody remembered to update."""
        assert (
            _post(signed_in_as(Role.SUPER_ADMIN), "/api/v1/admin/countries", TANZANIA).status_code
            == 201
        )

    def test_a_catalogue_admin_without_a_second_factor_never_gets_a_token(self) -> None:
        """§30.2, at the door rather than at the endpoint.

        `MfaSatisfied` guards these views, but the check that actually protects
        the console is earlier: an administrative role cannot sign in at all
        without TOTP. Asserted here so that the console's dependence on it is
        recorded where the console is tested.
        """
        from django.utils import timezone

        from apps.identity import repositories as identity_repo
        from apps.identity import services as identity_services
        from apps.identity.services import MfaRequiredError

        identity_services.register_tourist(
            email="unenrolled-admin@example.com",
            password="correct-horse-battery-staple-42",
            first_name="Un",
            last_name="Enrolled",
        )
        user = identity_repo.find_user_by_email("unenrolled-admin@example.com")
        assert user is not None
        identity_repo.mark_email_verified(user, now=timezone.now())
        identity_repo.grant_role(user, Role.CATALOGUE_ADMIN)

        with pytest.raises(MfaRequiredError):
            identity_services.authenticate(
                email="unenrolled-admin@example.com",
                password="correct-horse-battery-staple-42",
            )


class TestAnUnreachableRowIsIndistinguishableFromAMissingOne:
    """§30.3: *"a foreign principal receives 404, not 403"*."""

    def test_amending_a_nonexistent_destination_is_a_404(self, admin: APIClient) -> None:
        response = admin.patch(
            f"/api/v1/admin/destinations/{uuid.uuid4()}", {"is_active": True}, format="json"
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_an_identifier_of_the_wrong_entity_is_also_a_404(self, admin: APIClient) -> None:
        """A real `public_id`, addressed on the wrong collection.

        The scoped queryset is per entity, so a country's identifier does not
        resolve on `/admin/destinations`. Without that, an administrator could
        probe which identifiers exist anywhere by trying them everywhere.
        """
        country = _country(admin)
        response = admin.patch(
            f"/api/v1/admin/destinations/{country['public_id']}", {"is_active": True}, format="json"
        )
        assert response.status_code == 404

    def test_the_two_answers_match_exactly(self, admin: APIClient) -> None:
        country = _country(admin)
        wrong_entity = admin.patch(
            f"/api/v1/admin/destinations/{country['public_id']}", {"is_active": True}, format="json"
        )
        missing = admin.patch(
            f"/api/v1/admin/destinations/{uuid.uuid4()}", {"is_active": True}, format="json"
        )
        assert wrong_entity.status_code == missing.status_code == 404
        assert wrong_entity.json()["error"]["code"] == missing.json()["error"]["code"]


# ---------------------------------------------------------------------------
# The shape of a request
# ---------------------------------------------------------------------------


class TestTheRequestShapeIsClosed:
    def test_an_unknown_field_is_refused_and_named(self, admin: APIClient) -> None:
        """§30.6: *"unknown fields rejected rather than ignored"*.

        Ignoring it means an admin form that stopped saving a field keeps
        reporting success, and the administrator finds out when a tourist
        does.
        """
        response = _post(admin, "/api/v1/admin/countries", {**TANZANIA, "capital": "Dodoma"})
        assert response.status_code == 422
        assert any(detail["field"] == "capital" for detail in response.json()["error"]["details"])

    def test_an_accommodation_cannot_be_given_a_price(self, admin: APIClient) -> None:
        """ADR 0013, on the wire.

        The Platform does not sell the room in v1, so there is no rate field
        to suppress — `base_rate` is simply not a field this endpoint has, and
        a client that sends one is told so rather than watching it vanish.
        """
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        for forbidden in ("base_rate", "provider", "cancellation_policy", "star_rating"):
            response = _post(
                admin,
                "/api/v1/admin/accommodation",
                {
                    "destination": destination["public_id"],
                    "name": "Ocean Breeze",
                    "slug": "ocean-breeze",
                    "property_type": "HOTEL",
                    "latitude": "-3.3700",
                    "longitude": "36.6800",
                    forbidden: "1",
                },
            )
            assert response.status_code == 422, forbidden
            assert any(
                detail["field"] == forbidden for detail in response.json()["error"]["details"]
            )

    def test_an_eighth_decimal_place_is_refused(self, admin: APIClient) -> None:
        """§13.1: *"a maximum of seven decimal places"*.

        Seven is roughly 11 mm. The eighth digit is noise, and storing noise in
        a geography column makes every distance downstream look more precise
        than it is.
        """
        country = _country(admin)
        region = _region(admin, country)
        response = _post(
            admin,
            "/api/v1/admin/destinations",
            {"region": region["public_id"], **ARUSHA, "latitude": "-3.38691234"},
        )
        assert response.status_code == 422

    def test_half_a_coordinate_is_refused(self, admin: APIClient) -> None:
        """A latitude with no longitude would land the row in the Gulf of
        Guinea while reporting success."""
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        response = admin.patch(
            f"/api/v1/admin/destinations/{destination['public_id']}",
            {"latitude": "-3.4000"},
            format="json",
        )
        assert response.status_code == 422

    def test_an_out_of_range_coordinate_is_refused(self, admin: APIClient) -> None:
        country = _country(admin)
        region = _region(admin, country)
        response = _post(
            admin,
            "/api/v1/admin/destinations",
            {"region": region["public_id"], **ARUSHA, "latitude": "91.0000000"},
        )
        assert response.status_code == 422

    def test_an_invalid_timezone_is_a_422_naming_the_field(self, admin: APIClient) -> None:
        """§8.6 tier 2 reaching HTTP.

        `Africa/Zanzibar` looks exactly like an IANA name and is not one. The
        rule lives in `domain.hierarchy` and reaches the row through
        `full_clean`, which is *not* a serializer — so the check this asserts
        is that a `django.core.exceptions.ValidationError` becomes a 422 with
        the field named, rather than a 500 an administrator cannot act on.
        """
        country = _country(admin)
        region = _region(admin, country)
        response = _post(
            admin,
            "/api/v1/admin/destinations",
            {"region": region["public_id"], **ARUSHA, "timezone": "Mars/Olympus_Mons"},
        )
        assert response.status_code == 422
        assert any(detail["field"] == "timezone" for detail in response.json()["error"]["details"])

    def test_a_dangling_reference_is_a_422_not_a_404(self, admin: APIClient) -> None:
        """The request is well-formed and points at nothing.

        404 would make "the region you named does not exist" indistinguishable
        from "the destination you are editing does not exist", which are
        different problems with different fixes.
        """
        response = _post(
            admin, "/api/v1/admin/destinations", {"region": str(uuid.uuid4()), **ARUSHA}
        )
        assert response.status_code == 422
        assert any(detail["field"] == "region" for detail in response.json()["error"]["details"])

    def test_a_duplicate_slug_is_refused_rather_than_silently_renamed(
        self, admin: APIClient
    ) -> None:
        """The partial unique index of §7.7, surfaced as a 422.

        `full_clean` validates the model's constraints, so this never reaches
        the database as an `IntegrityError` and never becomes a 500.
        """
        country = _country(admin)
        region = _region(admin, country)
        _destination(admin, region)
        response = _post(
            admin, "/api/v1/admin/destinations", {"region": region["public_id"], **ARUSHA}
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# What comes back
# ---------------------------------------------------------------------------


class TestWhatTheConsoleGetsBack:
    def test_no_sequential_integer_appears_in_a_response(self, admin: APIClient) -> None:
        """§7.2: *"Sequential integers are never returned to clients."*

        Asserted over the whole nested payload rather than the top level: a
        destination carries its region, which carries its country, and any one
        of the three is a place a serializer could leak a primary key.
        """
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        assert "id" not in _keys(destination)
        assert uuid.UUID(destination["public_id"])
        assert uuid.UUID(destination["region"]["country"]["public_id"])

    def test_a_destination_carries_its_timezone_and_its_hierarchy(self, admin: APIClient) -> None:
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        assert destination["timezone"] == "Africa/Dar_es_Salaam"
        assert destination["region"]["slug"] == "arusha-region"
        assert destination["region"]["country"]["iso_code"] == "TZ"

    def test_a_coordinate_round_trips_at_the_precision_it_was_sent(self, admin: APIClient) -> None:
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        assert destination["latitude"] == "-3.3869000"
        assert destination["longitude"] == "36.6830000"

    def test_a_new_destination_is_not_public_until_somebody_says_so(self, admin: APIClient) -> None:
        """§7.5.6 defaults `is_active` to false, and nothing on this path
        overrides it. A market is staged and then published, which is why
        §41.12's Arusha test has to activate one explicitly."""
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        row = Destination.all_objects.get(public_id=destination["public_id"])
        assert row.is_active is False


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


class TestEveryAdministrativeActionIsAudited:
    """§41.13, one property per part of the sentence it quotes."""

    def test_a_creation_records_the_actor_the_role_the_ip_and_the_request_id(
        self, admin: APIClient
    ) -> None:
        country = _country(admin)
        [entry] = _entries(AuditAction.CATALOGUE_CREATED, "country")
        assert entry.entity_id == country["public_id"]
        assert entry.actor_user_id is not None
        assert entry.actor_role == Role.CATALOGUE_ADMIN.value
        assert entry.ip == "127.0.0.1"
        assert entry.request_id

    def test_a_creation_records_the_state_it_created(self, admin: APIClient) -> None:
        """`before` is an empty dict rather than absent: beside a populated
        `after` it reads as a creation at a glance, where a missing key reads
        as a bug in whatever wrote the entry."""
        _country(admin)
        [entry] = _entries(AuditAction.CATALOGUE_CREATED, "country")
        assert entry.before == {}
        assert entry.after["iso_code"] == "TZ"
        assert entry.after["default_timezone"] == "Africa/Dar_es_Salaam"

    def test_an_amendment_records_both_sides_of_the_change(self, admin: APIClient) -> None:
        """Opening a market is an update whose diff is one flag.

        There is no separate activate endpoint precisely so that this is the
        only record of it, and so that the record is a diff rather than an
        assertion.
        """
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        admin.patch(
            f"/api/v1/admin/destinations/{destination['public_id']}",
            {"is_active": True},
            format="json",
        )
        [entry] = _entries(AuditAction.CATALOGUE_UPDATED, "destination")
        assert entry.before["is_active"] is False
        assert entry.after["is_active"] is True
        assert entry.before["slug"] == entry.after["slug"] == "arusha"

    def test_a_reference_is_recorded_as_an_identifier_a_person_can_look_up(
        self, admin: APIClient
    ) -> None:
        """Not the foreign key. §7.2 keeps sequential integers off the wire,
        and an audit entry is read by a person who then has to find the row."""
        country = _country(admin)
        region = _region(admin, country)
        [entry] = _entries(AuditAction.CATALOGUE_CREATED, "region")
        assert entry.after["country"] == country["public_id"]
        assert region["public_id"] == entry.entity_id

    def test_a_coordinate_is_recorded_as_degrees_not_as_geometry(self, admin: APIClient) -> None:
        country = _country(admin)
        region = _region(admin, country)
        _destination(admin, region)
        [entry] = _entries(AuditAction.CATALOGUE_CREATED, "destination")
        assert entry.after["centroid"] == {"lat": "-3.3869", "lon": "36.683"}

    def test_a_retirement_and_a_restoration_are_separate_entries(self, admin: APIClient) -> None:
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        path = f"/api/v1/admin/destinations/{destination['public_id']}"

        assert admin.delete(path).status_code == 204
        assert Destination.all_objects.get(public_id=destination["public_id"]).deleted_at

        assert admin.post(f"{path}/restore").status_code == 204
        assert not Destination.all_objects.get(public_id=destination["public_id"]).deleted_at

        [retired] = _entries(AuditAction.CATALOGUE_DELETED, "destination")
        [restored] = _entries(AuditAction.CATALOGUE_RESTORED, "destination")
        assert retired.before["slug"] == "arusha"
        assert restored.after["slug"] == "arusha"

    def test_a_soft_deletion_keeps_the_row_and_releases_the_slug(self, admin: APIClient) -> None:
        """§7.7, both halves, through the API this time. Reusing the slug is
        what proves the partial unique index is doing its job."""
        country = _country(admin)
        region = _region(admin, country)
        first = _destination(admin, region)
        admin.delete(f"/api/v1/admin/destinations/{first['public_id']}")

        second = _destination(admin, region)
        assert second["public_id"] != first["public_id"]
        assert Destination.all_objects.filter(public_id=first["public_id"]).exists()

    def test_a_failed_write_leaves_no_entry(self, admin: APIClient) -> None:
        """The half that rots.

        An audit entry written outside the transaction survives the rollback
        and then describes a change that never happened — which is worse than
        no entry, because an investigator trusts it.
        """
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        before = AuditLog.objects.count()

        response = admin.patch(
            f"/api/v1/admin/destinations/{destination['public_id']}",
            {"timezone": "Mars/Olympus_Mons"},
            format="json",
        )
        assert response.status_code == 422
        assert AuditLog.objects.count() == before
        assert Destination.all_objects.get(public_id=destination["public_id"]).timezone == (
            "Africa/Dar_es_Salaam"
        )

    def test_a_refused_request_leaves_no_entry(self, admin: APIClient) -> None:
        """A tourist's attempt is not a catalogue action and does not get a
        catalogue entry. §37.2 audits the *authorisation* denial through its
        own action, which is a different record with a different meaning."""
        before = AuditLog.objects.filter(entity_type="country").count()
        assert _post(signed_in_as(), "/api/v1/admin/countries", TANZANIA).status_code == 403
        assert AuditLog.objects.filter(entity_type="country").count() == before


class TestTheAuditTrailCoversEveryCuratedEntity:
    """Not just the ones §41.12 happens to walk through.

    An entity added to `services.ENTITIES` with a route and no audit call is
    exactly the omission §41.13 forbids, and it is invisible in review — the
    write works, the response is right, and nothing is recorded.
    """

    def test_every_entity_with_a_create_route_writes_an_entry(self, admin: APIClient) -> None:
        from apps.catalogue.services import ENTITIES

        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        located = {
            "destination": destination["public_id"],
            "latitude": "-3.3700",
            "longitude": "36.6800",
        }
        bodies = {
            "tag": ("/api/v1/admin/tags", {"slug": "wildlife", "label": "Wildlife"}),
            "attraction": (
                "/api/v1/admin/attractions",
                {"name": "Ngorongoro Crater", "slug": "ngorongoro-crater", **located},
            ),
            "activity": (
                "/api/v1/admin/activities",
                {
                    "name": "Crater Day Trip",
                    "slug": "crater-day-trip",
                    "duration_minutes": 480,
                    "price_per_person": "250.00",
                    "currency": "USD",
                    "max_pax": 6,
                    **located,
                },
            ),
            "accommodation": (
                "/api/v1/admin/accommodation",
                {
                    "name": "Crater Lodge",
                    "slug": "crater-lodge",
                    "property_type": "LODGE",
                    **located,
                },
            ),
        }
        # The three already created above, plus one of each remaining entity.
        assert set(bodies) | {"country", "region", "destination"} == set(ENTITIES)

        for key, (path, body) in bodies.items():
            assert _post(admin, path, body).status_code == 201, key

        recorded = set(
            AuditLog.objects.filter(action=str(AuditAction.CATALOGUE_CREATED)).values_list(
                "entity_type", flat=True
            )
        )
        assert recorded == set(ENTITIES)


class TestTheWriteReachesTheRowItClaimsTo:
    def test_a_country_created_over_http_is_the_row_in_the_table(self, admin: APIClient) -> None:
        payload = _country(admin)
        row = Country.all_objects.get(public_id=payload["public_id"])
        assert (row.iso_code, row.default_currency) == ("TZ", "TZS")

    def test_an_accommodation_created_over_http_carries_no_commercial_state(
        self, admin: APIClient
    ) -> None:
        """ADR 0013 at the schema, not at the serializer. There is no column
        to set, which is why there is no field to send."""
        country = _country(admin)
        region = _region(admin, country)
        destination = _destination(admin, region)
        created = _post(
            admin,
            "/api/v1/admin/accommodation",
            {
                "destination": destination["public_id"],
                "name": "Ocean Breeze",
                "slug": "ocean-breeze",
                "property_type": "HOTEL",
                "latitude": "-3.3700",
                "longitude": "36.6800",
                "check_in_time": "14:00",
            },
        )
        assert created.status_code == 201, created.data
        row = Accommodation.all_objects.get(public_id=created.data["data"]["public_id"])
        for absent in ("base_rate", "provider_id", "cancellation_policy_id", "star_rating"):
            assert not hasattr(row, absent)
        assert str(row.check_in_time) == "14:00:00"


def _keys(payload: dict[str, Any]) -> set[str]:
    """Every key anywhere in a nested response body."""
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
