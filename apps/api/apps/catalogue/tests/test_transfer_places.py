"""`transfer_places` — the facts §12.4 prices a transfer leg on.

§12.2 binds a leg's endpoint to a destination, an accommodation, an activity
meeting point or a bare coordinate. §12.4 then prices it on the *destination's*
region (step 3), the country above that (step 4) and `is_gateway` (the airport
surcharge). `transport` may read none of those — §6.4 gives it `location,
provider` — so this is the accessor that carries them across, and ADR 0023
records why the resolving happens on this side of the boundary.

**The join is the point.** A caller holding `accommodation_id = 41` must get
back the region that property sits in, not a null and not the accommodation's
own nonexistent one. Every test here is about that hop being taken correctly
for each of the three bindings that need it.

It is deliberately separate from `place_facts`, which the planner calls for
every item in an itinerary. Folding these columns into that one would make
every stay and attraction carry a region id nobody reads.
"""

from __future__ import annotations

import pytest

from apps.catalogue import services
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
)

pytestmark = pytest.mark.django_db


class TestEachBindingResolvesToItsDestination:
    def test_a_destination_resolves_to_itself(self) -> None:
        """§12.2's first binding, and the base case rather than a special one."""
        row = make_destination()
        (place,) = services.transfer_places("destination", [row.pk]).values()
        assert place.destination_id == row.pk
        assert place.destination_slug == row.slug
        assert place.destination_name == row.name

    def test_an_accommodation_resolves_to_the_destination_it_sits_in(self) -> None:
        destination = make_destination()
        row = make_accommodation(destination)
        places = services.transfer_places("accommodation", [row.pk])
        assert places[row.pk].destination_id == destination.pk

    def test_an_activity_resolves_to_the_destination_it_sits_in(self) -> None:
        destination = make_destination()
        row = make_activity(destination)
        places = services.transfer_places("activity", [row.pk])
        assert places[row.pk].destination_id == destination.pk

    def test_an_attraction_resolves_to_the_destination_it_sits_in(self) -> None:
        destination = make_destination()
        row = make_attraction(destination)
        places = services.transfer_places("attraction", [row.pk])
        assert places[row.pk].destination_id == destination.pk

    def test_the_key_is_the_id_that_was_asked_for(self) -> None:
        """Not the destination's. A caller holding an accommodation id should
        not have to know the answer came from a join in order to look it up —
        and with two properties in one destination, a destination-keyed map
        would silently lose one of them."""
        destination = make_destination()
        first = make_accommodation(destination, slug="first-lodge")
        second = make_accommodation(destination, slug="second-lodge")
        places = services.transfer_places("accommodation", [first.pk, second.pk])
        assert set(places) == {first.pk, second.pk}


class TestTheFactsTheLadderNeeds:
    def test_the_region_and_country_come_from_the_hierarchy(self) -> None:
        """Steps 3 and 4. A region read from the wrong level would price every
        leg on the country default and look almost right."""
        row = make_destination()
        (place,) = services.transfer_places("destination", [row.pk]).values()
        assert place.region_id == row.region_id
        assert place.country_id == row.region.country_id

    def test_the_region_survives_the_join_from_an_accommodation(self) -> None:
        """The one that matters for a real leg: a tourist's hotel is the
        origin, and step 3 prices on the region it stands in."""
        destination = make_destination()
        hotel = make_accommodation(destination)
        places = services.transfer_places("accommodation", [hotel.pk])
        assert places[hotel.pk].region_id == destination.region_id

    def test_the_gateway_flag_travels(self) -> None:
        """§12.4's airport surcharge is "IF origin or target is a gateway
        destination", and this is the only place that fact can be read."""
        gateway = make_destination(is_gateway=True, gateway_type="AIRPORT", gateway_code="ZZZ")
        (place,) = services.transfer_places("destination", [gateway.pk]).values()
        assert place.is_gateway is True

    def test_an_ordinary_destination_is_not_a_gateway(self) -> None:
        """The control. Without it the flag could be hard-coded true and the
        assertion above would still pass."""
        (place,) = services.transfer_places("destination", [make_destination().pk]).values()
        assert place.is_gateway is False

    def test_the_timezone_is_the_destinations_own(self) -> None:
        """§12.4 evaluates the night window against "pickup_at local time", so
        the caller needs a zone to convert into before it can ask."""
        row = make_destination()
        (place,) = services.transfer_places("destination", [row.pk]).values()
        assert place.timezone == row.timezone

    def test_the_coordinate_comes_from_the_destination_centroid(self) -> None:
        row = make_destination()
        (place,) = services.transfer_places("destination", [row.pk]).values()
        assert float(place.coordinates.lat) == pytest.approx(row.centroid.y, abs=1e-6)
        assert float(place.coordinates.lon) == pytest.approx(row.centroid.x, abs=1e-6)


class TestItStaysOneQuery:
    def test_many_accommodations_cost_one_query(self, django_assert_num_queries) -> None:  # type: ignore[no-untyped-def]
        """An itinerary prices every transfer at once. A query per endpoint
        would be an N+1 on the path the whole batching effort exists for, and
        `select_related` reaching through to the region is what keeps the
        country lookup out of the loop as well."""
        destination = make_destination()
        ids = [make_accommodation(destination, slug=f"lodge-{n}").pk for n in range(5)]
        with django_assert_num_queries(1):
            places = services.transfer_places("accommodation", ids)
            assert {p.country_id for p in places.values()} == {destination.region.country_id}
        assert len(places) == len(ids)

    def test_nothing_asked_for_is_nothing_queried(self, django_assert_num_queries) -> None:  # type: ignore[no-untyped-def]
        with django_assert_num_queries(0):
            assert services.transfer_places("accommodation", []) == {}

    def test_an_unknown_kind_is_refused_by_name(self) -> None:
        """`vehicle` is a real table in another module and exactly the sort of
        thing somebody would try. Naming the legal set in the message is what
        turns a guess into a fix."""
        with pytest.raises(Exception, match="referenceable"):
            services.transfer_places("vehicle", [1])
