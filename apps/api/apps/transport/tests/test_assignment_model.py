"""The two dispatch tables, and the four promises the database itself makes —
§7.6, §11.5, §11.7, ADR 0029.

Every assertion here is about a constraint, because a constraint is the only
part of dispatch that holds when two requests arrive at once. The eligibility
filter and the offer service check the same things first; those checks produce
a good error message, and these produce the guarantee.

Two of them are named acceptance tests. TC-084 is
`assignment_no_overlapping_work` — and it is asserted against the constraint
rather than against the service that also checks, because the service check is
a read followed by a write and TC-083 is precisely the case where that gap
matters. TC-083's other half is `offer_one_live_per_assignment`.

Nothing here constructs a `booking` or a `driver`. Both belong to modules
`transport` may not see, so those columns are bare integers by design
(ADR 0012) and plausible integers here are the contract, not laziness.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from django.db import IntegrityError, transaction

from apps.transport.models import (
    AssignmentStatus,
    DriverAssignment,
    DriverOffer,
    OfferStatus,
)

pytestmark = pytest.mark.django_db

NOON = dt.datetime(2027, 4, 1, 12, 0, tzinfo=dt.UTC)
HOUR = dt.timedelta(hours=1)

_bookings = iter(range(9000, 9999))


def an_assignment(**overrides: Any) -> DriverAssignment:
    values: dict[str, Any] = {
        "booking_id": next(_bookings),
        "starts_at": NOON,
        "ends_at": NOON + HOUR,
        "status": AssignmentStatus.PENDING,
    }
    values.update(overrides)
    return DriverAssignment.objects.create(**values)


def an_assigned(driver_id: int = 77, **overrides: Any) -> DriverAssignment:
    """A driver is holding this one — the state that blocks their diary."""
    values: dict[str, Any] = {
        "driver_id": driver_id,
        "vehicle_id": driver_id * 10,
        "status": AssignmentStatus.ASSIGNED,
    }
    values.update(overrides)
    return an_assignment(**values)


def an_offer(assignment: DriverAssignment | None = None, **overrides: Any) -> DriverOffer:
    values: dict[str, Any] = {
        "assignment": assignment or an_assignment(),
        "driver_id": 77,
        "vehicle_id": 770,
        "rank": 1,
        "score": Decimal("0.81250"),
        "expires_at": NOON - HOUR,
    }
    values.update(overrides)
    return DriverOffer.objects.create(**values)


class TestTheWindow:
    def test_an_assignment_starts_pending_with_nobody_on_it(self) -> None:
        """§11.7: created PENDING at booking confirmation, before anybody has
        been asked."""
        assignment = an_assignment()

        assert assignment.status == AssignmentStatus.PENDING
        assert assignment.driver_id is None
        assert assignment.pickup_pin_hash is None

    def test_a_window_that_runs_backwards_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            an_assignment(starts_at=NOON, ends_at=NOON - HOUR)

    def test_one_transfer_has_one_live_assignment(self) -> None:
        """R2's `1 : 0..1`. Two would mean two drivers arriving for one
        tourist, each believing the job is theirs."""
        first = an_assignment()

        with pytest.raises(IntegrityError), transaction.atomic():
            an_assignment(booking_id=first.booking_id)

    def test_a_driver_arrives_with_a_vehicle_or_with_neither(self) -> None:
        """§11.4 discloses the car to the tourist before they travel. A driver
        with no vehicle recorded is a disclosure that cannot be made."""
        with pytest.raises(IntegrityError), transaction.atomic():
            an_assignment(driver_id=5, vehicle_id=None, status=AssignmentStatus.ASSIGNED)


class TestTc084OverlappingWork:
    def test_a_driver_may_not_hold_two_overlapping_assignments(self) -> None:
        """TC-084, and §7.6's constraint verbatim. Asserted here against the
        database rather than against the service, because the service checks
        by reading first and this is the case where that gap matters."""
        an_assigned(driver_id=77, starts_at=NOON, ends_at=NOON + 2 * HOUR)

        with pytest.raises(IntegrityError), transaction.atomic():
            an_assigned(driver_id=77, starts_at=NOON + HOUR, ends_at=NOON + 3 * HOUR)

    def test_back_to_back_work_is_allowed(self) -> None:
        """The range is half-open. A driver whose job ends at 14:00 is free at
        14:00 — refusing that would be refusing how a transfer driver earns."""
        an_assigned(driver_id=77, starts_at=NOON, ends_at=NOON + HOUR)

        assert an_assigned(driver_id=77, starts_at=NOON + HOUR, ends_at=NOON + 2 * HOUR).pk

    def test_two_different_drivers_may_work_the_same_hour(self) -> None:
        an_assigned(driver_id=77)

        assert an_assigned(driver_id=78).pk

    def test_a_finished_job_stops_blocking_the_diary(self) -> None:
        """Partial on the live statuses. Without that a busy week would
        permanently retire a driver from every hour they had ever worked."""
        an_assigned(driver_id=77, status=AssignmentStatus.COMPLETED)

        assert an_assigned(driver_id=77).pk

    def test_a_cancelled_job_stops_blocking_it_too(self) -> None:
        an_assigned(driver_id=77, status=AssignmentStatus.CANCELLED)

        assert an_assigned(driver_id=77).pk

    def test_an_unassigned_window_blocks_nobody(self) -> None:
        """Two PENDING assignments overlap constantly — every transfer leaving
        the airport at nine does. The constraint is keyed on the driver, and
        `NULL <> NULL` in an exclusion constraint is what makes that work."""
        an_assignment(starts_at=NOON, ends_at=NOON + HOUR)

        assert an_assignment(starts_at=NOON, ends_at=NOON + HOUR).pk

    def test_a_driver_in_progress_is_still_blocked(self) -> None:
        """EN_ROUTE, ARRIVED and STARTED are as live as ASSIGNED: a driver
        halfway through a transfer cannot start another."""
        an_assigned(driver_id=77, status=AssignmentStatus.STARTED)

        with pytest.raises(IntegrityError), transaction.atomic():
            an_assigned(driver_id=77, starts_at=NOON + dt.timedelta(minutes=30))


class TestTc083OneOfferAtATime:
    def test_an_assignment_has_one_live_offer(self) -> None:
        """TC-083's other half. §11.5 offers to `candidate[i]`, singular, and
        advances; two open offers would let two drivers both be told the job is
        theirs before either replied."""
        offer = an_offer()

        with pytest.raises(IntegrityError), transaction.atomic():
            an_offer(assignment=offer.assignment, driver_id=78, vehicle_id=780, rank=2)

    def test_the_next_candidate_may_be_offered_once_the_first_is_done(self) -> None:
        offer = an_offer()
        offer.status = OfferStatus.DECLINED
        offer.save(update_fields=["status"])

        assert an_offer(assignment=offer.assignment, driver_id=78, vehicle_id=780, rank=2).pk

    def test_a_driver_is_asked_about_a_job_once(self) -> None:
        """Re-offering after a decline is how a dispatcher loop with an
        off-by-one quietly harasses somebody."""
        offer = an_offer()
        offer.status = OfferStatus.DECLINED
        offer.save(update_fields=["status"])

        with pytest.raises(IntegrityError), transaction.atomic():
            an_offer(assignment=offer.assignment, driver_id=77, rank=2)

    def test_two_assignments_may_each_have_a_live_offer(self) -> None:
        an_offer()

        assert an_offer(driver_id=78, vehicle_id=780).pk


class TestWhatAnOfferRecords:
    def test_an_offer_keeps_its_own_arithmetic(self) -> None:
        """§11.6: a provider dispute is about one driver's offer, and it must
        be answerable with the exact computation — which has to outlive
        whatever retention the audit log has."""
        offer = an_offer(
            score_components={"proximity": "0.31", "rating": "0.96", "score": "0.8125"}
        )
        offer.refresh_from_db()

        assert offer.score == Decimal("0.81250")
        assert offer.score_components["proximity"] == "0.31"

    def test_a_score_outside_zero_to_one_is_refused(self) -> None:
        """Every component §11.6 defines is a fraction and the weights sum to
        one, so a score above one is arithmetic that has gone wrong — and a
        stored one would silently outrank every honest candidate."""
        with pytest.raises(IntegrityError), transaction.atomic():
            an_offer(score=Decimal("1.50000"))

    def test_a_rank_below_one_is_refused(self) -> None:
        """Rank is a place in §11.6's list, and lists start at one."""
        with pytest.raises(IntegrityError), transaction.atomic():
            an_offer(rank=0)

    def test_an_unknown_status_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            an_offer(status="MAYBE")
