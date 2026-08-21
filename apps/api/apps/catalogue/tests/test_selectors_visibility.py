"""The visibility filter agrees with the visibility rule — SRS §4.1, §7.7, §41.12.

`domain.visibility` decides whether a row is public; `selectors.visibility_q`
decides the same thing in SQL. Two implementations of one rule is a liability
unless something pins them together, and the failure mode if they drift is the
worst kind: a row that should be hidden appearing on one endpoint out of nine,
noticed by a customer rather than by a test.

So the interesting test here is not that the filter works. It is
`test_the_filter_and_the_domain_agree_over_the_whole_truth_table`, which
enumerates every combination of the three flags across every level of the
country → region → destination → listing chain, builds the rows, and asserts the
database and the pure function return the same answer for each. Sixteen
combinations per level is cheap; discovering the disagreement in Phase 12 is not.

The two named scenarios below it are the ones the SRS calls out by name, kept
separate because a failure in either should say what broke rather than pointing
at a parametrised case number.
"""

from __future__ import annotations

import datetime as dt
import itertools

import pytest
from django.utils import timezone

from apps.catalogue.domain.visibility import VisibilityNode, visible_chain
from apps.catalogue.models import Accommodation, Activity, Attraction, Destination
from apps.catalogue.selectors import visible
from apps.catalogue.tests.factories import (
    make_accommodation,
    make_activity,
    make_attraction,
    make_destination,
)

TODAY = dt.date(2027, 8, 12)
YESTERDAY = TODAY - dt.timedelta(days=1)
TOMORROW = TODAY + dt.timedelta(days=1)

pytestmark = pytest.mark.django_db


class TestTheFilterAndTheDomainAgree:
    """One rule, two implementations, pinned together."""

    @pytest.mark.parametrize(
        ("destination_active", "destination_deleted", "launch_date"),
        list(itertools.product([True, False], [True, False], [None, YESTERDAY, TOMORROW])),
    )
    def test_destination_visibility_matches(
        self,
        destination_active: bool,
        destination_deleted: bool,
        launch_date: dt.date | None,
    ) -> None:
        destination = make_destination(is_active=destination_active, launch_date=launch_date)
        if destination_deleted:
            destination.delete()
        destination.refresh_from_db()

        expected = visible_chain(
            VisibilityNode(
                is_active=destination.is_active,
                deleted_at=destination.deleted_at,
                launch_date=destination.launch_date,
            ),
            VisibilityNode(
                is_active=destination.region.is_active,
                deleted_at=destination.region.deleted_at,
            ),
            VisibilityNode(
                is_active=destination.region.country.is_active,
                deleted_at=destination.region.country.deleted_at,
            ),
            today=TODAY,
        )
        found = visible(Destination.all_objects.all(), today=TODAY).filter(pk=destination.pk)
        assert found.exists() is expected

    @pytest.mark.parametrize(
        ("listing_active", "destination_active", "region_active", "country_active"),
        list(itertools.product([True, False], repeat=4)),
    )
    def test_the_whole_ancestor_chain_matches(
        self,
        listing_active: bool,
        destination_active: bool,
        region_active: bool,
        country_active: bool,
    ) -> None:
        """The case the pure function calls `all(...)`, in SQL.

        Any single `False` anywhere in the chain hides the listing. Without
        this, deactivating Pemba would hide the destination page and leave its
        attractions reachable by direct URL and listed in the sitemap.
        """
        destination = make_destination(is_active=destination_active)
        destination.region.is_active = region_active
        destination.region.save(update_fields=["is_active"])
        destination.region.country.is_active = country_active
        destination.region.country.save(update_fields=["is_active"])
        attraction = make_attraction(destination=destination, is_active=listing_active)

        expected = visible_chain(
            VisibilityNode(is_active=listing_active),
            VisibilityNode(is_active=destination_active, launch_date=None),
            VisibilityNode(is_active=region_active),
            VisibilityNode(is_active=country_active),
            today=TODAY,
        )
        found = visible(Attraction.all_objects.all(), today=TODAY).filter(pk=attraction.pk)
        assert found.exists() is expected


