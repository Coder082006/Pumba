"""The booking state machine — SRS §20.2, §28.4, Appendix A.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§20.1: "No other module may write booking.status." §20.2's table is transcribed
below three times over, once per column, and each column is data rather than
code so a reviewer can check it against the SRS line by line:

- the **edges** are `BOOKING_MACHINE`'s transitions;
- the **actors** are `ACTORS`;
- the **guards** are the predicates attached to each transition.

**Thirteen edges, not fourteen.** §20.2 lists AWAITING_PROVIDER → CANCELLED
twice: once for a provider's rejection or a lapsed window, once in the general
cancellation row. The common machine refuses a duplicate edge — "express
alternatives as one transition with a guard" — so it is one edge whose actors
are the union of both rows and whose guard accepts either row's condition.

**Guards read facts; they never fetch them.** Every guard is a predicate over a
context the caller has already resolved: whether payment was captured, whether
the holds committed, what time it is. `common.state_machine` requires it, and it
is what lets every edge be tested here with a dictionary and no database.

**Terminal states** are COMPLETED, REFUNDED, NO_SHOW and FAILED. CANCELLED is
not terminal, because it moves to REFUNDED when the refund settles. A NO_SHOW
reversed on dispute (§20.10) is a compensating refund, not an edge (ADR 0025).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum

from apps.common.state_machine import IllegalTransitionError, StateMachine, Transition

__all__ = [
    "BookingState",
    "Actor",
    "BOOKING_MACHINE",
    "ACTORS",
    "TOURIST_CANCELLABLE",
    "apply",
    "force",
    "is_tourist_cancellable",
]


class BookingState(StrEnum):
    """§28.4's ten states. Mirrors `models.BookingStatus`."""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    AWAITING_PROVIDER = "AWAITING_PROVIDER"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
    NO_SHOW = "NO_SHOW"
    FAILED = "FAILED"


class Actor(StrEnum):
    """§20.2's Actor column."""

    TOURIST = "TOURIST"
    PROVIDER = "PROVIDER"
    DRIVER = "DRIVER"
    SYSTEM = "SYSTEM"
    ADMIN = "ADMIN"


B = BookingState


def _flag(context: Mapping[str, object], key: str) -> bool:
    return context.get(key) is True


# -- the Guard column, one predicate per row ---------------------------------


def quote_valid_and_hold_live(context: Mapping[str, object]) -> bool:
    """DRAFT → PENDING: "Valid quote token; hold live"."""
    return _flag(context, "quote_valid") and _flag(context, "hold_live")


def captured_and_committed_instant(context: Mapping[str, object]) -> bool:
    """PENDING → CONFIRMED: "Payment captured; hold committed; instant-confirmation service"."""
    return (
        _flag(context, "payment_captured")
        and _flag(context, "hold_committed")
        and context.get("confirmation_mode") == "INSTANT"
    )


def captured_on_request(context: Mapping[str, object]) -> bool:
    """PENDING → AWAITING_PROVIDER: "Payment captured; confirmation_mode = ON_REQUEST"."""
    return _flag(context, "payment_captured") and context.get("confirmation_mode") == "ON_REQUEST"


def payment_failed_or_hold_expired(context: Mapping[str, object]) -> bool:
    """PENDING → FAILED: "Payment failed or hold expired".

    Plus one condition ADR 0025's third addendum adds: the component's provider
    was no longer sellable at capture. BR-037 requires the provider be VERIFIED
    "at the moment of confirmation", and §20.8's answer to a component that
    cannot be supplied at that moment is to fail it and confirm the rest.
    """
    return (
        _flag(context, "payment_failed")
        or _flag(context, "hold_expired")
        or _flag(context, "provider_unsellable")
    )


def within_response_window(context: Mapping[str, object]) -> bool:
    """AWAITING_PROVIDER → CONFIRMED: "Within provider_response_hours".

    The deadline is the rule (ADR 0025 decision 7). An acceptance at exactly
    the deadline is in time; one a moment after is not, whatever the sweep has
    or has not done yet.
    """
    now, due = context.get("now"), context.get("response_due_at")
    return isinstance(now, datetime) and isinstance(due, datetime) and now <= due


def declined_lapsed_or_policy_evaluated(context: Mapping[str, object]) -> bool:
    """AWAITING_PROVIDER → CANCELLED, both of §20.2's rows for it.

    "Rejected, or response window elapsed → automatic full refund", or the
    general cancellation row's "Policy evaluated; refund computed".
    """
    return (
        _flag(context, "provider_declined")
        or _flag(context, "response_window_elapsed")
        or _flag(context, "policy_evaluated")
    )


def service_started(context: Mapping[str, object]) -> bool:
    """CONFIRMED → IN_PROGRESS: "Service start reached, or driver sets STARTED"."""
    return _flag(context, "service_start_reached") or _flag(context, "driver_started")


def service_ended(context: Mapping[str, object]) -> bool:
    """IN_PROGRESS → COMPLETED: "Service end reached, or driver sets COMPLETED"."""
    return _flag(context, "service_end_reached") or _flag(context, "driver_completed")


def wait_elapsed_with_evidence(context: Mapping[str, object]) -> bool:
    """→ NO_SHOW: "Documented wait elapsed; evidence recorded". Both, not either."""
    return _flag(context, "wait_elapsed") and _flag(context, "evidence_recorded")


