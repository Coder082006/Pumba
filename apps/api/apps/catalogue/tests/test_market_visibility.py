"""The market tier — SRS §4.2 (v1.5), §41.12. ADR 0018.

**These tests are written before the model exists.** They are the specification
of what commit 3 must build, not a description of what it built, and the whole
value of writing them first is that the regression they guard against produces
no error and no odd-looking output.

The regression: `market` is a new ancestor, and an ancestor that is *not*
wired into the visibility chain hides nothing. Get it wrong and an announced
market's destinations become reachable by direct URL and appear in the sitemap,
while the market tile on the landing page correctly reads "not open yet". Every
individual piece looks right. Only the combination is wrong, and nothing raises.

That is the same failure `domain/visibility.py` was written to prevent one
level down, and the same failure this session has now found six times in other
forms: a mechanism that exists, is tested in isolation, and is connected to
nothing.

## The two predicates

The market tier introduces exactly one new rule, and it is a *narrowing* of an
existing one rather than a second implementation of it:

* **`is_open`** — active, not deleted, `launch_date` reached. This is
  `is_publicly_visible` applied to a market node, with `market` inserted into
  the ancestor chain. It governs whether the catalogue beneath the market is
  browsable. No new logic.
* **`is_listed`** — active, not deleted, **ignoring `launch_date`**. Genuinely
  new, applies to `market` alone, and the destination selector is the only
  caller permitted to use it.

`TestTheTwoPredicatesDifferExactlyOnce` is the centre of this file. The two
functions must agree on every combination except one — active, undeleted,
not yet launched — and that single disagreement is the entire feature.

## Why these fail rather than error

`Market` does not exist yet, so the imports are deferred and the module-level
`xfail` is conditioned on their absence. Until commit 3 the suite runs green
with these reported as expected failures; the moment `Market` lands the
condition goes false, the marker stops applying, and these become ordinary
tests that must pass. Nobody has to remember to remove anything.

`strict=True` so that a test which starts passing while the marker still
applies is a failure rather than a quiet XPASS.
"""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

import pytest

TODAY = dt.date(2027, 8, 12)
YESTERDAY = TODAY - dt.timedelta(days=1)
TOMORROW = TODAY + dt.timedelta(days=1)


def _market_tier_exists() -> bool:
    """Is the tier this file specifies built yet?

    Narrow on purpose: only the three names commit 3 introduces. A broader
    guard would go on suppressing these tests after an unrelated import in
    this module started failing, which is how a specification file becomes
    decoration.
    """
    try:
        from apps.catalogue.domain.visibility import is_listed  # noqa: F401
        from apps.catalogue.models import Market  # noqa: F401
        from apps.catalogue.tests.factories import make_market  # noqa: F401
    except ImportError:
        return False
    return True


MARKET_TIER_EXISTS = _market_tier_exists()

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.xfail(
        not MARKET_TIER_EXISTS,
        strict=True,
        reason="ADR 0018: the market tier is specified here and built in the next commit",
    ),
]


# ---------------------------------------------------------------------------
# The pure rule
# ---------------------------------------------------------------------------


class TestTheTwoPredicatesDifferExactlyOnce:
    """`is_listed` and `is_open` over the whole truth table.

    If these ever agree everywhere, the selector shows nothing before launch
    and the feature is gone. If they disagree anywhere else, an unlaunched
    market's catalogue is reachable and the guard is gone. Both failures are
    silent, so the table is enumerated rather than sampled.
    """

    @pytest.mark.parametrize(
        ("is_active", "deleted", "launch_date"),
        list(itertools.product([True, False], [True, False], [None, YESTERDAY, TOMORROW])),
    )
    def test_the_only_disagreement_is_an_announced_market(
        self, is_active: bool, deleted: bool, launch_date: dt.date | None
    ) -> None:
        from apps.catalogue.domain.visibility import is_listed, is_publicly_visible

        deleted_at = dt.datetime(2027, 1, 1, tzinfo=dt.UTC) if deleted else None
        listed = is_listed(is_active=is_active, deleted_at=deleted_at)
        open_ = is_publicly_visible(
            is_active=is_active,
            deleted_at=deleted_at,
            launch_date=launch_date,
            today=TODAY,
        )

        announced = is_active and not deleted and launch_date == TOMORROW
        assert (listed != open_) is announced

    def test_is_listed_ignores_the_launch_date_entirely(self) -> None:
        """It takes no `launch_date` argument, so it cannot read one.

        Stated as a signature test rather than a behaviour test: a version
        that accepted the argument and ignored it would be one refactor away
        from consulting it.
        """
        import inspect

        from apps.catalogue.domain.visibility import is_listed

        assert "launch_date" not in inspect.signature(is_listed).parameters
        assert "today" not in inspect.signature(is_listed).parameters

    def test_a_deactivated_market_is_not_listed(self) -> None:
        """Pemba. §4.1 seeds it `is_active = false`, and it must not appear in
        the selector at all — not as a tile reading "coming soon"."""
        from apps.catalogue.domain.visibility import is_listed

        assert is_listed(is_active=False, deleted_at=None) is False

    def test_a_deleted_market_is_not_listed(self) -> None:
        from apps.catalogue.domain.visibility import is_listed

        assert is_listed(is_active=True, deleted_at=dt.datetime(2027, 1, 1, tzinfo=dt.UTC)) is False


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------


