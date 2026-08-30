"""The trip read path — SRS §6.5, §7.2, §30.3, §10.6.

Four things are held here, and three of them fail silently if they break.

* **A foreign principal sees nothing, and learns nothing.** §30.3 wants 404
  rather than 403, so `get_trip` filters by owner instead of checking after the
  fact. The test that matters is not "it returns None" but that it returns the
  *same* None for a stranger's trip and for a trip that never existed.
* **No integer identifier reaches a DTO.** §7.2, and this module stores five
  cross-module references as integers (ADR 0012), so it has more chances to
  leak one than any module before it.
* **Reading a trip is a fixed number of queries.** The N+1 here would be one
  per catalogue table per row, and it would arrive as a slow page rather than
  as a failure.
* **Findings carry public ids.** The domain works in primary keys because
  §10.4's tie-break needs a cheap total order; the wire may not see them.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, timedelta
from uuid import UUID, uuid4

import pytest

from apps.trip import selectors
from apps.trip.domain.findings import Finding, Severity, SuggestedAction
from apps.trip.models import ItemType
from apps.trip.tests import external_rows
from apps.trip.tests.factories import AT, make_flight, make_item, make_itinerary, make_trip

pytestmark = pytest.mark.django_db


def a_trip_with_everything() -> tuple[object, object]:
    """A trip carrying one of every reference the item table permits."""
    destination = external_rows.make_destination()
    trip = make_trip(destination_id=destination.id)
    itinerary = make_itinerary(trip)
    activity = external_rows.make_activity(destination)

    make_item(
        itinerary,
        item_type=ItemType.STAY,
        day_number=1,
        sequence_no=1,
        accommodation_id=external_rows.make_accommodation_id(destination),
    )
    make_item(
        itinerary,
        item_type=ItemType.ACTIVITY,
        day_number=1,
        sequence_no=2,
        activity_id=activity.id,
    )
    make_item(
        itinerary,
        item_type=ItemType.ATTRACTION,
        day_number=1,
        sequence_no=3,
        attraction_id=external_rows.make_attraction_id(destination),
    )
    make_item(
        itinerary,
        item_type=ItemType.TRANSFER,
        day_number=1,
        sequence_no=4,
        origin_point=external_rows.make_destination().centroid,
        target_point=destination.centroid,
        origin_destination_id=destination.id,
        distance_m=9100,
        travel_seconds=780,
        estimate_quality="APPROXIMATE",
    )
    make_flight(trip, gateway_destination_id=external_rows.make_destination(is_gateway=True).id)
    return trip, destination


class TestOwnership:
    def test_a_tourist_reads_their_own_trip(self) -> None:
        trip, _ = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        assert dto.reference == trip.reference

    def test_a_stranger_gets_the_same_answer_as_for_a_trip_that_never_existed(self) -> None:
        """§30.3's actual requirement, and the reason it is one assertion.

        Testing only that a stranger gets `None` would pass for an
        implementation that raised `PermissionDenied` for one and `NotFound`
        for the other — which is precisely the leak §30.3 forbids, because the
        difference tells an attacker the trip exists.
        """
        trip, _ = a_trip_with_everything()
        stranger = external_rows.make_tourist_id()

        foreign = selectors.get_trip(trip.public_id, tourist_id=stranger)
        imaginary = selectors.get_trip(uuid4(), tourist_id=stranger)
        assert foreign is imaginary is None

    def test_the_list_contains_only_the_caller_s_trips(self) -> None:
        mine, _ = a_trip_with_everything()
        make_trip()  # somebody else's
        summaries = selectors.list_trips(tourist_id=mine.tourist_id)
        assert [s.public_id for s in summaries] == [mine.public_id]

    def test_trips_of_is_the_only_door(self) -> None:
        """Every read composes from this, so the ownership predicate lives in
        one place rather than in each caller's `filter()`."""
        trip, _ = a_trip_with_everything()
        assert selectors.trips_of(trip.tourist_id).count() == 1
        assert selectors.trips_of(external_rows.make_tourist_id()).count() == 0