def policy_evaluated(context: Mapping[str, object]) -> bool:
    """→ CANCELLED: "Policy evaluated; refund computed"."""
    return _flag(context, "policy_evaluated")


def refund_settled(context: Mapping[str, object]) -> bool:
    """CANCELLED → REFUNDED: "Refund settled at PSP"."""
    return _flag(context, "refund_settled")


BOOKING_MACHINE: StateMachine[BookingState] = StateMachine(
    name="booking",
    initial=B.DRAFT,
    terminal=frozenset({B.COMPLETED, B.REFUNDED, B.NO_SHOW, B.FAILED}),
    transitions=[
        Transition(B.DRAFT, B.PENDING, quote_valid_and_hold_live),
        Transition(B.PENDING, B.CONFIRMED, captured_and_committed_instant),
        Transition(B.PENDING, B.AWAITING_PROVIDER, captured_on_request),
        Transition(B.PENDING, B.FAILED, payment_failed_or_hold_expired),
        Transition(B.AWAITING_PROVIDER, B.CONFIRMED, within_response_window),
        Transition(B.AWAITING_PROVIDER, B.CANCELLED, declined_lapsed_or_policy_evaluated),
        Transition(B.CONFIRMED, B.IN_PROGRESS, service_started),
        Transition(B.IN_PROGRESS, B.COMPLETED, service_ended),
        Transition(B.CONFIRMED, B.NO_SHOW, wait_elapsed_with_evidence),
        Transition(B.IN_PROGRESS, B.NO_SHOW, wait_elapsed_with_evidence),
        Transition(B.PENDING, B.CANCELLED, policy_evaluated),
        Transition(B.CONFIRMED, B.CANCELLED, policy_evaluated),
        Transition(B.CANCELLED, B.REFUNDED, refund_settled),
    ],
)


#: §20.2's Actor column, per edge. The AWAITING_PROVIDER → CANCELLED entry is
#: the union of its two rows. ADMIN appears only where §20.2 names "Admin";
#: every other administrative move is `force`, which is SUPER_ADMIN's (BR-038).
ACTORS: Mapping[tuple[BookingState, BookingState], frozenset[Actor]] = {
    (B.DRAFT, B.PENDING): frozenset({Actor.TOURIST}),
    (B.PENDING, B.CONFIRMED): frozenset({Actor.SYSTEM}),
    (B.PENDING, B.AWAITING_PROVIDER): frozenset({Actor.SYSTEM}),
    (B.PENDING, B.FAILED): frozenset({Actor.SYSTEM}),
    (B.AWAITING_PROVIDER, B.CONFIRMED): frozenset({Actor.PROVIDER}),
    (B.AWAITING_PROVIDER, B.CANCELLED): frozenset(
        {Actor.PROVIDER, Actor.SYSTEM, Actor.TOURIST, Actor.ADMIN}
    ),
    (B.CONFIRMED, B.IN_PROGRESS): frozenset({Actor.SYSTEM, Actor.DRIVER}),
    (B.IN_PROGRESS, B.COMPLETED): frozenset({Actor.SYSTEM, Actor.DRIVER}),
    (B.CONFIRMED, B.NO_SHOW): frozenset({Actor.DRIVER, Actor.PROVIDER}),
    (B.IN_PROGRESS, B.NO_SHOW): frozenset({Actor.DRIVER, Actor.PROVIDER}),
    (B.PENDING, B.CANCELLED): frozenset({Actor.TOURIST, Actor.PROVIDER, Actor.ADMIN}),
    (B.CONFIRMED, B.CANCELLED): frozenset({Actor.TOURIST, Actor.PROVIDER, Actor.ADMIN}),
    (B.CANCELLED, B.REFUNDED): frozenset({Actor.SYSTEM}),
}


#: BR-042: "A tourist may cancel any booking not yet IN_PROGRESS". Read against
#: the machine, that is exactly the sources of a tourist-actor CANCELLED edge.
TOURIST_CANCELLABLE = frozenset(
    source
    for (source, target), actors in ACTORS.items()
    if target is B.CANCELLED and Actor.TOURIST in actors
)


def apply(
    source: BookingState,
    target: BookingState,
    *,
    actor: Actor,
    context: Mapping[str, object] | None = None,
) -> BookingState:
    """One §20.2 transition, checked on all three columns.

    An undeclared edge and an actor the row does not name are both
    `ILLEGAL_TRANSITION` — for this actor the transition does not exist. A
    declared edge whose guard refuses is `TRANSITION_GUARD_FAILED`. Both are 409.
    """
    if actor not in ACTORS.get((source, target), frozenset()):
        raise IllegalTransitionError(BOOKING_MACHINE.name, source, target)
    return BOOKING_MACHINE.transition(source, target, context)


def force(source: BookingState, target: BookingState) -> BookingState:
    """BR-038's exceptional control: the guard is bypassed, the table is not.

    A SUPER_ADMIN may move a booking along any declared edge whose guard would
    refuse. An undeclared edge is refused for them too — an exceptional control
    that could invent an edge would make §20.2 advisory (ADR 0025).
    """
    if target not in BOOKING_MACHINE.allowed_targets(source):
        raise IllegalTransitionError(BOOKING_MACHINE.name, source, target)
    return target


def is_tourist_cancellable(state: BookingState) -> bool:
    return state in TOURIST_CANCELLABLE
