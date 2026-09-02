"""The hold state machine — SRS §17.2, Appendix A.

Pure-domain tests: no database, no fixtures. What they mostly assert is that
the machine has no edges nobody asked for, because the expensive mistake in a
hold lifecycle is not a missing transition — that fails loudly the first time
it is needed — but a spare one, which returns capacity twice and is only
visible as an oversell weeks later.
"""

from __future__ import annotations

import pytest

from apps.common.state_machine import IllegalTransitionError
from apps.inventory.domain.lifecycle import (
    HOLD_MACHINE,
    LIVE_STATES,
    HoldState,
    is_live_state,
    returns_capacity,
)
from apps.inventory.models import HoldStatus, InventoryHold

TERMINAL = [HoldState.COMMITTED, HoldState.RELEASED, HoldState.EXPIRED]


class TestTheMachineMatchesSection172:
    def test_it_starts_held(self) -> None:
        assert HOLD_MACHINE.initial is HoldState.HELD

    def test_it_has_the_four_states_and_no_others(self) -> None:
        assert HOLD_MACHINE.states == frozenset(HoldState)

    @pytest.mark.parametrize("target", TERMINAL)
    def test_every_end_is_reachable_from_held(self, target: HoldState) -> None:
        assert HOLD_MACHINE.can(HoldState.HELD, target)

    @pytest.mark.parametrize("state", TERMINAL)
    def test_every_end_is_terminal(self, state: HoldState) -> None:
        assert HOLD_MACHINE.is_terminal(state)

    def test_held_is_the_only_state_with_anywhere_to_go(self) -> None:
        assert HOLD_MACHINE.allowed_targets(HoldState.HELD) == frozenset(TERMINAL)


class TestThereIsNoWayBack:
    """The spare-edge problem, stated as tests."""

    @pytest.mark.parametrize("source", TERMINAL)
    def test_a_finished_hold_cannot_be_re_armed(self, source: HoldState) -> None:
        """Re-arming would return capacity a CHECK constraint is the only
        remaining guard on — the race §17.1 I2 exists to prevent. Wanting the
        seats again means asking for them again, under lock, against whatever
        the counter says now."""
        with pytest.raises(IllegalTransitionError):
            HOLD_MACHINE.transition(source, HoldState.HELD)

    def test_a_released_hold_cannot_be_committed(self) -> None:
        """BR-026: "An expired hold may not be committed". The same is true of
        a released one, and this is the edge that would sell capacity that had
        already been given back."""
        with pytest.raises(IllegalTransitionError):
            HOLD_MACHINE.transition(HoldState.RELEASED, HoldState.COMMITTED)

    def test_an_expired_hold_cannot_be_committed(self) -> None:
        """BR-026 exactly."""
        with pytest.raises(IllegalTransitionError):
            HOLD_MACHINE.transition(HoldState.EXPIRED, HoldState.COMMITTED)

    def test_a_committed_hold_cannot_be_released(self) -> None:
        """A cancellation after capture moves the *booking* and writes a
        compensating counter change (§20.9); the hold recorded that capacity
        became sold, and that stays true of the moment it describes."""
        with pytest.raises(IllegalTransitionError):
            HOLD_MACHINE.transition(HoldState.COMMITTED, HoldState.RELEASED)


class TestLiveness:
    def test_only_held_counts_against_the_counter(self) -> None:
        assert frozenset({HoldState.HELD}) == LIVE_STATES

    def test_held_is_live(self) -> None:
        assert is_live_state(HoldState.HELD)

    @pytest.mark.parametrize("state", TERMINAL)
    def test_a_finished_hold_is_not(self, state: HoldState) -> None:
        assert not is_live_state(state)


class TestWhichEndsGiveCapacityBack:
    """The one line of arithmetic §17.5 and §20.8 both have to get right."""

    def test_releasing_returns_it(self) -> None:
        assert returns_capacity(HoldState.RELEASED)

    def test_expiring_returns_it(self) -> None:
        assert returns_capacity(HoldState.EXPIRED)

    def test_committing_does_not(self) -> None:
        """§17.2: the counters *move* from held to sold. The total spoken for
        is unchanged, and decrementing here would free a seat that was just
        paid for."""
        assert not returns_capacity(HoldState.COMMITTED)


class TestReleasedAndExpiredStayDistinct:
    def test_they_are_two_states_and_not_one(self) -> None:
        """The counter arithmetic is identical, which is the temptation. They
        answer different questions: RELEASED means something decided, EXPIRED
        means nobody did. §17.4's reconciliation is an investigation, and one
        that cannot tell an abandonment from a failure is missing the half of
        the data that says which system to look at."""
        assert HoldState.RELEASED is not HoldState.EXPIRED
        assert returns_capacity(HoldState.RELEASED) == returns_capacity(HoldState.EXPIRED)


class TestItAgreesWithTheColumn:
    def test_the_states_match_the_model_s_choices(self) -> None:
        """The domain may not import Django, so the enum is written twice. A
        state added to one and not the other is a row the machine cannot
        move."""
        assert {s.value for s in HoldState} == set(HoldStatus.values)

    def test_the_default_status_is_the_machine_s_initial_state(self) -> None:
        """A row that arrived in a state the machine does not start in would
        be unmovable from birth."""
        field = InventoryHold._meta.get_field("status")
        assert field.default == HOLD_MACHINE.initial.value