class TestNoIntegerIdentifiersEscape:
    """§7.2: sequential integers are never returned to clients."""

    def _walk(self, value: object, seen: set[int] | None = None) -> list[str]:
        """Every field name reachable from a DTO whose name looks like an id."""
        seen = seen if seen is not None else set()
        if id(value) in seen or value is None:
            return []
        seen.add(id(value))

        if isinstance(value, list | tuple):
            return [name for item in value for name in self._walk(item, seen)]
        if not is_dataclass(value):
            return []

        offenders: list[str] = []
        for f in fields(value):
            child = getattr(value, f.name)
            if (f.name == "id" or f.name.endswith("_id")) and f.name != "public_id":
                offenders.append(f"{type(value).__name__}.{f.name}")
            offenders.extend(self._walk(child, seen))
        return offenders

    def test_no_dto_in_a_full_trip_carries_an_id_field(self) -> None:
        """Walks the whole graph rather than checking `TripDTO` alone.

        The five cross-module references are on `ItineraryItemDTO`, two levels
        down, and they are stored as integers by ADR 0012 — so this module has
        more chances to leak one than any before it.
        """
        trip, _ = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert self._walk(dto) == []

    def test_the_guard_would_notice(self) -> None:
        """A check nobody has seen fail is not a check."""
        from dataclasses import dataclass

        @dataclass(frozen=True)
        class Leaky:
            destination_id: int

        assert self._walk(Leaky(destination_id=1)) == ["Leaky.destination_id"]

    def test_references_are_resolved_to_names(self) -> None:
        trip, destination = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        assert dto.destination.slug == destination.slug
        stay = next(i for i in dto.itinerary.items if i.item_type == ItemType.STAY)
        assert stay.accommodation is not None
        assert stay.accommodation.name


class TestQueryCount:
    def test_reading_a_trip_is_a_fixed_number_of_queries(
        self, django_assert_num_queries: object
    ) -> None:
        """The N+1 this shape invites is one query per catalogue table per
        row. It would arrive as a slow page rather than as a failure, which is
        why the number is pinned rather than merely kept in mind.

        Seven, and the breakdown is worth writing down so the next person to
        change it knows which one they moved: the trip with its itinerary
        (`select_related`), the items, the flights, and one `resolve_refs` for
        each of the four catalogue tables an itinerary can reference —
        accommodation, activity, attraction and destination. Destination is one
        query rather than three despite being referenced by the trip, a
        transfer endpoint and a flight gateway, which is the point of gathering
        the ids before resolving them.
        """
        trip, _ = a_trip_with_everything()
        with django_assert_num_queries(7):  # type: ignore[operator]
            selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)

    def test_more_items_do_not_cost_more_queries(self, django_assert_num_queries: object) -> None:
        """The assertion that actually catches an N+1. A fixed count on a
        fixed fixture passes for an implementation that scales with rows."""
        trip, destination = a_trip_with_everything()
        itinerary = trip.itinerary
        for n in range(5, 12):
            make_item(
                itinerary,
                item_type=ItemType.ATTRACTION,
                day_number=1,
                sequence_no=n,
                attraction_id=external_rows.make_attraction_id(destination),
            )
        with django_assert_num_queries(7):  # type: ignore[operator]
            selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)

    def test_listing_trips_does_not_scale_with_trips(
        self, django_assert_num_queries: object
    ) -> None:
        tourist = external_rows.make_tourist_id()
        destination = external_rows.make_destination()
        for _ in range(4):
            make_trip(tourist_id=tourist, destination_id=destination.id)
        with django_assert_num_queries(2):  # type: ignore[operator]
            assert len(selectors.list_trips(tourist_id=tourist)) == 4


class TestFindings:
    def test_item_ids_become_public_ids(self) -> None:
        """The domain works in primary keys because §10.4's tie-break needs a
        cheap total order. §7.2 does not let those reach a client."""
        item_public = uuid4()
        finding = Finding(
            code="VR-02",
            severity=Severity.ERROR,
            message="Two things overlap.",
            item_ids=(41,),
            suggested_action=SuggestedAction.RESCHEDULE_ITEM,
        )
        dto = selectors.to_finding_dto(finding, {41: item_public})
        assert dto.item_ids == (item_public,)
        assert dto.severity == "ERROR"
        assert dto.suggested_action == "RESCHEDULE_ITEM"

    def test_an_unknown_item_id_is_dropped_rather_than_nulled(self) -> None:
        """A null inside `item_ids` is something every client render would
        have to defend against, for a reference that cannot be followed."""
        finding = Finding(
            code="VR-16", severity=Severity.WARNING, message="No stay.", item_ids=(99,)
        )
        assert selectors.to_finding_dto(finding, {}).item_ids == ()

    def test_a_trip_level_finding_names_no_item(self) -> None:
        finding = Finding(code="VR-16", severity=Severity.WARNING, message="No stay.")
        assert selectors.to_finding_dto(finding, {}).item_ids == ()


