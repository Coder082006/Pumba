"""The hold state machine — SRS §17.2, Appendix A.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§17.2 draws it as one live state and three ends:

    (quote)                     (payment captured)
       |                               |
       v            commit             v
    +------+  ------------------>  +-----------+   counters move from
    | HELD |                       | COMMITTED |   *_held to *_sold
    +--+---+                       +-----------+
       |
       | expiry sweeper / explicit release / payment failure
       v
    +----------+   +---------+
    | RELEASED |   | EXPIRED |     counters decrement *_held
    +----------+   +---------+

**All three ends are terminal, and keeping them apart is the point.** The
counter arithmetic for RELEASED and EXPIRED is identical — both give the
capacity back — so it is tempting to collapse them into one "gone" state. They
answer different questions. RELEASED means something decided: a re-quote
superseded this hold, a payment failed, a tourist walked away. EXPIRED means
nobody decided anything and the clock ran out. §17.4's reconciliation and
§38.5's "oversell incidents: 0" are both investigations, and an investigation
that cannot tell an abandonment from a failure is missing the half of the data
that says which system to look at.

**There is no path back to HELD.** A hold whose capacity has been returned is
finished; wanting it again means asking for capacity again, under lock, against
whatever the counter says now. Re-arming a released hold would return capacity
that a database CHECK is the only remaining guard on — which is exactly the
race §17.1 I2 exists to prevent.

**COMMITTED is terminal too**, which is worth stating because a booking can
still be cancelled afterwards. That cancellation moves the *booking* and writes
a compensating counter change (§20.9); it does not move the hold backwards. The
hold recorded that capacity became sold, and that remains true of the moment it
describes.
"""

from __future__ import annotations

from enum import StrEnum

from apps.common.state_machine import StateMachine, Transition

__all__ = ["HoldState", "HOLD_MACHINE", "LIVE_STATES", "is_live_state", "returns_capacity"]


class HoldState(StrEnum):
    """§17.2's four states.

    A `StrEnum` mirroring `models.HoldStatus`, because this module may not
    import Django. `test_lifecycle.py` compares the two sets — a state added to
    one and not the other is the kind of drift that shows up as a row the
    machine cannot move.
    """

    HELD = "HELD"
    COMMITTED = "COMMITTED"
    RELEASED = "RELEASED"
    EXPIRED = "EXPIRED"


#: The states in which a hold is still holding capacity. Exactly one, and the
#: set exists rather than an `== HELD` comparison so that the question is asked
#: in one place — §17.5's sweeper, §20.8's commit and §9.4.5's re-quote all ask
#: it, and three spellings of one predicate is how they drift apart.
LIVE_STATES = frozenset({HoldState.HELD})


def is_live_state(state: HoldState) -> bool:
    """Whether this state still counts against `capacity_held`.

    Only half the liveness question: §17.1 I5 also requires that an expired
    TTL is honoured *before* the sweeper reaches it, and a timestamp is not
    something this module can see. `models.InventoryHold.is_live` is the other
    half, and both apply.
    """
    return state in LIVE_STATES


def returns_capacity(target: HoldState) -> bool:
    """Whether arriving in `target` gives `capacity_held` back.

    True for RELEASED and EXPIRED, false for COMMITTED — which moves the same
    quantity from `capacity_held` to `capacity_sold` rather than releasing it,
    so the total spoken for does not change. Stated as a function because the
    §20.8 commit routine and the §17.5 sweeper both have to get it right and
    the difference between them is one line of arithmetic.
    """
    return target in (HoldState.RELEASED, HoldState.EXPIRED)


HOLD_MACHINE: StateMachine[HoldState] = StateMachine(
    name="inventory_hold",
    initial=HoldState.HELD,
    terminal=frozenset({HoldState.COMMITTED, HoldState.RELEASED, HoldState.EXPIRED}),
    transitions=[
        # §20.8, on payment capture: `*_held -= qty; *_sold += qty`.
        Transition(HoldState.HELD, HoldState.COMMITTED),
        # §9.4.5 step 2 (a re-quote supersedes), §8.9's PaymentFailed, or a
        # tourist abandoning the basket. Somebody decided.
        Transition(HoldState.HELD, HoldState.RELEASED),
        # §17.5's sweeper. Nobody decided; the TTL ran out.
        Transition(HoldState.HELD, HoldState.EXPIRED),
    ],
)
