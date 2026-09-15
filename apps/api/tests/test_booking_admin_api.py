"""§27.9's exceptional booking controls over HTTP — BR-038.

BR-038: "An administrator may force a transition only with a recorded reason,
and only with the SUPER_ADMIN role". In Phase 7 this is also the only way a
basket reaches CONFIRMED outside a test, because payment capture is Phase 8.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.apps import apps as django_apps
from rest_framework.test import APIClient

from apps.administration.models import AuditLog
from apps.booking import services
from apps.booking.models import Booking, BookingVoucher
from apps.booking.tests import scenario
from apps.common.authz import Role
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

REASON = "Paid by bank transfer, confirmed by finance."


def _pending() -> Booking:
    built = scenario.build()
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    return Booking.objects.get(trip_id=built.trip_id)


def _force(client: APIClient, booking: Booking, target: str, reason: str = REASON) -> Any:
    return client.post(
        f"/api/v1/admin/bookings/{booking.public_id}/force-transition",
        {"status": target, "reason": reason},
        format="json",
    )


class TestForcingAConfirmation:
    def test_a_super_admin_confirms_a_pending_basket(self) -> None:
        booking = _pending()
        response = _force(signed_in_as(Role.SUPER_ADMIN), booking, "CONFIRMED")
        assert response.status_code == 200, response.data
        booking.refresh_from_db()
        assert booking.status == "CONFIRMED"
        assert BookingVoucher.objects.filter(booking=booking).count() == 1
        trip = django_apps.get_model("trip", "Trip").objects.get(id=booking.trip_id)
        assert trip.status == "CONFIRMED"

    def test_it_is_audited_with_its_reason(self) -> None:
        booking = _pending()
        _force(signed_in_as(Role.SUPER_ADMIN), booking, "CONFIRMED")
        entry = AuditLog.objects.get(action="booking.force_transition")
        assert (entry.before["status"], entry.after["status"]) == ("PENDING", "CONFIRMED")
        assert entry.reason == REASON

    def test_the_history_names_the_super_admin(self) -> None:
        booking = _pending()
        _force(signed_in_as(Role.SUPER_ADMIN), booking, "CONFIRMED")
        last = booking.history.order_by("id").last()
        assert last is not None and last.actor_role == "SUPER_ADMIN"


class TestBR038TheLimits:
    def test_a_reason_is_required(self) -> None:
        booking = _pending()
        response = _force(signed_in_as(Role.SUPER_ADMIN), booking, "CONFIRMED", reason="")
        assert response.status_code == 422
        booking.refresh_from_db()
        assert booking.status == "PENDING"

    @pytest.mark.parametrize(
        "role", [Role.SUPPORT_AGENT, Role.FINANCE_OFFICER, Role.COMPLIANCE_ADMIN]
    )
    def test_no_other_administrator_may(self, role: Role) -> None:
        booking = _pending()
        assert _force(signed_in_as(role), booking, "CONFIRMED").status_code == 403

    def test_a_tourist_may_not(self) -> None:
        booking = _pending()
        assert _force(signed_in_as(), booking, "CONFIRMED").status_code == 403

    def test_an_undeclared_edge_is_refused_even_for_a_super_admin(self) -> None:
        """Guards are bypassed; §20.2's table is not."""
        booking = _pending()
        response = _force(signed_in_as(Role.SUPER_ADMIN), booking, "COMPLETED")
        assert response.status_code == 409
        assert response.data["error"]["code"] == "ILLEGAL_TRANSITION"
        assert not AuditLog.objects.filter(action="booking.force_transition").exists()


class TestForcingACancellation:
    def test_it_cancels_as_the_platform_with_a_full_refund(self) -> None:
        booking = _pending()
        admin = signed_in_as(Role.SUPER_ADMIN)
        _force(admin, booking, "CONFIRMED")
        response = _force(admin, booking, "CANCELLED")
        assert response.status_code == 200
        booking.refresh_from_db()
        assert (booking.cancelled_by, booking.cancellation_reason) == ("PLATFORM", "ADMIN_ACTION")


class TestReissuingAVoucher:
    def test_it_adds_an_issue_and_is_audited(self) -> None:
        booking = _pending()
        admin = signed_in_as(Role.SUPER_ADMIN)
        _force(admin, booking, "CONFIRMED")
        response = admin.post(
            f"/api/v1/admin/bookings/{booking.public_id}/voucher",
            {"reason": "The tourist lost the printed copy."},
            format="json",
        )
        assert response.status_code == 201, response.data
        assert response.data["data"]["issue_number"] == 2
        assert AuditLog.objects.filter(action="booking.voucher_reissued").exists()

    def test_an_unconfirmed_booking_has_nothing_to_reissue(self) -> None:
        booking = _pending()
        response = signed_in_as(Role.SUPER_ADMIN).post(
            f"/api/v1/admin/bookings/{booking.public_id}/voucher",
            {"reason": "Trying to reissue too early."},
            format="json",
        )
        assert response.status_code == 409