class TestShape:
    def test_a_transfer_keeps_its_provenance(self) -> None:
        """ADR 0019: the label §12.6 requires cannot be lost between the row
        and the screen."""
        trip, _ = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        transfer = next(i for i in dto.itinerary.items if i.item_type == ItemType.TRANSFER)
        assert transfer.estimate_quality == "APPROXIMATE"
        assert transfer.is_approximate
        assert transfer.travel_seconds == 780

    def test_a_stay_carries_no_money(self) -> None:
        """ADR 0013, enforced by the database and restated by the DTO shape."""
        trip, _ = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        stay = next(i for i in dto.itinerary.items if i.item_type == ItemType.STAY)
        assert stay.unit_price is None and stay.line_total is None and stay.currency is None
        assert not stay.is_approximate

    def test_a_trip_with_no_itinerary_is_not_an_error(self) -> None:
        """§10.2 creates the itinerary with the trip, but a row can exist
        before that in a partially-applied write, and a read must not raise."""
        trip = make_trip()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        assert dto.itinerary is None

    def test_flights_are_returned_with_their_gateway_named(self) -> None:
        trip, _ = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        assert len(dto.flights) == 1
        assert dto.flights[0].gateway.name
        assert dto.flights[0].actual_at is None

    def test_a_soft_deleted_destination_makes_the_trip_unreadable(self) -> None:
        """§7.5.10 makes `destination_id` NOT NULL, so a trip whose
        destination has been retired is a broken row rather than a trip with a
        missing field. The caller renders 404 instead of a shape the client's
        types say cannot exist.
        """
        trip, destination = a_trip_with_everything()
        destination.delete()
        assert selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id) is None

    def test_the_summary_is_narrower_than_the_trip(self) -> None:
        """§24.20's list does not render an itinerary, and a list endpoint
        that returns the detail shape teaches its callers to depend on fields
        it will later have to stop loading."""
        trip, _ = a_trip_with_everything()
        summary = selectors.list_trips(tourist_id=trip.tourist_id)[0]
        assert not hasattr(summary, "itinerary")
        assert not hasattr(summary, "flights")


class TestPartySize:
    def test_it_matches_the_domain_s_definition(self) -> None:
        """`PartyFacts.size` and `TripDTO.party_size` are written twice,
        because the domain may not import the DTO module. A test is what stops
        them drifting — the failure would be an activity capacity check that
        disagreed with the number shown beside it."""
        from apps.trip.domain.validation import PartyFacts

        trip = make_trip(adults=2, children=3, infants=1)
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        assert dto.party_size == PartyFacts(adults=2, children=3, infants=1).size


class TestTimes:
    def test_instants_survive_the_round_trip_in_utc(self) -> None:
        """§7.2 stores TIMESTAMPTZ in UTC. A DTO that handed back a naive
        datetime would be rendered in the server's zone by whatever formatted
        it next."""
        trip, _ = a_trip_with_everything()
        dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
        assert dto is not None
        item = dto.itinerary.items[0]
        assert item.starts_at.tzinfo is not None
        assert item.starts_at.astimezone(UTC) == AT
        assert item.ends_at - item.starts_at == timedelta(minutes=60)


def test_uuids_are_uuids_not_strings() -> None:
    """A serializer will format them; a DTO that pre-stringified would make
    every consumer parse them back to compare."""
    finding = Finding(code="VR-01", severity=Severity.ERROR, message="x", item_ids=(1,))
    dto = selectors.to_finding_dto(finding, {1: UUID(int=7)})
    assert isinstance(dto.item_ids[0], UUID)


@pytest.mark.django_db
def test_generated_at_is_absent_before_a_generate_has_run() -> None:
    """§10.2 creates itinerary v1 empty. `generated_at` being null is how a
    client tells "never generated" from "generated and found nothing", which
    §24.14 renders as a guided prompt rather than an empty timeline."""
    trip = make_trip()
    make_itinerary(trip)
    dto = selectors.get_trip(trip.public_id, tourist_id=trip.tourist_id)
    assert dto is not None and dto.itinerary is not None
    assert dto.itinerary.generated_at is None
    assert dto.itinerary.version == 1
    assert dto.itinerary.validation_state == "NOT_VALIDATED"
    assert dto.itinerary.items == ()
    assert not dto.itinerary.has_errors
