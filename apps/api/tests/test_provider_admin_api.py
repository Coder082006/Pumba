"""§27.7's provider console over HTTP — SRS §7.5.3, §26.2, §27.7, BR-037.

**Why these exist in Phase 7.** A booking is payable to a provider (§7.5.12
makes the column NOT NULL) and BR-037 refuses one that is not VERIFIED. There
was no provider table, so there was nobody a booking could be made with. These
tests are an administrator creating one, verifying it, and pointing a listing
at it — the smallest real path to something sellable.

The trail is the interesting part. §26.2 has no DRAFT → VERIFIED edge, and an
administrator's single "verify" must still leave submission and review in the
audit log. `TestVerificationLeavesTheTrail` reads the log, not the response.
"""

from __future__ import annotations

from typing import Any

import pytest
from rest_framework.test import APIClient

from apps.administration.models import AuditLog
from apps.catalogue.tests.factories import make_activity, make_destination, make_region
from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

PROVIDERS = "/api/v1/admin/providers"


@pytest.fixture
def compliance() -> APIClient:
    return signed_in_as(Role.COMPLIANCE_ADMIN)


@pytest.fixture
def region() -> Any:
    return make_region()


def body(home: Any, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "legal_name": "Blue Lagoon Excursions Limited",
        "trading_name": "Blue Lagoon",
        "provider_type": "ACTIVITY",
        "contact_email": "ops@bluelagoon.example",
        "contact_phone": "+255700000010",
        "region": home.slug,
    }
    data.update(overrides)
    return data


def created(client: APIClient, region: Any, **overrides: Any) -> dict[str, Any]:
    response = client.post(PROVIDERS, body(region, **overrides), format="json")
    assert response.status_code == 201, response.json()
    data: dict[str, Any] = response.json()["data"]
    return data


def status_to(client: APIClient, provider_id: str, status: str, reason: str = "") -> Any:
    return client.post(
        f"{PROVIDERS}/{provider_id}/status", {"status": status, "reason": reason}, format="json"
    )


class TestCreating:
    def test_a_new_provider_is_a_draft_nobody_can_sell(
        self, compliance: APIClient, region: Any
    ) -> None:
        data = created(compliance, region)
        assert data["verify_status"] == "DRAFT"
        assert data["is_sellable"] is False
        assert data["region"] == region.slug

    def test_it_names_its_region_rather_than_numbering_it(
        self, compliance: APIClient, region: Any
    ) -> None:
        """§7.2: integers stay inside the database."""
        data = created(compliance, region)
        assert "region_id" not in data and "id" in data
        assert not str(data["id"]).isdigit()

    def test_an_unknown_region_is_a_field_error(self, compliance: APIClient, region: Any) -> None:
        response = compliance.post(PROVIDERS, body(region, region="atlantis"), format="json")
        assert response.status_code == 422
        assert response.json()["error"]["details"][0]["field"] == "region"

    def test_every_required_field_is_named(self, compliance: APIClient) -> None:
        response = compliance.post(PROVIDERS, {"trading_name": "Half"}, format="json")
        assert response.status_code == 422
        named = {d["field"] for d in response.json()["error"]["details"]}
        assert {"legal_name", "provider_type", "contact_email", "region"} <= named

    def test_a_status_cannot_be_set_on_create(self, compliance: APIClient, region: Any) -> None:
        """The only way to VERIFIED is the audited status route."""
        response = compliance.post(PROVIDERS, body(region, verify_status="VERIFIED"), format="json")
        assert response.status_code == 422

    def test_creating_one_is_audited(self, compliance: APIClient, region: Any) -> None:
        data = created(compliance, region)
        entry = AuditLog.objects.get(action="provider.created", entity_id=str(data["id"]))
        assert entry.after["trading_name"] == "Blue Lagoon"


