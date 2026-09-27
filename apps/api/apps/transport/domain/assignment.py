"""The assignment state machine — SRS §11.7, §11.8, §11.9, §13.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§11.7's table is transcribed three times over, once per column, each as data
rather than code so a reviewer can check it against the SRS line by line — the
same discipline `booking.domain.lifecycle` applies to §20.2:

- the **edges** are `ASSIGNMENT_MACHINE`'s transitions;
- the **actors** are `ACTORS`;
- the **guards** are the predicates attached to each transition.

**Eleven states' worth of edges, and three of them are not in §11.7's diagram.**
The diagram draws nine states and stops; §11.8 requires two more paths that its
picture omits:

- **UNFULFILLED → ASSIGNED.** §11.8 says an administrator "may assign manually"
  after the escalation. Without the edge, the escalation would raise a ticket
  about a state nobody could act on, and the operations queue would be a list
  of dead ends.
- **→ INCIDENT**, from EN_ROUTE, ARRIVED or STARTED. §11.8's "Driver sets
  INCIDENT with a reason" is a breakdown mid-trip, which is neither a
  completion nor a cancellation: the tourist got part of the service and §11.8
  settles it pro rata. Cancelling it would refund the wrong amount and
  completing it would pay the driver for a journey that did not happen.

**Guards read facts; they never fetch them.** Every predicate is a function of a
context the caller has already resolved — whether the offer is still live,
whether the driver is inside the geofence, what time it is. `common.state_machine`
requires it, and it is what lets every edge be tested here with a dictionary and
no database. In particular `no_overlap` is a *fact supplied by the caller*: the
real guarantee is ADR 0029's EXCLUDE constraint, and a guard that tried to check
it would be a read followed by a write with TC-083's race in the gap.

**A geofence violation is permitted, not refused.** §13: "A transition attempted
outside the geofence requires an `override_reason`, is permitted, and is flagged
in `audit_log`." So the guard passes on an override and the *service* records
why — a driver whose GPS has drifted at an airport must still be able to say
the tourist is in the car. TC-100 asserts the flag, not a refusal.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum

from apps.common.state_machine import IllegalTransitionError, StateMachine, Transition

__all__ = [
    "AssignmentState",
    "Actor",
    "ASSIGNMENT_MACHINE",
    "ACTORS",
    "CANCELLABLE_BEFORE_START",
    "INCIDENT_REPORTABLE",
    "EN_ROUTE_WINDOW",
    "apply",
    "force",
    "blocks_the_diary",
]

#: §11.7: ASSIGNED → EN_ROUTE is guarded "within pickup_at ± 4 h". A driver
#: setting out a day early has mistaken the job; one setting out five hours
#: late has not set out at all.
EN_ROUTE_WINDOW = timedelta(hours=4)


class AssignmentState(StrEnum):
    """§11.7's nine, plus §11.8's INCIDENT. Mirrors `models.AssignmentStatus`."""

    PENDING = "PENDING"
    OFFERED = "OFFERED"
    ASSIGNED = "ASSIGNED"
    EN_ROUTE = "EN_ROUTE"
    ARRIVED = "ARRIVED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    UNFULFILLED = "UNFULFILLED"
    INCIDENT = "INCIDENT"


class Actor(StrEnum):
    """§11.7's Trigger column, and §11.8's.

    DISPATCHER rather than SYSTEM, because §11.7 names it: the thing that makes
    an offer is a specific scheduled job and not "the system" in general, and
    keeping the distinction means an audit entry says which.
    """

    DISPATCHER = "DISPATCHER"
    DRIVER = "DRIVER"
    TOURIST = "TOURIST"
    PROVIDER = "PROVIDER"
    ADMIN = "ADMIN"


A = AssignmentState


def _flag(context: Mapping[str, object], key: str) -> bool:
    return context.get(key) is True


# -- the Guard column, one predicate per row ---------------------------------


def eligible_candidate_exists(context: Mapping[str, object]) -> bool:
    """PENDING → OFFERED: "eligible candidate exists".

    An offer to nobody is how a dispatcher loop spins: it would mark the
    assignment OFFERED, find no candidate to send to, and never come back.
    """
    return _flag(context, "candidate_available")


def offer_live_and_no_overlap(context: Mapping[str, object]) -> bool:
    """OFFERED → ASSIGNED: "offer not expired; no overlap". Both.

    `no_overlap` is the caller's reading of §11.6 rule 5, kept here so the edge
    is honest about its precondition. It is *not* the guarantee — ADR 0029's
    EXCLUDE constraint is, because a guard cannot close the gap between
    checking and writing, and TC-083 lives in exactly that gap.
    """
    return _flag(context, "offer_live") and _flag(context, "no_overlap")


def candidates_remain(context: Mapping[str, object]) -> bool:
    """OFFERED → PENDING: "candidates remain".

    The loop of §11.5. When they do not, the assignment goes to UNFULFILLED
    instead, which is the other edge out of this state's successor.
    """
    return _flag(context, "candidates_remain")


def within_the_en_route_window(context: Mapping[str, object]) -> bool:
    """ASSIGNED → EN_ROUTE: "within pickup_at ± 4 h".

    Symmetric, as §11.7 writes it. Early is as wrong as late: a driver who
    sets out at dawn for an evening pickup has opened the wrong job, and
    letting them would put a tourist's transfer into EN_ROUTE nine hours
    before anybody is at the airport.
    """
    now, pickup_at = context.get("now"), context.get("pickup_at")
    if not isinstance(now, datetime) or not isinstance(pickup_at, datetime):
        return False
    return abs(now - pickup_at) <= EN_ROUTE_WINDOW


def inside_pickup_geofence_or_override(context: Mapping[str, object]) -> bool:
    """EN_ROUTE → ARRIVED: "inside geofence (300 m) or override".

    §13 makes the override *permitted* and flagged, not refused. An airport
    forecourt is the worst GPS environment a driver will meet all day, and a
    system that refused ARRIVED there would be a system drivers work around by
    telephone.
    """
    return _flag(context, "inside_pickup_geofence") or _flag(context, "override")


def tourist_present(context: Mapping[str, object]) -> bool:
    """ARRIVED → STARTED: "tourist present; optional PIN confirmation".

    §11.9 makes the PIN the evidence and "configurable per market" — so the
    PIN is required only where it is switched on, and where it is, presence
    alone is not enough. Both facts are the caller's to resolve; this decides
    what they add up to.
    """
    if not _flag(context, "tourist_present"):
        return False
    if _flag(context, "pin_required"):
        return _flag(context, "pin_verified")
    return True


def inside_dropoff_geofence_or_override(context: Mapping[str, object]) -> bool:
    """STARTED → COMPLETED: "inside drop-off geofence or override".

    The same permission as arrival, for the same reason, and it matters more
    here: this is the transition that earns the driver their money (BR-071).
    """
    return _flag(context, "inside_dropoff_geofence") or _flag(context, "override")


def policy_evaluated(context: Mapping[str, object]) -> bool:
    """→ CANCELLED: the booking's own cancellation has been priced.

    §11.7 draws the edge from "any state before STARTED" and says nothing about
    the money; §20.9 does, and an assignment cancelled before its booking has
    been through the policy would leave a refund nobody computed.
    """
    return _flag(context, "policy_evaluated")


def reason_given(context: Mapping[str, object]) -> bool:
    """→ INCIDENT: §11.8's "Driver sets INCIDENT **with a reason**".

    The reason is the requirement, not decoration. A CRITICAL ticket that says
    only "incident" cannot be triaged, and §11.8's response — replacement
    dispatched, full refund, goodwill credit — depends on which incident it was.
    """
    return bool(str(context.get("reason") or "").strip())


def a_driver_to_assign(context: Mapping[str, object]) -> bool:
    """UNFULFILLED → ASSIGNED: §11.8's manual assignment.

    Not in §11.7's diagram; see the module docstring. The same two facts the
    automatic path needs, because an administrator assigning by hand must not
    be able to double-book a driver the dispatcher would have skipped.
    """
    return _flag(context, "no_overlap") and _flag(context, "driver_chosen")


#: §11.7: "Any state before STARTED → CANCELLED".
CANCELLABLE_BEFORE_START = (A.PENDING, A.OFFERED, A.ASSIGNED, A.EN_ROUTE, A.ARRIVED)

#: §11.8's breakdown and no-show paths. Not PENDING or OFFERED: nobody is on
#: the job yet, so there is no incident to have — that is UNFULFILLED.
INCIDENT_REPORTABLE = (A.EN_ROUTE, A.ARRIVED, A.STARTED)


ASSIGNMENT_MACHINE: StateMachine[AssignmentState] = StateMachine(
    name="driver_assignment",
    initial=A.PENDING,
    # UNFULFILLED is deliberately *not* terminal: §11.8 lets operations assign
    # manually out of it, and a terminal UNFULFILLED would make the escalation
    # a ticket about a state nobody can act on.
    terminal=frozenset({A.COMPLETED, A.CANCELLED, A.INCIDENT}),
    transitions=[
        Transition(A.PENDING, A.OFFERED, eligible_candidate_exists),
        Transition(A.OFFERED, A.ASSIGNED, offer_live_and_no_overlap),
        Transition(A.OFFERED, A.PENDING, candidates_remain),
        Transition(A.PENDING, A.UNFULFILLED),
        Transition(A.ASSIGNED, A.EN_ROUTE, within_the_en_route_window),
        Transition(A.EN_ROUTE, A.ARRIVED, inside_pickup_geofence_or_override),
        Transition(A.ARRIVED, A.STARTED, tourist_present),
        Transition(A.STARTED, A.COMPLETED, inside_dropoff_geofence_or_override),
        Transition(A.UNFULFILLED, A.ASSIGNED, a_driver_to_assign),
        *[Transition(source, A.CANCELLED, policy_evaluated) for source in CANCELLABLE_BEFORE_START],
        *[Transition(source, A.INCIDENT, reason_given) for source in INCIDENT_REPORTABLE],
    ],
)


#: §11.7's Trigger column, per edge, and §11.8's for the two it adds.
ACTORS: Mapping[tuple[AssignmentState, AssignmentState], frozenset[Actor]] = {
    (A.PENDING, A.OFFERED): frozenset({Actor.DISPATCHER}),
    (A.OFFERED, A.ASSIGNED): frozenset({Actor.DRIVER}),
    (A.OFFERED, A.PENDING): frozenset({Actor.DRIVER, Actor.DISPATCHER}),
    (A.PENDING, A.UNFULFILLED): frozenset({Actor.DISPATCHER}),
    (A.ASSIGNED, A.EN_ROUTE): frozenset({Actor.DRIVER}),
    (A.EN_ROUTE, A.ARRIVED): frozenset({Actor.DRIVER}),
    (A.ARRIVED, A.STARTED): frozenset({Actor.DRIVER}),
    (A.STARTED, A.COMPLETED): frozenset({Actor.DRIVER, Actor.TOURIST, Actor.PROVIDER, Actor.ADMIN}),
    (A.UNFULFILLED, A.ASSIGNED): frozenset({Actor.ADMIN}),
    **{
        (source, A.CANCELLED): frozenset({Actor.TOURIST, Actor.PROVIDER, Actor.ADMIN})
        for source in CANCELLABLE_BEFORE_START
    },
    **{
        (source, A.INCIDENT): frozenset({Actor.DRIVER, Actor.ADMIN})
        for source in INCIDENT_REPORTABLE
    },
}


#: The states in which a driver is genuinely unavailable. Mirrors
#: `models.LIVE_ASSIGNMENT_STATUSES`, which is the same list expressed as the
#: condition on ADR 0029's EXCLUDE constraint — and the two are asserted equal
#: in the tests, because a machine and a constraint that disagree about what
#: "busy" means is how a driver ends up double-booked or permanently retired.
_BLOCKING = frozenset({A.ASSIGNED, A.EN_ROUTE, A.ARRIVED, A.STARTED})


def blocks_the_diary(state: AssignmentState) -> bool:
    """Whether an assignment in this state makes its driver unavailable."""
    return state in _BLOCKING


def apply(
    source: AssignmentState,
    target: AssignmentState,
    *,
    actor: Actor,
    context: Mapping[str, object] | None = None,
) -> AssignmentState:
    """One §11.7 transition, checked on all three columns.

    An undeclared edge and an actor the row does not name are both
    `ILLEGAL_TRANSITION` — for this actor the transition does not exist, which
    is the honest answer and not a hint about what somebody else could do. A
    declared edge whose guard refuses is `TRANSITION_GUARD_FAILED`. Both 409.
    """
    if actor not in ACTORS.get((source, target), frozenset()):
        raise IllegalTransitionError(ASSIGNMENT_MACHINE.name, source, target)
    return ASSIGNMENT_MACHINE.transition(source, target, context)


def force(source: AssignmentState, target: AssignmentState) -> AssignmentState:
    """Operations' exceptional control: the guard is bypassed, the table is not.

    §11.8 gives an administrator the escalation paths, and a transfer going
    wrong at an airport is not a situation a guard should be able to deadlock.
    What it cannot do is invent an edge: an assignment still cannot go from
    PENDING to COMPLETED, because no sequence of real events produces that and
    a row that claims it would corrupt the settlement behind it. An exceptional
    control that could invent an edge would make §11.7 advisory.
    """
    if target not in ASSIGNMENT_MACHINE.allowed_targets(source):
        raise IllegalTransitionError(ASSIGNMENT_MACHINE.name, source, target)
    return target
