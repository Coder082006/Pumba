"""The booking state machine — SRS §20.2, §41.8.

§37.7's acceptance criterion: "every transition in Section 20.2 has a passing
positive and negative test". §41.8: "every transition succeeds when legal and is
rejected with 409 when not". This file is that matrix, and it is written as
*the whole set*: every declared edge once with its guard satisfied and once with
it refused, every actor §20.2 does not name refused, and every state's
reachable set asserted by equality so an edge somebody adds fails here.

Pure-domain: no database. Each row's context is the smallest dictionary that
satisfies its guard, so the negative case is that dictionary with one fact
removed — which is what proves the fact is load-bearing.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from apps.booking.domain.lifecycle import (
    ACTORS,
    BOOKING_MACHINE,
    TOURIST_CANCELLABLE,
    Actor,
    BookingState,
    apply,
    force,
    is_tourist_cancellable,
)
from apps.booking.models import Booking, BookingStatus
from apps.common.state_machine import GuardFailedError, IllegalTransitionError

B = BookingState
A = Actor
NOW = datetime(2027, 3, 1, 9, 0, tzinfo=UTC)

#: §20.2, row by row: (source, target, an actor it names, a context satisfying
#: its guard, that context with the load-bearing fact removed).
Row = tuple[BookingState, BookingState, Actor, Mapping[str, object], Mapping[str, object]]

ROWS: list[Row] = [
    (
        B.DRAFT,
        B.PENDING,
        A.TOURIST,
        {"quote_valid": True, "hold_live": True},
        {"quote_valid": True},
    ),
    (
        B.PENDING,
        B.CONFIRMED,
        A.SYSTEM,
        {"payment_captured": True, "hold_committed": True, "confirmation_mode": "INSTANT"},
        {"payment_captured": True, "hold_committed": True, "confirmation_mode": "ON_REQUEST"},
    ),
    (
        B.PENDING,
        B.AWAITING_PROVIDER,
        A.SYSTEM,
        {"payment_captured": True, "confirmation_mode": "ON_REQUEST"},
        {"payment_captured": False, "confirmation_mode": "ON_REQUEST"},
    ),
    (B.PENDING, B.FAILED, A.SYSTEM, {"hold_expired": True}, {}),
    (
        B.AWAITING_PROVIDER,
        B.CONFIRMED,
        A.PROVIDER,
        {"now": NOW, "response_due_at": NOW},
        {"now": NOW + timedelta(seconds=1), "response_due_at": NOW},
    ),
    (B.AWAITING_PROVIDER, B.CANCELLED, A.PROVIDER, {"provider_declined": True}, {}),
    (B.CONFIRMED, B.IN_PROGRESS, A.SYSTEM, {"service_start_reached": True}, {}),
    (B.IN_PROGRESS, B.COMPLETED, A.DRIVER, {"driver_completed": True}, {}),
    (
        B.CONFIRMED,
        B.NO_SHOW,
        A.DRIVER,
        {"wait_elapsed": True, "evidence_recorded": True},
        {"wait_elapsed": True},
    ),
    (
        B.IN_PROGRESS,
        B.NO_SHOW,
        A.PROVIDER,
        {"wait_elapsed": True, "evidence_recorded": True},
        {"evidence_recorded": True},
    ),
    (B.PENDING, B.CANCELLED, A.TOURIST, {"policy_evaluated": True}, {}),
    (B.CONFIRMED, B.CANCELLED, A.ADMIN, {"policy_evaluated": True}, {}),
    (B.CANCELLED, B.REFUNDED, A.SYSTEM, {"refund_settled": True}, {}),
]


def _id(row: tuple[object, ...]) -> str:
    return f"{row[0]}->{row[1]}"


class TestTheDeclaredSet:
    def test_it_starts_in_draft(self) -> None:
        assert BOOKING_MACHINE.initial is B.DRAFT

    def test_it_has_the_ten_states_and_no_others(self) -> None:
        assert BOOKING_MACHINE.states == frozenset(BookingState)

    def test_the_terminal_states_are_the_four_ends(self) -> None:
        """CANCELLED is not among them: it still moves to REFUNDED."""
        ends = {B.COMPLETED, B.REFUNDED, B.NO_SHOW, B.FAILED}
        assert BOOKING_MACHINE.terminal == frozenset(ends)

    def test_every_row_of_the_matrix_is_an_edge_and_every_edge_a_row(self) -> None:
        """The matrix below cannot silently skip an edge."""
        declared = {(t.source, t.target) for t in BOOKING_MACHINE.transitions}
        assert {(row[0], row[1]) for row in ROWS} == declared
        assert set(ACTORS) == declared

    @pytest.mark.parametrize(
        ("source", "targets"),
        [
            (B.DRAFT, {B.PENDING}),
            (B.PENDING, {B.CONFIRMED, B.AWAITING_PROVIDER, B.FAILED, B.CANCELLED}),
            (B.AWAITING_PROVIDER, {B.CONFIRMED, B.CANCELLED}),
            (B.CONFIRMED, {B.IN_PROGRESS, B.NO_SHOW, B.CANCELLED}),
            (B.IN_PROGRESS, {B.COMPLETED, B.NO_SHOW}),
            (B.CANCELLED, {B.REFUNDED}),
            (B.COMPLETED, set()),
            (B.REFUNDED, set()),
            (B.NO_SHOW, set()),
            (B.FAILED, set()),
        ],
    )
    def test_each_state_goes_exactly_where_section_20_2_draws(
        self, source: BookingState, targets: set[BookingState]
    ) -> None:
        assert BOOKING_MACHINE.allowed_targets(source) == frozenset(targets)


class TestEveryTransitionSucceedsWhenLegal:
    @pytest.mark.parametrize("row", ROWS, ids=_id)
    def test_positive(self, row: Row) -> None:
        source, target, actor, satisfied, _ = row
        assert apply(source, target, actor=actor, context=satisfied) is target


class TestEveryTransitionIsRefusedWhenItsGuardIsNot:
    @pytest.mark.parametrize("row", ROWS, ids=_id)
    def test_negative(self, row: Row) -> None:
        source, target, actor, _, unsatisfied = row
        with pytest.raises(GuardFailedError) as refused:
            apply(source, target, actor=actor, context=unsatisfied)
        assert refused.value.status_code == 409


class TestTheActorColumnIsEnforced:
    @pytest.mark.parametrize(
        ("source", "target", "actor"),
        [
            (source, target, actor)
            for (source, target), allowed in ACTORS.items()
            for actor in Actor
            if actor not in allowed
        ],
        ids=lambda value: str(value),
    )
    def test_an_actor_the_row_does_not_name_is_refused(
        self, source: BookingState, target: BookingState, actor: Actor
    ) -> None:
        """Even with every guard satisfied: for this actor the edge does not exist."""
        everything: dict[str, object] = {
            key: value for row in ROWS for key, value in row[3].items()
        }
        with pytest.raises(IllegalTransitionError):
            apply(source, target, actor=actor, context=everything)

    def test_a_tourist_cannot_confirm_their_own_booking(self) -> None:
        """The edge a malicious client would try first."""
        with pytest.raises(IllegalTransitionError):
            apply(
                B.PENDING,
                B.CONFIRMED,
                actor=A.TOURIST,
                context={
                    "payment_captured": True,
                    "hold_committed": True,
                    "confirmation_mode": "INSTANT",
                },
            )

    def test_the_merged_row_admits_both_rows_actors(self) -> None:
        """AWAITING_PROVIDER → CANCELLED is §20.2's two rows as one edge."""
        assert ACTORS[(B.AWAITING_PROVIDER, B.CANCELLED)] == {
            A.PROVIDER,
            A.SYSTEM,
            A.TOURIST,
            A.ADMIN,
        }


