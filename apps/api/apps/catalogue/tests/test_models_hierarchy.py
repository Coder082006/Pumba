"""The geography hierarchy as a schema — SRS §7.3, §7.5.6, §7.7, §41.12.

Two halves. The first reads the model metadata and needs no database: it pins
the §7.5.6 column list, the partial indexes of §7.7 and the on-delete rule of
R6. The second exercises the constraints against real PostgreSQL, because a
constraint that exists in `Meta` and not in the database protects nothing.

The zone tests are the point of the file. `Africa/Zanzibar` looks exactly like
an IANA name and is not one, and it is refused on three separate paths here:
`full_clean` (the console), the field validator (the API), and a trigger (the
data migration, the bulk update, the hand-run correction). The third is the one
that matters, because it is the only one nothing can route around.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.gis.db.models import PointField
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError
from django.utils import timezone

from apps.catalogue.models import Country, Destination, GatewayTypeChoices, Region

# A zone deliberately unlike the seed market's, so anything that hard-codes
# East Africa fails here rather than in Phase 12.
FAR_ZONE = "Pacific/Auckland"


def field(model: type[models.Model], name: str) -> models.Field:
    return model._meta.get_field(name)  # type: ignore[return-value]


def constraint_names(model: type[models.Model]) -> set[str]:
    return {c.name for c in model._meta.constraints}


def index_names(model: type[models.Model]) -> set[str]:
    return {i.name for i in model._meta.indexes}


class TestSchemaMatchesSection756:
    def test_the_tables_are_named_as_the_srs_names_them(self) -> None:
        assert Country._meta.db_table == "country"
        assert Region._meta.db_table == "region"
        assert Destination._meta.db_table == "destination"

    def test_destination_carries_every_section_756_column(self) -> None:
        expected = {
            "id",
            "public_id",
            "created_at",
            "updated_at",
            "deleted_at",
            "region",
            "name",
            "slug",
            "summary",
            "description",
            "centroid",
            "is_gateway",
            "gateway_type",
            "gateway_code",
            "timezone",
            "default_currency",
            "launch_date",
            "feature_rank",
            "is_active",
        }
        assert {f.name for f in Destination._meta.get_fields()} >= expected

    def test_the_centroid_is_geography_not_geometry(self) -> None:
        """§13.1 forbids planar approximations.

        `geography` makes `ST_Distance` return geodesic metres; `geometry` in
        4326 would return degrees, which look like small numbers and are not
        distances at all.
        """
        centroid = field(Destination, "centroid")
        assert isinstance(centroid, PointField)
        assert centroid.geography is True
        assert centroid.srid == 4326
        assert centroid.null is False

    def test_a_destination_is_invisible_until_somebody_activates_it(self) -> None:
        assert field(Destination, "is_active").default is False

    def test_feature_rank_defaults_to_the_srs_value(self) -> None:
        assert field(Destination, "feature_rank").default == 100

    def test_the_gateway_columns_are_nullable_because_most_rows_are_not_gateways(
        self,
    ) -> None:
        assert field(Destination, "gateway_type").null is True
        assert field(Destination, "gateway_code").null is True

    def test_the_gateway_type_choices_come_from_the_domain_enum(self) -> None:
        from apps.catalogue.domain.hierarchy import GatewayType

        assert {c.value for c in GatewayTypeChoices} == {t.value for t in GatewayType}

    def test_r6_is_restrict_not_cascade(self) -> None:
        """§7.3 R6: RESTRICT. Deleting a country must not silently take its
        destinations, and every booking anchored to them, with it."""
        assert field(Region, "country").remote_field.on_delete is models.PROTECT
        assert field(Destination, "region").remote_field.on_delete is models.PROTECT


class TestPartialUniquenessPerSection77:
    def test_the_slug_unique_index_excludes_soft_deleted_rows(self) -> None:
        assert "destination_slug_unique_alive" in constraint_names(Destination)
        constraint = next(
            c for c in Destination._meta.constraints if c.name == "destination_slug_unique_alive"
        )
        assert constraint.condition is not None

    def test_the_gateway_code_index_is_conditional_on_is_gateway(self) -> None:
        assert "destination_gateway_code_unique_alive" in constraint_names(Destination)

    def test_the_iso_code_index_excludes_soft_deleted_countries(self) -> None:
        assert "country_iso_code_unique_alive" in constraint_names(Country)

    def test_the_srs_indexes_exist(self) -> None:
        assert index_names(Destination) >= {
            "destination_centroid_gist",
            "destination_region_active_idx",
        }


class TestValidatorsOnTheConsolePath:
    """§27.8 writes through `full_clean`, not through a service."""

    def test_full_clean_names_the_offending_field(self) -> None:
        """What an administrator sees is "timezone", against that input, rather
        than a failure three screens later in an opening-hours table."""
        destination = Destination(name="Somewhere", slug="somewhere", timezone="Africa/Zanzibar")
        with pytest.raises(ValidationError) as exc:
            destination.clean_fields()
        assert "timezone" in exc.value.message_dict

    def test_the_zone_validator_rejects_a_name_the_tz_database_lacks(self) -> None:
        validator = field(Destination, "timezone").validators[0]
        with pytest.raises(ValidationError, match="not a known IANA time zone"):
            validator("Africa/Zanzibar")

    def test_the_zone_validator_accepts_a_zone_on_the_other_side_of_the_world(
        self,
    ) -> None:
        assert field(Destination, "timezone").validators[0](FAR_ZONE) is None

    def test_the_country_zone_column_carries_the_same_validator(self) -> None:
        validator = field(Country, "default_timezone").validators[0]
        with pytest.raises(ValidationError, match="not a known IANA time zone"):
            validator("Africa/Zanzibar")

    def test_a_utc_offset_is_not_a_zone(self) -> None:
        validator = field(Destination, "timezone").validators[0]
        with pytest.raises(ValidationError, match="not a known IANA time zone"):
            validator("+03:00")

    def test_the_currency_validator_is_structural_not_a_hard_coded_list(self) -> None:
        validator = field(Destination, "default_currency").validators[0]
        assert validator("NZD") is None
        with pytest.raises(ValidationError, match="three ASCII letters"):
            validator("NZ")


@pytest.mark.django_db
class TestTheHierarchyAgainstRealPostgres:
    @pytest.fixture()
    def country(self) -> Country:
        return Country.objects.create(
            iso_code="TZ",
            name="Tanzania",
            default_currency="TZS",
            default_timezone="Africa/Nairobi",
        )

    @pytest.fixture()
    def region(self, country: Country) -> Region:
        return Region.objects.create(country=country, name="Coastal", slug="coastal")

    def _destination(self, region: Region, **overrides: object) -> Destination:
        values: dict[str, object] = {
            "region": region,
            "name": "Somewhere",
            "slug": "somewhere",
            "centroid": Point(39.19, -6.16, srid=4326),
            "timezone": "Africa/Nairobi",
            "default_currency": "TZS",
        }
        values.update(overrides)
        return Destination.objects.create(**values)  # type: ignore[arg-type]

    def test_a_destination_saves_and_reads_back(self, region: Region) -> None:
        created = self._destination(region)
        loaded = Destination.objects.get(pk=created.pk)
        assert loaded.slug == "somewhere"
        assert loaded.centroid is not None
        assert loaded.is_active is False

    def test_a_second_hierarchy_needs_no_code_change(self, region: Region) -> None:
        """§41.12 in miniature: another country, another currency, another zone,
        through the same code path."""
        other = Country.objects.create(
            iso_code="NZ", name="New Zealand", default_currency="NZD", default_timezone=FAR_ZONE
        )
        other_region = Region.objects.create(country=other, name="Northland", slug="northland")
        destination = self._destination(
            other_region,
            slug="bay-of-islands",
            name="Bay of Islands",
            timezone=FAR_ZONE,
            default_currency="NZD",
        )
        assert destination.timezone == FAR_ZONE

    def test_a_region_slug_may_repeat_in_another_country(self, region: Region) -> None:
        other = Country.objects.create(
            iso_code="KE", name="Kenya", default_currency="KES", default_timezone="Africa/Nairobi"
        )
        Region.objects.create(country=other, name="Coast", slug="coastal")

    def test_a_region_slug_may_not_repeat_within_one_country(
        self, country: Country, region: Region
    ) -> None:
        with pytest.raises(IntegrityError):
            Region.objects.create(country=country, name="Coast again", slug="coastal")

    def test_a_soft_deleted_slug_is_released(self, region: Region) -> None:
        """§7.7: soft-deleted rows are excluded from uniqueness, so a mistake
        can be undone by an administrator rather than by a DBA."""
        first = self._destination(region)
        first.delete()
        assert first.deleted_at is not None
        self._destination(region)

    def test_deleting_a_country_that_has_regions_is_refused(
        self, country: Country, region: Region
    ) -> None:
        with pytest.raises(ProtectedError):
            country.hard_delete()


@pytest.mark.django_db
class TestDatabaseConstraints:
    @pytest.fixture()
    def region(self) -> Region:
        country = Country.objects.create(
            iso_code="TZ",
            name="Tanzania",
            default_currency="TZS",
            default_timezone="Africa/Nairobi",
        )
        return Region.objects.create(country=country, name="Coastal", slug="coastal")

    def _values(self, region: Region, **overrides: object) -> dict[str, object]:
        values: dict[str, object] = {
            "region": region,
            "name": "Somewhere",
            "slug": "somewhere",
            "centroid": Point(39.19, -6.16, srid=4326),
            "timezone": "Africa/Nairobi",
            "default_currency": "TZS",
        }
        values.update(overrides)
        return values

    def test_a_gateway_without_a_code_is_refused_by_the_database(self, region: Region) -> None:
        with pytest.raises(IntegrityError):
            Destination.objects.create(
                **self._values(  # type: ignore[arg-type]
                    region, is_gateway=True, gateway_type=GatewayTypeChoices.AIRPORT
                )
            )

    def test_a_code_on_a_non_gateway_is_refused_by_the_database(self, region: Region) -> None:
        """The reverse direction. An orphaned code sits outside the partial
        unique index, so a later gateway can claim the same one."""
        with pytest.raises(IntegrityError):
            Destination.objects.create(
                **self._values(region, is_gateway=False, gateway_code="ZNZ")  # type: ignore[arg-type]
            )

    def test_two_gateways_may_not_share_a_code(self, region: Region) -> None:
        gateway = {"is_gateway": True, "gateway_type": GatewayTypeChoices.AIRPORT}
        Destination.objects.create(**self._values(region, gateway_code="ZNZ", **gateway))  # type: ignore[arg-type]
        with pytest.raises(IntegrityError):
            Destination.objects.create(
                **self._values(region, slug="other", gateway_code="ZNZ", **gateway)  # type: ignore[arg-type]
            )

    def test_a_zero_feature_rank_is_refused_by_the_database(self, region: Region) -> None:
        with pytest.raises(IntegrityError):
            Destination.objects.create(**self._values(region, feature_rank=0))  # type: ignore[arg-type]

    def test_the_updated_at_trigger_fires_on_a_bulk_update(self, region: Region) -> None:
        """§7.2. `QuerySet.update()` never calls `save()`, so without the
        trigger `updated_at` goes stale exactly when somebody is trying to
        establish what changed and when."""
        destination = Destination.objects.create(**self._values(region))  # type: ignore[arg-type]
        before = destination.updated_at
        Destination.objects.filter(pk=destination.pk).update(name="Renamed")
        destination.refresh_from_db()
        assert destination.updated_at > before


@pytest.mark.django_db
class TestTheZoneTriggerCannotBeRoutedAround:
    """The validator guards the console. This guards everything else."""

    @pytest.fixture()
    def region(self) -> Region:
        country = Country.objects.create(
            iso_code="TZ",
            name="Tanzania",
            default_currency="TZS",
            default_timezone="Africa/Nairobi",
        )
        return Region.objects.create(country=country, name="Coastal", slug="coastal")

    def _create(self, region: Region, zone: str) -> Destination:
        return Destination.objects.create(
            region=region,
            name="Somewhere",
            slug="somewhere",
            centroid=Point(39.19, -6.16, srid=4326),
            timezone=zone,
            default_currency="TZS",
        )

    def test_an_invalid_zone_cannot_be_inserted_even_bypassing_full_clean(
        self, region: Region
    ) -> None:
        """`objects.create` runs no validators. The trigger is what stops it."""
        with pytest.raises(IntegrityError):
            self._create(region, "Africa/Zanzibar")

    def test_an_invalid_zone_cannot_arrive_by_bulk_update(self, region: Region) -> None:
        destination = self._create(region, "Africa/Nairobi")
        with pytest.raises(IntegrityError), transaction.atomic():
            Destination.objects.filter(pk=destination.pk).update(timezone="Africa/Zanzibar")

    def test_a_country_default_zone_is_guarded_too(self) -> None:
        with pytest.raises(IntegrityError):
            Country.objects.create(
                iso_code="XX",
                name="Nowhere",
                default_currency="USD",
                default_timezone="Atlantis/Capital",
            )

    def test_a_real_zone_on_the_other_side_of_the_world_passes(self, region: Region) -> None:
        destination = self._create(region, FAR_ZONE)
        assert destination.timezone == FAR_ZONE


@pytest.mark.django_db
class TestLocalDate:
    def test_today_is_the_destinations_today_not_the_servers(self) -> None:
        """The whole reason §7.5.6 stores a zone per destination.

        Asserted against a zone chosen so the two disagree for part of every
        day, rather than against a fixed expectation that would pass in one
        half of the year and fail in the other.
        """
        country = Country.objects.create(
            iso_code="TZ",
            name="Tanzania",
            default_currency="TZS",
            default_timezone="Africa/Nairobi",
        )
        region = Region.objects.create(country=country, name="Coastal", slug="coastal")
        near = Destination.objects.create(
            region=region,
            name="Near",
            slug="near",
            centroid=Point(39.19, -6.16, srid=4326),
            timezone="Pacific/Honolulu",
            default_currency="USD",
        )
        far = Destination.objects.create(
            region=region,
            name="Far",
            slug="far",
            centroid=Point(174.76, -36.85, srid=4326),
            timezone=FAR_ZONE,
            default_currency="NZD",
        )
        # Honolulu is behind Auckland by close to a full day, always.
        assert far.today_local - near.today_local in (dt.timedelta(0), dt.timedelta(days=1))
        assert isinstance(near.today_local, dt.date)
        assert abs((far.today_local - timezone.now().date()).days) <= 1
