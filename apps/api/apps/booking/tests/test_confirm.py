"""The confirmation routine — SRS §20.8, BR-030, BR-070, BR-071, TC-070.

§20.8 is twenty steps inside one transaction. Each assertion below names the
step it proves, so a reader can check the routine against the specification
without reading the routine.

Phase 7 has no payment to capture, so the routine is called the way Phase 8's
webhook will call it — `payment_captured=True` — and the way SUPER_ADMIN's
force-transition will, with `forced_by`.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.booking import services
from apps.booking.models import Booking, BookingStatusHistory
from apps.booking.tests import scenario
from apps.common.state_machine import GuardFailedError

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


class TestAnInstantBookingConfirms:
    def test_step_12_the_booking_is_confirmed(self) -> None:
        built = in_basket()
        result = services.confirm_trip(built.trip_id, payment_captured=True)
        row = Booking.objects.get()
        assert row.status == "CONFIRMED"
        assert result.confirmed == (row.reference,)

    def test_step_13_it_records_when(self) -> None:
        built = in_basket()
        now = timezone.now()
        services.confirm_trip(built.trip_id, payment_captured=True, now=now)
        assert Booking.objects.get().confirmed_at == now

    def test_steps_7_to_11_held_capacity_becomes_sold(self) -> None:
        """BR-030: not CONFIRMED until the hold is committed."""
        built = in_basket(adults=3)
        services.confirm_trip(built.trip_id, payment_captured=True)
        departure = _departure(built)
        assert (departure.capacity_held, departure.capacity_sold) == (0, 3)  # type: ignore[attr-defined]
        hold = django_apps.get_model("inventory", "InventoryHold").objects.get()
        assert hold.status == "COMMITTED"

    def test_step_14_commission_is_computed_from_the_frozen_rate(self) -> None:
        """BR-070: a rule change after the basket must not reach this booking."""
        built = in_basket(adults=2, price_per_person="95.00")
        Booking.objects.update(commission_rate=Decimal("15.00"))
        services.confirm_trip(built.trip_id, payment_captured=True)
        row = Booking.objects.get()
        assert row.gross_amount == Decimal("190.00")
        assert (row.commission_amount, row.net_amount) == (Decimal("28.50"), Decimal("161.50"))

    def test_step_15_a_history_row_names_the_system(self) -> None:
        built = in_basket()
        services.confirm_trip(built.trip_id, payment_captured=True)
        last = BookingStatusHistory.objects.order_by("id").last()
        assert last is not None
        assert (last.from_status, last.to_status, last.actor_role) == (
            "PENDING",
            "CONFIRMED",
            "SYSTEM",
        )

    def test_steps_16_and_17_the_trip_confirms_and_its_item_is_locked(self) -> None:
        built = in_basket()
        services.confirm_trip(built.trip_id, payment_captured=True)
        trip = _trip(built)
        assert trip.status == "CONFIRMED"  # type: ignore[attr-defined]
        assert trip.confirmed_at is not None  # type: ignore[attr-defined]
        item = django_apps.get_model("trip", "ItineraryItem").objects.get(
            public_id=built.item_public_id
        )
        assert item.is_locked is True


class TestAnOnRequestBookingWaits:
    """§14.4: "the booking enters AWAITING_PROVIDER on payment"."""

    def test_step_12_it_awaits_its_provider(self) -> None:
        built = in_basket(confirmation_mode="ON_REQUEST")
        result = services.confirm_trip(built.trip_id, payment_captured=True)
        row = Booking.objects.get()
        assert row.status == "AWAITING_PROVIDER"
        assert result.awaiting_provider == (row.reference,)
        assert row.confirmed_at is None

    def test_its_deadline_is_provider_response_hours_away(self) -> None:
        """ADR 0025 decision 7: the deadline is stored and is the rule."""
        built = in_basket(confirmation_mode="ON_REQUEST")
        now = timezone.now()
        services.confirm_trip(built.trip_id, payment_captured=True, now=now)
        assert Booking.objects.get().response_due_at == now + timedelta(hours=24)

    def test_its_capacity_is_still_sold(self) -> None:
        """Money was taken, so the seat is taken; a decline gives it back."""
        built = in_basket(confirmation_mode="ON_REQUEST", adults=2)
        services.confirm_trip(built.trip_id, payment_captured=True)
        assert _departure(built).capacity_sold == 2  # type: ignore[attr-defined]


class TestStep2ItIsIdempotent:
    def test_running_it_twice_confirms_once(self) -> None:
        built = in_basket()
        services.confirm_trip(built.trip_id, payment_captured=True)
        again = services.confirm_trip(built.trip_id, payment_captured=True)
        assert again.moved is False
        assert BookingStatusHistory.objects.filter(to_status="CONFIRMED").count() == 1
        assert _departure(built).capacity_sold == 2  # type: ignore[attr-defined]

    def test_a_trip_with_no_basket_confirms_nothing(self) -> None:
        built = scenario.build()
        assert services.confirm_trip(built.trip_id, payment_captured=True).moved is False


class TestItNeedsAPayment:
    def test_without_a_captured_payment_the_guard_refuses_and_nothing_moves(self) -> None:
        """BR-030: "cannot become CONFIRMED until its covering payment is CAPTURED"."""
        built = in_basket()
        with pytest.raises(GuardFailedError):
            services.confirm_trip(built.trip_id, payment_captured=False)
        assert Booking.objects.get().status == "PENDING"
        assert _departure(built).capacity_sold == 0  # type: ignore[attr-defined]

    def test_a_super_admin_may_force_it_and_is_named(self) -> None:
        """BR-038: guards bypassed, the actor and reason recorded."""
        built = in_basket()
        services.confirm_trip(
            built.trip_id, payment_captured=False, forced_by=42, reason="Paid by bank transfer."
        )
        last = BookingStatusHistory.objects.order_by("id").last()
        assert last is not None
        assert (last.to_status, last.actor_role, last.actor_user_id) == (
            "CONFIRMED",
            "SUPER_ADMIN",
            42,
        )
        assert last.reason == "Paid by bank transfer."


class TestStep9TheHardCase:
    """§20.8: "Failure at step 9 (a hold expired while the payment was in
    flight) is the one genuinely hard case" — re-acquire if possible, otherwise
    fail that one booking and confirm the rest."""

    def _expire_holds(self) -> None:
        django_apps.get_model("inventory", "InventoryHold").objects.update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )

    def test_a_dead_hold_with_seats_still_free_is_re_acquired_and_confirms(self) -> None:
        built = in_basket(adults=2, capacity=12)
        self._expire_holds()
        result = services.confirm_trip(built.trip_id, payment_captured=True)
        assert result.confirmed and not result.failed
        assert Booking.objects.get().status == "CONFIRMED"
        departure = _departure(built)
        assert (departure.capacity_held, departure.capacity_sold) == (0, 2)  # type: ignore[attr-defined]

    def test_a_dead_hold_whose_seats_the_sweeper_gave_away_fails_that_booking(self) -> None:
        """The sweeper expired the hold and somebody else took the seats."""
        built = in_basket(adults=2, capacity=2)
        self._expire_holds()
        # A sweep that released the seats while payment was in flight, and a
        # second tourist who bought them.
        django_apps.get_model("inventory", "InventoryHold").objects.update(status="EXPIRED")
        django_apps.get_model("inventory", "ActivityDeparture").objects.filter(
            id=built.departure_id
        ).update(capacity_held=0, capacity_sold=2)

        result = services.confirm_trip(built.trip_id, payment_captured=True)

        assert result.failed == (Booking.objects.get().reference,)
        assert Booking.objects.get().status == "FAILED"

    def test_the_failed_component_publishes_its_refund(
        self,
        monkeypatch: pytest.MonkeyPatch,
        django_capture_on_commit_callbacks: object,
    ) -> None:
        """§20.8: "initiate an automatic partial refund for the failed component".
        Phase 8 subscribes; here the obligation is observed leaving."""
        from apps.common import events

        received: list[object] = []
        monkeypatch.setattr(
            events,
            "_subscribers",
            {services.ComponentFailedAfterCapture: [received.append]},
        )
        built = in_basket(adults=2, capacity=2)
        django_apps.get_model("inventory", "InventoryHold").objects.update(
            status="EXPIRED", expires_at=timezone.now() - timedelta(seconds=1)
        )
        django_apps.get_model("inventory", "ActivityDeparture").objects.filter(
            id=built.departure_id
        ).update(capacity_held=0, capacity_sold=2)

        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            services.confirm_trip(built.trip_id, payment_captured=True)

        [event] = received
        assert event.reason == "SOLD_OUT"  # type: ignore[attr-defined]
        booking = Booking.objects.get()
        assert event.gross_amount == str(booking.gross_amount)  # type: ignore[attr-defined]

    def test_when_nothing_could_be_secured_the_trip_is_cancelled_not_confirmed(self) -> None:
        """ADR 0025, third addendum: paid, so not PRICED; empty, so not CONFIRMED."""
        built = in_basket(adults=2, capacity=2)
        django_apps.get_model("inventory", "InventoryHold").objects.update(
            status="EXPIRED", expires_at=timezone.now() - timedelta(seconds=1)
        )
        django_apps.get_model("inventory", "ActivityDeparture").objects.filter(
            id=built.departure_id
        ).update(capacity_held=0, capacity_sold=2)
        services.confirm_trip(built.trip_id, payment_captured=True)
        assert _trip(built).status == "CANCELLED"  # type: ignore[attr-defined]

    def test_nothing_is_oversold_by_a_re_acquisition(self) -> None:
        built = in_basket(adults=2, capacity=2)
        django_apps.get_model("inventory", "InventoryHold").objects.update(
            status="EXPIRED", expires_at=timezone.now() - timedelta(seconds=1)
        )
        django_apps.get_model("inventory", "ActivityDeparture").objects.filter(
            id=built.departure_id
        ).update(capacity_held=0, capacity_sold=1)
        services.confirm_trip(built.trip_id, payment_captured=True)
        departure = _departure(built)
        assert departure.capacity_sold == 1  # type: ignore[attr-defined]
        assert Booking.objects.get().status == "FAILED"


class TestBR037AtCapture:
    """BR-037: VERIFIED "at the moment of confirmation"."""

    def test_a_provider_suspended_since_the_basket_fails_its_component(self) -> None:
        built = in_basket(adults=2)
        django_apps.get_model("provider", "Provider").objects.filter(id=built.provider_id).update(
            verify_status="SUSPENDED"
        )
        result = services.confirm_trip(built.trip_id, payment_captured=True)
        assert result.failed and not result.confirmed
        assert Booking.objects.get().status == "FAILED"

    def test_its_held_seats_are_released_not_sold(self) -> None:
        built = in_basket(adults=2)
        django_apps.get_model("provider", "Provider").objects.filter(id=built.provider_id).update(
            verify_status="SUSPENDED"
        )
        services.confirm_trip(built.trip_id, payment_captured=True)
        departure = _departure(built)
        assert (departure.capacity_held, departure.capacity_sold) == (0, 0)  # type: ignore[attr-defined]

    def test_a_verified_provider_confirms(self) -> None:
        built = in_basket()
        assert services.confirm_trip(built.trip_id, payment_captured=True).confirmed
