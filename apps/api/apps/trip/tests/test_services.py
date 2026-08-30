"""The trip use cases — SRS §10.2, §20.5, §30.3.

The behaviour worth defending here is what happens to somebody who should not
be able to do something. §30.3 wants a foreign principal to receive 404 rather
than 403 on every endpoint, and the service layer is where that is
*implemented* — so every write is tested against a stranger, and the assertion
is that they get the same answer as for a trip that never existed.

Two locks apply and neither subsumes the other, so both are exercised: the
trip's own editability (§20.5) and an individual item's `is_locked` (§10.3).
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest

from apps.common.errors import ConflictError, NotFoundError, ValidationError
from apps.common.events import clear_subscribers, subscribe
from apps.trip import services
from apps.trip.models import ItemType, ItineraryItem, Trip, TripStatus
from apps.trip.tests import external_rows
from apps.trip.tests.factories import AT

pytestmark = pytest.mark.django_db

START = date(2027, 6, 1)
END = date(2027, 6, 6)


@pytest.fixture(autouse=True)
def _no_stray_subscribers() -> object:
    """Events are global. A handler left behind by one test firing inside
    another is the sort of coupling that makes a suite order-dependent."""
    clear_subscribers()
    yield
    clear_subscribers()


def a_destination() -> object:
    return external_rows.make_destination()


def open_trip(**overrides: object) -> object:
    destination = overrides.pop("destination_row", None) or a_destination()
    values: dict[str, object] = {
        "tourist_id": external_rows.make_tourist_id(),
        "destination": destination.slug,
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "today": START - timedelta(days=30),
    }
    values.update(overrides)
    return services.create_trip(**values)  # type: ignore[arg-type]


def owner_of(dto: object) -> int:
    return Trip.objects.get(public_id=dto.public_id).tourist_id  # type: ignore[attr-defined]


class TestCreateTrip:
    def test_it_starts_in_draft_with_an_empty_itinerary(self) -> None:
        """§10.2: DRAFT, and "itinerary v1 created (empty)"."""
        trip = open_trip()
        assert trip.status == TripStatus.DRAFT
        assert trip.itinerary is not None
        assert trip.itinerary.version == 1
        assert trip.itinerary.items == ()

    def test_the_currency_comes_from_the_destination(self) -> None:
        """§4.2 forbids a hard-coded "TZS"; the currency is resolved from the
        destination like every other behaviour."""
        destination = a_destination()
        trip = open_trip(destination_row=destination)
        assert trip.currency == destination.default_currency

    def test_both_rows_land_or_neither_does(self) -> None:
        """A trip with no itinerary is a shape no read path expects, and the
        very first thing the client does after creating one would return a
        null it has no branch for."""
        before = Trip.objects.count()
        with pytest.raises(NotFoundError):
            open_trip(destination="no-such-place")
        assert Trip.objects.count() == before

    def test_an_invisible_destination_is_indistinguishable_from_a_missing_one(self) -> None:
        """§30.3. Choosing a destination is a public read, so one whose market
        has not opened must not be discoverable by trying to plan against it."""
        hidden = external_rows.make_destination()
        hidden.is_active = False
        hidden.save(update_fields=["is_active"])

        with pytest.raises(NotFoundError) as invisible:
            open_trip(destination=hidden.slug)
        with pytest.raises(NotFoundError) as absent:
            open_trip(destination="never-existed")
        assert type(invisible.value) is type(absent.value)

    def test_a_trip_may_not_run_backwards(self) -> None:
        with pytest.raises(ValidationError, match="before it starts"):
            open_trip(start_date=END, end_date=START)

    def test_a_trip_may_not_exceed_the_configured_maximum(self) -> None:
        """`trip.max_days` is a `system_setting`, not a literal: a market
        selling month-long safaris and one selling weekend breaks disagree
        about it, and §4.1 will not have that need a deployment."""
        with pytest.raises(ValidationError, match="days"):
            open_trip(start_date=START, end_date=START + timedelta(days=365))

    def test_it_publishes_after_commit(self, django_capture_on_commit_callbacks: object) -> None:
        """§8.9: on commit, not during. A handler that ran inside the
        transaction could act on a trip a later rollback removed — so the
        callbacks have to be captured and run, rather than merely awaited.
        A test that just asserted `seen` would pass for a `publish` that never
        deferred at all."""
        seen: list[services.TripCreated] = []
        subscribe(services.TripCreated, seen.append)
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            trip = open_trip()
        assert [e.trip_public_id for e in seen] == [str(trip.public_id)]


class TestOwnership:
    """§30.3 on every write, not only on the read."""

    def _stranger(self) -> int:
        return external_rows.make_tourist_id()

    def test_a_stranger_cannot_read_it(self) -> None:
        trip = open_trip()
        assert services.get_trip(trip.public_id, tourist_id=self._stranger()) is None

    @pytest.mark.parametrize(
        "call",
        [
            lambda pid, tid: services.update_trip(pid, tourist_id=tid, title="theirs"),
            lambda pid, tid: services.remove_item(pid, uuid4(), tourist_id=tid),
            lambda pid, tid: services.set_flights(pid, tourist_id=tid, flights=[]),
            lambda pid, tid: services.cancel_trip(pid, tourist_id=tid),
        ],
    )
    def test_every_write_refuses_a_stranger_as_not_found(self, call: object) -> None:
        """`NotFoundError`, never `PermissionDeniedError`. The difference is
        the leak: a 403 confirms the trip exists."""
        trip = open_trip()
        with pytest.raises(NotFoundError):
            call(trip.public_id, self._stranger())  # type: ignore[operator]

    def test_a_stranger_gets_the_same_error_as_for_an_imaginary_trip(self) -> None:
        trip = open_trip()
        stranger = self._stranger()
        with pytest.raises(NotFoundError) as foreign:
            services.update_trip(trip.public_id, tourist_id=stranger, title="x")
        with pytest.raises(NotFoundError) as imaginary:
            services.update_trip(uuid4(), tourist_id=stranger, title="x")
        assert type(foreign.value) is type(imaginary.value)

    def test_a_stranger_s_write_changes_nothing(self) -> None:
        trip = open_trip()
        with pytest.raises(NotFoundError):
            services.update_trip(trip.public_id, tourist_id=self._stranger(), title="theirs")
        assert Trip.objects.get(public_id=trip.public_id).title is None


class TestUpdateTrip:
    def test_it_changes_what_it_was_given(self) -> None:
        trip = open_trip()
        updated = services.update_trip(
            trip.public_id, tourist_id=owner_of(trip), title="Honeymoon", adults=3
        )
        assert updated.title == "Honeymoon"
        assert updated.adults == 3
        assert updated.reference == trip.reference

    def test_shortening_a_trip_leaves_stranded_items_alone(self) -> None:
        """Deliberate. VR-01 reports them against the item the tourist can see
        and move; deleting them silently removes something they chose."""
        trip = open_trip()
        owner = owner_of(trip)
        services.add_item(
            trip.public_id,
            tourist_id=owner,
            item_type=ItemType.FREE_TIME,
            day_number=5,
            sequence_no=1,
            title="Late day",
            starts_at=AT,
            ends_at=AT,
        )
        updated = services.update_trip(
            trip.public_id, tourist_id=owner, end_date=START + timedelta(days=1)
        )
        assert len(updated.itinerary.items) == 1

    def test_a_priced_trip_refuses_edits(self) -> None:
        """§20.5 through the domain predicate, not a status literal. PRICED
        holds inventory with a TTL; editing under that quote would leave the
        holds describing a trip that no longer exists."""
        trip = open_trip()
        Trip.objects.filter(public_id=trip.public_id).update(status=TripStatus.PRICED)
        with pytest.raises(ConflictError, match="cannot be edited"):
            services.update_trip(trip.public_id, tourist_id=owner_of(trip), title="x")


class TestItems:
    def _add(self, trip: object, **overrides: object) -> object:
        fields: dict[str, object] = {
            "item_type": ItemType.FREE_TIME,
            "day_number": 1,
            "sequence_no": 1,
            "title": "Beach",
            "starts_at": AT,
            "ends_at": AT,
        }
        fields.update(overrides)
        return services.add_item(trip.public_id, tourist_id=owner_of(trip), **fields)

    def test_an_item_is_added_and_removed(self) -> None:
        trip = open_trip()
        added = self._add(trip)
        assert len(added.itinerary.items) == 1

        removed = services.remove_item(
            trip.public_id, added.itinerary.items[0].public_id, tourist_id=owner_of(trip)
        )
        assert removed.itinerary.items == ()

    def test_a_transfer_may_not_be_added_by_hand(self) -> None:
        """§10.4 *inserts* transfers. One written by hand would be rewritten
        by the next generate — or worse, survive it and disagree with the leg
        beside it. §24.17's add-custom-leg is a Phase 6 surface over
        `transport`, not this one."""
        trip = open_trip()
        with pytest.raises(ValidationError, match="cannot be added directly"):
            self._add(trip, item_type=ItemType.TRANSFER)

    def test_a_locked_item_refuses_to_change(self) -> None:
        """§10.3, §10.8's LOCKED_ITEM_CONFLICT. Proved to fire rather than
        merely declared."""
        trip = open_trip()
        added = self._add(trip)
        item_id = added.itinerary.items[0].public_id
        ItineraryItem.objects.filter(public_id=item_id).update(is_locked=True)

        with pytest.raises(services.LockedItemError) as caught:
            services.remove_item(trip.public_id, item_id, tourist_id=owner_of(trip))
        assert str(item_id) in str(caught.value)
        assert caught.value.code == "LOCKED_ITEM_CONFLICT"

    def test_a_locked_item_survives_the_refusal(self) -> None:
        trip = open_trip()
        added = self._add(trip)
        item_id = added.itinerary.items[0].public_id
        ItineraryItem.objects.filter(public_id=item_id).update(is_locked=True)
        with pytest.raises(services.LockedItemError):
            services.remove_item(trip.public_id, item_id, tourist_id=owner_of(trip))
        assert ItineraryItem.objects.filter(public_id=item_id).exists()

    def test_an_unknown_item_is_not_found(self) -> None:
        trip = open_trip()
        with pytest.raises(NotFoundError):
            services.remove_item(trip.public_id, uuid4(), tourist_id=owner_of(trip))

    def test_an_item_from_another_trip_is_not_found(self) -> None:
        """The lookup is scoped to the trip in the URL. Without that, a valid
        item id would act on whichever trip the caller named."""
        mine = open_trip()
        theirs = open_trip()
        added = self._add(theirs)
        with pytest.raises(NotFoundError):
            services.remove_item(
                mine.public_id, added.itinerary.items[0].public_id, tourist_id=owner_of(mine)
            )


class TestFlights:
    def _flight(self, gateway: str, direction: str = "INBOUND") -> dict[str, object]:
        return {
            "gateway": gateway,
            "direction": direction,
            "flight_number": "451",
            "airline_iata": "NZ",
            "scheduled_at": AT,
            "pax_count": 2,
        }

    def test_a_put_replaces_the_set(self) -> None:
        trip = open_trip()
        gateway = external_rows.make_destination(is_gateway=True)
        owner = owner_of(trip)

        both = services.set_flights(
            trip.public_id,
            tourist_id=owner,
            flights=[self._flight(gateway.slug), self._flight(gateway.slug, "OUTBOUND")],
            today=START - timedelta(days=30),
        )
        assert len(both.flights) == 2

        one = services.set_flights(
            trip.public_id,
            tourist_id=owner,
            flights=[self._flight(gateway.slug)],
            today=START - timedelta(days=30),
        )
        assert [f.direction for f in one.flights] == ["INBOUND"]

    def test_an_unknown_gateway_is_not_found(self) -> None:
        trip = open_trip()
        with pytest.raises(NotFoundError, match="gateway"):
            services.set_flights(
                trip.public_id,
                tourist_id=owner_of(trip),
                flights=[self._flight("no-such-airport")],
                today=START - timedelta(days=30),
            )

    def test_a_non_gateway_destination_is_accepted(self) -> None:
        """Deliberate. A destination the tourist believes they are flying into
        is a fact about their trip; refusing it because the catalogue has not
        set `is_gateway` would surface the platform's bookkeeping as the
        tourist's problem. VR-07 and VR-08 are what care about the timing."""
        trip = open_trip()
        ordinary = external_rows.make_destination()
        result = services.set_flights(
            trip.public_id,
            tourist_id=owner_of(trip),
            flights=[self._flight(ordinary.slug)],
            today=START - timedelta(days=30),
        )
        assert result.flights[0].gateway.slug == ordinary.slug


