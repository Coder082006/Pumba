"""The trip schema's invariants, asserted against PostgreSQL.

Nothing writes a trip yet, which is exactly why these are worth having now: the
constraints are the contract the sequencer will be written against, and a
constraint nobody has ever seen fire is a constraint that might not be there.
Each test below writes the malformed row and expects the database to refuse it,
rather than checking that the constraint is declared — a declaration test would
pass against a migration that never ran.

The §7.5.11 shape rules get the most attention because they are the ones with
real consequences downstream. A TRANSFER with no `estimate_quality` is a leg
whose duration has lost its provenance (ADR 0019); a STAY carrying a price is
the accommodation subsystem creeping back in through a nullable column (ADR
0013). Both are the sort of row that inserts cleanly and fails somewhere else.
"""

from __future__ import annotations

from contextlib import AbstractContextManager as ContextManager
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.trip.models import (
    EstimateQuality,
    ItemType,
    ItineraryItem,
    Trip,
    TripStatus,
    ValidationState,
)
from apps.trip.tests import external_rows
from apps.trip.tests.factories import AT, make_flight, make_item, make_itinerary, make_trip

pytestmark = pytest.mark.django_db

HERE = Point(174.05, -35.28, srid=4326)
THERE = Point(173.98, -35.22, srid=4326)


def refuses() -> ContextManager[None]:
    """`IntegrityError` inside its own atomic block.

    Without the nested atomic, the first refused write poisons the surrounding
    transaction and every later assertion in the test fails for the wrong
    reason.
    """
    return transaction.atomic()


