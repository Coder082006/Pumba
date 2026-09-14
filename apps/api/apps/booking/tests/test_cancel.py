"""Cancelling a booking — SRS §20.2, §20.9, BR-040 to BR-048, TC-100 to TC-102.

`test_domain_cancellation.py` proves the arithmetic. This file proves what the
arithmetic is wired to: the snapshot and not the live policy (BR-040), the
preview equal to the refund (BR-043), the seats back on sale at once (BR-048),
the state rules (BR-042), and the trip that ends when its last component does.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps

from apps.booking import services
from apps.booking.domain.lifecycle import Actor
from apps.booking.models import Booking, BookingStatusHistory
from apps.booking.services import CancellationNotPermittedError
from apps.booking.tests import scenario

pytestmark = pytest.mark.django_db


def confirmed(**options: object) -> scenario.Scenario:
    built = scenario.build(**options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    services.confirm_trip(built.trip_id, payment_captured=True)
    return built


def _row() -> Booking:
    return Booking.objects.get()


def _departure(built: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "ActivityDeparture").objects.get(
        id=built.departure_id
    )


def _trip(built: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)


class TestTheTouristCancels:
    def test_a_confirmed_booking_is_cancelled(self) -> None:
        confirmed()
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        row = _row()
        assert row.status == "CANCELLED"
        assert (row.cancelled_by, row.cancellation_reason) == ("TOURIST", "TOURIST_REQUEST")
        assert row.cancelled_at is not None

    def test_the_history_names_the_tourist(self) -> None:
        confirmed()
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        last = BookingStatusHistory.objects.order_by("id").last()
        assert last is not None
        assert (last.from_status, last.to_status, last.actor_role, last.actor_user_id) == (
            "CONFIRMED",
            "CANCELLED",
            "TOURIST",
            5,
        )

    def test_br_048_the_seats_return_to_sale_at_once(self) -> None:
        built = confirmed(adults=3)
        assert _departure(built).capacity_sold == 3  # type: ignore[attr-defined]
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert _departure(built).capacity_sold == 0  # type: ignore[attr-defined]

    def test_the_trip_ends_when_its_last_component_does(self) -> None:
        built = confirmed()
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert _trip(built).status == "CANCELLED"  # type: ignore[attr-defined]

    def test_cancelling_twice_is_refused_not_refunded_twice(self) -> None:
        confirmed()
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        with pytest.raises(CancellationNotPermittedError):
            services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert BookingStatusHistory.objects.filter(to_status="CANCELLED").count() == 1


class TestBR042WhenATouristMayCancel:
    @pytest.mark.parametrize("status", ["IN_PROGRESS", "COMPLETED", "FAILED", "REFUNDED"])
    def test_not_once_it_has_started_or_ended(self, status: str) -> None:
        confirmed()
        Booking.objects.update(status=status)
        with pytest.raises(CancellationNotPermittedError):
            services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)

    def test_an_awaiting_booking_may_be_cancelled(self) -> None:
        confirmed(confirmation_mode="ON_REQUEST")
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert _row().status == "CANCELLED"


class TestAPendingBooking:
    def _pending(self, **options: object) -> scenario.Scenario:
        built = scenario.build(**options)  # type: ignore[arg-type]
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        return built

    def test_nothing_is_refunded_because_nothing_was_paid(self) -> None:
        self._pending()
        result = services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert result.refund_amount == Decimal("0.00")

    def test_its_held_seats_are_released(self) -> None:
        built = self._pending(adults=2)
        assert _departure(built).capacity_held == 2  # type: ignore[attr-defined]
        services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert _departure(built).capacity_held == 0  # type: ignore[attr-defined]


class TestBR040TheSnapshotNotTheLivePolicy:
    def test_editing_the_policy_after_the_basket_changes_nothing(self) -> None:
        """BR-040: "computed from the policy snapshot on the booking, never from
        the provider's current policy"."""
        built = confirmed(policy_code="MODERATE_7D", days_ahead=30)
        django_apps.get_model("catalogue", "CancellationPolicy").objects.filter(
            id=built.policy_id
        ).update(tiers=[])
        result = services.cancel_booking(_row().public_id, actor=Actor.TOURIST, actor_user_id=5)
        assert result.refund_percent == Decimal(100)


class TestBR043ThePreviewIsTheRefund:
    @pytest.mark.parametrize("hours_before", [400, 100, 10], ids=["full", "half", "none"])
    def test_they_agree_at_the_same_instant(self, hours_before: int) -> None:
        """One instant in each of MODERATE_7D's three bands."""
        confirmed(days_ahead=30)
        row = _row()
        at = row.starts_at - timedelta(hours=hours_before)
        preview = services.preview_cancellation(row, now=at)
        done = services.cancel_booking(row.public_id, actor=Actor.TOURIST, actor_user_id=5, now=at)
        assert (preview.refund_percent, preview.refund_amount) == (
            done.refund_percent,
            done.refund_amount,
        )

    def test_a_preview_writes_nothing(self) -> None:
        confirmed()
        services.preview_cancellation(_row())
        assert _row().status == "CONFIRMED"
        assert BookingStatusHistory.objects.filter(to_status="CANCELLED").count() == 0

    def test_the_preview_says_whether_cancelling_is_possible(self) -> None:
        confirmed()
        assert services.preview_cancellation(_row()).cancellable is True
        Booking.objects.update(status="IN_PROGRESS")
        assert services.preview_cancellation(_row()).cancellable is False


class TestTC102AProviderCancels:
    def test_the_tourist_gets_everything_back_whatever_the_tier(self) -> None:
        """TC-102: "100% tourist refund regardless of tier (BR-045)"."""
        confirmed(policy_code="NON_REFUNDABLE")
        row = _row()
        result = services.cancel_booking(row.public_id, actor=Actor.PROVIDER, actor_user_id=8)
        assert result.refund_percent == Decimal(100)
        assert result.refund_amount == row.gross_amount + row.fee_amount + row.tax_amount
        assert (_row().cancelled_by, _row().cancellation_reason) == (
            "PROVIDER",
            "PROVIDER_UNAVAILABLE",
        )
