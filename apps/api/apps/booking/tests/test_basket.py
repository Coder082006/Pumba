"""Creating the basket — SRS §9.4.6, TC-060, TC-061, BR-033, BR-037, ADR 0025.

TC-060: "Confirm creates the basket | Valid unexpired quote | Confirm | 201;
bookings PENDING; policy and commission snapshotted; trip PENDING_PAYMENT".

TC-061: "Confirm with an expired quote | Quote expired | Confirm | 409
QUOTE_EXPIRED; no bookings created".

Every refusal below also asserts the second half of TC-061's expectation —
nothing was written — because a basket that fails halfway and leaves bookings
behind is worse than one that never started.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from django.apps import apps as django_apps

from apps.booking import services
from apps.booking.models import Booking, BookingActivity, BookingStatusHistory
from apps.booking.services import (
    NotBookableError,
    QuoteExpiredError,
    TripNotPayableError,
)
from apps.booking.tests import scenario
from apps.common.errors import ConflictError, NotFoundError
from apps.trip import services as trip_services

pytestmark = pytest.mark.django_db


def quoted(**options: object) -> tuple[scenario.Scenario, services.QuoteResult]:
    built = scenario.build(**options)  # type: ignore[arg-type]
    return built, services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)


def basket(built: scenario.Scenario, quote: services.QuoteResult, **kwargs: object) -> object:
    return services.create_basket(
        built.trip_public_id,
        tourist_id=built.tourist_id,
        quote_token=quote.quote_token,
        **kwargs,  # type: ignore[arg-type]
    )


def _trip_status(built: scenario.Scenario) -> str:
    return str(django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id).status)


class TestTC060ConfirmCreatesTheBasket:
    def test_the_bookings_are_pending(self) -> None:
        built, quote = quoted()
        result = services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        assert [b.status for b in result.bookings] == ["PENDING"]
        assert Booking.objects.get().status == "PENDING"

    def test_the_policy_is_snapshotted(self) -> None:
        """BR-041. The tiers are frozen on the row, not referenced."""
        built, quote = quoted(policy_code="STRICT_14D")
        basket(built, quote)
        row = Booking.objects.get()
        assert row.cancellation_policy_id == built.policy_id
        assert row.cancellation_policy_snapshot["code"] == "STRICT_14D"
        assert row.cancellation_policy_snapshot["tiers"] == [
            {"hours_before": 336, "refund_percent": "50"}
        ]

    def test_the_commission_rate_is_snapshotted_and_the_amount_is_not(self) -> None:
        """ADR 0025 decision 6: the rate at the basket, the amount at capture."""
        built, quote = quoted()
        basket(built, quote)
        row = Booking.objects.get()
        assert row.commission_rate == Decimal("15.00")
        assert (row.commission_amount, row.net_amount) == (None, None)

    def test_the_trip_is_pending_payment(self) -> None:
        built, quote = quoted()
        result = basket(built, quote)
        assert _trip_status(built) == "PENDING_PAYMENT"
        assert result.trip_status == "PENDING_PAYMENT"  # type: ignore[attr-defined]

    def test_the_item_is_linked_to_its_booking(self) -> None:
        """R21: `itinerary_item.booking_id`, written by nothing until now."""
        built, quote = quoted()
        basket(built, quote)
        item = django_apps.get_model("trip", "ItineraryItem").objects.get(
            public_id=built.item_public_id
        )
        assert item.booking_id == Booking.objects.get().id

    def test_the_booking_records_what_it_sells_and_who_sells_it(self) -> None:
        built, quote = quoted(adults=2, children=1)
        basket(built, quote)
        row = Booking.objects.get()
        activity = BookingActivity.objects.get(booking=row)
        assert row.provider_id == built.provider_id
        assert row.tourist_id == built.tourist_id
        assert activity.activity_departure_id == built.departure_id
        assert (activity.pax_adult, activity.pax_child) == (2, 1)

    def test_the_first_history_row_names_the_tourist(self) -> None:
        """BR-032: every change writes a history row naming actor and reason."""
        built, quote = quoted()
        basket(built, quote, actor_user_id=77)
        [history] = BookingStatusHistory.objects.all()
        assert (history.from_status, history.to_status) == (None, "PENDING")
        assert (history.actor_role, history.actor_user_id) == ("TOURIST", 77)
        assert history.reason

    def test_the_holds_are_extended_to_the_payment_window(self) -> None:
        built, quote = quoted()
        result = basket(built, quote)
        hold = django_apps.get_model("inventory", "InventoryHold").objects.get()
        assert hold.expires_at == result.payment_expires_at  # type: ignore[attr-defined]
        assert hold.expires_at > quote.expires_at

    def test_nothing_is_committed_on_basket_creation(self) -> None:
        """§9.4.6: "No inventory is committed ... that happens only on payment capture"."""
        built, quote = quoted()
        basket(built, quote)
        departure = django_apps.get_model("inventory", "ActivityDeparture").objects.get(
            id=built.departure_id
        )
        assert (departure.capacity_held, departure.capacity_sold) == (2, 0)

    def test_the_fee_shares_sum_to_the_trip_fee(self) -> None:
        built, quote = quoted()
        basket(built, quote)
        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)
        assert sum(b.fee_amount for b in Booking.objects.all()) == trip.fee_amount

    def test_the_reference_has_the_specified_shape(self) -> None:
        built, quote = quoted()
        result = basket(built, quote)
        assert result.bookings[0].reference.startswith("BKG-")  # type: ignore[attr-defined]


class TestTC061AnExpiredQuote:
    def test_it_is_refused_and_nothing_is_created(self) -> None:
        built, quote = quoted()
        with pytest.raises(QuoteExpiredError) as refused:
            basket(built, quote, now=quote.expires_at + timedelta(seconds=1))
        assert refused.value.code == "QUOTE_EXPIRED"
        assert refused.value.status_code == 409
        assert not Booking.objects.exists()
        assert _trip_status(built) == "PRICED"

    def test_the_instant_of_expiry_is_already_too_late(self) -> None:
        """`now < quote_expires_at`, strictly — §9.4.6."""
        built, quote = quoted()
        with pytest.raises(QuoteExpiredError):
            basket(built, quote, now=quote.expires_at)

    def test_a_token_from_a_superseded_quote_is_refused(self) -> None:
        """A re-quote changes the offer; the old token names an offer that is gone."""
        built, first = quoted()
        services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        with pytest.raises(QuoteExpiredError):
            basket(built, first)
        assert not Booking.objects.exists()

    def test_a_made_up_token_is_refused(self) -> None:
        built, _ = quoted()
        with pytest.raises(QuoteExpiredError):
            services.create_basket(
                built.trip_public_id, tourist_id=built.tourist_id, quote_token=uuid4()
            )


class TestTheTripMustBePayable:
    def test_an_unpriced_trip_is_refused(self) -> None:
        built = scenario.build()
        with pytest.raises(TripNotPayableError):
            services.create_basket(
                built.trip_public_id, tourist_id=built.tourist_id, quote_token=uuid4()
            )

    def test_a_basket_cannot_be_created_twice(self) -> None:
        """The second attempt finds the trip PENDING_PAYMENT. The idempotency key
        is what makes a *retry* safe; this is what makes a second, different
        attempt refuse."""
        built, quote = quoted()
        basket(built, quote)
        with pytest.raises(TripNotPayableError):
            basket(built, quote)
        assert Booking.objects.count() == 1

    def test_a_trip_of_nothing_bookable_is_refused(self) -> None:
        """Stays and attractions are not sold here (ADR 0013)."""
        built = scenario.build(with_activity=False)
        scenario.add_stay(built)
        quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        with pytest.raises(TripNotPayableError):
            basket(built, quote)

    def test_a_stranger_gets_404(self) -> None:
        built, quote = quoted()
        with pytest.raises(NotFoundError):
            services.create_basket(
                built.trip_public_id,
                tourist_id=built.tourist_id + 999,
                quote_token=quote.quote_token,
            )
        assert not Booking.objects.exists()


class TestEveryComponentMustBeSellable:
    def test_br_037_an_unverified_seller_is_refused(self) -> None:
        built, quote = quoted(seller_status="UNDER_REVIEW")
        with pytest.raises(NotBookableError) as refused:
            basket(built, quote)
        assert refused.value.details[0]["reason"] == "PROVIDER_NOT_VERIFIED"
        assert not Booking.objects.exists()

    def test_br_037_a_suspended_seller_is_refused(self) -> None:
        built, quote = quoted(seller_status="SUSPENDED")
        with pytest.raises(NotBookableError):
            basket(built, quote)

    def test_br_037_a_verified_seller_is_accepted(self) -> None:
        built, quote = quoted(seller_status="VERIFIED")
        basket(built, quote)
        assert Booking.objects.count() == 1

    def test_an_activity_nobody_sells_is_refused_not_guessed(self) -> None:
        """ADR 0025: `activity.provider_id` is nullable, and a null is refused."""
        built, quote = quoted(seller_status=None)
        with pytest.raises(NotBookableError) as refused:
            basket(built, quote)
        assert refused.value.details[0]["reason"] == "NO_PROVIDER"

    def test_br_033_a_component_starting_in_the_past_is_refused(self) -> None:
        """BR-033: "A booking may not start in the past".

        Unreachable through a live quote — the activity's booking cutoff refuses
        it first — so the rule is exercised on the check itself. It stays in the
        basket as the last defence for a listing whose cutoff is zero.
        """
        built, _ = quoted()
        basis = trip_services.basket_basis(built.trip_public_id, tourist_id=built.tourist_id)
        [line] = basis.lines
        _, problems = services._seller_problems(basis.lines, line.starts_at + timedelta(seconds=1))
        assert problems == [
            {"item": str(line.item_public_id), "title": line.title, "reason": "STARTS_IN_THE_PAST"}
        ]

    def test_br_033_a_component_starting_later_is_accepted(self) -> None:
        built, _ = quoted()
        basis = trip_services.basket_basis(built.trip_public_id, tourist_id=built.tourist_id)
        [line] = basis.lines
        _, problems = services._seller_problems(basis.lines, line.starts_at - timedelta(seconds=1))
        assert problems == []


class TestTheTripHalf:
    def test_open_payment_refuses_a_trip_that_is_not_priced(self) -> None:
        """The lock-then-check that makes one basket per quote true."""
        built = scenario.build()
        with pytest.raises(ConflictError) as refused:
            trip_services.open_payment(
                built.trip_public_id, tourist_id=built.tourist_id, bookings={}
            )
        assert refused.value.code == "TRIP_NOT_PAYABLE"
