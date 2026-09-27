"""§11.7's machine, checked against §11.7 — edges, actors and guards.

The machine is the only thing standing between a transfer and a row that
claims something impossible: a driver who arrived at a job they never accepted,
a completed assignment nobody was ever assigned to, a settlement against a
journey that did not happen. So what is asserted here is mostly what the
machine *refuses*.

Three assertions are structural rather than behavioural, and they are the ones
most likely to catch a future mistake:

- the edge set is compared against §11.7's table transcribed independently, so
  adding an edge in the machine and forgetting the SRS fails here;
- every edge has an actor row, so an edge nobody can trigger cannot be added
  by accident;
- `blocks_the_diary` is compared against `models.LIVE_ASSIGNMENT_STATUSES`,
  which is the condition on ADR 0029's EXCLUDE constraint. A machine and a
  constraint that disagree about what "busy" means is how a driver ends up
  double-booked or permanently retired.

No database. This module is pure and this file keeps it so.
"""

from __future__ import annotations

import datetime as dt

import pytest

from apps.common.state_machine import GuardFailedError, IllegalTransitionError
from apps.transport.domain.assignment import (
    ACTORS,
    ASSIGNMENT_MACHINE,
    Actor,
    AssignmentState,
    apply,
    blocks_the_diary,
    force,
)

A = AssignmentState

PICKUP = dt.datetime(2027, 5, 1, 9, 0, tzinfo=dt.UTC)

#: §11.7's table, transcribed here from the SRS rather than from the machine.
#: The diagram's nine states give the first nine rows; the last three are the
#: paths §11.8 requires and §11.7 does not draw (see the module under test).
SPECIFIED_EDGES = {
    (A.PENDING, A.OFFERED),
    (A.OFFERED, A.ASSIGNED),
    (A.OFFERED, A.PENDING),
    (A.PENDING, A.UNFULFILLED),
    (A.ASSIGNED, A.EN_ROUTE),
    (A.EN_ROUTE, A.ARRIVED),
    (A.ARRIVED, A.STARTED),
    (A.STARTED, A.COMPLETED),
    # "Any state before STARTED → CANCELLED".
    (A.PENDING, A.CANCELLED),
    (A.OFFERED, A.CANCELLED),
    (A.ASSIGNED, A.CANCELLED),
    (A.EN_ROUTE, A.CANCELLED),
    (A.ARRIVED, A.CANCELLED),
    # §11.8: manual assignment after escalation, and the incident paths.
    (A.UNFULFILLED, A.ASSIGNED),
    (A.EN_ROUTE, A.INCIDENT),
    (A.ARRIVED, A.INCIDENT),
    (A.STARTED, A.INCIDENT),
}


class TestTheTableItself:
    def test_the_machine_declares_exactly_the_specified_edges(self) -> None:
        """Transcribed independently above, so a new edge added to the machine
        without a line in the SRS fails here rather than shipping."""
        declared = {
            (transition.source, transition.target)
            for transition in ASSIGNMENT_MACHINE.transitions  # type: ignore[attr-defined]
        }

        assert declared == SPECIFIED_EDGES

    def test_every_edge_names_somebody_who_can_trigger_it(self) -> None:
        """An edge with no actor row is unreachable through `apply` — it would
        look declared and behave as though it did not exist."""
        declared = {
            (transition.source, transition.target)
            for transition in ASSIGNMENT_MACHINE.transitions  # type: ignore[attr-defined]
        }

        assert set(ACTORS) == declared

    def test_the_terminal_states_are_the_three_with_nowhere_to_go(self) -> None:
        """UNFULFILLED is deliberately not among them: §11.8 lets operations
        assign manually out of it, and a terminal UNFULFILLED would make the
        escalation a ticket about a state nobody can act on."""
        assert ASSIGNMENT_MACHINE.terminal == frozenset({A.COMPLETED, A.CANCELLED, A.INCIDENT})
        assert ASSIGNMENT_MACHINE.allowed_targets(A.UNFULFILLED) == frozenset({A.ASSIGNED})

    def test_what_the_machine_calls_busy_is_what_the_constraint_calls_busy(self) -> None:
        """The machine's `blocks_the_diary` and ADR 0029's EXCLUDE condition
        are two expressions of one rule. Disagreement means either a driver is
        double-booked or a finished job retires them for ever."""
        from apps.transport.models import LIVE_ASSIGNMENT_STATUSES

        assert {state for state in A if blocks_the_diary(state)} == {
            A(status) for status in LIVE_ASSIGNMENT_STATUSES
        }


