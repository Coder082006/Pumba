"""The payment state machine — SRS §21.4, Appendix A.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§21.4's diagram is transcribed below as data, the way §20.2's table is in
`booking.domain.lifecycle`, so a reviewer can check the edges against the SRS
one line at a time.

**PM4 decides what a guard is for here.** "The PSP is the authority on payment
state; the Platform reconciles to it, never the reverse." So these guards do
not second-guess the gateway — a `CAPTURED` webhook is not something to argue
with. What they do is refuse a transition the PSP could not have meant: a
capture with no reference, a refund that claims to settle more than was taken,
a state that would move backwards.

**Going backwards is the ordering rule, and it lives here.** §21.5: events "may
arrive out of order; the handler applies a state transition only if it is legal
and advances the state, otherwise it records and ignores". `advances` answers
that question, and `rank` is the order it answers it against — an authorised
webhook arriving after the capture webhook must not un-capture the payment, and
a duplicate must not re-apply.

**Terminal states** are REFUNDED, FAILED, EXPIRED and CHARGEBACK_LOST. CAPTURED
is not terminal — a captured payment still has refunds and disputes ahead of
it — and neither is PARTIALLY_REFUNDED, which is the state a payment sits in
while some of its components live on.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from enum import StrEnum

from apps.common.state_machine import StateMachine, Transition

__all__ = [
    "PaymentState",
    "PAYMENT_MACHINE",
    "RANK",
    "TERMINAL",
    "advances",
    "is_terminal",
    "apply",
]


class PaymentState(StrEnum):
    """§21.4's ten states. Mirrors `models.PaymentStatus`."""

    INITIATED = "INITIATED"
    PENDING = "PENDING"
    AUTHORISED = "AUTHORISED"
    CAPTURED = "CAPTURED"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED"
    REFUNDED = "REFUNDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    DISPUTED = "DISPUTED"
    CHARGEBACK_LOST = "CHARGEBACK_LOST"


P = PaymentState


def _flag(context: Mapping[str, object], key: str) -> bool:
    return context.get(key) is True


def _amount(context: Mapping[str, object], key: str) -> Decimal:
    value = context.get(key)
    return value if isinstance(value, Decimal) else Decimal("0")


# -- guards -------------------------------------------------------------------


def psp_accepted(context: Mapping[str, object]) -> bool:
    """INITIATED → PENDING: the PSP took the intent and named it.

    A payment with no reference is one nothing can ever reconcile: §21.9 joins
    the settlement report on `psp_reference`, and a row without one is money we
    could not match to an event.
    """
    return bool(context.get("psp_reference"))


def customer_authorised(context: Mapping[str, object]) -> bool:
    """PENDING → AUTHORISED: the customer completed their part.

    Unreachable while capture is automatic (§21.4's note: card payments
    authorise and capture in one step). Declared because the on-request flow
    wants a hold before the provider accepts, and because §21.4 draws it.
    """
    return _flag(context, "customer_authorised")


def captured(context: Mapping[str, object]) -> bool:
    """→ CAPTURED: the money is taken.

    The one guard that carries a real refusal. BR-060: the charge is computed
    server-side, and a capture for an amount other than the one this payment
    asked for is TC-073's tampering signature — it is refused here rather than
    confirming a trip against a figure nobody authorised.
    """
    if not _flag(context, "psp_captured"):
        return False
    charged = context.get("captured_amount")
    expected = context.get("expected_amount")
    if charged is None or expected is None:
        return True
    return charged == expected


def declined(context: Mapping[str, object]) -> bool:
    """→ FAILED: §21.8's taxonomy, whatever the code within it."""
    return bool(context.get("failure_code"))


def abandoned(context: Mapping[str, object]) -> bool:
    """→ EXPIRED: the payment window closed with nobody paying (§17.2)."""
    return _flag(context, "window_elapsed")


def partially_refunded(context: Mapping[str, object]) -> bool:
    """→ PARTIALLY_REFUNDED: some of it is back, and some is not.

    BR-044 is the boundary: a refund may never exceed what was captured and not
    already refunded. Refunding *everything* is the next edge down, not this
    one, so the state says which of the two happened without anyone doing
    arithmetic to find out.
    """
    refunded = _amount(context, "refunded_total")
    captured_amount = _amount(context, "captured_amount")
    return Decimal("0") < refunded < captured_amount


def fully_refunded(context: Mapping[str, object]) -> bool:
    """→ REFUNDED: all of it is back. Never more (BR-044)."""
    refunded = _amount(context, "refunded_total")
    captured_amount = _amount(context, "captured_amount")
    return captured_amount > Decimal("0") and refunded == captured_amount


