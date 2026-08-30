"""The read seam ADR 0012 promised — SRS §6.4, §6.5.

ADR 0012 stores a cross-module reference as a plain integer and says the row is
read back through "a service call returning a DTO". Nothing implemented that
call for two phases: `catalogue.services` was an administrator write surface
and a seed loader, with no read interface at all, so `trip` had no supported
way to turn `destination_id` into "Stone Town".

The tests below hold the four properties that make this a boundary rather than
a hole: the integer goes in and never comes out, one query serves any number of
ids, a withdrawn listing is still named, and a soft-deleted one is not.
"""

from __future__ import annotations

import pytest

from apps.catalogue import services
from apps.catalogue.dto import ListingRefDTO
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
    make_region,
)
from apps.common.errors import ValidationError

pytestmark = pytest.mark.django_db


class TestTheReferenceableSet:
    def test_it_is_exactly_these_four(self) -> None:
        """The tables `trip.itinerary_item` holds ids for. Stated as a set so
        that widening it is a deliberate edit rather than a side effect of
        adding a curated table."""
        assert set(services.REFERENCEABLE) == {
            "destination",
            "attraction",
            "activity",
            "accommodation",
        }

    def test_an_unknown_kind_is_refused(self) -> None:
        """Not a silent empty dict. A typo'd kind would otherwise render an
        entire itinerary with no names and nothing would say why."""
        with pytest.raises(ValidationError, match="referenceable"):
            services.resolve_refs("provider", [1])


class TestResolution:
    def test_it_names_a_destination(self) -> None:
        destination = make_destination(name="Stone Town", slug="stone-town")
        refs = services.resolve_refs("destination", [destination.id])
        assert refs[destination.id] == ListingRefDTO(
            public_id=destination.public_id, slug="stone-town", name="Stone Town"
        )

    def test_the_integer_never_comes_back(self) -> None:
        """§7.2 and ADR 0012 together: the id is an input the caller already
        holds, and what returns is identified by `public_id`. A DTO carrying
        the integer would put it one serializer away from the wire."""
        destination = make_destination()
        ref = services.resolve_refs("destination", [destination.id])[destination.id]
        assert not hasattr(ref, "id")
        assert ref.public_id == destination.public_id

    @pytest.mark.parametrize(
        ("kind", "factory"),
        [
            ("attraction", make_attraction),
            ("activity", make_activity),
            ("accommodation", make_accommodation),
        ],
    )
    def test_every_referenceable_kind_resolves(self, kind: str, factory: object) -> None:
        row = factory(destination=make_destination())  # type: ignore[operator]
        refs = services.resolve_refs(kind, [row.id])
        assert refs[row.id].name == row.name

    def test_an_empty_request_makes_no_query(self, django_assert_num_queries: object) -> None:
        """An itinerary of stays and free time references nothing. Asking the
        database anyway would put a query on the page for every kind that
        happened not to appear."""
        with django_assert_num_queries(0):  # type: ignore[operator]
            assert services.resolve_refs("destination", []) == {}

    def test_none_values_are_ignored(self) -> None:
        """Callers pass a column straight through, and most of those columns
        are nullable by design — the five subject references on
        `itinerary_item` are null for every item type but one."""
        destination = make_destination()
        refs = services.resolve_refs("destination", [destination.id, None])  # type: ignore[list-item]
        assert list(refs) == [destination.id]

    def test_many_ids_cost_one_query(self, django_assert_num_queries: object) -> None:
        """The reason this takes a list at all. A fortnight's itinerary has a
        reference on most of its rows, and a per-row lookup would be an N+1
        across four tables to render one screen."""
        # One region, five destinations: `make_destination()` with no
        # argument builds a whole country each time, and `country.iso_code` is
        # unique.
        region = make_region()
        ids = [make_destination(region, slug=f"d-{n}").id for n in range(5)]
        with django_assert_num_queries(1):  # type: ignore[operator]
            refs = services.resolve_refs("destination", ids)
        assert len(refs) == 5

    def test_a_repeated_id_is_asked_for_once(self) -> None:
        """Two items at the same hotel is the ordinary case, not an edge one."""
        destination = make_destination()
        refs = services.resolve_refs("destination", [destination.id] * 4)
        assert list(refs) == [destination.id]

    def test_a_missing_id_is_simply_absent(self) -> None:
        """The caller knows what it asked for and is the only layer that can
        decide what a dangling reference means."""
        assert services.resolve_refs("destination", [9_999_999]) == {}


class TestVisibility:
    def test_a_withdrawn_listing_is_still_named(self) -> None:
        """Deliberate, and the opposite of what a public read would do.

        A trip may hold an item whose listing was deactivated after it was
        added — which is exactly what VR-09 exists to report. Filtering it out
        here would leave the item nameless, and a finding that cannot say
        *which* listing is unavailable is not actionable.
        """
        destination = make_destination(is_active=False)
        assert destination.id in services.resolve_refs("destination", [destination.id])

    def test_a_soft_deleted_listing_is_not(self) -> None:
        """`deleted_at` means the row is gone rather than hidden (§7.7), and
        `all_objects` is the administrative path. A caller here is rendering a
        tourist's itinerary, not auditing."""
        destination = make_destination()
        destination.delete()
        assert services.resolve_refs("destination", [destination.id]) == {}
