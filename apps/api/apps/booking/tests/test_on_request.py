"""On-request activities — SRS §14.4, §20.2, §41.5, BR-045, BR-048, ADR 0025.

§14.4: ON_REQUEST "enters AWAITING_PROVIDER on payment, and the provider must
accept within `provider_response_hours`, default 24, or it auto-cancels with
full refund".

§41.5 names "the on-request timeout scenario" as acceptance and gives it no TC
number; `TestTheTimeout` is it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.booking import services
from apps.booking.models import Booking, BookingStatusHistory
from apps.booking.services import BookingNotAwaitingError
from apps.booking.tasks import expire_provider_responses
from apps.booking.tests import scenario
from apps.common.state_machine import GuardFailedError

pytestmark = pytest.mark.django_db


def awaiting(**options: object) -> scenario.Scenario:
    built = scenario.build(confirmation_mode="ON_REQUEST", **options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    services.confirm_trip(built.trip_id, payment_captured=True)
    return built


def _booking() -> Booking:
    return Booking.objects.get()


def _departure(built: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "ActivityDeparture").objects.get(
        id=built.departure_id
    )


def _trip(built: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)


class TestAccepting:
    def test_an_acceptance_in_time_confirms(self) -> None:
        awaiting()
        row = _booking()
        services.accept_request(row.public_id, actor_user_id=9)
        row.refresh_from_db()
        assert row.status == "CONFIRMED"
        assert row.confirmed_at is not None

    def test_an_acceptance_at_the_deadline_is_still_in_time(self) -> None:
        awaiting()
        row = _booking()
        services.accept_request(row.public_id, actor_user_id=9, now=row.response_due_at)
        assert _booking().status == "CONFIRMED"

    def test_an_acceptance_after_the_deadline_is_refused_even_before_the_sweep(self) -> None:
        """ADR 0025 decision 7: the deadline is the rule, not the sweep."""
        awaiting()
        row = _booking()
        assert row.response_due_at is not None
        with pytest.raises(GuardFailedError):
            services.accept_request(
                row.public_id, actor_user_id=9, now=row.response_due_at + timedelta(seconds=1)
            )
        assert _booking().status == "AWAITING_PROVIDER"

    def test_the_history_names_the_provider(self) -> None:
        awaiting()
        services.accept_request(_booking().public_id, actor_user_id=9)
        last = BookingStatusHistory.objects.order_by("id").last()
        assert last is not None
        assert (last.to_status, last.actor_role, last.actor_user_id) == (
            "CONFIRMED",
            "PROVIDER",
            9,
        )

    def test_an_instant_booking_cannot_be_accepted(self) -> None:
        built = scenario.build()
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        services.confirm_trip(built.trip_id, payment_captured=True)
        with pytest.raises(BookingNotAwaitingError):
            services.accept_request(_booking().public_id, actor_user_id=9)


class TestDeclining:
    def test_a_decline_cancels_with_the_provider_named(self) -> None:
        awaiting()
        services.decline_request(_booking().public_id, actor_user_id=9, reason="Boat in repair.")
        row = _booking()
        assert row.status == "CANCELLED"
        assert (row.cancelled_by, row.cancellation_reason) == ("PROVIDER", "PROVIDER_DECLINED")

    def test_br_048_the_seats_go_back_on_sale_at_once(self) -> None:
        built = awaiting(adults=2)
        assert _departure(built).capacity_sold == 2  # type: ignore[attr-defined]
        services.decline_request(_booking().public_id, actor_user_id=9, reason="")
        assert _departure(built).capacity_sold == 0  # type: ignore[attr-defined]

    def test_br_045_the_refund_is_everything_including_the_fee(
        self, monkeypatch: pytest.MonkeyPatch, django_capture_on_commit_callbacks: object
    ) -> None:
        from apps.common import events

        received: list[object] = []
        monkeypatch.setattr(events, "_subscribers", {services.BookingCancelled: [received.append]})
        awaiting()
        row = _booking()
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            services.decline_request(row.public_id, actor_user_id=9, reason="")
        [event] = received
        expected = row.gross_amount + row.fee_amount + row.tax_amount
        assert Decimal(event.refund_amount) == expected  # type: ignore[attr-defined]
        assert event.cancelled_by == "PROVIDER"  # type: ignore[attr-defined]

    def test_a_trip_whose_only_component_was_declined_is_cancelled(self) -> None:
        """§20.5: "all component bookings end cancelled" means the trip is CANCELLED."""
        built = awaiting()
        services.decline_request(_booking().public_id, actor_user_id=9, reason="")
        assert _trip(built).status == "CANCELLED"  # type: ignore[attr-defined]


class TestTheTimeout:
    """§41.5's "on-request timeout scenario"."""

    def _lapse(self) -> None:
        Booking.objects.update(response_due_at=timezone.now() - timedelta(seconds=1))

    def test_an_unanswered_booking_is_cancelled_with_a_full_refund(self) -> None:
        awaiting()
        self._lapse()
        assert expire_provider_responses() == {"cancelled": 1}
        row = _booking()
        assert row.status == "CANCELLED"
        assert (row.cancelled_by, row.cancellation_reason) == (
            "PROVIDER",
            "PROVIDER_RESPONSE_TIMEOUT",
        )

    def test_the_system_is_the_actor(self) -> None:
        awaiting()
        self._lapse()
        expire_provider_responses()
        last = BookingStatusHistory.objects.order_by("id").last()
        assert last is not None
        assert (last.actor_role, last.actor_user_id) == ("SYSTEM", None)

    def test_a_booking_still_in_its_window_is_left_alone(self) -> None:
        awaiting()
        assert expire_provider_responses() == {"cancelled": 0}
        assert _booking().status == "AWAITING_PROVIDER"

    def test_an_accepted_booking_is_not_cancelled_by_a_late_sweep(self) -> None:
        awaiting()
        services.accept_request(_booking().public_id, actor_user_id=9)
        self._lapse()
        assert expire_provider_responses() == {"cancelled": 0}
        assert _booking().status == "CONFIRMED"

    def test_running_it_twice_cancels_once(self) -> None:
        awaiting()
        self._lapse()
        expire_provider_responses()
        assert expire_provider_responses() == {"cancelled": 0}
        assert BookingStatusHistory.objects.filter(to_status="CANCELLED").count() == 1


class TestItIsScheduled:
    def test_the_sweep_is_a_beat_row(self) -> None:
        periodic = django_apps.get_model("django_celery_beat", "PeriodicTask")
        row = periodic.objects.get(task="booking.expire_provider_responses")
        assert row.interval.every == 300