class TestDispatching:
    def test_the_dispatcher_offers_when_there_is_somebody_to_offer_to(self) -> None:
        assert (
            apply(
                A.PENDING,
                A.OFFERED,
                actor=Actor.DISPATCHER,
                context={"candidate_available": True},
            )
            is A.OFFERED
        )

    def test_an_offer_to_nobody_is_refused(self) -> None:
        """This is how a dispatcher loop spins: it marks the assignment
        OFFERED, finds nobody to send to, and never comes back."""
        with pytest.raises(GuardFailedError):
            apply(A.PENDING, A.OFFERED, actor=Actor.DISPATCHER, context={})

    def test_a_driver_cannot_offer_themselves_a_job(self) -> None:
        """§11.7 gives this edge to the dispatcher. For a driver it does not
        exist, which is the honest answer rather than a hint."""
        with pytest.raises(IllegalTransitionError):
            apply(A.PENDING, A.OFFERED, actor=Actor.DRIVER, context={"candidate_available": True})

    def test_accepting_needs_a_live_offer_and_a_free_diary(self) -> None:
        """§11.7: "offer not expired; no overlap" — both."""
        assert (
            apply(
                A.OFFERED,
                A.ASSIGNED,
                actor=Actor.DRIVER,
                context={"offer_live": True, "no_overlap": True},
            )
            is A.ASSIGNED
        )

    def test_accepting_an_expired_offer_is_refused(self) -> None:
        with pytest.raises(GuardFailedError):
            apply(
                A.OFFERED,
                A.ASSIGNED,
                actor=Actor.DRIVER,
                context={"offer_live": False, "no_overlap": True},
            )

    def test_accepting_a_job_that_clashes_is_refused(self) -> None:
        """The guard is the error message; ADR 0029's constraint is the
        guarantee. Both exist because a guard cannot close the gap between
        checking and writing, and TC-083 lives in that gap."""
        with pytest.raises(GuardFailedError):
            apply(
                A.OFFERED,
                A.ASSIGNED,
                actor=Actor.DRIVER,
                context={"offer_live": True, "no_overlap": False},
            )

    def test_a_decline_goes_back_to_pending_while_candidates_remain(self) -> None:
        """§11.5's loop."""
        assert (
            apply(A.OFFERED, A.PENDING, actor=Actor.DRIVER, context={"candidates_remain": True})
            is A.PENDING
        )

    def test_the_list_running_out_is_unfulfilled_and_needs_no_guard(self) -> None:
        """TC-090. §11.7's Guard column for this row is a dash, and that is
        deliberate: the dispatcher having nobody left is not a condition to be
        satisfied, it is the situation."""
        assert apply(A.PENDING, A.UNFULFILLED, actor=Actor.DISPATCHER) is A.UNFULFILLED

    def test_operations_may_assign_by_hand_after_an_escalation(self) -> None:
        """§11.8: "administrator may assign manually". Not drawn in §11.7."""
        assert (
            apply(
                A.UNFULFILLED,
                A.ASSIGNED,
                actor=Actor.ADMIN,
                context={"no_overlap": True, "driver_chosen": True},
            )
            is A.ASSIGNED
        )

    def test_a_manual_assignment_may_not_double_book_either(self) -> None:
        """An administrator working round a failed dispatch is still not
        allowed to put one driver in two places."""
        with pytest.raises(GuardFailedError):
            apply(
                A.UNFULFILLED,
                A.ASSIGNED,
                actor=Actor.ADMIN,
                context={"no_overlap": False, "driver_chosen": True},
            )


