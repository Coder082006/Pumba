"""Planning facts — SRS §10.4, §10.6, §15.2.

The seam `trip` reads to *generate* an itinerary, as opposed to `resolve_refs`,
which it reads to *render* a stored one. They are separate because they are
asked at different moments and cost different amounts, and merging them would
put the expensive read on the cheap path.

Two properties carry the weight here:

* **Visibility is not applied, and `is_active` is returned instead.** A trip
  may hold an item whose listing was withdrawn after it was added; filtering it
  out would leave VR-09 unable to say which listing became unavailable.
* **Opening hours are evaluated in catalogue, in the destination's zone.**
  §15.2's rule is subtle — a range crossing midnight belongs to the previous
  local day — and a second implementation in `trip` would drift from this one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from apps.catalogue import services
from apps.catalogue.tests.factories import (
    DEFAULT_ZONE,
    make_activity,
    make_attraction,
    make_destination,
    make_region,
)
from apps.common.errors import ValidationError

pytestmark = pytest.mark.django_db


class TestPlaceFacts:
    def test_it_returns_coordinates(self) -> None:
        """§10.4 compares locations and asks for travel between them. `trip`
        never reads a geometry column itself, which is what keeps §13.1's
        "geography, never planar" a decision made in one module."""
        destination = make_destination()
        facts = services.place_facts("destination", [destination.id])[destination.id]
        assert facts.coordinates.lat == Decimal("-35.28")
        assert facts.coordinates.lon == Decimal("174.05")
        assert facts.slug == destination.slug

    def test_a_withdrawn_listing_is_returned_and_flagged(self) -> None:
        """The property VR-09 depends on. Filtering it out would leave the
        finding unable to name the listing that became unavailable."""
        destination = make_destination(is_active=False)
        facts = services.place_facts("destination", [destination.id])[destination.id]
        assert facts.is_active is False

    def test_many_ids_cost_one_query(self, django_assert_num_queries: object) -> None:
        region = make_region()
        ids = [make_destination(region, slug=f"d-{n}").id for n in range(5)]
        with django_assert_num_queries(1):  # type: ignore[operator]
            assert len(services.place_facts("destination", ids)) == 5

    def test_an_empty_request_makes_no_query(self, django_assert_num_queries: object) -> None:
        with django_assert_num_queries(0):  # type: ignore[operator]
            assert services.place_facts("destination", []) == {}

    def test_an_unknown_kind_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="referenceable"):
            services.place_facts("provider", [1])


class TestActivityFacts:
    def test_it_carries_what_the_rules_need(self) -> None:
        """VR-05 reads the pax range, VR-06 the cutoff, VR-15 the minimum age
        and §10.7 the prices. Fetching them separately would be four passes
        over the same row."""
        activity = make_activity(min_pax=2, max_pax=12)
        facts = services.activity_facts([activity.id])[activity.id]
        assert facts.min_pax == 2
        assert facts.max_pax == 12
        assert facts.booking_cutoff_hours == 24
        assert facts.duration_minutes == activity.duration_minutes
        assert facts.currency == activity.currency

    def test_a_group_price_is_carried_when_set(self) -> None:
        """§7.5.9's `price_per_group` is nullable, and §10.7 treats its
        presence as replacing the per-person arithmetic rather than adding
        to it."""
        activity = make_activity(price_per_group=Decimal("400.00"))
        facts = services.activity_facts([activity.id])[activity.id]
        assert facts.price_per_group == Decimal("400.00")

    def test_one_query_for_many(self, django_assert_num_queries: object) -> None:
        destination = make_destination()
        ids = [make_activity(destination, slug=f"a-{n}").id for n in range(4)]
        with django_assert_num_queries(1):  # type: ignore[operator]
            assert len(services.activity_facts(ids)) == 4


class TestAttractionFacts:
    def test_visit_minutes_may_be_absent(self) -> None:
        """§15.1 calls it a *recommended* duration and the column is
        nullable, so an attraction without one is placed as a zero-length
        anchor rather than given an invented length."""
        attraction = make_attraction(visit_minutes=None)
        assert services.attraction_facts([attraction.id])[attraction.id].visit_minutes is None

    def test_it_carries_the_duration_when_published(self) -> None:
        attraction = make_attraction(visit_minutes=90)
        assert services.attraction_facts([attraction.id])[attraction.id].visit_minutes == 90


class TestOpeningStatus:
    """VR-12, evaluated where §15.2's rule already lives."""

    def _with_hours(self, hours: object) -> object:
        return make_attraction(opening_hours=hours)

    def test_open_inside_the_published_range(self) -> None:
        attraction = self._with_hours({"mon": [["09:00", "17:00"]]})
        # 2027-06-07 is a Monday. Auckland is UTC+12 in June, so 22:00 UTC on
        # the 6th is 10:00 local on the 7th — inside the range.
        when = datetime(2027, 6, 6, 22, 0, tzinfo=UTC)
        assert services.opening_status([(attraction.id, when)])[attraction.id] is True

    def test_closed_outside_it(self) -> None:
        attraction = self._with_hours({"mon": [["09:00", "17:00"]]})
        # 07:00 UTC on the 7th is 19:00 local — after closing.
        when = datetime(2027, 6, 7, 7, 0, tzinfo=UTC)
        assert services.opening_status([(attraction.id, when)])[attraction.id] is False

    def test_it_is_evaluated_in_the_destination_zone_not_utc(self) -> None:
        """The reason this lives in catalogue at all.

        The same instant is inside the range in the destination's zone and
        outside it in UTC. A validator that compared clock times without the
        zone would warn a tourist that a museum was shut while they were
        standing in it.
        """
        attraction = self._with_hours({"mon": [["09:00", "17:00"]]})
        assert DEFAULT_ZONE == "Pacific/Auckland"
        when = datetime(2027, 6, 6, 22, 0, tzinfo=UTC)  # Sunday in UTC
        assert when.strftime("%A") == "Sunday"
        assert services.opening_status([(attraction.id, when)])[attraction.id] is True

    def test_unpublished_hours_are_none_not_closed(self) -> None:
        """§15.2: null means "not published", which is not the same as
        closed. VR-12 warns on False alone, so this is what stops a caution
        appearing on most of the catalogue."""
        attraction = make_attraction(opening_hours=None)
        when = datetime(2027, 6, 7, 7, 0, tzinfo=UTC)
        assert services.opening_status([(attraction.id, when)])[attraction.id] is None

    def test_unparseable_hours_are_none_rather_than_closed(self) -> None:
        """A malformed row is an administrator's problem. Reporting it to the
        tourist as "this may be shut" would surface our data error as their
        planning error."""
        # A valid day key with a malformed value, so this exercises the
        # parser's own error rather than the unknown-key branch.
        attraction = make_attraction(opening_hours={"mon": "nonsense"})
        when = datetime(2027, 6, 7, 7, 0, tzinfo=UTC)
        assert services.opening_status([(attraction.id, when)])[attraction.id] is None

    def test_an_unknown_attraction_is_none(self) -> None:
        when = datetime(2027, 6, 7, 7, 0, tzinfo=UTC)
        assert services.opening_status([(9_999_999, when)])[9_999_999] is None

    def test_many_attractions_cost_one_query(self, django_assert_num_queries: object) -> None:
        """The N+1 this signature exists to prevent: resolving a timezone per
        item would be one query per attraction on a page that has several."""
        destination = make_destination()
        ids = [
            make_attraction(
                destination,
                slug=f"x-{n}",
                opening_hours={"mon": [["09:00", "17:00"]]},
            ).id
            for n in range(4)
        ]
        when = datetime(2027, 6, 6, 22, 0, tzinfo=UTC)
        with django_assert_num_queries(1):  # type: ignore[operator]
            statuses = services.opening_status([(i, when) for i in ids])
        assert all(statuses[i] is True for i in ids)

    def test_an_empty_request_makes_no_query(self, django_assert_num_queries: object) -> None:
        with django_assert_num_queries(0):  # type: ignore[operator]
            assert services.opening_status([]) == {}
