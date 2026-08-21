"""Catalogue writes — SRS §8.6, §27.8, §7.7, §41.12.

Three properties, and each one is here because its absence is silent.

**Nothing is written without `full_clean`.** §8.6 puts validation in the model
layer so the console, the admin API and the seed loader cannot disagree about
what a valid row is. `apps.catalogue.validators` exists purely so that call
reaches the domain functions, and a repository that skipped it would leave a
timezone validated on one path and not another — with the bad row only
surfacing when somebody renders an opening-hours table in it.

**Mass assignment is refused, not filtered.** The writable set is declared per
entity. An unknown or forbidden key raises rather than being dropped, because a
dropped key means an admin form that stopped saving a field still reports
success.

**Deletion is soft, and releases the slug.** §7.7. The partial unique indexes
are what make that true, and the test asserts both halves: the row survives, and
the slug can be reused.

`test_an_accommodation_cannot_be_given_a_price` is the ADR 0013 guard on the
write side. The read side cannot leak a rate because there is no column; the
write side cannot accept one because the writable set does not name it.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.core.exceptions import ValidationError

from apps.catalogue import repositories
from apps.catalogue.models import Accommodation, Attraction, Destination, PropertyType
from apps.catalogue.selectors import visible
from apps.catalogue.tests.factories import make_accommodation as build_accommodation
from apps.catalogue.tests.factories import make_destination, make_region

TODAY = dt.date(2027, 8, 12)

pytestmark = pytest.mark.django_db


def _destination_fields(region: object, **overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "region": region,
        "name": "Arusha",
        "slug": "arusha",
        "centroid": Point(36.68, -3.37, srid=4326),
        "timezone": "Africa/Dar_es_Salaam",
        "default_currency": "TZS",
    }
    fields.update(overrides)
    return fields


class TestValidationRunsOnEveryWrite:
    def test_a_destination_is_created_and_comes_back_as_a_dto(self) -> None:
        dto = repositories.create_destination(**_destination_fields(make_region()))
        assert dto.slug == "arusha"
        assert dto.timezone == "Africa/Dar_es_Salaam"
        assert dto.region.country.iso_code

    def test_an_unknown_timezone_is_refused_at_the_repository(self) -> None:
        """The §27.8 console path. `validators.validate_iana_timezone` wraps
        `domain.hierarchy`, and `full_clean` is what reaches it — the database
        trigger is the backstop, not the first line."""
        with pytest.raises(ValidationError) as exc:
            repositories.create_destination(
                **_destination_fields(make_region(), timezone="Mars/Olympus_Mons")
            )
        assert "timezone" in exc.value.message_dict

    def test_a_cross_field_rule_is_checked_on_a_partial_update(self) -> None:
        """`full_clean` runs over the whole row, not the changed fields.

        An entrance fee without a currency is the case: a PATCH that sets only
        the fee would pass a changed-fields-only validation and persist money
        with no currency, which §7.2 forbids outright.
        """
        destination = make_destination()
        attraction = Attraction.objects.create(
            destination=destination,
            name="Jozani Forest",
            slug="jozani-forest",
            coordinates=Point(39.41, -6.26, srid=4326),
        )
        with pytest.raises(ValidationError):
            repositories.update_attraction(attraction.public_id, entrance_fee=Decimal("10.00"))

    def test_a_valid_pair_updates(self) -> None:
        destination = make_destination()
        attraction = Attraction.objects.create(
            destination=destination,
            name="Jozani Forest",
            slug="jozani-forest",
            coordinates=Point(39.41, -6.26, srid=4326),
        )
        dto = repositories.update_attraction(
            attraction.public_id, entrance_fee=Decimal("10.00"), fee_currency="USD"
        )
        assert (dto.entrance_fee, dto.fee_currency) == (Decimal("10.00"), "USD")


class TestMassAssignmentIsRefused:
    @pytest.mark.parametrize("field", ["deleted_at", "public_id", "created_at", "rating_avg"])
    def test_a_forbidden_field_raises(self, field: str) -> None:
        with pytest.raises(repositories.UnwritableFieldError, match=field):
            repositories.create_destination(
                **_destination_fields(make_region(), **{field: "anything"})
            )

    def test_the_error_names_what_is_writable(self) -> None:
        """So an administrator or a serializer author can act on it, rather
        than reading the source to find out."""
        with pytest.raises(repositories.UnwritableFieldError, match="writable fields are"):
            repositories.create_destination(**_destination_fields(make_region(), nonsense=1))

    def test_an_unknown_key_is_not_silently_dropped(self) -> None:
        """The failure this prevents: a renamed column, a form that keeps
        posting the old name, and a save that reports success forever."""
        with pytest.raises(repositories.UnwritableFieldError):
            repositories.create_destination(**_destination_fields(make_region(), tz="UTC"))

    def test_an_accommodation_cannot_be_given_a_price(self) -> None:
        """ADR 0013, on the write side.

        There is no rate, no policy, no provider and no rating to set. The
        writable set is the enforcement, and it is short because the table is.
        """
        destination = make_destination()
        for field in ("base_rate", "provider_id", "cancellation_policy", "star_rating"):
            with pytest.raises(repositories.UnwritableFieldError, match=field):
                repositories.create_accommodation(
                    destination=destination,
                    name="Ocean Breeze",
                    slug="ocean-breeze",
                    property_type=PropertyType.HOTEL,
                    coordinates=Point(39.29, -5.72, srid=4326),
                    **{field: 1},
                )


class TestLifecycle:
    def test_deactivating_a_destination_hides_everything_beneath_it(self) -> None:
        """§4.1's Pemba switch, through the write path this time.

        One flag on one row, and its listings leave the public surface with it
        — because `selectors.visibility_q` walks the chain rather than each
        endpoint remembering to.
        """
        destination = make_destination()
        stay = build_accommodation(destination=destination)
        assert visible(Accommodation.objects.all(), today=TODAY).filter(pk=stay.pk).exists()

        repositories.set_active(Destination, destination.public_id, active=False)
        assert not visible(Accommodation.objects.all(), today=TODAY).filter(pk=stay.pk)

    def test_soft_deletion_keeps_the_row_and_releases_the_slug(self) -> None:
        """§7.7, both halves. The partial unique index is what makes the second
        half true, and reusing the slug is the case that proves it."""
        destination = make_destination()
        first = build_accommodation(destination=destination, slug="ocean-breeze")
        repositories.soft_delete(Accommodation, first.public_id)

        assert Accommodation.all_objects.filter(pk=first.pk).exists()
        assert not visible(Accommodation.objects.all(), today=TODAY).filter(pk=first.pk)
        second = build_accommodation(destination=destination, slug="ocean-breeze")
        assert second.pk != first.pk

    def test_restoring_a_row_whose_slug_was_reused_fails_rather_than_renaming(self) -> None:
        """Correct, not unfortunate: two live rows may not share a slug, and
        silently renaming one would break whichever URL was published."""
        from django.db import IntegrityError

        destination = make_destination()
        first = build_accommodation(destination=destination, slug="ocean-breeze")
        repositories.soft_delete(Accommodation, first.public_id)
        build_accommodation(destination=destination, slug="ocean-breeze")

        with pytest.raises(IntegrityError):
            repositories.restore(Accommodation, first.public_id)

    def test_a_new_destination_is_not_public_until_somebody_says_so(self) -> None:
        """§7.5.6 defaults `is_active` to false, and the repository does not
        override it. §41.12's Arusha test must pass because an administrator
        activated the market, not because creating it published it.
        """
        dto = repositories.create_destination(**_destination_fields(make_region()))
        assert not visible(Destination.objects.all(), today=TODAY).filter(public_id=dto.public_id)

        repositories.set_active(Destination, dto.public_id, active=True)
        assert visible(Destination.objects.all(), today=TODAY).filter(public_id=dto.public_id)


class TestAdministratorsSeeWhatThePublicCannot:
    def test_a_repository_read_reaches_a_soft_deleted_row(self) -> None:
        """The separation stated in the module docstring, asserted. Restoring a
        deleted row is impossible if the write path cannot load one.
        """
        destination = make_destination()
        stay = build_accommodation(destination=destination)
        repositories.soft_delete(Accommodation, stay.public_id)
        repositories.restore(Accommodation, stay.public_id)
        assert visible(Accommodation.objects.all(), today=TODAY).filter(pk=stay.pk).exists()