class TestTheJourney:
    def test_a_driver_sets_out_within_four_hours_of_the_pickup(self) -> None:
        assert (
            apply(
                A.ASSIGNED,
                A.EN_ROUTE,
                actor=Actor.DRIVER,
                context={"now": PICKUP - dt.timedelta(hours=1), "pickup_at": PICKUP},
            )
            is A.EN_ROUTE
        )

    def test_setting_out_a_day_early_is_refused(self) -> None:
        """§11.7's window is symmetric. A driver leaving at dawn for an evening
        pickup has opened the wrong job, and letting them would put the
        tourist's transfer EN_ROUTE nine hours before anybody is at the
        airport."""
        with pytest.raises(GuardFailedError):
            apply(
                A.ASSIGNED,
                A.EN_ROUTE,
                actor=Actor.DRIVER,
                context={"now": PICKUP - dt.timedelta(days=1), "pickup_at": PICKUP},
            )

    def test_setting_out_five_hours_late_is_refused(self) -> None:
        with pytest.raises(GuardFailedError):
            apply(
                A.ASSIGNED,
                A.EN_ROUTE,
                actor=Actor.DRIVER,
                context={"now": PICKUP + dt.timedelta(hours=5), "pickup_at": PICKUP},
            )

    def test_exactly_four_hours_out_is_in_time(self) -> None:
        """A boundary stated in the SRS is inclusive unless it says otherwise,
        and a driver refused at exactly the limit would telephone instead."""
        assert (
            apply(
                A.ASSIGNED,
                A.EN_ROUTE,
                actor=Actor.DRIVER,
                context={"now": PICKUP - dt.timedelta(hours=4), "pickup_at": PICKUP},
            )
            is A.EN_ROUTE
        )

    def test_a_missing_pickup_time_refuses_rather_than_guesses(self) -> None:
        with pytest.raises(GuardFailedError):
            apply(A.ASSIGNED, A.EN_ROUTE, actor=Actor.DRIVER, context={"now": PICKUP})

    def test_arriving_inside_the_geofence_is_allowed(self) -> None:
        assert (
            apply(
                A.EN_ROUTE,
                A.ARRIVED,
                actor=Actor.DRIVER,
                context={"inside_pickup_geofence": True},
            )
            is A.ARRIVED
        )

    def test_arriving_outside_it_is_refused_without_an_override(self) -> None:
        with pytest.raises(GuardFailedError):
            apply(
                A.EN_ROUTE,
                A.ARRIVED,
                actor=Actor.DRIVER,
                context={"inside_pickup_geofence": False},
            )

    def test_arriving_outside_it_is_permitted_with_one(self) -> None:
        """TC-100, and §13 verbatim: the override "is permitted, and is flagged
        in audit_log". An airport forecourt is the worst GPS environment a
        driver meets all day, and a system that refused ARRIVED there is a
        system drivers work around by telephone."""
        assert (
            apply(
                A.EN_ROUTE,
                A.ARRIVED,
                actor=Actor.DRIVER,
                context={"inside_pickup_geofence": False, "override": True},
            )
            is A.ARRIVED
        )

    def test_the_tourist_has_to_be_in_the_car(self) -> None:
        assert (
            apply(A.ARRIVED, A.STARTED, actor=Actor.DRIVER, context={"tourist_present": True})
            is A.STARTED
        )

        with pytest.raises(GuardFailedError):
            apply(A.ARRIVED, A.STARTED, actor=Actor.DRIVER, context={"tourist_present": False})

    def test_where_the_pin_is_switched_on_presence_alone_is_not_enough(self) -> None:
        """§11.9 makes the PIN the evidence and "configurable per market", so
        it is required only where it is on — and where it is, this is the whole
        point of it."""
        with pytest.raises(GuardFailedError):
            apply(
                A.ARRIVED,
                A.STARTED,
                actor=Actor.DRIVER,
                context={"tourist_present": True, "pin_required": True},
            )

        assert (
            apply(
                A.ARRIVED,
                A.STARTED,
                actor=Actor.DRIVER,
                context={"tourist_present": True, "pin_required": True, "pin_verified": True},
            )
            is A.STARTED
        )

    def test_completing_needs_the_drop_off_geofence_or_an_override(self) -> None:
        """The transition that earns the driver their money (BR-071), so the
        same permission as arrival and for the same reason."""
        assert (
            apply(
                A.STARTED,
                A.COMPLETED,
                actor=Actor.DRIVER,
                context={"inside_dropoff_geofence": True},
            )
            is A.COMPLETED
        )

        with pytest.raises(GuardFailedError):
            apply(A.STARTED, A.COMPLETED, actor=Actor.DRIVER, context={})

    def test_a_whole_transfer_walks_end_to_end(self) -> None:
        """The happy path, as one sequence, because each edge passing alone
        does not prove they chain."""
        state = A.PENDING
        state = apply(
            state, A.OFFERED, actor=Actor.DISPATCHER, context={"candidate_available": True}
        )
        state = apply(
            state,
            A.ASSIGNED,
            actor=Actor.DRIVER,
            context={"offer_live": True, "no_overlap": True},
        )
        state = apply(
            state, A.EN_ROUTE, actor=Actor.DRIVER, context={"now": PICKUP, "pickup_at": PICKUP}
        )
        state = apply(
            state, A.ARRIVED, actor=Actor.DRIVER, context={"inside_pickup_geofence": True}
        )
        state = apply(state, A.STARTED, actor=Actor.DRIVER, context={"tourist_present": True})
        state = apply(
            state, A.COMPLETED, actor=Actor.DRIVER, context={"inside_dropoff_geofence": True}
        )

        assert state is A.COMPLETED
        assert ASSIGNMENT_MACHINE.is_terminal(state)


