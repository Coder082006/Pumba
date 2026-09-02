"""The two Beat jobs of §8.8 and §17.4 — TC-052, and the drift alarm.

Both are compositions rather than logic: `inventory` decides what an expired
hold is and `trip` decides what a lapsed quote is, and what is asserted here is
that they are run in the right order, that running them twice is safe, and that
each reports what it did.

`CELERY_TASK_ALWAYS_EAGER` is true under `config.settings.ci`, so a task is
called directly — which is also how Beat will reach it, minus the queue.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.booking import services
from apps.booking.tasks import reconcile_inventory, release_expired_holds

from . import scenario

pytestmark = pytest.mark.django_db


def _departure(case: scenario.Scenario) -> object:
    return django_apps.get_model("inventory", "ActivityDeparture").objects.get(id=case.departure_id)


def _trip(case: scenario.Scenario) -> object:
    return django_apps.get_model("trip", "Trip").objects.get(id=case.trip_id)


def _age_the_hold(case: scenario.Scenario) -> None:
    """Move every hold on this trip into the past.

    The clock is the thing under test, so it is moved rather than waited for —
    and moved on the *row* rather than by patching `timezone.now`, because the
    sweeper reads the column and a patched clock would prove something about
    the patch.
    """
    django_apps.get_model("inventory", "InventoryHold").objects.filter(trip_id=case.trip_id).update(
        expires_at=timezone.now() - dt.timedelta(seconds=1)
    )


class TestTheSweeper:
    """TC-052: *"Hold EXPIRED; counters decremented; trip returns to
    DRAFT-equivalent."*"""

    def test_it_returns_the_capacity(self) -> None:
        case = scenario.build(adults=2)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)

        release_expired_holds()
        assert _departure(case).capacity_held == 0

    def test_it_returns_the_trip_to_draft(self) -> None:
        """The half `inventory` cannot do: §6.4 forbids it reaching `trip`."""
        case = scenario.build()
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)

        release_expired_holds()
        trip = _trip(case)
        assert trip.status == "DRAFT"
        assert trip.quote_expires_at is None

    def test_the_hold_reads_as_expired(self) -> None:
        case = scenario.build()
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)

        release_expired_holds()
        hold = django_apps.get_model("inventory", "InventoryHold").objects.get(trip_id=case.trip_id)
        assert hold.status == "EXPIRED"

    def test_it_reports_what_it_did(self) -> None:
        case = scenario.build()
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)

        assert release_expired_holds() == {"holds": 1, "trips": 1}

    def test_a_quiet_run_reports_nothing_rather_than_saying_nothing(self) -> None:
        """ "The sweeper ran" and "the sweeper found nothing" are different
        statements, and an operator reads the second."""
        assert release_expired_holds() == {"holds": 0, "trips": 0}

    def test_a_live_hold_is_left_alone(self) -> None:
        case = scenario.build(adults=2)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)

        release_expired_holds()
        assert _departure(case).capacity_held == 2
        assert _trip(case).status == "PRICED"

    def test_running_it_twice_returns_the_capacity_once(self) -> None:
        """§8.8: "Idempotent". `acks_late` means a worker lost mid-run
        redelivers, and a second pass that decremented again would oversell by
        exactly what it had already given back."""
        case = scenario.build(adults=2, capacity=12)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)

        release_expired_holds()
        release_expired_holds()
        assert _departure(case).capacity_held == 0

    def test_one_trip_with_several_expired_holds_moves_once(self) -> None:
        """Three dead holds on one trip are one trip to move. `expire_quote`
        is idempotent anyway; calling it three times would log three
        transitions where one happened."""
        case = scenario.build(adults=2, capacity=12)
        first = _departure(case)
        second = django_apps.get_model("inventory", "ActivityDeparture").objects.create(
            activity_id=case.activity_id,
            departs_at=case.departs_at + dt.timedelta(days=1),
            capacity_total=12,
        )
        item_model = django_apps.get_model("trip", "ItineraryItem")
        trip = _trip(case)
        item_model.objects.create(
            itinerary=trip.itinerary,
            item_type="ACTIVITY",
            day_number=3,
            sequence_no=1,
            title="Harbour Kayak Tour",
            starts_at=second.departs_at,
            ends_at=second.departs_at + dt.timedelta(minutes=180),
            activity_id=case.activity_id,
        )
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)

        assert release_expired_holds() == {"holds": 2, "trips": 1}
        first.refresh_from_db()
        second.refresh_from_db()
        assert (first.capacity_held, second.capacity_held) == (0, 0)

    def test_the_trip_can_be_quoted_again_afterwards(self) -> None:
        """What a tourist actually does: walk away, come back, ask again."""
        case = scenario.build(adults=2, capacity=2)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)
        release_expired_holds()

        again = services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert again.trip.status == "PRICED"
        assert _departure(case).capacity_held == 2


class TestTheReconciler:
    """§17.4, over `capacity_held` — ADR 0022 for why not `capacity_sold`."""

    def test_a_healthy_platform_reports_no_drift(self) -> None:
        case = scenario.build(adults=2)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        assert reconcile_inventory() == {"drifted": 0}

    def test_capacity_held_by_nothing_is_drift(self) -> None:
        """The leak this job exists to catch: capacity no tourist can buy and
        no sweeper will ever return, which ends as a boat with empty seats and
        a screen saying it is full."""
        case = scenario.build()
        django_apps.get_model("inventory", "ActivityDeparture").objects.filter(
            id=case.departure_id
        ).update(capacity_held=3)
        assert reconcile_inventory() == {"drifted": 1}

    def test_it_alerts_rather_than_repairing(self) -> None:
        """§38.5 targets zero oversell incidents *and* zero unresolved
        reconciliation exceptions. A job that quietly corrected the counter
        would meet the second by destroying the evidence for the first."""
        case = scenario.build()
        django_apps.get_model("inventory", "ActivityDeparture").objects.filter(
            id=case.departure_id
        ).update(capacity_held=3)
        reconcile_inventory()
        assert _departure(case).capacity_held == 3

    def test_a_swept_hold_leaves_no_drift_behind(self) -> None:
        """The end-to-end invariant: after the sweeper has run, the counters
        and the holds agree again."""
        case = scenario.build(adults=2)
        services.quote_trip(case.trip_public_id, tourist_id=case.tourist_id)
        _age_the_hold(case)
        release_expired_holds()
        assert reconcile_inventory() == {"drifted": 0}


class TestTheyAreScheduled:
    def test_the_sweeper_runs_every_sixty_seconds(self) -> None:
        """§8.8, verbatim: "Beat, every 60 s"."""
        from django_celery_beat.models import PeriodicTask

        row = PeriodicTask.objects.get(task="booking.release_expired_holds")
        assert row.interval is not None
        assert (row.interval.every, row.interval.period) == (60, "seconds")

    def test_the_reconciler_runs_nightly(self) -> None:
        from django_celery_beat.models import PeriodicTask

        row = PeriodicTask.objects.get(task="booking.reconcile_inventory")
        assert row.crontab is not None

    def test_both_names_are_ones_a_worker_can_resolve(self) -> None:
        """A `PeriodicTask` naming an unregistered task fires on schedule and
        is discarded every time — a log full of `NotRegistered` and a job an
        operator believes is running."""
        from django_celery_beat.models import PeriodicTask

        scheduled = set(
            PeriodicTask.objects.filter(task__startswith="booking.").values_list("task", flat=True)
        )
        assert scheduled == {release_expired_holds.name, reconcile_inventory.name}