class TestCancel:
    def test_a_draft_trip_cancels(self) -> None:
        trip = open_trip()
        cancelled = services.cancel_trip(trip.public_id, tourist_id=owner_of(trip))
        assert cancelled.status == TripStatus.CANCELLED
        assert cancelled.cancelled_at is not None

    def test_a_completed_trip_does_not(self) -> None:
        """§20.5's bound on "any state": a journey that has happened cannot be
        made not to have happened, and the path for that is §21's refund."""
        trip = open_trip()
        Trip.objects.filter(public_id=trip.public_id).update(status=TripStatus.COMPLETED)
        with pytest.raises(ConflictError):
            services.cancel_trip(trip.public_id, tourist_id=owner_of(trip))

    def test_cancelling_twice_is_refused(self) -> None:
        trip = open_trip()
        owner = owner_of(trip)
        services.cancel_trip(trip.public_id, tourist_id=owner)
        with pytest.raises(ConflictError):
            services.cancel_trip(trip.public_id, tourist_id=owner)

    def test_it_publishes_after_commit(self, django_capture_on_commit_callbacks: object) -> None:
        seen: list[services.TripCancelled] = []
        trip = open_trip()
        subscribe(services.TripCancelled, seen.append)
        with django_capture_on_commit_callbacks(execute=True):  # type: ignore[operator]
            services.cancel_trip(trip.public_id, tourist_id=owner_of(trip))
        assert [e.trip_public_id for e in seen] == [str(trip.public_id)]


class TestWritesReturnWhatAReadWould:
    def test_the_write_result_matches_a_subsequent_read(self) -> None:
        """Two shapes for the same trip is how a client starts to disagree
        with itself about what it has."""
        trip = open_trip()
        owner = owner_of(trip)
        written = services.update_trip(trip.public_id, tourist_id=owner, title="Honeymoon")
        read = services.get_trip(trip.public_id, tourist_id=owner)
        assert written == read
