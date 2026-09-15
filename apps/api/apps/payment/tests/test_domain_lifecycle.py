"""§21.4's machine, edge by edge — the pattern `booking` established.

Five things are asserted, and the last two are the ones that catch a change
nobody meant to make:

1. every declared edge succeeds when its guard is satisfied;
2. every declared edge is refused when it is not;
3. `allowed_targets` is an **exact set** for all ten states, so an edge added
   or removed fails here rather than somewhere downstream;
4. no undeclared pair is reachable;
5. §21.5's ordering rule — a duplicate, a late event and anything after a
   terminal state all fail to advance, which is what makes TC-071 a no-op
   rather than a second capture.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.common.state_machine import GuardFailedError, IllegalTransitionError
from apps.payment.domain.lifecycle import (
    PAYMENT_MACHINE,
    RANK,
    PaymentState,
    advances,
    apply,
    is_terminal,
)

P = PaymentState

CAPTURED_CONTEXT = {
    "psp_captured": True,
    "captured_amount": Decimal("834.75"),
    "expected_amount": Decimal("834.75"),
}

#: Per edge: a context its guard accepts, and one it does not. The second is
#: never simply empty where a guard has something to say — a guard that only
#: refuses `{}` is a guard that refuses nothing.
EDGES: list[tuple[PaymentState, PaymentState, dict[str, object], dict[str, object]]] = [
    (P.INITIATED, P.PENDING, {"psp_reference": "pi_1"}, {"psp_reference": ""}),
    (P.INITIATED, P.FAILED, {"failure_code": "CARD_DECLINED"}, {"failure_code": ""}),
    (P.PENDING, P.AUTHORISED, {"customer_authorised": True}, {"customer_authorised": False}),
    (P.PENDING, P.CAPTURED, CAPTURED_CONTEXT, {"psp_captured": False}),
    (P.AUTHORISED, P.CAPTURED, CAPTURED_CONTEXT, {"psp_captured": False}),
    (P.PENDING, P.FAILED, {"failure_code": "INSUFFICIENT_FUNDS"}, {}),
    (P.AUTHORISED, P.FAILED, {"failure_code": "EXPIRED_INSTRUMENT"}, {}),
    (P.PENDING, P.EXPIRED, {"window_elapsed": True}, {"window_elapsed": False}),
    (P.AUTHORISED, P.EXPIRED, {"window_elapsed": True}, {}),
    (
        P.CAPTURED,
        P.PARTIALLY_REFUNDED,
        {"refunded_total": Decimal("55.00"), "captured_amount": Decimal("110.00")},
        # The whole amount is not a *partial* refund.
        {"refunded_total": Decimal("110.00"), "captured_amount": Decimal("110.00")},
    ),
    (
        P.CAPTURED,
        P.REFUNDED,
        {"refunded_total": Decimal("110.00"), "captured_amount": Decimal("110.00")},
        {"refunded_total": Decimal("55.00"), "captured_amount": Decimal("110.00")},
    ),
    (
        P.PARTIALLY_REFUNDED,
        P.REFUNDED,
        {"refunded_total": Decimal("110.00"), "captured_amount": Decimal("110.00")},
        {"refunded_total": Decimal("90.00"), "captured_amount": Decimal("110.00")},
    ),
    (P.CAPTURED, P.DISPUTED, {"chargeback_opened": True}, {"chargeback_opened": False}),
    (P.PARTIALLY_REFUNDED, P.DISPUTED, {"chargeback_opened": True}, {}),
    (P.DISPUTED, P.CAPTURED, {"dispute_outcome": "WON"}, {"dispute_outcome": "LOST"}),
    (P.DISPUTED, P.CHARGEBACK_LOST, {"dispute_outcome": "LOST"}, {"dispute_outcome": "WON"}),
]

#: §21.4's diagram, read as a lookup. Every state, including the four that go
#: nowhere: a terminal state that grew an edge would be a payment coming back
#: from the dead.
TARGETS: dict[PaymentState, set[PaymentState]] = {
    P.INITIATED: {P.PENDING, P.FAILED},
    P.PENDING: {P.AUTHORISED, P.CAPTURED, P.FAILED, P.EXPIRED},
    P.AUTHORISED: {P.CAPTURED, P.FAILED, P.EXPIRED},
    P.CAPTURED: {P.PARTIALLY_REFUNDED, P.REFUNDED, P.DISPUTED},
    P.PARTIALLY_REFUNDED: {P.REFUNDED, P.DISPUTED},
    P.DISPUTED: {P.CAPTURED, P.CHARGEBACK_LOST},
    P.REFUNDED: set(),
    P.FAILED: set(),
    P.EXPIRED: set(),
    P.CHARGEBACK_LOST: set(),
}


class TestEveryTransitionSucceedsWhenLegal:
    @pytest.mark.parametrize(
        ("source", "target", "context"),
        [(source, target, ok) for source, target, ok, _ in EDGES],
        ids=lambda value: str(value),
    )
    def test_positive(
        self, source: PaymentState, target: PaymentState, context: dict[str, object]
    ) -> None:
        assert apply(source, target, context) is target


class TestEveryTransitionIsRefusedWhenItsGuardIsNot:
    @pytest.mark.parametrize(
        ("source", "target", "context"),
        [(source, target, bad) for source, target, _, bad in EDGES],
        ids=lambda value: str(value),
    )
    def test_negative(
        self, source: PaymentState, target: PaymentState, context: dict[str, object]
    ) -> None:
        with pytest.raises(GuardFailedError):
            apply(source, target, context)


class TestTheDeclaredEdgesAreExactlyTheseOnes:
    @pytest.mark.parametrize("state", list(P), ids=lambda value: str(value))
    def test_allowed_targets_match_section_214(self, state: PaymentState) -> None:
        assert PAYMENT_MACHINE.allowed_targets(state) == frozenset(TARGETS[state])

    def test_every_declared_edge_is_covered_by_a_case(self) -> None:
        """The parametrised lists above must not fall behind the machine."""
        declared = {(source, target) for source, targets in TARGETS.items() for target in targets}
        assert {(source, target) for source, target, _, _ in EDGES} == declared


class TestUndeclaredEdgesAreRefused:
    def test_every_undeclared_pair_is_illegal(self) -> None:
        for source in P:
            for target in P:
                if target in TARGETS[source]:
                    continue
                with pytest.raises(IllegalTransitionError):
                    apply(source, target, {})


class TestTheGuardsThatRefuseSomethingReal:
    def test_a_capture_for_the_wrong_amount_is_refused(self) -> None:
        """TC-073, at the state machine rather than at the door.

        BR-060 computes the charge server-side; a capture for a different
        figure is a payment nobody authorised, and confirming a trip against it
        would be worse than failing loudly here.
        """
        with pytest.raises(GuardFailedError):
            apply(
                P.PENDING,
                P.CAPTURED,
                {
                    "psp_captured": True,
                    "captured_amount": Decimal("8.34"),
                    "expected_amount": Decimal("834.75"),
                },
            )

    def test_a_capture_the_caller_cannot_check_is_allowed_through(self) -> None:
        """PM4: the PSP is the authority. Where no expected amount was resolved
        there is nothing to disagree with, and refusing would strand money that
        has already moved."""
        assert apply(P.PENDING, P.CAPTURED, {"psp_captured": True}) is P.CAPTURED

    def test_a_pending_payment_cannot_reach_pending_again(self) -> None:
        with pytest.raises(IllegalTransitionError):
            apply(P.PENDING, P.PENDING, {"psp_reference": "pi_1"})

    def test_refunding_more_than_was_captured_is_not_a_full_refund(self) -> None:
        """BR-044. Over-refunding is refused before the PSP by
        `assert_refundable`; this is the second door, on the state itself."""
        with pytest.raises(GuardFailedError):
            apply(
                P.CAPTURED,
                P.REFUNDED,
                {"refunded_total": Decimal("200.00"), "captured_amount": Decimal("110.00")},
            )


class TestSection215Ordering:
    def test_a_duplicate_event_does_not_advance(self) -> None:
        """TC-071: the same event twice is one effect."""
        assert advances(P.CAPTURED, P.CAPTURED) is False

    def test_a_late_event_does_not_advance(self) -> None:
        """An `authorised` webhook arriving after its own capture. Applied, it
        would un-capture a paid trip; ignored, it is two events crossing."""
        assert advances(P.CAPTURED, P.AUTHORISED) is False

    def test_nothing_follows_a_terminal_state(self) -> None:
        for state in (P.REFUNDED, P.FAILED, P.EXPIRED, P.CHARGEBACK_LOST):
            assert is_terminal(state)
            assert advances(state, P.CAPTURED) is False

    def test_the_ordinary_forward_arrivals_do_advance(self) -> None:
        assert advances(P.INITIATED, P.PENDING)
        assert advances(P.PENDING, P.CAPTURED)
        assert advances(P.CAPTURED, P.PARTIALLY_REFUNDED)
        assert advances(P.PARTIALLY_REFUNDED, P.REFUNDED)

    def test_a_dispute_resolution_advances_though_its_rank_does_not(self) -> None:
        """Winning returns a payment to CAPTURED — the same rank, and still
        progress. The rank table cannot say that, so the edge is named."""
        assert RANK[P.DISPUTED] == RANK[P.CAPTURED]
        assert advances(P.DISPUTED, P.CAPTURED)
        assert advances(P.DISPUTED, P.CHARGEBACK_LOST)

    def test_every_state_is_ranked(self) -> None:
        """A state added to the machine and not to the table would rank as a
        KeyError in the webhook handler, at the worst possible moment."""
        assert set(RANK) == set(P)
