"""Public visibility — SRS §4.1, §7.7, §41.12.

Pemba is the cheapest test in the suite and it is here: a destination seeded
`is_active = false` that must not appear anywhere public. The rest of this file
exists because three flags and a parent chain is exactly the shape of rule that
looks obviously correct and is wrong in one of its sixteen combinations.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime

import pytest

from apps.catalogue.domain.visibility import (
    VisibilityNode,
    hidden_reason,
    is_publicly_visible,
    visible_chain,
)

TODAY = date(2027, 8, 12)
DELETED = datetime(2027, 1, 1, tzinfo=UTC)


def visible(**overrides: object) -> bool:
    kwargs: dict[str, object] = {
        "is_active": True,
        "deleted_at": None,
        "launch_date": None,
        "today": TODAY,
    }
    kwargs.update(overrides)
    return is_publicly_visible(**kwargs)  # type: ignore[arg-type]


class TestTheRowItself:
    def test_an_active_undeleted_unlaunched_row_is_visible(self) -> None:
        assert visible() is True

    def test_pemba_is_not_visible(self) -> None:
        # SRS §4.1: "Pemba (Chake Chake) | Deferred; record created but
        # is_active = false". The whole flag exists for this row.
        assert visible(is_active=False) is False

    def test_a_deleted_row_is_not_visible(self) -> None:
        assert visible(deleted_at=DELETED) is False

    def test_a_deleted_row_is_not_visible_even_while_active(self) -> None:
        # Deletion outranks activation. The other order would resurrect a row
        # by toggling a flag.
        assert visible(is_active=True, deleted_at=DELETED) is False

    def test_a_future_launch_date_is_not_yet_visible(self) -> None:
        assert visible(launch_date=date(2027, 8, 13)) is False

    def test_a_launch_date_of_today_is_visible(self) -> None:
        # A market that launches on the 12th is open on the 12th.
        assert visible(launch_date=TODAY) is True

    def test_a_past_launch_date_is_visible(self) -> None:
        assert visible(launch_date=date(2020, 1, 1)) is True

    def test_a_future_launch_date_does_not_override_inactivity(self) -> None:
        assert visible(is_active=False, launch_date=date(2020, 1, 1)) is False

    @pytest.mark.parametrize(
        ("is_active", "deleted", "launched"),
        list(itertools.product([True, False], repeat=3)),
    )
    def test_the_whole_truth_table(self, is_active: bool, deleted: bool, launched: bool) -> None:
        """Visible iff active AND not deleted AND launched. All eight rows."""
        got = visible(
            is_active=is_active,
            deleted_at=DELETED if deleted else None,
            launch_date=date(2020, 1, 1) if launched else date(2099, 1, 1),
        )
        assert got is (is_active and not deleted and launched)


class TestTheParentChain:
    def test_an_empty_chain_is_not_visible(self) -> None:
        # A listing with no destination is a bug, and True would publish it.
        assert visible_chain(today=TODAY) is False

    def test_a_chain_of_visible_nodes_is_visible(self) -> None:
        assert visible_chain(*(VisibilityNode(True),) * 4, today=TODAY) is True

    def test_an_inactive_parent_hides_an_active_child(self) -> None:
        # Deactivating Pemba must take its attractions with it. Without this,
        # they stay reachable by direct URL and stay in the sitemap.
        chain = (VisibilityNode(is_active=False), VisibilityNode(is_active=True))
        assert visible_chain(*chain, today=TODAY) is False

    def test_an_unlaunched_destination_hides_its_listings(self) -> None:
        chain = (
            VisibilityNode(is_active=True, launch_date=date(2099, 1, 1)),
            VisibilityNode(is_active=True),
        )
        assert visible_chain(*chain, today=TODAY) is False

    def test_a_deleted_grandparent_hides_the_whole_branch(self) -> None:
        chain = (
            VisibilityNode(is_active=True, deleted_at=DELETED),
            VisibilityNode(is_active=True),
            VisibilityNode(is_active=True),
        )
        assert visible_chain(*chain, today=TODAY) is False

    def test_a_hidden_child_is_hidden_under_a_visible_parent(self) -> None:
        chain = (VisibilityNode(is_active=True), VisibilityNode(is_active=False))
        assert visible_chain(*chain, today=TODAY) is False


class TestHiddenReason:
    def test_a_visible_row_has_no_reason(self) -> None:
        assert hidden_reason(VisibilityNode(True), today=TODAY) is None

    def test_an_empty_chain_reports_the_missing_parent(self) -> None:
        assert hidden_reason(today=TODAY) == "NO_PARENT"

    def test_it_reports_the_outermost_failure_first(self) -> None:
        # "The region is inactive" is the actionable answer. Reporting the
        # attraction would send an administrator to re-toggle a flag that was
        # never the problem.
        chain = (VisibilityNode(is_active=False), VisibilityNode(is_active=False))
        assert hidden_reason(*chain, today=TODAY) == "INACTIVE@0"

    def test_it_names_the_depth_of_the_failing_node(self) -> None:
        chain = (VisibilityNode(True), VisibilityNode(True), VisibilityNode(is_active=False))
        assert hidden_reason(*chain, today=TODAY) == "INACTIVE@2"

    def test_it_distinguishes_the_three_reasons(self) -> None:
        assert hidden_reason(VisibilityNode(True, deleted_at=DELETED), today=TODAY) == "DELETED@0"
        assert hidden_reason(VisibilityNode(is_active=False), today=TODAY) == "INACTIVE@0"
        assert (
            hidden_reason(VisibilityNode(True, launch_date=date(2099, 1, 1)), today=TODAY)
            == "NOT_YET_LAUNCHED@0"
        )

    def test_deletion_outranks_inactivity_in_the_reason_too(self) -> None:
        node = VisibilityNode(is_active=False, deleted_at=DELETED)
        assert hidden_reason(node, today=TODAY) == "DELETED@0"


class TestAgreementBetweenTheTwoEntryPoints:
    @pytest.mark.parametrize(
        ("is_active", "deleted", "launched"),
        list(itertools.product([True, False], repeat=3)),
    )
    def test_a_single_node_chain_agrees_with_the_bare_predicate(
        self, is_active: bool, deleted: bool, launched: bool
    ) -> None:
        deleted_at = DELETED if deleted else None
        launch_date = date(2020, 1, 1) if launched else date(2099, 1, 1)
        node = VisibilityNode(is_active, deleted_at, launch_date)
        assert visible_chain(node, today=TODAY) is is_publicly_visible(
            is_active=is_active,
            deleted_at=deleted_at,
            launch_date=launch_date,
            today=TODAY,
        )

    @pytest.mark.parametrize(
        ("is_active", "deleted", "launched"),
        list(itertools.product([True, False], repeat=3)),
    )
    def test_hidden_reason_is_none_exactly_when_visible(
        self, is_active: bool, deleted: bool, launched: bool
    ) -> None:
        node = VisibilityNode(
            is_active,
            DELETED if deleted else None,
            date(2020, 1, 1) if launched else date(2099, 1, 1),
        )
        assert (hidden_reason(node, today=TODAY) is None) is visible_chain(node, today=TODAY)
