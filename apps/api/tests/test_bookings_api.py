"""API-05 over HTTP — SRS §9.3.5, §30.3.

    GET  /bookings                               T/P/D  List, scoped by role
    GET  /bookings/{id}                          T/P/D  Detail incl. history and provider
    POST /bookings/{id}/cancel                   T/P    Cancel; returns computed refund
    GET  /bookings/{id}/cancellation-preview     T      Refund that would apply
    POST /bookings/{id}/voucher                  T      Download PDF voucher
    POST /bookings/{id}/accept | /decline        P      Provider response

Every identifier route is asked for by a stranger and must answer 404, not 403
(§30.3) — and a mutating one must leave the booking exactly as it was.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from django.apps import apps as django_apps
from rest_framework.test import APIClient

from apps.booking import services
from apps.booking.models import Booking
from apps.booking.tests import scenario
from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db


def _signed_in() -> tuple[APIClient, int]:
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    return client, int(profile.id)


def _booked(**options: object) -> tuple[APIClient, Booking]:
    """A signed-in tourist with one confirmed (or awaiting) booking."""
    client, tourist_id = _signed_in()
    built = scenario.build(tourist_id=tourist_id, **options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=tourist_id, quote_token=quote.quote_token
    )
    services.confirm_trip(built.trip_id, payment_captured=True)
    return client, Booking.objects.get(trip_id=built.trip_id)


def _data(response: Any) -> Any:
    assert response.status_code == 200, getattr(response, "data", response.content)
    return response.data["data"]


class TestListing:
    def test_a_tourist_sees_their_own_bookings_with_titles(self) -> None:
        client, booking = _booked()
        [row] = _data(client.get("/api/v1/bookings"))
        assert row["reference"] == booking.reference
        assert row["title"] == "Harbour Kayak Tour"

    def test_a_tourist_does_not_see_anybody_else_s(self) -> None:
        _booked()
        stranger, _ = _signed_in()
        assert _data(stranger.get("/api/v1/bookings")) == []

    def test_support_reads_every_booking(self) -> None:
        _booked()
        assert len(_data(signed_in_as(Role.SUPPORT_AGENT).get("/api/v1/bookings"))) == 1

    def test_a_catalogue_administrator_sees_none(self) -> None:
        _booked()
        assert _data(signed_in_as(Role.CATALOGUE_ADMIN).get("/api/v1/bookings")) == []

    def test_it_filters_by_status(self) -> None:
        client, _ = _booked()
        assert _data(client.get("/api/v1/bookings", {"status": "CANCELLED"})) == []

    def test_anonymous_is_401(self) -> None:
        assert APIClient().get("/api/v1/bookings").status_code == 401


class TestDetail:
    def test_it_carries_history_and_provider_contact(self) -> None:
        client, booking = _booked()
        detail = _data(client.get(f"/api/v1/bookings/{booking.public_id}"))
        assert [h["to_status"] for h in detail["history"]] == ["PENDING", "CONFIRMED"]
        assert detail["provider"]["name"] == "Harbour Adventures"
        assert detail["has_voucher"] is True
        assert "provider_id" not in detail and "tourist_id" not in detail


class TestAForeignPrincipalGets404:
    """§30.3 on every identifier route, and nothing moves."""

    @pytest.mark.parametrize(
        ("method", "suffix", "body"),
        [
            ("get", "", None),
            ("get", "/cancellation-preview", None),
            ("post", "/cancel", {"reason": "not mine"}),
            ("post", "/voucher", None),
        ],
    )
    def test_a_stranger_cannot_tell_it_exists(
        self, method: str, suffix: str, body: dict[str, str] | None
    ) -> None:
        _, booking = _booked()
        stranger, _ = _signed_in()
        response = getattr(stranger, method)(
            f"/api/v1/bookings/{booking.public_id}{suffix}", body, format="json"
        )
        assert response.status_code == 404
        booking.refresh_from_db()
        assert booking.status == "CONFIRMED"

    def test_a_nonexistent_booking_is_the_same_404(self) -> None:
        client, _ = _booked()
        assert client.get(f"/api/v1/bookings/{uuid.uuid4()}").status_code == 404


class TestCancellingOverTheWire:
    def test_the_preview_and_the_cancellation_agree(self) -> None:
        client, booking = _booked(days_ahead=30)
        preview = _data(client.get(f"/api/v1/bookings/{booking.public_id}/cancellation-preview"))
        done = _data(client.post(f"/api/v1/bookings/{booking.public_id}/cancel", {}, format="json"))
        assert preview["refund_amount"] == done["refund_amount"]
        assert done["booking"]["status"] == "CANCELLED"

    def test_a_started_booking_is_409(self) -> None:
        client, booking = _booked()
        Booking.objects.filter(pk=booking.pk).update(status="IN_PROGRESS")
        response = client.post(f"/api/v1/bookings/{booking.public_id}/cancel", {}, format="json")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "CANCELLATION_NOT_PERMITTED"

    def test_support_can_read_but_not_cancel(self) -> None:
        """GLOBAL_READ is a read: the write-scoped selector finds nothing."""
        _, booking = _booked()
        response = signed_in_as(Role.SUPPORT_AGENT).post(
            f"/api/v1/bookings/{booking.public_id}/cancel", {}, format="json"
        )
        assert response.status_code == 404


class TestTheVoucher:
    def test_it_downloads_as_a_pdf_attachment(self) -> None:
        client, booking = _booked()
        response = client.post(f"/api/v1/bookings/{booking.public_id}/voucher")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/pdf"
        assert f"{booking.reference}-voucher-1.pdf" in response["Content-Disposition"]
        assert booking.reference.encode() in response.content


class TestProviderResponses:
    """No provider login until Phase 11; SUPER_ADMIN holds the permission."""

    def test_an_administrator_with_the_permission_accepts(self) -> None:
        _, booking = _booked(confirmation_mode="ON_REQUEST")
        admin = signed_in_as(Role.SUPER_ADMIN)
        result = _data(admin.post(f"/api/v1/bookings/{booking.public_id}/accept"))
        assert result["status"] == "CONFIRMED"

    def test_a_decline_needs_a_reason(self) -> None:
        _, booking = _booked(confirmation_mode="ON_REQUEST")
        admin = signed_in_as(Role.SUPER_ADMIN)
        response = admin.post(f"/api/v1/bookings/{booking.public_id}/decline", {}, format="json")
        assert response.status_code == 422

    def test_a_tourist_may_not_accept_their_own_booking(self) -> None:
        client, booking = _booked(confirmation_mode="ON_REQUEST")
        response = client.post(f"/api/v1/bookings/{booking.public_id}/accept")
        assert response.status_code == 403
        booking.refresh_from_db()
        assert booking.status == "AWAITING_PROVIDER"