class TestAmending:
    def test_contact_details_change_and_the_diff_is_kept(
        self, compliance: APIClient, region: Any
    ) -> None:
        data = created(compliance, region)
        response = compliance.patch(
            f"{PROVIDERS}/{data['id']}", {"contact_phone": "+255700000099"}, format="json"
        )
        assert response.status_code == 200
        entry = AuditLog.objects.get(action="provider.updated", entity_id=str(data["id"]))
        assert entry.before["contact_phone"] == "+255700000010"
        assert entry.after["contact_phone"] == "+255700000099"

    def test_the_type_cannot_change(self, compliance: APIClient, region: Any) -> None:
        """A type change would put every listing it owns in breach of §7.5.3."""
        data = created(compliance, region)
        response = compliance.patch(
            f"{PROVIDERS}/{data['id']}", {"provider_type": "TRANSPORT"}, format="json"
        )
        assert response.status_code == 422

    def test_an_unknown_provider_is_404(self, compliance: APIClient) -> None:
        response = compliance.get(f"{PROVIDERS}/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


class TestVerificationLeavesTheTrail:
    def test_verifying_a_draft_walks_submission_and_review(
        self, compliance: APIClient, region: Any
    ) -> None:
        data = created(compliance, region)
        response = status_to(compliance, data["id"], "VERIFIED")

        assert response.status_code == 200, response.json()
        result = response.json()["data"]
        assert result["steps"] == ["SUBMITTED", "UNDER_REVIEW", "VERIFIED"]
        assert result["provider"]["is_sellable"] is True
        assert result["provider"]["verified_at"] is not None

        trail = [
            (e.before["verify_status"], e.after["verify_status"])
            for e in AuditLog.objects.filter(
                action="provider.status_changed", entity_id=str(data["id"])
            ).order_by("id")
        ]
        assert trail == [
            ("DRAFT", "SUBMITTED"),
            ("SUBMITTED", "UNDER_REVIEW"),
            ("UNDER_REVIEW", "VERIFIED"),
        ]

    def test_a_suspension_needs_a_reason(self, compliance: APIClient, region: Any) -> None:
        data = created(compliance, region)
        status_to(compliance, data["id"], "VERIFIED")
        assert status_to(compliance, data["id"], "SUSPENDED").status_code == 422

    def test_a_suspended_provider_cannot_be_sold_and_the_reason_is_kept(
        self, compliance: APIClient, region: Any
    ) -> None:
        data = created(compliance, region)
        status_to(compliance, data["id"], "VERIFIED")
        response = status_to(compliance, data["id"], "SUSPENDED", reason="Insurance lapsed")

        assert response.status_code == 200
        assert response.json()["data"]["provider"]["is_sellable"] is False
        entry = AuditLog.objects.filter(action="provider.status_changed").latest("id")
        assert entry.reason == "Insurance lapsed"

    def test_a_draft_cannot_be_suspended(self, compliance: APIClient, region: Any) -> None:
        """Suspension is a single edge from VERIFIED, not a walk."""
        data = created(compliance, region)
        response = status_to(compliance, data["id"], "SUSPENDED", reason="No")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"

    def test_a_verified_provider_cannot_be_walked_to_rejection(
        self, compliance: APIClient, region: Any
    ) -> None:
        data = created(compliance, region)
        status_to(compliance, data["id"], "VERIFIED")
        response = status_to(compliance, data["id"], "REJECTED", reason="Too late")
        assert response.status_code == 409

    def test_a_refused_change_writes_nothing(self, compliance: APIClient, region: Any) -> None:
        data = created(compliance, region)
        status_to(compliance, data["id"], "SUSPENDED", reason="No")
        assert not AuditLog.objects.filter(action="provider.status_changed").exists()


class TestListing:
    def test_it_filters_by_status(self, compliance: APIClient, region: Any) -> None:
        kept = created(compliance, region, trading_name="Verified One")
        created(compliance, region, trading_name="Still Draft")
        status_to(compliance, kept["id"], "VERIFIED")

        response = compliance.get(PROVIDERS, {"verify_status": "VERIFIED"})
        assert [row["trading_name"] for row in response.json()["data"]] == ["Verified One"]


class TestWhoMayUseIt:
    """§5.2: VERIFICATION_DECIDE is COMPLIANCE_ADMIN's and SUPER_ADMIN's."""

    def test_a_super_admin_may(self, region: Any) -> None:
        created(signed_in_as(Role.SUPER_ADMIN), region)

    @pytest.mark.parametrize("role", [Role.CATALOGUE_ADMIN, Role.FINANCE_OFFICER])
    def test_other_administrators_may_not(self, role: Role, region: Any) -> None:
        response = signed_in_as(role).post(PROVIDERS, body(region), format="json")
        assert response.status_code == 403

    def test_a_tourist_may_not(self, region: Any) -> None:
        assert signed_in_as().post(PROVIDERS, body(region), format="json").status_code == 403

    def test_nobody_anonymous_may(self, region: Any) -> None:
        assert APIClient().get(PROVIDERS).status_code == 401


class TestAssigningAnActivity:
    def test_an_activity_provider_may_sell_an_activity(
        self, compliance: APIClient, region: Any
    ) -> None:
        provider = created(compliance, region)
        activity = make_activity(make_destination(region))
        catalogue = signed_in_as(Role.CATALOGUE_ADMIN)

        response = catalogue.put(
            f"/api/v1/admin/activities/{activity.public_id}/provider",
            {"provider": provider["id"]},
            format="json",
        )

        assert response.status_code == 200, response.json()
        activity.refresh_from_db()
        assert activity.provider_id is not None
        entry = AuditLog.objects.get(action="catalogue.updated", entity_id=str(activity.public_id))
        assert entry.after["provider_id"] == activity.provider_id

    def test_a_transport_provider_may_not(self, compliance: APIClient, region: Any) -> None:
        """§7.5.3: "a provider may not own listings of a type inconsistent with
        provider_type"."""
        provider = created(compliance, region, provider_type="TRANSPORT")
        activity = make_activity(make_destination(region))

        response = signed_in_as(Role.CATALOGUE_ADMIN).put(
            f"/api/v1/admin/activities/{activity.public_id}/provider",
            {"provider": provider["id"]},
            format="json",
        )

        assert response.status_code == 422
        activity.refresh_from_db()
        assert activity.provider_id is None
