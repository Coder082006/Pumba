"""A discarded plan gives its seats back — `trip.TripDeleted`, §8.9, §17.5.

`DELETE /trips/{id}` is `trip`'s endpoint and the holds behind a PRICED trip
are `inventory`'s rows, and §6.4 forbids either module from reaching the other.
The seam is the event bus, and this is the test that it is actually wired:
`handlers.register()` runs in `BookingConfig.ready()`, which is exactly the
kind of one-line start-up hook that is easy to delete and impossible to notice.

The assertions are about capacity, not about the handler — a test that only
checked `get_subscribers` would pass for a handler subscribed to the wrong
event.
"""

from __future__ import annotations

import pytest
from django.apps import apps as django_apps

from apps.booking import services
from apps.booking.tests import scenario
from apps.common.errors import ConflictError
from apps.trip import services as trip_services

pytestmark = pytest.mark.django_db


def _in_basket(**options: object) -> scenario.Scenario:
    built = scenario.build(**options)  # type: ignore[arg-type]
    quote = services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
    services.create_basket(
        built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
    )
    return built


def _hold(built: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "InventoryHold").objects.get(trip_id=built.trip_id)


def _departure(built: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "ActivityDeparture").objects.get(
        id=built.departure_id
    )


class TestAPricedTripThatIsDeleted:
    def test_the_held_seats_come_back_at_once(self, django_capture_on_commit_callbacks) -> None:  # type: ignore[no-untyped-def]
        """The callbacks are executed explicitly because `publish` defers to
        commit, and a test wrapped in a rolled-back transaction never commits.
        That deferral is the guarantee §8.9 exists for, so the test bends to it
        rather than the other way round."""
        built = scenario.build(adults=2)
        services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        assert _departure(built).capacity_held == 2  # type: ignore[attr-defined]

        with django_capture_on_commit_callbacks(execute=True):
            trip_services.delete_trip(built.trip_public_id, tourist_id=built.tourist_id)

        assert _hold(built).status == "RELEASED"  # type: ignore[attr-defined]
        assert _departure(built).capacity_held == 0  # type: ignore[attr-defined]

    def test_the_trip_is_gone_rather_than_flagged(self) -> None:
        built = scenario.build()
        services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)

        trip_services.delete_trip(built.trip_public_id, tourist_id=built.tourist_id)

        trips = django_apps.get_model("trip", "Trip").objects.filter(id=built.trip_id)
        assert not trips.exists()


class TestADraftThatNeverHeldAnything:
    def test_it_deletes_without_the_handler_finding_work(self) -> None:
        """`inventory.release` answers zero rather than raising, so a DRAFT
        needs no special case anywhere on this path."""
        built = scenario.build()

        trip_services.delete_trip(built.trip_public_id, tourist_id=built.tourist_id)

        assert not django_apps.get_model("trip", "Trip").objects.filter(id=built.trip_id).exists()


class TestABasketIsNotAPlan:
    def test_a_trip_in_payment_is_refused(self) -> None:
        """§7.2: booking records are not deleted. The basket is the boundary."""
        built = _in_basket()

        with pytest.raises(ConflictError):
            trip_services.delete_trip(built.trip_public_id, tourist_id=built.tourist_id)

    def test_a_trip_whose_basket_failed_is_refused_too(self) -> None:
        """The case the status check alone would let through.

        A failed basket puts the trip back in DRAFT and leaves
        `itinerary_item.booking_id` pointing at the FAILED bookings — which are
        records §7.2 keeps. Deleting the trip would leave them naming a trip
        that no longer exists.
        """
        built = _in_basket()
        services.fail_basket(built.trip_id, cause=services.BasketFailure.PAYMENT_FAILED)

        with pytest.raises(ConflictError):
            trip_services.delete_trip(built.trip_public_id, tourist_id=built.tourist_id)

    def test_cancelling_it_is_the_way_out(self) -> None:
        """What the refusal tells the tourist to do actually works."""
        built = _in_basket()

        services.cancel_trip(built.trip_public_id, tourist_id=built.tourist_id, actor_user_id=None)

        trip = django_apps.get_model("trip", "Trip").objects.get(id=built.trip_id)
        assert trip.status == "CANCELLED"
        assert _hold(built).status == "RELEASED"  # type: ignore[attr-defined]