class TestTrip:
    def test_defaults_are_the_specified_ones(self) -> None:
        trip = make_trip()
        assert trip.status == TripStatus.DRAFT
        assert trip.adults == 2 and trip.children == 0 and trip.infants == 0
        assert trip.subtotal_amount == Decimal("0")
        assert trip.version == 0
        assert trip.cancelled_at is None

    def test_reference_is_unique(self) -> None:
        first = make_trip()
        with pytest.raises(IntegrityError), refuses():
            make_trip(reference=first.reference)

    def test_end_date_may_not_precede_start(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_trip(start_date=date(2027, 6, 6), end_date=date(2027, 6, 1))

    def test_a_single_day_trip_is_allowed(self) -> None:
        """The constraint is `>=`, not `>`. A day trip is §10.9's own example."""
        trip = make_trip(start_date=date(2027, 6, 1), end_date=date(2027, 6, 1))
        assert trip.start_date == trip.end_date

    def test_a_trip_needs_an_adult(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_trip(adults=0)

    def test_the_total_must_equal_its_parts(self) -> None:
        """§7.5.10's third constraint, and the one that makes a displayed
        total trustworthy: a path that writes a subtotal and forgets the fee
        fails here rather than undercharging."""
        with pytest.raises(IntegrityError), refuses():
            make_trip(
                subtotal_amount=Decimal("100.00"),
                fee_amount=Decimal("5.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("100.00"),
            )

    def test_a_consistent_total_is_accepted(self) -> None:
        trip = make_trip(
            subtotal_amount=Decimal("100.00"),
            fee_amount=Decimal("5.00"),
            tax_amount=Decimal("2.50"),
            total_amount=Decimal("107.50"),
        )
        assert trip.total_amount == Decimal("107.50")

    def test_currency_is_validated_on_the_full_clean_path(self) -> None:
        """The console writes through `full_clean`, not through a service."""
        trip = make_trip()
        trip.currency = "NZDD"
        with pytest.raises(ValidationError):
            trip.full_clean()

    def test_the_tourist_key_is_enforced_by_the_database(self) -> None:
        """ADR 0012: an id in the model, a FOREIGN KEY in the migration.

        The point of the arrangement is that the integrity survives the
        boundary, so it is worth proving rather than assuming — a plain
        `BigIntegerField` with no key would accept this silently.
        """
        with pytest.raises(IntegrityError), refuses():
            make_trip(tourist_id=9_999_999)

    def test_the_destination_key_is_enforced_by_the_database(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_trip(destination_id=9_999_999)


class TestItinerary:
    def test_one_per_trip(self) -> None:
        trip = make_trip()
        make_itinerary(trip)
        with pytest.raises(IntegrityError), refuses():
            make_itinerary(trip)

    def test_starts_at_version_one_and_unvalidated(self) -> None:
        """§10.2: v1 is the empty itinerary created with the trip. It is a
        real version a tourist can open, not a placeholder for one."""
        itinerary = make_itinerary()
        assert itinerary.version == 1
        assert itinerary.validation_state == ValidationState.NOT_VALIDATED
        assert itinerary.generated_at is None
        assert itinerary.total_distance_m is None

    def test_version_zero_is_refused(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_itinerary(version=0)

    def test_deleting_the_trip_takes_the_itinerary(self) -> None:
        trip = make_trip()
        make_itinerary(trip)
        trip.delete()
        assert not Trip.objects.filter(pk=trip.pk).exists()


class TestItemPositions:
    def test_a_position_is_unique_within_an_itinerary(self) -> None:
        itinerary = make_itinerary()
        make_item(itinerary, day_number=1, sequence_no=1)
        with pytest.raises(IntegrityError), refuses():
            make_item(itinerary, day_number=1, sequence_no=1)

    def test_the_same_position_in_another_itinerary_is_fine(self) -> None:
        make_item(make_itinerary(), day_number=1, sequence_no=1)
        make_item(make_itinerary(), day_number=1, sequence_no=1)
        assert ItineraryItem.objects.count() == 2

    def test_positions_are_one_based(self) -> None:
        """§7.5.11 says 1-based, and §10.4 line 20 renumbers from 1. A zero
        would sort first and never be reachable by the renumbering."""
        with pytest.raises(IntegrityError), refuses():
            make_item(day_number=0)
        with pytest.raises(IntegrityError), refuses():
            make_item(sequence_no=0)

    def test_an_item_may_not_end_before_it_starts(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(minutes=-30)

    def test_a_zero_length_item_is_allowed(self) -> None:
        """`>=`, not `>`. A check-out anchor has no duration."""
        item = make_item(minutes=0)
        assert item.ends_at == item.starts_at


class TestStayIsAnAnchor:
    """ADR 0013. A STAY fixes where the tourist sleeps and nothing else."""

    def test_a_curated_property_is_accepted(self) -> None:
        item = make_item(
            item_type=ItemType.STAY,
            accommodation_id=external_rows.make_accommodation_id(),
        )
        assert item.location_point is None

    def test_a_confirmed_free_entry_pin_is_accepted(self) -> None:
        item = make_item(item_type=ItemType.STAY, location_point=HERE)
        assert item.accommodation_id is None

    def test_it_may_not_be_both(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(
                item_type=ItemType.STAY,
                accommodation_id=external_rows.make_accommodation_id(),
                location_point=HERE,
            )

    def test_it_may_not_be_neither(self) -> None:
        """A stay anchor with no location is a night whose transfers cannot
        be planned — §10.9's own words for why VR-16 exists."""
        with pytest.raises(IntegrityError), refuses():
            make_item(item_type=ItemType.STAY)

    def test_it_carries_no_price(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(
                item_type=ItemType.STAY,
                location_point=HERE,
                unit_price=Decimal("180.00"),
                currency="NZD",
            )

    def test_it_carries_no_booking(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(item_type=ItemType.STAY, location_point=HERE, booking_id=1)


class TestActivityAndAttractionShapes:
    def test_an_activity_names_an_activity(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(item_type=ItemType.ACTIVITY)

    def test_an_activity_without_a_bound_departure_is_a_valid_draft(self) -> None:
        """Deliberately permitted. §10.2 binds the departure at generate, and
        an activity chosen before its date is settled is an ordinary draft
        state. VR-06 is what refuses to *quote* one."""
        activity = external_rows.make_activity()
        item = make_item(item_type=ItemType.ACTIVITY, activity_id=activity.id)
        assert item.activity_departure_id is None

    def test_an_activity_may_carry_its_departure(self) -> None:
        activity = external_rows.make_activity()
        item = make_item(
            item_type=ItemType.ACTIVITY,
            activity_id=activity.id,
            activity_departure_id=external_rows.make_activity_departure_id(activity),
        )
        assert item.activity_departure_id is not None

    def test_an_activity_may_not_also_name_an_attraction(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(
                item_type=ItemType.ACTIVITY,
                activity_id=external_rows.make_activity().id,
                attraction_id=external_rows.make_attraction_id(),
            )

    def test_an_attraction_names_an_attraction(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(item_type=ItemType.ATTRACTION)

    def test_an_attraction_is_accepted(self) -> None:
        item = make_item(
            item_type=ItemType.ATTRACTION,
            attraction_id=external_rows.make_attraction_id(),
        )
        assert item.attraction_id is not None


class TestTransferCarriesItsProvenance:
    """ADR 0019: a leg may be estimated, but never anonymously."""

    def _transfer(self, **overrides: object) -> ItineraryItem:
        values: dict[str, object] = {
            "item_type": ItemType.TRANSFER,
            "origin_point": HERE,
            "target_point": THERE,
            "distance_m": 9_100,
            "travel_seconds": 780,
            "estimate_quality": EstimateQuality.APPROXIMATE,
        }
        values.update(overrides)
        return make_item(**values)  # type: ignore[arg-type]

    def test_a_complete_transfer_is_accepted(self) -> None:
        assert self._transfer().estimate_quality == EstimateQuality.APPROXIMATE

    def test_it_needs_both_endpoints(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            self._transfer(target_point=None)

    def test_it_needs_a_duration(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            self._transfer(travel_seconds=None)

    def test_a_duration_without_provenance_is_refused(self) -> None:
        """The defect this constraint exists for: a number on screen that
        nobody can tell was measured or guessed."""
        with pytest.raises(IntegrityError), refuses():
            self._transfer(estimate_quality=None)

    def test_only_a_transfer_may_claim_provenance(self) -> None:
        """The other direction. Without it an ACTIVITY could be stamped
        ROUTED and inherit a credibility it never earned."""
        with pytest.raises(IntegrityError), refuses():
            make_item(
                item_type=ItemType.ATTRACTION,
                attraction_id=external_rows.make_attraction_id(),
                estimate_quality=EstimateQuality.ROUTED,
            )

    def test_endpoints_may_be_destinations_or_merely_points(self) -> None:
        """The destination columns are set when an endpoint happens to be a
        destination. A hotel-to-attraction leg has coordinates at both ends
        and a destination at neither, which is why they are nullable."""
        destination = external_rows.make_destination()
        item = self._transfer(origin_destination_id=destination.id)
        assert item.target_destination_id is None


class TestFreeTime:
    def test_it_references_nothing(self) -> None:
        item = make_item(item_type=ItemType.FREE_TIME)
        assert item.accommodation_id is None and item.activity_id is None

    def test_it_may_not_reference_an_attraction(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(
                item_type=ItemType.FREE_TIME,
                attraction_id=external_rows.make_attraction_id(),
            )


class TestMoneyCarriesItsCurrency:
    """§7.2, in both directions."""

    def test_a_price_without_a_currency_is_refused(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_item(
                item_type=ItemType.ACTIVITY,
                activity_id=external_rows.make_activity().id,
                unit_price=Decimal("95.00"),
                line_total=Decimal("190.00"),
                currency=None,
            )

    def test_a_priced_item_with_its_currency_is_accepted(self) -> None:
        item = make_item(
            item_type=ItemType.ACTIVITY,
            activity_id=external_rows.make_activity().id,
            unit_price=Decimal("95.00"),
            line_total=Decimal("190.00"),
            currency="NZD",
            pax_count=2,
            quantity=2,
        )
        assert item.line_total == Decimal("190.00")


class TestTripFlight:
    def test_at_most_one_per_direction(self) -> None:
        """R19's '0..2' is exactly this."""
        trip = make_trip()
        make_flight(trip, direction="INBOUND")
        make_flight(trip, direction="OUTBOUND")
        with pytest.raises(IntegrityError), refuses():
            make_flight(trip, direction="INBOUND")

    def test_a_flight_carries_a_passenger(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_flight(pax_count=0)

    def test_the_actual_time_is_absent_by_default(self) -> None:
        """§11.2: V1 integrates no flight-status feed. NULL here is the
        ordinary case, not missing data, which is why VR-07 works from
        `scheduled_at` until somebody says otherwise."""
        flight = make_flight()
        assert flight.actual_at is None
        assert flight.scheduled_at == AT

    def test_the_gateway_key_is_enforced(self) -> None:
        with pytest.raises(IntegrityError), refuses():
            make_flight(gateway_destination_id=9_999_999)

    def test_deleting_the_trip_takes_its_flights(self) -> None:
        trip = make_trip()
        make_flight(trip)
        trip.delete()
        assert not Trip.objects.filter(pk=trip.pk).exists()


class TestUpdatedAtTrigger:
    def test_a_raw_update_still_moves_updated_at(self) -> None:
        """`common/0002` attaches the trigger precisely because `save()` is
        not the only writer. A bulk `update()` bypasses the model entirely."""
        trip = make_trip()
        before = trip.updated_at
        Trip.objects.filter(pk=trip.pk).update(title="Renamed")
        trip.refresh_from_db()
        assert trip.updated_at > before