def _subtree(market: Any) -> dict[str, Any]:
    """One row of every public kind, hanging off `market`."""
    from apps.catalogue.tests.factories import (
        make_accommodation,
        make_activity,
        make_attraction,
        make_destination,
        make_region,
    )

    region = make_region(country=market.country, market=market)
    destination = make_destination(region=region)
    return {
        "destination": destination,
        "attraction": make_attraction(destination=destination),
        "activity": make_activity(destination=destination),
        "accommodation": make_accommodation(destination=destination),
    }


class TestAnAnnouncedMarketHidesEverythingBeneathIt:
    """The headline regression, and the reason this file exists.

    The market is listed. Its tile is on the landing page. Nothing under it is
    public. Every assertion here would pass trivially if `market` were simply
    absent from the chain *and* the market were inactive — which is why the
    market in these tests is deliberately **active**, with only its launch date
    in the future. That is the state no other guard catches.
    """

    @staticmethod
    def _announced() -> dict[str, Any]:
        """Built inside the test body, not in a fixture.

        `xfail` covers what the test call raises; whether it also covers a
        fixture blowing up during setup is a pytest implementation detail this
        file should not be resting on, and until commit 3 lands every one of
        these imports raises.
        """
        from apps.catalogue.tests.factories import make_market

        return _subtree(make_market(is_active=True, launch_date=TOMORROW))

    @pytest.mark.parametrize("kind", ["destination", "attraction", "activity", "accommodation"])
    def test_nothing_under_it_is_publicly_visible(self, kind: str) -> None:
        from apps.catalogue.selectors import visible

        row = self._announced()[kind]
        manager = type(row).all_objects
        assert not visible(manager.all(), today=TODAY).filter(pk=row.pk)

    @pytest.mark.parametrize("kind", ["destination", "attraction", "activity", "accommodation"])
    def test_all_of_it_appears_the_day_the_market_opens(self, kind: str) -> None:
        """The other half of the promise. Hiding a subtree for ever is easy;
        §4.1 requires it to appear with nobody deploying anything."""
        from apps.catalogue.selectors import visible

        row = self._announced()[kind]
        manager = type(row).all_objects
        assert visible(manager.all(), today=TOMORROW).filter(pk=row.pk)

    def test_the_market_itself_is_listed_while_its_catalogue_is_not(self) -> None:
        """Both halves in one assertion, because separately they each look
        like a bug."""
        from apps.catalogue.models import Market
        from apps.catalogue.selectors import listed_markets, visible
        from apps.catalogue.tests.factories import make_market

        market = make_market(is_active=True, launch_date=TOMORROW)
        assert listed_markets(today=TODAY).filter(pk=market.pk).exists()
        assert not visible(Market.all_objects.all(), today=TODAY).filter(pk=market.pk)


class TestADeactivatedMarketHidesEverythingBeneathIt:
    """Pemba, one tier up. §4.1 defers it; §4.2 forbids `if region in
    ("Unguja","Pemba")`, so deferring it has to be data."""

    @pytest.mark.parametrize("kind", ["destination", "attraction", "activity", "accommodation"])
    def test_nothing_under_it_is_publicly_visible(self, kind: str) -> None:
        from apps.catalogue.selectors import visible
        from apps.catalogue.tests.factories import make_market

        deferred = _subtree(make_market(slug="pemba", name="Pemba", is_active=False))
        row = deferred[kind]
        manager = type(row).all_objects
        assert not visible(manager.all(), today=TODAY).filter(pk=row.pk)

    def test_it_is_not_in_the_selector_either(self) -> None:
        from apps.catalogue.selectors import listed_markets
        from apps.catalogue.tests.factories import make_market

        market = make_market(slug="pemba", name="Pemba", is_active=False)
        assert not listed_markets(today=TODAY).filter(pk=market.pk).exists()


