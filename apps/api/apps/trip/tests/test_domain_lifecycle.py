"""The trip state machine — SRS §20.5.

A transition table is data, and data is only as good as the assertions against
it. These tests are written as *the whole set*, not as samples: every test that
checks a permitted edge is paired with the observation that the set of
permitted edges from that state is exactly what §20.5 draws. A sampled test
passes happily while an extra edge nobody meant to add sits beside it, and an
extra edge in this machine is a trip that can be moved somewhere the money and
inventory behind it have not been moved to.
"""

from __future__ import annotations

import pytest

from apps.common.state_machine import IllegalTransitionError
from apps.trip.domain.lifecycle import (
    EDITABLE_STATES,
    TRIP_MACHINE,
    TripState,
    is_editable,
)

S = TripState


class TestTheDeclaredSet:
    def test_the_states_are_exactly_the_seven(self) -> None:
        """§7.5.10, §20.5 and Appendix A all name the same seven. An eighth
        would be a specification change, not an implementation detail."""
        assert TRIP_MACHINE.states == {
            S.DRAFT,
            S.PRICED,
            S.PENDING_PAYMENT,
            S.CONFIRMED,
            S.IN_PROGRESS,
            S.COMPLETED,
            S.CANCELLED,
        }

    def test_it_starts_in_draft(self) -> None:
        """§10.2: `POST /trips` sets `trip.status = DRAFT`."""
        assert TRIP_MACHINE.initial is S.DRAFT

    def test_the_terminal_states_are_completed_and_cancelled(self) -> None:
        assert TRIP_MACHINE.terminal == {S.COMPLETED, S.CANCELLED}

    @pytest.mark.parametrize(
        ("source", "targets"),
        [
            (S.DRAFT, {S.PRICED, S.CANCELLED}),
            (S.PRICED, {S.PENDING_PAYMENT, S.DRAFT, S.CANCELLED}),
            (S.PENDING_PAYMENT, {S.CONFIRMED, S.DRAFT, S.CANCELLED}),
            (S.CONFIRMED, {S.IN_PROGRESS, S.CANCELLED}),
            (S.IN_PROGRESS, {S.COMPLETED, S.CANCELLED}),
            (S.COMPLETED, set()),
            (S.CANCELLED, set()),
        ],
    )
    def test_each_state_reaches_exactly_these(
        self, source: TripState, targets: set[TripState]
    ) -> None:
        """The table as a whole, read back one row at a time.

        Stated as equality rather than membership: the failure worth catching
        is an edge somebody added, not one somebody removed — a removed edge
        breaks a feature loudly, an added one breaks an invariant quietly.
        """
        assert TRIP_MACHINE.allowed_targets(source) == targets


class TestTheHappyPath:
    def test_draft_to_completed(self) -> None:
        """§20.5's spine, walked end to end."""
        state = TRIP_MACHINE.initial
        for target in (
            S.PRICED,
            S.PENDING_PAYMENT,
            S.CONFIRMED,
            S.IN_PROGRESS,
            S.COMPLETED,
        ):
            state = TRIP_MACHINE.transition(state, target)
        assert TRIP_MACHINE.is_terminal(state)


class TestTheWaysBackToDraft:
    def test_a_quote_may_expire(self) -> None:
        """The TTL of `quote.ttl_minutes` elapsed: the prices and the holds
        behind them are no longer honoured, and the trip is editable again."""
        assert TRIP_MACHINE.transition(S.PRICED, S.DRAFT) is S.DRAFT

    def test_a_payment_may_fail(self) -> None:
        assert TRIP_MACHINE.transition(S.PENDING_PAYMENT, S.DRAFT) is S.DRAFT

    def test_a_confirmed_trip_does_not_go_back(self) -> None:
        """Money has moved. The path back is §21's refund, not this table."""
        with pytest.raises(IllegalTransitionError):
            TRIP_MACHINE.transition(S.CONFIRMED, S.DRAFT)


class TestCancellation:
    @pytest.mark.parametrize(
        "source", [S.DRAFT, S.PRICED, S.PENDING_PAYMENT, S.CONFIRMED, S.IN_PROGRESS]
    )
    def test_any_live_state_may_be_cancelled(self, source: TripState) -> None:
        """§20.5: "Any state -> CANCELLED"."""
        assert TRIP_MACHINE.transition(source, S.CANCELLED) is S.CANCELLED

    def test_a_completed_trip_may_not_be_cancelled(self) -> None:
        """The bound on "any state", and it is the right one: a journey that
        has happened cannot be made not to have happened. The money path for a
        completed trip is a refund, not a state change."""
        with pytest.raises(IllegalTransitionError):
            TRIP_MACHINE.transition(S.COMPLETED, S.CANCELLED)

    def test_cancelling_twice_is_not_a_transition(self) -> None:
        with pytest.raises(IllegalTransitionError):
            TRIP_MACHINE.transition(S.CANCELLED, S.CANCELLED)


class TestSkippingAhead:
    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (S.DRAFT, S.CONFIRMED),
            (S.DRAFT, S.PENDING_PAYMENT),
            (S.PRICED, S.CONFIRMED),
            (S.PENDING_PAYMENT, S.IN_PROGRESS),
            (S.CONFIRMED, S.COMPLETED),
        ],
    )
    def test_a_stage_may_not_be_skipped(self, source: TripState, target: TripState) -> None:
        """Each of these skips a step that has a side effect behind it —
        pricing, capture, or a booking actually starting. The one that would
        hurt most is DRAFT to CONFIRMED, which is a confirmed trip nobody
        paid for."""
        with pytest.raises(IllegalTransitionError):
            TRIP_MACHINE.transition(source, target)


class TestEditability:
    def test_only_a_draft_may_be_edited(self) -> None:
        assert set(EDITABLE_STATES) == {S.DRAFT}
        assert is_editable(S.DRAFT)

    @pytest.mark.parametrize(
        "state",
        [S.PRICED, S.PENDING_PAYMENT, S.CONFIRMED, S.IN_PROGRESS, S.COMPLETED, S.CANCELLED],
    )
    def test_everything_else_is_closed_to_item_mutations(self, state: TripState) -> None:
        """PRICED is included deliberately. Its prices are held against
        inventory with a TTL; editing items underneath that quote would leave
        the holds describing a trip that no longer exists."""
        assert not is_editable(state)


class TestItAgreesWithTheModel:
    def test_the_state_names_match_trip_status(self) -> None:
        """The one piece of drift this arrangement makes possible.

        `TripState` cannot import Django and `TripStatus` cannot leave it, so
        the two enums are written twice. A value added to one and not the
        other is a row whose status the machine cannot move — a failure that
        would otherwise surface as a 409 in production rather than here.
        """
        from apps.trip.models import TripStatus

        assert {s.value for s in TripState} == {s.value for s in TripStatus}