class TestGoingWrong:
    def test_cancelling_is_allowed_from_every_state_before_started(self) -> None:
        """§11.7: "Any state before STARTED → CANCELLED"."""
        for source in (A.PENDING, A.OFFERED, A.ASSIGNED, A.EN_ROUTE, A.ARRIVED):
            assert (
                apply(source, A.CANCELLED, actor=Actor.TOURIST, context={"policy_evaluated": True})
                is A.CANCELLED
            )

    def test_a_started_transfer_cannot_be_cancelled(self) -> None:
        """The tourist is in the car. What happens now is a completion or an
        incident, and either settles differently from a cancellation."""
        with pytest.raises(IllegalTransitionError):
            apply(A.STARTED, A.CANCELLED, actor=Actor.ADMIN, context={"policy_evaluated": True})

    def test_cancelling_before_the_policy_has_been_priced_is_refused(self) -> None:
        """§11.7 says nothing about the money and §20.9 does. An assignment
        cancelled before its booking went through the policy would leave a
        refund nobody computed."""
        with pytest.raises(GuardFailedError):
            apply(A.ASSIGNED, A.CANCELLED, actor=Actor.TOURIST, context={})

    def test_an_incident_needs_a_reason(self) -> None:
        """§11.8: "Driver sets INCIDENT **with a reason**". A CRITICAL ticket
        saying only "incident" cannot be triaged, and §11.8's response depends
        on which incident it was."""
        assert (
            apply(
                A.STARTED,
                A.INCIDENT,
                actor=Actor.DRIVER,
                context={"reason": "Alternator failed outside Chwaka"},
            )
            is A.INCIDENT
        )

        with pytest.raises(GuardFailedError):
            apply(A.STARTED, A.INCIDENT, actor=Actor.DRIVER, context={"reason": "   "})

    def test_there_is_no_incident_before_anybody_is_on_the_job(self) -> None:
        """A PENDING assignment has no driver to have a breakdown. That
        situation is UNFULFILLED, and conflating them would put a CRITICAL
        ticket on a queue that should have seen a HIGH one."""
        with pytest.raises(IllegalTransitionError):
            apply(A.PENDING, A.INCIDENT, actor=Actor.DRIVER, context={"reason": "broken"})


class TestForce:
    def test_operations_may_bypass_a_guard(self) -> None:
        """§11.8's escalations. A transfer going wrong at an airport is not a
        situation a guard should be able to deadlock."""
        assert force(A.EN_ROUTE, A.ARRIVED) is A.ARRIVED

    def test_operations_may_not_invent_an_edge(self) -> None:
        """No sequence of real events takes an assignment from PENDING to
        COMPLETED, and a row claiming it would corrupt the settlement behind
        it. An exceptional control that could do this would make §11.7
        advisory."""
        with pytest.raises(IllegalTransitionError):
            force(A.PENDING, A.COMPLETED)