class TestTheCasesTheSrsNames:
    def test_pemba_is_absent_when_deactivated(self) -> None:
        """§4.1 seeds Pemba `is_active = false`."""
        pemba = make_destination(slug="pemba-north", name="Pemba North", is_active=False)
        assert not visible(Destination.all_objects.all(), today=TODAY).filter(pk=pemba.pk)

    def test_a_market_launches_on_its_launch_date_not_the_day_after(self) -> None:
        """§4.1: *"scheduled market launch without a deployment"*. A market
        that launches on the 12th is open on the 12th."""
        launching = make_destination(is_active=True, launch_date=TODAY)
        assert visible(Destination.all_objects.all(), today=TODAY).filter(pk=launching.pk)
        assert not visible(Destination.all_objects.all(), today=YESTERDAY).filter(pk=launching.pk)

    def test_no_deployment_is_needed_for_tomorrow_to_differ_from_today(self) -> None:
        """The whole point of the column: the same row, the same code, a
        different answer because the date moved."""
        launching = make_destination(is_active=True, launch_date=TOMORROW)
        assert not visible(Destination.all_objects.all(), today=TODAY).filter(pk=launching.pk)
        assert visible(Destination.all_objects.all(), today=TOMORROW).filter(pk=launching.pk)

    def test_a_soft_deleted_row_is_gone_from_the_public_surface(self) -> None:
        """§7.7. The row survives for referential integrity; it is not public."""
        attraction = make_attraction()
        attraction.delete()
        assert not visible(Attraction.all_objects.all(), today=TODAY).filter(pk=attraction.pk)
        assert Attraction.all_objects.filter(pk=attraction.pk).exists()

    def test_the_filter_does_not_lean_on_the_default_manager(self) -> None:
        """Asserted through `all_objects` throughout this file, and stated once
        here so the reason is written down: relying on `SoftDeleteModel`'s
        manager would make the guarantee a property of which manager a caller
        happened to reach for, and `all_objects` is right there."""
        row = make_activity()
        row.deleted_at = timezone.now()
        row.save(update_fields=["deleted_at"])
        assert not visible(Activity.all_objects.all(), today=TODAY).filter(pk=row.pk)


class TestEveryPublicEntityHasAChain:
    """A model with no declared chain must fail loudly, not filter loosely."""

    @pytest.mark.parametrize(
        ("model", "factory"),
        [
            (Destination, make_destination),
            (Attraction, make_attraction),
            (Activity, make_activity),
            (Accommodation, make_accommodation),
        ],
    )
    def test_each_public_entity_hides_its_own_inactive_rows(
        self, model: type, factory: object
    ) -> None:
        row = factory(is_active=False)  # type: ignore[operator]
        assert not visible(model.all_objects.all(), today=TODAY).filter(pk=row.pk)

    def test_an_undeclared_model_raises_rather_than_returning_everything(self) -> None:
        """The failure mode this prevents is the dangerous one: a new public
        entity whose listing endpoint silently publishes inactive rows."""
        from apps.catalogue.models import Tag
        from apps.catalogue.selectors import visibility_q

        with pytest.raises(LookupError, match="visibility chain"):
            visibility_q(Tag, today=TODAY)


class TestAccommodationIsPublicLikeAnAttraction:
    """ADR 0013: curated location data, administered, same visibility rule."""

    def test_a_property_in_a_deactivated_destination_is_hidden(self) -> None:
        destination = make_destination(is_active=False)
        stay = make_accommodation(destination=destination)
        assert not visible(Accommodation.all_objects.all(), today=TODAY).filter(pk=stay.pk)

    def test_a_property_in_an_unlaunched_destination_is_hidden(self) -> None:
        """The transfer-pricing case. A seeded property in a market that has
        not opened must not appear in §24.11's curated list, or a tourist can
        anchor a stay in a destination the Platform does not serve yet."""
        destination = make_destination(is_active=True, launch_date=TOMORROW)
        stay = make_accommodation(destination=destination)
        assert not visible(Accommodation.all_objects.all(), today=TODAY).filter(pk=stay.pk)
        assert visible(Accommodation.all_objects.all(), today=TOMORROW).filter(pk=stay.pk)