def chargeback_opened(context: Mapping[str, object]) -> bool:
    """CAPTURED → DISPUTED: the issuer pulled the money back pending a decision."""
    return _flag(context, "chargeback_opened")


def dispute_won(context: Mapping[str, object]) -> bool:
    """DISPUTED → CAPTURED: §21.4's "resolved". The money stays."""
    return context.get("dispute_outcome") == "WON"


def dispute_lost(context: Mapping[str, object]) -> bool:
    """DISPUTED → CHARGEBACK_LOST. Terminal: the money is gone."""
    return context.get("dispute_outcome") == "LOST"


PAYMENT_MACHINE: StateMachine[PaymentState] = StateMachine(
    name="payment",
    initial=P.INITIATED,
    terminal=frozenset({P.REFUNDED, P.FAILED, P.EXPIRED, P.CHARGEBACK_LOST}),
    transitions=[
        Transition(P.INITIATED, P.PENDING, psp_accepted),
        # §21.4: "PSP rejected" straight from INITIATED, without the customer
        # ever seeing a form.
        Transition(P.INITIATED, P.FAILED, declined),
        Transition(P.PENDING, P.AUTHORISED, customer_authorised),
        # Auto-capture: the card authorises and captures in one step, so the
        # webhook that arrives says CAPTURED and never passes through
        # AUTHORISED. Both edges exist because §21.4 draws both paths.
        Transition(P.PENDING, P.CAPTURED, captured),
        Transition(P.AUTHORISED, P.CAPTURED, captured),
        Transition(P.PENDING, P.FAILED, declined),
        Transition(P.AUTHORISED, P.FAILED, declined),
        Transition(P.PENDING, P.EXPIRED, abandoned),
        Transition(P.AUTHORISED, P.EXPIRED, abandoned),
        Transition(P.CAPTURED, P.PARTIALLY_REFUNDED, partially_refunded),
        Transition(P.CAPTURED, P.REFUNDED, fully_refunded),
        Transition(P.PARTIALLY_REFUNDED, P.REFUNDED, fully_refunded),
        Transition(P.CAPTURED, P.DISPUTED, chargeback_opened),
        Transition(P.PARTIALLY_REFUNDED, P.DISPUTED, chargeback_opened),
        Transition(P.DISPUTED, P.CAPTURED, dispute_won),
        Transition(P.DISPUTED, P.CHARGEBACK_LOST, dispute_lost),
    ],
)


#: How far along a payment each state is, for §21.5's ordering rule.
#:
#: Not a total order over the machine — DISPUTED sits beside CAPTURED rather
#: than after it, because a dispute is not progress — but an order over the
#: question that matters: would applying this event move the payment forward?
#: Equal ranks do not advance, which is what makes a replayed webhook a no-op
#: rather than a second capture.
RANK: Mapping[PaymentState, int] = {
    P.INITIATED: 0,
    P.PENDING: 1,
    P.AUTHORISED: 2,
    P.CAPTURED: 3,
    P.DISPUTED: 3,
    P.PARTIALLY_REFUNDED: 4,
    P.REFUNDED: 5,
    P.FAILED: 5,
    P.EXPIRED: 5,
    P.CHARGEBACK_LOST: 5,
}

TERMINAL = frozenset({P.REFUNDED, P.FAILED, P.EXPIRED, P.CHARGEBACK_LOST})


def is_terminal(state: PaymentState) -> bool:
    return state in TERMINAL


def advances(source: PaymentState, target: PaymentState) -> bool:
    """§21.5: would this event move the payment forward?

    Three answers are "no", and each is a real arrival:

    - the **same state twice** — a duplicate webhook (TC-071);
    - a **lower rank** — an `authorised` event overtaken by its own capture,
      which is the ordinary case of two events crossing on the wire;
    - **anything at all after a terminal state** — a payment that has failed
      does not become pending because a late event says so.

    The dispute edges are the exception the rank table cannot express: winning
    a dispute returns a payment to CAPTURED, which is the same rank, and losing
    it ends at CHARGEBACK_LOST. Both are progress, and both are named rather
    than ranked.
    """
    if source is target:
        return False
    if is_terminal(source):
        return False
    if (source, target) in {(P.DISPUTED, P.CAPTURED), (P.DISPUTED, P.CHARGEBACK_LOST)}:
        return True
    return RANK[target] > RANK[source]


def apply(
    source: PaymentState, target: PaymentState, context: Mapping[str, object] | None = None
) -> PaymentState:
    """One §21.4 transition, guard and all.

    Actorless, unlike the booking machine: every edge here belongs to the
    system reconciling to the PSP. §21.4 has no actor column because there is
    nobody else in the room — a tourist does not move a payment, they pay.
    """
    return PAYMENT_MACHINE.transition(source, target, context)
