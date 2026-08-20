"""Attractions and the tag vocabulary — SRS §15.1, §15.2, §15.3, §24.7, §4.2.

Three things are worth pinning here beyond the column list.

**§15.3's fee is informational.** It is paid on site, excluded from the trip
total, and must never become an input to a subtotal. The schema cannot enforce
"never summed" on its own; what it can enforce is that the figure never exists
without its currency, which is where the arithmetic would go wrong first.

**§15.2's hours are evaluated in the destination's zone.** The attraction does
not store one. That is deliberate: a destination correcting its zone corrects
every attraction in it at once, and there is no second copy to disagree.

**§24.7's chips are data.** The tag vocabulary is a table, the columns that use
it are arrays, and a trigger keeps them from drifting apart - an array is not a
foreign key, and a misspelt slug produces an attraction that no filter ever
matches, silently.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.contrib.gis.db.models import PointField
from django.db import IntegrityError, models, transaction

from apps.catalogue.models import Attraction, Tag
from apps.catalogue.tests.factories import (
    make_attraction,
    make_destination,
    make_tag,
)


def field(model: type[models.Model], name: str) -> models.Field:
    return model._meta.get_field(name)  # type: ignore[return-value]


def constraint_names(model: type[models.Model]) -> set[str]:
    return {c.name for c in model._meta.constraints}


class TestSchemaMatchesSection151:
    def test_the_tables_are_named_as_the_srs_names_them(self) -> None:
        assert Attraction._meta.db_table == "attraction"
        assert Tag._meta.db_table == "tag"

    def test_every_section_151_field_exists(self) -> None:
        expected = {
            "name",
            "description",
            "destination",
            "coordinates",
            "opening_hours",
            "entrance_fee",
            "fee_currency",
            "visit_minutes",
            "tags",
            "accessibility_notes",
            "feature_rank",
            "is_active",
        }
        assert {f.name for f in Attraction._meta.get_fields()} >= expected

    def test_an_attraction_is_not_a_bookable_product(self) -> None:
        """§15.1: "not a bookable product". No price, no capacity, no provider:
        where one can be visited commercially, §15.4 says an `activity` carries
        those, linked to it."""
        names = {f.name for f in Attraction._meta.get_fields()}
        assert not names & {"provider", "provider_id", "capacity", "price_per_person"}

    def test_the_coordinates_are_geography_not_geometry(self) -> None:
        coordinates = field(Attraction, "coordinates")
        assert isinstance(coordinates, PointField)
        assert coordinates.geography is True
        assert coordinates.srid == 4326

    def test_the_attraction_stores_no_timezone_of_its_own(self) -> None:
        """§15.2 evaluates hours in the *destination's* zone. A second copy
        here would be a second thing to correct, and one of the two would be
        wrong for however long nobody noticed."""
        assert "timezone" not in {f.name for f in Attraction._meta.fields}

    def test_the_timezone_property_reads_through_to_the_destination(self) -> None:
        assert isinstance(Attraction.timezone, property)

    def test_unpublished_hours_are_null_rather_than_an_empty_week(self) -> None:
        """`{}` and `None` mean different things to `domain.opening_hours`:
        unknown is not closed, and the page renders them differently."""
        assert field(Attraction, "opening_hours").null is True
        assert field(Attraction, "opening_hours").default is None

    def test_the_fee_is_paired_with_a_currency_column(self) -> None:
        """§7.2: "Never store money without its currency"."""
        assert "attraction_fee_has_a_currency" in constraint_names(Attraction)

    def test_the_fee_is_a_decimal_never_a_float(self) -> None:
        fee = field(Attraction, "entrance_fee")
        assert isinstance(fee, models.DecimalField)
        assert (fee.max_digits, fee.decimal_places) == (14, 2)


@pytest.mark.django_db
class TestTheFeeIsInformationalPerSection153:
    def test_a_free_attraction_is_zero_not_absent(self) -> None:
        attraction = make_attraction(entrance_fee=Decimal("0.00"), fee_currency="NZD")
        attraction.refresh_from_db()
        assert attraction.entrance_fee == Decimal("0.00")

    def test_an_unknown_fee_carries_no_currency(self) -> None:
        attraction = make_attraction(entrance_fee=None, fee_currency=None)
        assert attraction.entrance_fee is None

    def test_a_fee_without_a_currency_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_attraction(entrance_fee=Decimal("12.00"), fee_currency=None)

    def test_a_currency_without_a_fee_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_attraction(entrance_fee=None, fee_currency="NZD")

    def test_a_negative_fee_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_attraction(entrance_fee=Decimal("-1.00"), fee_currency="NZD")

    def test_the_fee_currency_need_not_match_the_destination(self) -> None:
        """A site may price in USD inside a market that trades in something
        else. §7.2 wants the currency stored, not assumed."""
        attraction = make_attraction(entrance_fee=Decimal("5.00"), fee_currency="USD")
        assert attraction.fee_currency == "USD"


@pytest.mark.django_db
class TestOtherConstraints:
    def test_a_zero_visit_duration_is_refused(self) -> None:
        """§15.5 spends this in the itinerary; a zero-minute visit would let a
        planner schedule an unbounded number of them in one day."""
        with pytest.raises(IntegrityError):
            make_attraction(visit_minutes=0)

    def test_an_unknown_visit_duration_is_allowed(self) -> None:
        assert make_attraction(visit_minutes=None).visit_minutes is None

    def test_a_zero_feature_rank_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_attraction(feature_rank=0)

    def test_a_slug_is_released_by_soft_deletion(self) -> None:
        first = make_attraction()
        first.delete()
        make_attraction(destination=first.destination)

    def test_deleting_a_destination_with_attractions_is_refused(self) -> None:
        attraction = make_attraction()
        from django.db.models.deletion import ProtectedError

        with pytest.raises(ProtectedError):
            attraction.destination.hard_delete()


@pytest.mark.django_db
class TestTheTagVocabularyIsClosed:
    def test_a_known_tag_is_accepted(self) -> None:
        make_tag(slug="coastal")
        attraction = make_attraction(tags=["coastal"])
        attraction.refresh_from_db()
        assert attraction.tags == ["coastal"]

    def test_an_unknown_tag_is_refused(self) -> None:
        """The typo case. Without this the row saves and simply never appears
        under the chip somebody expected it under."""
        make_tag(slug="coastal")
        with pytest.raises(IntegrityError):
            make_attraction(tags=["coastal", "costal"])

    def test_an_unknown_tag_cannot_arrive_by_bulk_update(self) -> None:
        make_tag(slug="coastal")
        attraction = make_attraction(tags=["coastal"])
        with pytest.raises(IntegrityError), transaction.atomic():
            Attraction.objects.filter(pk=attraction.pk).update(tags=["heritage"])

    def test_a_soft_deleted_tag_is_no_longer_part_of_the_vocabulary(self) -> None:
        tag = make_tag(slug="coastal")
        tag.delete()
        with pytest.raises(IntegrityError):
            make_attraction(tags=["coastal"])

    def test_no_tags_is_the_default_and_is_allowed(self) -> None:
        assert make_attraction().tags == []

    def test_adding_a_word_to_the_vocabulary_is_a_row_not_a_release(self) -> None:
        """§4.2 and §24.7. "diving" is not in the seed list and needs no code."""
        make_tag(slug="diving", label="Diving", sort_order=60)
        attraction = make_attraction(tags=["diving"])
        assert attraction.tags == ["diving"]

    def test_the_chip_order_is_editorial_and_total(self) -> None:
        third = make_tag(slug="c", label="C", sort_order=30)
        first = make_tag(slug="a", label="A", sort_order=10)
        second = make_tag(slug="b", label="B", sort_order=10)
        assert list(Tag.objects.all()) == [first, second, third]


@pytest.mark.django_db
class TestNothingHereKnowsTheSeedMarket:
    def test_an_attraction_in_a_distant_destination_behaves_identically(self) -> None:
        """§4.2 and §41.12: the same code path, different data."""
        from apps.catalogue.tests.factories import OTHER_ZONE

        destination = make_destination(
            slug="valparaiso", name="Valparaiso", timezone=OTHER_ZONE, default_currency="CLP"
        )
        attraction = make_attraction(destination=destination, entrance_fee=None, fee_currency=None)
        assert attraction.timezone == OTHER_ZONE
