"""Cancelling a whole trip — SRS §9.3, §20.5, §20.9, BR-046.

BR-046: "Cancelling a whole trip evaluates each component independently against
its own policy." §33.10 names "trip-level cancellation with mixed policies" as a
critical scenario with no TC number; `TestMixedPolicies` is it.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.apps import apps as django_apps

from apps.booking import services
from apps.booking.models import Booking
from apps.booking.services import CancellationNotPermittedError
from apps.booking.tests import scenario

pytestmark = pytest.mark.django_db


def confirmed_trip(**options: object) -> scenario.Scenario:
    built = scenario.build(**options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    services.confirm_trip(built.trip_id, payment_captured=True)
    return built


def _add_second_component(built: scenario.Scenario, *, policy: str, price: str) -> None:
    """A second confirmed booking on the same trip under a different policy.

    Written as rows because the scenario builder makes one activity; what is
    under test is that each booking's own snapshot is what gets evaluated.
    """
    first = Booking.objects.get(trip_id=built.trip_id)
    tiers = {
        "NON_REFUNDABLE": [],
        "FLEX_48H": [{"hours_before": 48, "refund_percent": "100"}],
    }[policy]
    Booking.objects.create(
        reference="BKG-2027-0000002",
        trip_id=built.trip_id,
        tourist_id=built.tourist_id,
        provider_id=first.provider_id,
        booking_type="TRANSFER",
        status="CONFIRMED",
        starts_at=first.starts_at,
        ends_at=first.ends_at,
        pax_count=first.pax_count,
        gross_amount=Decimal(price),
        fee_amount=Decimal("0.00"),
        currency=first.currency,
        commission_rate=Decimal("15.00"),
        cancellation_policy_snapshot={"code": policy, "tiers": tiers},
    )


def _trip(built: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)


class TestMixedPolicies:
    """§33.10's "trip-level cancellation with mixed policies"."""

    def test_each_component_is_refunded_under_its_own_policy(self) -> None:
        built = confirmed_trip(policy_code="MODERATE_7D", days_ahead=30)
        _add_second_component(built, policy="NON_REFUNDABLE", price="111000.00")

        result = services.cancel_trip(
            built.trip_public_id, tourist_id=built.tourist_id, actor_user_id=5
        )

        by_code = {c.policy_code: c for c in result.components}
        assert by_code["MODERATE_7D"].refund_percent == Decimal(100)
        assert by_code["NON_REFUNDABLE"].refund_percent == Decimal(0)
        assert result.refund_amount == sum(c.refund_amount for c in result.components)

    def test_every_component_ends_cancelled_and_so_does_the_trip(self) -> None:
        built = confirmed_trip()
        _add_second_component(built, policy="FLEX_48H", price="50.00")
        services.cancel_trip(built.trip_public_id, tourist_id=built.tourist_id, actor_user_id=5)
        assert set(Booking.objects.values_list("status", flat=True)) == {"CANCELLED"}
        trip = _trip(built)
        assert trip.status == "CANCELLED"  # type: ignore[attr-defined]
        assert trip.cancelled_at is not None  # type: ignore[attr-defined]


class TestAllOrNothing:
    def test_a_started_component_refuses_the_whole_request_and_cancels_nothing(self) -> None:
        built = confirmed_trip()
        _add_second_component(built, policy="FLEX_48H", price="50.00")
        Booking.objects.filter(booking_type="TRANSFER").update(status="IN_PROGRESS")

        with pytest.raises(CancellationNotPermittedError) as refused:
            services.cancel_trip(built.trip_public_id, tourist_id=built.tourist_id, actor_user_id=5)

        assert refused.value.details[0]["status"] == "IN_PROGRESS"
        assert Booking.objects.filter(status="CONFIRMED").count() == 1


class TestAPreview:
    def test_it_matches_what_cancelling_returns(self) -> None:
        built = confirmed_trip(days_ahead=30)
        _add_second_component(built, policy="NON_REFUNDABLE", price="111000.00")
        now = Booking.objects.first().starts_at - timedelta(days=10)  # type: ignore[union-attr]

        preview = services.preview_trip_cancellation(
            built.trip_public_id, tourist_id=built.tourist_id, now=now
        )
        done = services.cancel_trip(
            built.trip_public_id, tourist_id=built.tourist_id, actor_user_id=5, now=now
        )
        assert preview.refund_amount == done.refund_amount

    def test_it_writes_nothing(self) -> None:
        built = confirmed_trip()
        services.preview_trip_cancellation(built.trip_public_id, tourist_id=built.tourist_id)
        assert Booking.objects.get().status == "CONFIRMED"


class TestATripWithoutBookings:
    def test_a_priced_trip_is_cancelled_and_its_holds_released(self) -> None:
        built = scenario.build(adults=2)
        services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        result = services.cancel_trip(
            built.trip_public_id, tourist_id=built.tourist_id, actor_user_id=5
        )
        assert result.trip.status == "CANCELLED"
        departure = django_apps.get_model("inventory", "ActivityDeparture").objects.get(
            id=built.departure_id
        )
        assert departure.capacity_held == 0
