"""Issuing and serving vouchers — SRS §41.8, ADR 0026.

§41.8: "Vouchers generate for every confirmed booking." The test settings render
through `FakeDocuments`, so the assertions read what a voucher says.
"""

from __future__ import annotations

import hashlib

import pytest
from django.apps import apps as django_apps

from apps.booking import services
from apps.booking.models import Booking, BookingVoucher
from apps.booking.services import VoucherIntegrityError
from apps.booking.tests import scenario
from apps.common.errors import NotFoundError
from apps.common.ports_registry import get_storage_port

pytestmark = pytest.mark.django_db


def paid(**options: object) -> scenario.Scenario:
    built = scenario.build(**options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    services.confirm_trip(built.trip_id, payment_captured=True)
    return built


class TestEveryConfirmedBookingHasOne:
    def test_confirmation_issues_the_first_voucher(self) -> None:
        paid()
        [voucher] = BookingVoucher.objects.all()
        assert voucher.issue_number == 1
        assert voucher.booking == Booking.objects.get()

    def test_it_says_what_was_booked_and_with_whom(self) -> None:
        built = paid(adults=2, children=1, policy_code="MODERATE_7D")
        content = BookingVoucher.objects.get().content
        booking = Booking.objects.get()
        assert content["booking_reference"] == booking.reference
        assert content["service_title"] == "Harbour Kayak Tour"
        assert content["party"] == "2 adults, 1 child"
        assert content["provider_name"] == "Harbour Adventures"
        assert "Pacific/Auckland" in content["when"]
        assert content["cancellation_terms"].startswith("Full refund more than 168 hours")
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)
        assert content["trip_reference"] == trip.reference

    def test_an_awaiting_booking_has_no_voucher_yet(self) -> None:
        """It is not confirmed until its provider accepts."""
        paid(confirmation_mode="ON_REQUEST")
        assert not BookingVoucher.objects.exists()

    def test_acceptance_issues_it(self) -> None:
        paid(confirmation_mode="ON_REQUEST")
        services.accept_request(Booking.objects.get().public_id, actor_user_id=9)
        assert BookingVoucher.objects.count() == 1

    def test_a_basket_that_is_not_paid_has_none(self) -> None:
        built = scenario.build()
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        assert not BookingVoucher.objects.exists()


class TestServingIt:
    def test_the_document_matches_its_recorded_hash(self) -> None:
        paid()
        name, data = services.voucher_document(Booking.objects.get())
        assert name.endswith("-voucher-1.pdf")
        assert hashlib.sha256(data).hexdigest() == BookingVoucher.objects.get().sha256

    def test_a_missing_file_is_re_rendered_from_the_frozen_content(self) -> None:
        """Every current environment stores in a per-process fake."""
        paid()
        storage = get_storage_port()
        key = BookingVoucher.objects.get().storage_key
        if storage.exists(key):
            storage.delete(key)
        _, data = services.voucher_document(Booking.objects.get())
        assert b"Harbour Kayak Tour" in data

    def test_a_voucher_whose_content_changed_is_refused(self) -> None:
        """ADR 0026: a document someone may have printed never silently changes."""
        paid()
        BookingVoucher.objects.update(sha256="0" * 64)
        with pytest.raises(VoucherIntegrityError):
            services.voucher_document(Booking.objects.get())

    def test_a_booking_without_one_is_404(self) -> None:
        built = scenario.build()
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        with pytest.raises(NotFoundError):
            services.voucher_document(Booking.objects.get())


class TestReissuing:
    def test_a_reissue_is_a_new_issue_and_the_old_one_is_kept(self) -> None:
        paid()
        booking = Booking.objects.get()
        services.issue_voucher(booking, issued_by=3, reason="Lost at the hotel.")
        assert list(BookingVoucher.objects.values_list("issue_number", flat=True)) == [1, 2]
        name, _ = services.voucher_document(booking)
        assert name.endswith("-voucher-2.pdf")
