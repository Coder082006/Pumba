"""The trip write primitives — SRS §7.2, §7.5.10, §8.6, §10.8.

The tests that matter here are the refusals. A repository that accepts too much
does not fail — it succeeds, on a field the caller was never meant to reach,
and the damage shows up in whatever reads that column next. So `NEVER_WRITABLE`
is checked field by field rather than as a set comparison, and each name in it
is one a request must not be able to set:

    status              moves only through §20.5's machine
    version             optimistic locking
    is_locked           decides whether a regeneration may touch an item
    booking_id          ties an item to money
    the money columns   only §10.7's computation writes those
    reference/public_id identity
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ObjectDoesNotExist

from apps.common.errors import ValidationError
from apps.common.reference import parse_reference
from apps.trip import repositories as repo
from apps.trip.models import ItemType, ItineraryItem, Trip, TripFlight, TripStatus
from apps.trip.tests import external_rows
from apps.trip.tests.factories import AT, make_item, make_itinerary, make_trip

pytestmark = pytest.mark.django_db


def a_trip() -> Trip:
    return repo.create_trip_row(
        tourist_id=external_rows.make_tourist_id(),
        destination_id=external_rows.make_destination().id,
        start_date=date(2027, 6, 1),
        end_date=date(2027, 6, 6),
        adults=2,
        currency="NZD",
    )


class TestCreateTrip:
    def test_it_starts_in_draft(self) -> None:
        """§10.2: `POST /trips` sets `trip.status = DRAFT`."""
        assert a_trip().status == TripStatus.DRAFT

    def test_it_allocates_a_well_formed_reference(self) -> None:
        parsed = parse_reference(a_trip().reference)
        assert parsed is not None
        assert parsed[0] == repo.TRIP_REFERENCE_PREFIX

    def test_references_differ_between_trips(self) -> None:
        assert a_trip().reference != a_trip().reference

    def test_the_money_columns_start_at_zero_and_agree(self) -> None:
        """§7.5.10's CHECK: total equals subtotal plus fee plus tax. A trip
        that started with a non-zero total and no lines would fail to insert,
        which is the constraint doing its job before anything reads it."""
        trip = a_trip()
        assert trip.total_amount == Decimal("0")
        assert trip.total_amount == trip.subtotal_amount + trip.fee_amount + trip.tax_amount


class TestMassAssignment:
    """The refusals. Each of these succeeds silently if the guard is removed."""

    @pytest.mark.parametrize(
        "field",
        ["status", "version", "reference", "public_id", "total_amount", "confirmed_at"],
    )
    def test_a_trip_write_refuses_a_protected_column(self, field: str) -> None:
        with pytest.raises(repo.UnwritableFieldError, match=field):
            repo.update_trip_row(a_trip(), **{field: "x"})

    @pytest.mark.parametrize("field", ["is_locked", "booking_id", "public_id"])
    def test_an_item_write_refuses_a_protected_column(self, field: str) -> None:
        """`is_locked` and `booking_id` are the two that matter most: the
        first decides whether §10.8's regeneration may rewrite the item, and
        the second ties it to a payment."""
        item = make_item(make_itinerary())
        with pytest.raises(repo.UnwritableFieldError, match=field):
            repo.update_item(item, **{field: True})

    def test_every_never_writable_column_is_actually_unwritable(self) -> None:
        """The set and the guard, checked against each other.

        `NEVER_WRITABLE` is documentation until something proves the writable
        sets exclude every name in it. Without this the list could drift from
        the guard and still read convincingly.
        """
        writable = repo._WRITABLE[Trip] | repo._WRITABLE[ItineraryItem] | repo._WRITABLE[TripFlight]
        assert repo.NEVER_WRITABLE & writable == frozenset()

    def test_an_unknown_field_is_refused_rather_than_ignored(self) -> None:
        """A typo that was silently dropped would look like a successful
        update that changed nothing."""
        with pytest.raises(repo.UnwritableFieldError, match="titel"):
            repo.update_trip_row(a_trip(), titel="Honeymoon")


class TestValidationIsTranslated:
    def test_a_model_rule_becomes_a_platform_error(self) -> None:
        """§32 gives the API one error envelope. A Django ValidationError
        escaping this layer reaches the handler as a 500 rather than the 422
        it is."""
        with pytest.raises(ValidationError, match="currency"):
            repo.update_trip_row(a_trip(), currency="NZDD")

    def test_the_message_names_the_field(self) -> None:
        with pytest.raises(ValidationError) as caught:
            repo.update_trip_row(a_trip(), currency="!!")
        assert "currency" in str(caught.value)

    def test_a_cross_field_rule_is_caught_on_a_partial_update(self) -> None:
        """`full_clean` runs over the whole row rather than the changed
        fields. Validating only what changed would pass an end date moved
        before a start date, because neither field is invalid alone."""
        trip = a_trip()
        with pytest.raises(ValidationError):
            repo.update_trip_row(trip, end_date=date(2027, 5, 1))


class TestPartialUpdate:
    def test_absent_keys_are_left_alone(self) -> None:
        trip = a_trip()
        original = trip.reference
        updated = repo.update_trip_row(trip, title="Honeymoon")
        assert updated.title == "Honeymoon"
        assert updated.reference == original
        assert updated.adults == 2


class TestItinerary:
    def test_it_starts_empty_at_version_one(self) -> None:
        """§10.2: "itinerary v1 created (empty)"."""
        itinerary = repo.create_itinerary(a_trip())
        assert itinerary.version == 1
        assert itinerary.items.count() == 0


class TestItems:
    def test_an_item_is_created_and_removed(self) -> None:
        itinerary = make_itinerary()
        item = repo.create_item(
            itinerary=itinerary,
            day_number=1,
            sequence_no=1,
            item_type=ItemType.FREE_TIME,
            title="Beach",
            starts_at=AT,
            ends_at=AT,
        )
        repo.delete_item(item)
        with pytest.raises(ObjectDoesNotExist):
            ItineraryItem.objects.get(pk=item.pk)

    def test_a_malformed_item_is_refused_before_it_reaches_the_database(self) -> None:
        """§7.5.11's per-type shape. An ACTIVITY with no activity would be
        rejected by the CHECK constraint anyway; catching it here means a
        422 naming the problem rather than a poisoned transaction."""
        with pytest.raises(ValidationError):
            repo.create_item(
                itinerary=make_itinerary(),
                day_number=1,
                sequence_no=1,
                item_type=ItemType.ACTIVITY,
                title="A tour",
                starts_at=AT,
                ends_at=AT,
            )


class TestFlights:
    def _flight(self, direction: str = "INBOUND") -> dict[str, object]:
        return {
            "direction": direction,
            "flight_number": "451",
            "airline_iata": "NZ",
            "gateway_destination_id": external_rows.make_destination(is_gateway=True).id,
            "scheduled_at": AT,
            "pax_count": 2,
        }

    def test_a_put_replaces_the_whole_set(self) -> None:
        """§9.4.2 is a PUT. Doing it as a diff would have to decide what an
        absent direction means, and §11.2's answer is that the tourist no
        longer has that flight."""
        trip = a_trip()
        repo.replace_flights(trip, [self._flight("INBOUND"), self._flight("OUTBOUND")])
        assert trip.flights.count() == 2

        repo.replace_flights(trip, [self._flight("INBOUND")])
        assert [f.direction for f in trip.flights.all()] == ["INBOUND"]

    def test_clearing_them_is_an_empty_list(self) -> None:
        trip = a_trip()
        repo.replace_flights(trip, [self._flight()])
        repo.replace_flights(trip, [])
        assert trip.flights.count() == 0

    def test_the_trip_comes_from_the_caller_not_the_payload(self) -> None:
        """A payload naming a different trip would write one tourist's flight
        onto another's, and the ownership check upstream would have passed —
        it validated the trip in the URL, not the one in the body."""
        mine = a_trip()
        theirs = a_trip()
        payload = self._flight()
        payload["trip"] = theirs
        repo.replace_flights(mine, [payload])
        assert mine.flights.count() == 1
        assert theirs.flights.count() == 0

    def test_two_flights_in_one_direction_are_refused(self) -> None:
        """R19: 1 : 0..2, one per direction.

        Asserted as the platform's `ValidationError` rather than as any
        exception: `full_clean` checks the UniqueConstraint, so this arrives
        as a 422 naming the conflict rather than as an IntegrityError that has
        already poisoned the transaction. Catching bare `Exception` here would
        pass either way and hide which one happens.
        """
        trip = a_trip()
        with pytest.raises(ValidationError):
            repo.replace_flights(trip, [self._flight("INBOUND"), self._flight("INBOUND")])


class TestOwnershipIsNotThisLayer:
    def test_a_repository_write_does_not_check_the_owner(self) -> None:
        """Deliberate, and worth pinning so the separation stays legible.

        Authorisation is `selectors.trips_of` and the service above it. A
        repository that also enforced it would be doing the job in two places,
        and the place that forgets is the one that matters. Every function here
        takes a row a caller has already proved it may write.
        """
        trip = make_trip()
        assert repo.update_trip_row(trip, title="Anyone's").title == "Anyone's"