class TestTheFilterAndTheDomainAgreeAtTheMarketLevel:
    """The same truth-table pin `test_selectors_visibility.py` applies to every
    other level, extended to the new one.

    Two implementations of one rule is a liability unless something holds them
    together; a new level is exactly when they drift.
    """

    @pytest.mark.parametrize(
        ("is_active", "deleted", "launch_date"),
        list(itertools.product([True, False], [True, False], [None, YESTERDAY, TOMORROW])),
    )
    def test_an_attractions_visibility_matches_the_pure_chain(
        self, is_active: bool, deleted: bool, launch_date: dt.date | None
    ) -> None:
        from apps.catalogue.domain.visibility import VisibilityNode, visible_chain
        from apps.catalogue.models import Attraction
        from apps.catalogue.selectors import visible
        from apps.catalogue.tests.factories import make_market

        market = make_market(is_active=is_active, launch_date=launch_date)
        if deleted:
            market.delete()
        market.refresh_from_db()
        attraction = _subtree(market)["attraction"]

        expected = visible_chain(
            VisibilityNode(is_active=True),
            VisibilityNode(is_active=True, launch_date=None),
            VisibilityNode(is_active=True),
            VisibilityNode(
                is_active=market.is_active,
                deleted_at=market.deleted_at,
                launch_date=market.launch_date,
            ),
            VisibilityNode(is_active=True),
            today=TODAY,
        )
        found = visible(Attraction.all_objects.all(), today=TODAY).filter(pk=attraction.pk)
        assert found.exists() is expected

    def test_market_has_a_declared_chain_of_its_own(self) -> None:
        """`visibility_q` raises for an undeclared model, and the market's own
        page is a public surface, so it must be declared rather than filtered
        ad hoc at the call site."""
        from apps.catalogue.models import Market
        from apps.catalogue.selectors import visibility_q

        visibility_q(Market, today=TODAY)  # must not raise

    def test_a_market_opens_on_its_launch_date_not_the_day_after(self) -> None:
        from apps.catalogue.models import Market
        from apps.catalogue.selectors import visible
        from apps.catalogue.tests.factories import make_market

        market = make_market(is_active=True, launch_date=TODAY)
        assert visible(Market.all_objects.all(), today=TODAY).filter(pk=market.pk)
        assert not visible(Market.all_objects.all(), today=YESTERDAY).filter(pk=market.pk)

    def test_a_deactivated_country_still_hides_its_markets(self) -> None:
        """The tier above did not stop existing. A market's chain is
        `market → country`, not `market` alone."""
        from apps.catalogue.models import Market
        from apps.catalogue.selectors import visible
        from apps.catalogue.tests.factories import make_country, make_market

        country = make_country(is_active=False)
        market = make_market(country=country, is_active=True)
        assert not visible(Market.all_objects.all(), today=TODAY).filter(pk=market.pk)


class TestTheSelectorIsTheOnlyThingUsingTheLooserRule:
    """`is_listed` is a hole in the visibility guarantee, deliberately opened
    for one screen. It stays that size only if something says so."""

    def test_listed_markets_is_not_built_on_visible(self) -> None:
        """Guards against the tempting simplification: implementing
        `listed_markets` as `visible(Market...)` would make the selector
        correct and empty before launch, which reads as a broken landing page
        rather than as a bug."""
        from apps.catalogue.selectors import listed_markets
        from apps.catalogue.tests.factories import make_market

        make_market(slug="unopened", is_active=True, launch_date=TOMORROW)
        assert listed_markets(today=TODAY).count() == 1

    def test_a_deleted_market_is_absent_from_the_selector(self) -> None:
        """§7.7. The looser predicate is looser about `launch_date` only."""
        from apps.catalogue.selectors import listed_markets
        from apps.catalogue.tests.factories import make_market

        market = make_market(is_active=True)
        market.delete()
        assert not listed_markets(today=TODAY).filter(pk=market.pk).exists()
