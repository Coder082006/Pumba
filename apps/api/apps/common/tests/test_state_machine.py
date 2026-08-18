"""State machine core tests — SRS §36.2, principle A4.

The machines themselves live in their owning modules; these tests pin the
mechanism every one of them will rely on.
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from apps.common.state_machine import (
    GuardFailedError,
    IllegalTransitionError,
    StateMachine,
    Transition,
)


class Status(StrEnum):
    DRAFT = "DRAFT"
    PRICED = "PRICED"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    CANCELLED = "CANCELLED"


def _trip_like() -> StateMachine[Status]:
    """A cut-down shape of the Trip machine in SRS §20.5."""
    return StateMachine(
        name="trip",
        initial=Status.DRAFT,
        terminal=frozenset({Status.CANCELLED}),
        transitions=[
            Transition(Status.DRAFT, Status.PRICED),
            Transition(Status.PRICED, Status.PENDING_PAYMENT),
            Transition(Status.PRICED, Status.DRAFT),
            Transition(Status.PENDING_PAYMENT, Status.CONFIRMED),
            Transition(Status.PENDING_PAYMENT, Status.CANCELLED),
        ],
    )


class TestDeclaredTransitions:
    def test_permits_a_declared_transition(self) -> None:
        machine = _trip_like()
        assert machine.transition(Status.DRAFT, Status.PRICED) is Status.PRICED

    def test_rejects_an_undeclared_transition(self) -> None:
        machine = _trip_like()
        with pytest.raises(IllegalTransitionError) as exc:
            machine.transition(Status.DRAFT, Status.CONFIRMED)
        # SRS §32.3: ILLEGAL_TRANSITION, HTTP 409.
        assert exc.value.code == "ILLEGAL_TRANSITION"
        assert exc.value.status_code == 409

    def test_rejects_leaving_a_terminal_state(self) -> None:
        machine = _trip_like()
        with pytest.raises(IllegalTransitionError):
            machine.transition(Status.CANCELLED, Status.DRAFT)

    def test_rejects_a_self_transition_unless_declared(self) -> None:
        machine = _trip_like()
        with pytest.raises(IllegalTransitionError):
            machine.transition(Status.DRAFT, Status.DRAFT)

    def test_can_reports_without_raising(self) -> None:
        machine = _trip_like()
        assert machine.can(Status.DRAFT, Status.PRICED)
        assert not machine.can(Status.DRAFT, Status.CONFIRMED)

    def test_allowed_targets(self) -> None:
        machine = _trip_like()
        assert machine.allowed_targets(Status.PRICED) == frozenset(
            {Status.PENDING_PAYMENT, Status.DRAFT}
        )
        assert machine.allowed_targets(Status.CANCELLED) == frozenset()

    def test_states_and_terminality(self) -> None:
        machine = _trip_like()
        assert machine.states == frozenset(Status)
        assert machine.is_terminal(Status.CANCELLED)
        assert not machine.is_terminal(Status.DRAFT)


class TestGuards:
    def test_guard_can_block_a_declared_transition(self) -> None:
        machine = StateMachine(
            name="booking",
            initial=Status.CONFIRMED,
            transitions=[
                Transition(
                    Status.CONFIRMED,
                    Status.CANCELLED,
                    guard=lambda ctx: bool(ctx.get("policy_permits")),
                    guard_name="policy_permits_cancellation",
                )
            ],
        )
        assert machine.transition(Status.CONFIRMED, Status.CANCELLED, {"policy_permits": True})

        with pytest.raises(GuardFailedError) as exc:
            machine.transition(Status.CONFIRMED, Status.CANCELLED, {"policy_permits": False})
        assert exc.value.guard == "policy_permits_cancellation"
        assert exc.value.status_code == 409

    def test_guard_name_defaults_to_the_function_name(self) -> None:
        def policy_allows(ctx: object) -> bool:
            return False

        transition = Transition(Status.DRAFT, Status.PRICED, guard=policy_allows)
        assert transition.guard_name == "policy_allows"

    def test_can_evaluates_the_guard_rather_than_ignoring_it(self) -> None:
        machine = StateMachine(
            name="booking",
            initial=Status.CONFIRMED,
            transitions=[
                Transition(
                    Status.CONFIRMED,
                    Status.CANCELLED,
                    guard=lambda ctx: bool(ctx.get("policy_permits")),
                )
            ],
        )
        assert machine.can(Status.CONFIRMED, Status.CANCELLED, {"policy_permits": True})
        assert not machine.can(Status.CONFIRMED, Status.CANCELLED, {"policy_permits": False})

    def test_missing_context_is_treated_as_empty(self) -> None:
        machine = StateMachine(
            name="m",
            initial=Status.DRAFT,
            transitions=[Transition(Status.DRAFT, Status.PRICED, guard=lambda ctx: "k" in ctx)],
        )
        with pytest.raises(GuardFailedError):
            machine.transition(Status.DRAFT, Status.PRICED)


class TestMalformedMachines:
    """A bad machine must fail at import, not in production."""

    def test_duplicate_transition_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="duplicate transition"):
            StateMachine(
                name="m",
                initial=Status.DRAFT,
                transitions=[
                    Transition(Status.DRAFT, Status.PRICED),
                    Transition(Status.DRAFT, Status.PRICED),
                ],
            )

    def test_outgoing_transition_from_a_terminal_state_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="terminal"):
            StateMachine(
                name="m",
                initial=Status.DRAFT,
                terminal=frozenset({Status.CANCELLED}),
                transitions=[Transition(Status.CANCELLED, Status.DRAFT)],
            )
