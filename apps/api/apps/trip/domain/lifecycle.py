"""The trip state machine — SRS §20.5, Appendix A.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

Declared here rather than in `common` because §36.2 gives `common` the
mechanism and each module the policy: "the single table-driven transition
validator used by every machine, so that no module implements its own". The
table below is the whole of trip's policy, and it is meant to be readable by
somebody checking it against §20.5 without reading any other file.

Two things about the shape are deliberate.

**Both ways back to DRAFT are one transition, not two.** §20.5 draws a quote
expiring and a payment failing as separate arrows, and they arrive from
different states, so they are separate edges — but neither is a special kind of
DRAFT. A trip that returns to DRAFT is editable again, which is the only fact
the rest of the system needs.

**CANCELLED is reachable from every non-terminal state and from nowhere else.**
§20.5's "Any state -> CANCELLED" is bounded by `common.state_machine`'s own
rule that a terminal state has no outgoing transitions — so a COMPLETED trip
cannot be cancelled, which is right: a journey that has happened cannot be made
not to have happened, and the money path for that is a refund (§21), not a
state change here.
"""

from __future__ import annotations

from enum import StrEnum

from apps.common.state_machine import StateMachine, Transition

__all__ = ["TripState", "TRIP_MACHINE", "EDITABLE_STATES", "is_editable"]


class TripState(StrEnum):
    """§20.5's seven states.

    A `StrEnum` mirroring `models.TripStatus`, because this module may not
    import Django. The two are kept in step by `test_lifecycle.py`, which
    compares the sets — a state added to one and not the other is the kind of
    drift that shows up as a row the machine cannot move.
    """

    DRAFT = "DRAFT"
    PRICED = "PRICED"
    PENDING_PAYMENT = "PENDING_PAYMENT"
    CONFIRMED = "CONFIRMED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


#: The states in which an itinerary may still be regenerated and its items
#: edited. Everything from PENDING_PAYMENT onward has money or inventory
#: committed against it, and §10.8's locked-item rule takes over.
EDITABLE_STATES = frozenset({TripState.DRAFT})


def is_editable(state: TripState) -> bool:
    """Whether §9.4.2's item mutations are permitted at all.

    Separate from the item-level `is_locked` check of §10.8, and both apply: a
    DRAFT trip may still contain a locked item if a booking was confirmed for
    part of it, and a PENDING_PAYMENT trip is closed to edits even where no
    single item is locked yet.
    """
    return state in EDITABLE_STATES


TRIP_MACHINE: StateMachine[TripState] = StateMachine(
    name="trip",
    initial=TripState.DRAFT,
    terminal=frozenset({TripState.COMPLETED, TripState.CANCELLED}),
    transitions=[
        # generate / price
        Transition(TripState.DRAFT, TripState.PRICED),
        # confirm
        Transition(TripState.PRICED, TripState.PENDING_PAYMENT),
        # quote expired — the TTL of `quote.ttl_minutes` elapsed, so the prices
        # and the holds behind them are no longer honoured.
        Transition(TripState.PRICED, TripState.DRAFT),
        # payment failed
        Transition(TripState.PENDING_PAYMENT, TripState.DRAFT),
        # payment captured — §20.8's confirmation routine
        Transition(TripState.PENDING_PAYMENT, TripState.CONFIRMED),
        # first booking IN_PROGRESS
        Transition(TripState.CONFIRMED, TripState.IN_PROGRESS),
        # all bookings terminal (COMPLETED / CANCELLED / NO_SHOW)
        Transition(TripState.IN_PROGRESS, TripState.COMPLETED),
        # "Any state -> CANCELLED", which the terminal set bounds to the five
        # non-terminal ones.
        Transition(TripState.DRAFT, TripState.CANCELLED),
        Transition(TripState.PRICED, TripState.CANCELLED),
        Transition(TripState.PENDING_PAYMENT, TripState.CANCELLED),
        Transition(TripState.CONFIRMED, TripState.CANCELLED),
        Transition(TripState.IN_PROGRESS, TripState.CANCELLED),
    ],
)
