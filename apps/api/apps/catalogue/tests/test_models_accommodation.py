"""Accommodation as a location record, and cancellation policies.

SRS §7.5.7, §14.5, §14.6 and §6.4, as amended in v1.2 — ADR 0013.

An `accommodation` row is where a property is and when its day starts and ends.
It is not a product: there is no provider, no rate, no capacity, no policy and
no `room_type`. Most of what this file asserts is therefore *absence*, and it is
written that way deliberately — a column that quietly comes back is how a
location record turns into unattributed supply, and nothing else in the suite
would notice.

**Coordinates are the load-bearing column.** §12.4 prices the transfer from
them, and the reason to curate a property at all is that its coordinate is
exact where a geocoded free-entry address is approximate.

**`cancellation_policy` is not deferred.** §14.6 has it referenced by properties
*and activities*, activities are still sold, so the table, its tier parsing and
its administration under §27.12 are untouched v1 code. Only the accommodation
reference to it went: a location record has nothing to cancel.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models
from django.db.models.deletion import ProtectedError

from apps.catalogue.domain.cancellation import parse_tiers, refund_percent_at
from apps.catalogue.models import Accommodation, CancellationPolicy, PropertyType
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_cancellation_policy,
    make_destination,
)


def field(model: type[models.Model], name: str) -> models.Field:
    return model._meta.get_field(name)  # type: ignore[return-value]


def constraint_names(model: type[models.Model]) -> set[str]:
    return {c.name for c in model._meta.constraints}


class TestItIsALocationRecordAndNotAProduct:
    """ADR 0013, asserted as absence because absence is what erodes."""

    #: Every column that made a claim only the property's owner could make.
    SOLD_BY_SOMEBODY = (
        "provider_id",
        "star_rating",
        "amenities",
        "cancellation_policy",
        "child_policy",
        "booking_cutoff_hours",
        "rating_avg",
        "rating_count",
    )

    @pytest.mark.parametrize("name", SOLD_BY_SOMEBODY)
    def test_the_columns_of_a_sellable_property_are_gone(self, name: str) -> None:
        assert name not in {f.name for f in Accommodation._meta.get_fields()}

    def test_room_type_is_not_a_model_any_more(self) -> None:
        with pytest.raises(LookupError):
            django_apps.get_model("catalogue", "RoomType")

    def test_a_property_has_no_room_types_accessor(self) -> None:
        """The reverse relation a `ForeignKey` installed. Its absence is what
        stops §24.11 quietly growing a price again."""
        assert not hasattr(Accommodation, "room_types")

    @pytest.mark.django_db
    def test_the_room_type_table_is_gone(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('room_type')")
            assert cursor.fetchone()[0] is None

    def test_nothing_here_reaches_inventory(self) -> None:
        """ADR 0011 and ADR 0012 together: no import, no relation, no reverse
        accessor, so catalogue cannot read a capacity counter at all."""
        reachable = {
            f.related_model._meta.app_label
            for f in Accommodation._meta.get_fields()
            if f.is_relation and getattr(f, "related_model", None) is not None
        }
        assert "inventory" not in reachable


class TestSchemaMatchesSection757:
    def test_the_tables_are_named_as_the_srs_names_them(self) -> None:
        assert Accommodation._meta.db_table == "accommodation"
        assert CancellationPolicy._meta.db_table == "cancellation_policy"

    def test_every_section_757_field_the_amendment_keeps_exists(self) -> None:
        expected = {
            "name",
            "slug",
            "destination",
            "property_type",
            "coordinates",
            "address_line",
            "check_in_time",
            "check_out_time",
            "is_active",
            "deleted_at",
        }
        assert {f.name for f in Accommodation._meta.get_fields()} >= expected

    def test_the_coordinate_is_a_geography_point(self) -> None:
        """§13.1 forbids planar approximations, so the column is `geography`
        and distances come back in geodesic metres."""
        coordinates = field(Accommodation, "coordinates")
        assert coordinates.geography is True
        assert coordinates.srid == 4326

    def test_the_property_types_are_the_five_the_srs_names(self) -> None:
        assert {c.value for c in PropertyType} == {
            "HOTEL",
            "RESORT",
            "LODGE",
            "GUESTHOUSE",
            "APARTMENT",
        }


@pytest.mark.django_db
class TestAccommodationConstraints:
    def test_a_property_saves_and_reads_back(self) -> None:
        stay = make_accommodation()
        stay.refresh_from_db()
        assert stay.property_type == PropertyType.LODGE
        assert stay.check_in_time == time(14, 0)

    def test_check_in_and_check_out_may_be_unpublished(self) -> None:
        """§14.5 as amended: a free-entry anchor and a curated property that
        has not published its times both fall back to the Appendix B
        destination defaults, so null is a state and not a gap."""
        stay = make_accommodation(check_in_time=None, check_out_time=None)
        assert (stay.check_in_time, stay.check_out_time) == (None, None)

    def test_a_slug_is_released_by_soft_deletion(self) -> None:
        first = make_accommodation()
        first.delete()
        make_accommodation(destination=first.destination)

    def test_two_live_properties_may_not_share_a_slug(self) -> None:
        first = make_accommodation()
        with pytest.raises(IntegrityError):
            make_accommodation(destination=first.destination)

    def test_a_zero_feature_rank_is_refused(self) -> None:
        """§16.5 ranks on it, and rank 0 is the value a bad import writes."""
        with pytest.raises(IntegrityError):
            make_accommodation(feature_rank=0)

    def test_deleting_a_destination_with_properties_is_refused(self) -> None:
        stay = make_accommodation()
        with pytest.raises(ProtectedError):
            stay.destination.hard_delete()

    def test_a_seeded_property_is_active_by_default(self) -> None:
        """Unlike `destination`, which defaults inactive per §7.5.6. A
        location record asserts nothing that needs review before it is usable,
        which is what makes Appendix C's forty seeded properties correct."""
        assert make_accommodation().is_active is True


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
        make_activity(cancellation_policy=policy)
        with pytest.raises(ProtectedError):
            policy.hard_delete()

    def test_a_listing_may_have_no_policy_yet(self) -> None:
        assert make_activity().cancellation_policy is None

    def test_a_location_record_has_no_policy_at_all(self) -> None:
        """ADR 0013. §14.6 kept the table because activities reference it; a
        property that cannot be booked has nothing to cancel, so the reference
        went rather than being left null forever."""
        assert not hasattr(make_accommodation(), "cancellation_policy")

    def test_policies_are_shared_across_markets(self) -> None:
        """§14.6 policies are referenced across destinations; nothing scopes
        them to one."""
        policy = make_cancellation_policy()
        first = make_activity(cancellation_policy=policy)
        elsewhere = make_destination(
            region=first.destination.region, slug="valparaiso", name="Valparaiso"
        )
        second = make_activity(destination=elsewhere, slug="cerro-walk", cancellation_policy=policy)
        assert first.cancellation_policy_id == second.cancellation_policy_id