class TestUndeclaredEdgesAreRefused:
    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (source, target)
            for source in BookingState
            for target in BookingState
            if target not in BOOKING_MACHINE.allowed_targets(source)
        ],
        ids=lambda value: str(value),
    )
    def test_every_undeclared_pair_is_illegal_for_everyone(
        self, source: BookingState, target: BookingState
    ) -> None:
        for actor in Actor:
            with pytest.raises(IllegalTransitionError) as refused:
                apply(source, target, actor=actor, context={})
            assert refused.value.code == "ILLEGAL_TRANSITION"

    @pytest.mark.parametrize(
        "pair",
        [
            (B.AWAITING_PROVIDER, B.IN_PROGRESS),
            (B.AWAITING_PROVIDER, B.NO_SHOW),
            (B.COMPLETED, B.REFUNDED),
        ],
        ids=_id,
    )
    def test_the_closures_adr_0025_names(self, pair: tuple[BookingState, BookingState]) -> None:
        assert pair[1] not in BOOKING_MACHINE.allowed_targets(pair[0])


class TestForcing:
    """BR-038: SUPER_ADMIN bypasses guards, never the table."""

    def test_a_declared_edge_is_forced_despite_its_guard(self) -> None:
        with pytest.raises(GuardFailedError):
            apply(B.PENDING, B.CONFIRMED, actor=A.SYSTEM, context={})
        assert force(B.PENDING, B.CONFIRMED) is B.CONFIRMED

    def test_an_undeclared_edge_cannot_be_forced(self) -> None:
        with pytest.raises(IllegalTransitionError):
            force(B.COMPLETED, B.CANCELLED)


class TestTouristCancellation:
    """BR-042: "A tourist may cancel any booking not yet IN_PROGRESS"."""

    def test_the_cancellable_states_are_derived_from_the_actor_column(self) -> None:
        assert {B.PENDING, B.AWAITING_PROVIDER, B.CONFIRMED} == TOURIST_CANCELLABLE

    @pytest.mark.parametrize("state", sorted(TOURIST_CANCELLABLE))
    def test_before_it_starts_they_may(self, state: BookingState) -> None:
        assert is_tourist_cancellable(state)

    @pytest.mark.parametrize("state", [B.IN_PROGRESS, B.COMPLETED, B.CANCELLED, B.FAILED, B.DRAFT])
    def test_after_it_starts_or_ends_they_may_not(self, state: BookingState) -> None:
        assert not is_tourist_cancellable(state)


class TestItAgreesWithTheColumn:
    def test_the_states_match_the_model_s_choices(self) -> None:
        assert {s.value for s in BookingState} == set(BookingStatus.values)

    def test_the_column_default_is_a_state_the_machine_can_move_on_from(self) -> None:
        """§7.5.12 defaults `status` to PENDING, not the machine's DRAFT, because
        no v1 path writes DRAFT: basket creation makes a PENDING line directly.
        The default must still be a state with somewhere to go."""
        default = Booking._meta.get_field("status").default
        assert default == BookingStatus.PENDING
        assert BOOKING_MACHINE.allowed_targets(BookingState(default))
