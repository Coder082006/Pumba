"""The facts a basket freezes, as `trip` resolves them — §9.4.6, ADR 0025.

`booking` may not read `catalogue` or `transport`, so everything a booking
snapshots arrives on `trip.services.basket_basis`. These tests read it the way
the basket will: a priced trip, then the DTO.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.booking import services as booking
from apps.booking.tests import scenario
from apps.common.errors import NotFoundError
from apps.trip import services as trip

pytestmark = pytest.mark.django_db


def priced(**options: object) -> scenario.Scenario:
    built = scenario.build(**options)  # type: ignore[arg-type]
    booking.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    return built


class TestAnActivityLine:
    def test_it_carries_the_quoted_price_and_the_bound_departure(self) -> None:
        built = priced(price_per_person="95.00", adults=2)
        [line] = trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id).lines

        assert line.item_type == "ACTIVITY"
        assert line.gross_amount == Decimal("190.00")
        assert line.activity_departure_id == built.departure_id
        assert (line.pax, line.pax_adult, line.pax_child) == (2, 2, 0)

    def test_it_names_the_seller_and_how_it_confirms(self) -> None:
        built = priced(confirmation_mode="ON_REQUEST")
        [line] = trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id).lines

        assert line.provider_id == built.provider_id
        assert line.confirmation_mode == "ON_REQUEST"

    def test_an_unassigned_activity_says_so_rather_than_guessing(self) -> None:
        """ADR 0025: the basket refuses it; the basis must not invent a seller."""
        built = priced(seller_status=None)
        [line] = trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id).lines
        assert line.provider_id is None

    def test_the_policy_arrives_as_the_json_a_booking_freezes(self) -> None:
        """BR-041. Percentages are strings so they survive JSON exactly."""
        built = priced(policy_code="MODERATE_7D")
        [line] = trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id).lines

        assert line.cancellation_policy_id == built.policy_id
        assert line.policy_snapshot == {
            "code": "MODERATE_7D",
            "name": "Moderate_7D",
            "tiers": [
                {"hours_before": 168, "refund_percent": "100"},
                {"hours_before": 48, "refund_percent": "50"},
            ],
        }


class TestTheTrip:
    def test_it_carries_the_quote_it_was_priced_under(self) -> None:
        built = priced()
        basis = trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id)

        assert basis.status == "PRICED"
        assert basis.priced_at is not None and basis.quote_expires_at is not None
        assert basis.total_amount == basis.subtotal_amount + basis.fee_amount + basis.tax_amount

    def test_a_stay_produces_no_line(self) -> None:
        """ADR 0013: an anchor, not a product."""
        built = scenario.build()
        scenario.add_stay(built)
        booking.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        lines = trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id).lines
        assert [line.item_type for line in lines] == ["ACTIVITY"]

    def test_a_stranger_gets_404(self) -> None:
        """§30.3: ownership is a filter."""
        built = priced()
        with pytest.raises(NotFoundError):
            trip.basket_basis(built.trip_public_id, tourist_id=built.tourist_id + 999)
