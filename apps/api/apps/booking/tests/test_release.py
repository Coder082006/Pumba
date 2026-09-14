"""Releasing a basket nobody paid for — §20.2, §20.5, §28.3, TC-072, ADR 0025.

TC-072: "Payment failure releases holds | Sandbox decline | Payment FAILED;
holds RELEASED; bookings FAILED; trip PRICED".

Phase 7 has no payment to decline, so the payment half is driven through
`fail_basket` directly; the hold-expiry half is driven the way production
drives it, by the sweeper. The two differ in exactly one place — where the trip
goes — and that difference is §20.5's, asserted below.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.booking import services
from apps.booking.models import Booking, BookingStatusHistory
from apps.booking.services import BasketFailure
from apps.booking.tasks import release_expired_holds
from apps.booking.tests import scenario

pytestmark = pytest.mark.django_db


def in_basket(**options: object) -> scenario.Scenario:
    built = scenario.build(**options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    return built


def _trip(built: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)


def _departure(built: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "ActivityDeparture").objects.get(
        id=built.departure_id
    )


def _age_the_holds(built: scenario.Scenario) -> None:
    django_apps.get_model("inventory", "InventoryHold").objects.filter(
        trip_id=built.trip_id
    ).update(expires_at=timezone.now() - dt.timedelta(seconds=1))


class TestTC072APaymentFailure:
    def test_the_bookings_fail(self) -> None:
        built = in_basket()
        assert services.fail_basket(built.trip_id, cause=BasketFailure.PAYMENT_FAILED) == 1
        assert Booking.objects.get().status == "FAILED"

    def test_the_holds_are_released(self) -> None:
        built = in_basket(adults=2)
        services.fail_basket(built.trip_id, cause=BasketFailure.PAYMENT_FAILED)
        hold = django_apps.get_model("inventory", "InventoryHold").objects.get()
        assert hold.status == "RELEASED"
        assert _departure(built).capacity_held == 0  # type: ignore[attr-defined]

    def test_the_trip_returns_to_priced_with_its_quote_intact(self) -> None:
        """§20.5: "payment failed → back to PRICED". Another card, same price."""
        built = in_basket()
        services.fail_basket(built.trip_id, cause=BasketFailure.PAYMENT_FAILED)
        trip = _trip(built)
        assert trip.status == "PRICED"  # type: ignore[attr-defined]
        assert trip.quote_expires_at is not None  # type: ignore[attr-defined]

    def test_the_history_names_the_system_and_the_reason(self) -> None:
        """BR-032, and ADR 0025's convention for a System actor."""
        built = in_basket()
        services.fail_basket(built.trip_id, cause=BasketFailure.PAYMENT_FAILED)
        last = BookingStatusHistory.objects.order_by("id").last()
        assert last is not None
        assert (last.from_status, last.to_status) == ("PENDING", "FAILED")
        assert (last.actor_role, last.actor_user_id) == ("SYSTEM", None)
        assert "Payment failed" in last.reason


class TestAHoldExpiringUnderABasket:
    """§17.5's sweeper, now that a basket may be sitting on the hold."""

    def test_the_sweeper_fails_the_basket(self) -> None:
        built = in_basket()
        _age_the_holds(built)
        assert release_expired_holds() == {"holds": 1, "trips": 1}
        assert Booking.objects.get().status == "FAILED"

    def test_the_trip_returns_to_draft_because_the_offer_lapsed(self) -> None:
        """§20.5: "quote expired → back to DRAFT". The plan is editable again."""
        built = in_basket()
        _age_the_holds(built)
        release_expired_holds()
        trip = _trip(built)
        assert trip.status == "DRAFT"  # type: ignore[attr-defined]
        assert trip.quote_expires_at is None  # type: ignore[attr-defined]

    def test_the_capacity_is_given_back(self) -> None:
        built = in_basket(adults=3)
        _age_the_holds(built)
        release_expired_holds()
        assert _departure(built).capacity_held == 0  # type: ignore[attr-defined]

    def test_running_it_again_changes_nothing(self) -> None:
        """§8.8: idempotent."""
        built = in_basket()
        _age_the_holds(built)
        release_expired_holds()
        assert release_expired_holds() == {"holds": 0, "trips": 0}
        assert BookingStatusHistory.objects.filter(to_status="FAILED").count() == 1

    def test_a_trip_with_only_a_quote_still_just_expires(self) -> None:
        """The pre-Phase-7 path is untouched: no basket, no bookings to fail."""
        built = scenario.build()
        services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        _age_the_holds(built)
        release_expired_holds()
        assert _trip(built).status == "DRAFT"  # type: ignore[attr-defined]
        assert not Booking.objects.exists()


class TestWhatIsNotFailed:
    def test_a_trip_with_no_basket_fails_nothing(self) -> None:
        built = scenario.build()
        assert services.fail_basket(built.trip_id, cause=BasketFailure.HOLD_EXPIRED) == 0

    def test_a_booking_already_past_pending_is_left_alone(self) -> None:
        """Only PENDING → FAILED is an edge. A confirmed booking is not failed
        by a late sweep."""
        built = in_basket()
        Booking.objects.update(status="CONFIRMED")
        assert services.fail_basket(built.trip_id, cause=BasketFailure.HOLD_EXPIRED) == 0
        assert Booking.objects.get().status == "CONFIRMED"

    def test_a_standing_quote_without_its_seats_cannot_become_a_basket(self) -> None:
        """§20.2's DRAFT → PENDING guard is "valid quote token; hold live".
        After a failed payment the token is valid and the hold is not."""
        from apps.common.errors import InventoryUnavailableError

        built = scenario.build()
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        services.fail_basket(built.trip_id, cause=BasketFailure.PAYMENT_FAILED)

        with pytest.raises(InventoryUnavailableError) as refused:
            services.create_basket(
                built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
            )
        assert refused.value.code == "HOLD_EXPIRED"
        assert Booking.objects.filter(status="PENDING").count() == 0

    def test_a_failed_basket_can_be_quoted_and_booked_again(self) -> None:
        """The way back: re-price, then a fresh basket. The old booking stays
        FAILED, as history, and a new one is created."""
        built = in_basket()
        services.fail_basket(built.trip_id, cause=BasketFailure.PAYMENT_FAILED)
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        assert sorted(Booking.objects.values_list("status", flat=True)) == ["FAILED", "PENDING"]
