"""Stays and cancellation policies — SRS §7.5.7, §14.2, §14.5, §14.6, §6.4.

The interesting assertions here are not about columns.

**`provider_id` is not a `ForeignKey`** (ADR 0012). §6.4 gives `catalogue` one
dependency and it is not `provider`; a `ForeignKey` would install a traversable
attribute and the boundary would be gone by attribute access rather than by any
import. The test asserts the absence, because the natural thing for a future
reader to do is "fix" it.

**Money never exists without its currency** (§7.2), and never as a float
(§18.5). Both are pinned on `room_type.base_rate`.

**A room that sleeps nobody cannot be sold.** `max_adults >= 1` is a CHECK
rather than a convention because `domain.occupancy.rooms_required` divides by
the occupancy, and a zero there is a zero division in a pricing path.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.db.models.deletion import ProtectedError

from apps.catalogue.domain.cancellation import parse_tiers, refund_percent_at
from apps.catalogue.models import Accommodation, CancellationPolicy, PropertyType, RoomType
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_cancellation_policy,
    make_destination,
    make_room_type,
)


def field(model: type[models.Model], name: str) -> models.Field:
    return model._meta.get_field(name)  # type: ignore[return-value]


def constraint_names(model: type[models.Model]) -> set[str]:
    return {c.name for c in model._meta.constraints}


class TestTheProviderReferenceIsSoft:
    """ADR 0012, and the §6.4 dependency column behind it."""

    def test_provider_id_is_a_plain_integer_column(self) -> None:
        provider = field(Accommodation, "provider_id")
        assert isinstance(provider, models.BigIntegerField)
        assert not isinstance(provider, models.ForeignKey)

    def test_there_is_no_traversable_provider_attribute(self) -> None:
        """The failure mode a `ForeignKey` would create: catalogue code reaching
        `accommodation.provider.payout_account` with no import to notice."""
        assert "provider" not in {f.name for f in Accommodation._meta.get_fields()}

    def test_the_column_is_indexed_because_it_is_filtered_on(self) -> None:
        assert field(Accommodation, "provider_id").db_index is True

    def test_it_is_nullable_until_the_provider_module_exists(self) -> None:
        assert field(Accommodation, "provider_id").null is True


class TestSchemaMatchesSection757:
    def test_the_tables_are_named_as_the_srs_names_them(self) -> None:
        assert Accommodation._meta.db_table == "accommodation"
        assert RoomType._meta.db_table == "room_type"
        assert CancellationPolicy._meta.db_table == "cancellation_policy"

    def test_every_section_757_accommodation_field_exists(self) -> None:
        expected = {
            "provider_id",
            "destination",
            "property_type",
            "coordinates",
            "address_line",
            "star_rating",
            "amenities",
            "check_in_time",
            "check_out_time",
            "cancellation_policy",
            "child_policy",
            "is_active",
            "deleted_at",
        }
        assert {f.name for f in Accommodation._meta.get_fields()} >= expected

    def test_every_section_757_room_type_field_exists(self) -> None:
        expected = {
            "accommodation",
            "name",
            "max_adults",
            "max_children",
            "bed_configuration",
            "size_sqm",
            "base_rate",
            "currency",
            "total_rooms",
            "amenities",
            "is_active",
        }
        assert {f.name for f in RoomType._meta.get_fields()} >= expected

    def test_the_rate_is_a_decimal_paired_with_a_currency(self) -> None:
        rate = field(RoomType, "base_rate")
        assert isinstance(rate, models.DecimalField)
        assert (rate.max_digits, rate.decimal_places) == (14, 2)
        assert field(RoomType, "currency") is not None

    def test_the_property_types_are_the_five_the_srs_names(self) -> None:
        assert {c.value for c in PropertyType} == {
            "HOTEL",
            "RESORT",
            "LODGE",
            "GUESTHOUSE",
            "APARTMENT",
        }

    def test_availability_is_not_this_modules_table(self) -> None:
        """ADR 0011: §6.4 gives `room_availability` to `inventory`. If it ever
        appears here, the DAG edge has been inverted."""
        tables = {model._meta.db_table for model in [Accommodation, RoomType]}
        assert "room_availability" not in tables
        assert not hasattr(RoomType, "availability")


@pytest.mark.django_db
class TestAccommodationConstraints:
    def test_a_property_saves_and_reads_back(self) -> None:
        stay = make_accommodation()
        stay.refresh_from_db()
        assert stay.property_type == PropertyType.LODGE
        assert stay.check_in_time == time(14, 0)

    def test_an_unrated_property_is_null_not_one_star(self) -> None:
        """§24.12 renders "new on the platform" rather than a misleading
        average, and it needs the two states to differ."""
        assert make_accommodation(star_rating=None).star_rating is None

    @pytest.mark.parametrize("stars", [0, 6, -1])
    def test_a_star_rating_outside_one_to_five_is_refused(self, stars: int) -> None:
        with pytest.raises(IntegrityError):
            make_accommodation(star_rating=stars)

    def test_the_rating_projection_starts_at_zero(self) -> None:
        """Q4: every listing is unrated in Phase 3, so this is launch-day
        behaviour rather than an edge case."""
        stay = make_accommodation()
        assert (stay.rating_avg, stay.rating_count) == (Decimal("0.00"), 0)

    def test_a_rating_above_the_scale_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_accommodation(rating_avg=Decimal("5.01"))

    def test_a_negative_rating_count_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_accommodation(rating_count=-1)

    def test_the_booking_cutoff_defaults_to_the_br103_value(self) -> None:
        assert make_accommodation().booking_cutoff_hours == 4

    def test_a_slug_is_released_by_soft_deletion(self) -> None:
        first = make_accommodation()
        first.delete()
        make_accommodation(destination=first.destination)

    def test_deleting_a_destination_with_properties_is_refused(self) -> None:
        stay = make_accommodation()
        with pytest.raises(ProtectedError):
            stay.destination.hard_delete()


@pytest.mark.django_db
class TestRoomTypeConstraints:
    def test_a_room_type_saves_and_reads_back(self) -> None:
        room = make_room_type()
        room.refresh_from_db()
        assert room.base_rate == Decimal("180.00")
        assert room.currency == "NZD"

    def test_a_room_that_sleeps_no_adults_is_refused(self) -> None:
        """`domain.occupancy.rooms_required` divides by the occupancy."""
        with pytest.raises(IntegrityError):
            make_room_type(max_adults=0)

    def test_a_room_that_takes_no_children_is_allowed(self) -> None:
        assert make_room_type(max_children=0).max_children == 0

    def test_a_negative_rate_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_room_type(base_rate=Decimal("-1.00"))

    def test_a_free_room_is_allowed(self) -> None:
        """Zero is a rate. A complimentary room in a package is not a
        constraint violation."""
        assert make_room_type(base_rate=Decimal("0.00")).base_rate == Decimal("0.00")

    def test_a_property_with_no_rooms_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_room_type(total_rooms=0)

    def test_a_zero_minimum_stay_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_room_type(min_nights=0)

    def test_an_unset_minimum_stay_is_allowed(self) -> None:
        assert make_room_type(min_nights=None).min_nights is None

    def test_two_rate_plans_may_share_everything_but_the_rate(self) -> None:
        """§14.2 models rate plans as separate room type rows in V1, so there
        is deliberately no uniqueness on name."""
        stay = make_accommodation()
        make_room_type(accommodation=stay, base_rate=Decimal("180.00"))
        make_room_type(accommodation=stay, base_rate=Decimal("210.00"))
        assert stay.room_types.count() == 2

    def test_deleting_a_property_with_room_types_is_refused(self) -> None:
        room = make_room_type()
        with pytest.raises(ProtectedError):
            room.accommodation.hard_delete()


@pytest.mark.django_db
class TestCancellationPolicies:
    def test_a_policy_round_trips_through_the_domain_reader(self) -> None:
        policy = make_cancellation_policy()
        policy.refresh_from_db()
        tiers = parse_tiers(policy.tiers)
        assert refund_percent_at(tiers, hours_before=Decimal(200)) == Decimal(100)
        assert refund_percent_at(tiers, hours_before=Decimal(100)) == Decimal(50)
        assert refund_percent_at(tiers, hours_before=Decimal(24)) == Decimal(0)

    def test_the_non_refundable_policy_is_simply_no_tiers(self) -> None:
        """§14.6's cleanest evidence that the generic form is the right one."""
        policy = make_cancellation_policy(code="NON_REFUNDABLE", name="Non-refundable", tiers=[])
        assert refund_percent_at(parse_tiers(policy.tiers), hours_before=Decimal(1000)) == 0

    def test_an_incoherent_policy_is_refused_on_the_console_path(self) -> None:
        """Refunds increasing as the date approaches is a data-entry error, and
        it is caught at the form rather than discovered during a refund."""
        policy = CancellationPolicy(
            code="BROKEN",
            name="Broken",
            tiers=[
                {"hours_before": 168, "refund_percent": 50},
                {"hours_before": 48, "refund_percent": 100},
            ],
        )
        with pytest.raises(ValidationError) as exc:
            policy.full_clean()
        assert "tiers" in exc.value.message_dict

    def test_an_object_where_an_array_belongs_is_refused_by_the_database(self) -> None:
        """The gross shape error a bad import makes. Nothing else about the
        JSON can be reached from a CHECK."""
        with pytest.raises(IntegrityError):
            CancellationPolicy.objects.create(code="X", name="X", tiers={"hours_before": 48})

    def test_a_code_is_unique_among_live_policies(self) -> None:
        make_cancellation_policy(
            code="FLEX_48H", tiers=[{"hours_before": 48, "refund_percent": 100}]
        )
        with pytest.raises(IntegrityError):
            make_cancellation_policy(code="FLEX_48H", tiers=[])

    def test_a_policy_in_use_cannot_be_hard_deleted(self) -> None:
        policy = make_cancellation_policy()
        make_accommodation(cancellation_policy=policy)
        with pytest.raises(ProtectedError):
            policy.hard_delete()

    def test_a_property_may_have_no_policy_yet(self) -> None:
        assert make_accommodation().cancellation_policy is None

    def test_policies_are_shared_across_markets(self) -> None:
        """§14.6 policies are referenced by properties and activities in any
        destination; nothing scopes them to one."""
        policy = make_cancellation_policy()
        first = make_accommodation(cancellation_policy=policy)
        elsewhere = make_destination(
            region=first.destination.region, slug="valparaiso", name="Valparaiso"
        )
        second = make_accommodation(
            destination=elsewhere, slug="hostal-cerro", cancellation_policy=policy
        )
        assert first.cancellation_policy_id == second.cancellation_policy_id
