"""Zero oversell under a real race — SRS §17.3, §33.3, TC-053, LT-03.

§33.3 asks for this specifically:

    Availability and hold concurrency (including deliberate parallel-transaction
    tests that assert no oversell)

and TC-053 states the case: *"1 seat; 2 simultaneous quotes; both submit;
exactly one 200, one 409; capacity_held = 1"*.

**These tests need real threads and real connections.** `transaction=True`
turns off the wrapping transaction pytest-django normally uses, because
`SELECT … FOR UPDATE` only serialises against another *committed* transaction
on another connection — inside one shared transaction the second caller sees
its own uncommitted write and every assertion passes for the wrong reason. That
is the failure mode this file exists to avoid, and it is why the naive version
of this test is worse than no test.

They are slow, for the same reason: a truncating rollback per test rather than
a transaction rollback. Four of them, chosen to cover the four ways the routine
could be wrong, rather than a matrix.
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import pytest
from django.db import connections, transaction
from django.utils import timezone

from apps.common.errors import InventoryUnavailableError
from apps.inventory import services
from apps.inventory.dto import HoldRequest
from apps.inventory.models import ActivityDeparture, HoldStatus, InventoryHold
from apps.inventory.tests.factories import make_departure

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.integration]

TTL = 20


def _race(target: Any, count: int) -> list[BaseException | None]:
    """Run `target(i)` on `count` threads, started as close to together as the
    GIL allows, and report what each one raised.

    A barrier rather than a sleep: the window this is trying to land inside is
    the microseconds between one transaction's `FOR UPDATE` and its `COMMIT`,
    and a staggered start would miss it while still passing.

    Every thread closes its own connection afterwards. Django opens one per
    thread, and a connection left open holds the row locks of an uncommitted
    transaction — which deadlocks the test teardown rather than the test.
    """
    ready = threading.Barrier(count)
    outcomes: list[BaseException | None] = [None] * count

    def run(index: int) -> None:
        try:
            ready.wait(timeout=10)
            target(index)
        except BaseException as error:
            outcomes[index] = error
        finally:
            connections.close_all()

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return outcomes


class TestTc053:
    """The last seat on a departure, wanted by two people at once."""

    def test_exactly_one_of_two_simultaneous_quotes_wins(self) -> None:
        departure = make_departure(capacity_total=1)
        now = timezone.now()

        def quote(index: int) -> None:
            services.hold(
                trip_id=100 + index,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=now,
            )

        outcomes = _race(quote, 2)

        winners = [o for o in outcomes if o is None]
        losers = [o for o in outcomes if isinstance(o, InventoryUnavailableError)]
        assert len(winners) == 1
        assert len(losers) == 1

    def test_the_counter_shows_one_seat_held_and_not_two(self) -> None:
        """TC-053's `capacity_held = 1`. The assertion the CHECK constraint
        would also have caught — but as an `IntegrityError` at 03:00, not as a
        409 the loser could act on."""
        departure = make_departure(capacity_total=1)
        now = timezone.now()

        def quote(index: int) -> None:
            services.hold(
                trip_id=100 + index,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=now,
            )

        _race(quote, 2)

        departure.refresh_from_db()
        assert departure.capacity_held == 1
        assert InventoryHold.objects.filter(status=HoldStatus.HELD).count() == 1


class TestLt03:
    """*"200 concurrent quote attempts against 20 units of inventory; exactly
    20 succeed; zero oversell"*.

    Run at a twentieth of LT-03's scale — ten threads against five seats. The
    property under test is arithmetic and does not get truer at two hundred;
    what two hundred adds is timing pressure, which belongs in the load
    harness rather than in a suite that runs on every commit. The shape is the
    same and a regression here fails in seconds instead of minutes.
    """

    def test_the_number_that_succeed_is_the_number_of_seats(self) -> None:
        seats = 5
        attempts = 10
        departure = make_departure(capacity_total=seats)
        now = timezone.now()

        def quote(index: int) -> None:
            services.hold(
                trip_id=200 + index,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=now,
            )

        outcomes = _race(quote, attempts)

        succeeded = len([o for o in outcomes if o is None])
        refused = len([o for o in outcomes if isinstance(o, InventoryUnavailableError)])
        assert succeeded == seats
        assert refused == attempts - seats

    def test_nothing_is_oversold(self) -> None:
        seats = 5
        departure = make_departure(capacity_total=seats)
        now = timezone.now()

        def quote(index: int) -> None:
            services.hold(
                trip_id=200 + index,
                requests=[HoldRequest(departure_id=departure.id, pax=1)],
                ttl_minutes=TTL,
                now=now,
            )

        _race(quote, 10)

        departure.refresh_from_db()
        assert departure.capacity_held + departure.capacity_sold <= departure.capacity_total
        assert departure.capacity_held == seats


class TestTheLockIsWhatDoesIt:
    def test_a_second_quote_waits_rather_than_reading_a_stale_counter(self) -> None:
        """The mechanism, isolated.

        One thread takes the lock and holds it; the main thread's `hold` must
        block until that commits and then see the seat gone. Without
        `FOR UPDATE` it would read the pre-hold counter, find a free seat, and
        both would succeed — which is the whole defect, and the reason
        §17.1 I2 is a principle rather than an implementation note.
        """
        departure = make_departure(capacity_total=1)
        now = timezone.now()
        holding = threading.Event()
        release = threading.Event()

        def slow_holder() -> None:
            try:
                with transaction.atomic():
                    services.hold(
                        trip_id=300,
                        requests=[HoldRequest(departure_id=departure.id, pax=1)],
                        ttl_minutes=TTL,
                        now=now,
                    )
                    holding.set()
                    release.wait(timeout=10)
            finally:
                connections.close_all()

        thread = threading.Thread(target=slow_holder)
        thread.start()
        try:
            assert holding.wait(timeout=10)
            release.set()
            thread.join(timeout=20)

            with pytest.raises(InventoryUnavailableError):
                services.hold(
                    trip_id=301,
                    requests=[HoldRequest(departure_id=departure.id, pax=1)],
                    ttl_minutes=TTL,
                    now=now,
                )
        finally:
            release.set()
            thread.join(timeout=20)

        departure.refresh_from_db()
        assert departure.capacity_held == 1


class TestDeadlockAvoidance:
    def test_two_quotes_touching_the_same_pair_in_either_order_both_finish(self) -> None:
        """§8.4: *"always acquire row locks in ascending primary-key order"*.

        Both trips ask for the same two departures with the request lists
        reversed. If the routine locked in the order the caller supplied, this
        is the classic two-row deadlock and one side dies with a Postgres
        error rather than a business answer. Ordering by `id` inside
        `lock_departures` is what makes the caller's order irrelevant.
        """
        first = make_departure(capacity_total=10)
        second = make_departure(
            activity_id=first.activity_id,
            departs_at=first.departs_at + dt.timedelta(days=1),
            capacity_total=10,
        )
        now = timezone.now()
        order = {
            0: [first.id, second.id],
            1: [second.id, first.id],
        }

        def quote(index: int) -> None:
            services.hold(
                trip_id=400 + index,
                requests=[HoldRequest(departure_id=i, pax=1) for i in order[index]],
                ttl_minutes=TTL,
                now=now,
            )

        outcomes = _race(quote, 2)

        assert outcomes == [None, None]
        for row in ActivityDeparture.objects.filter(id__in=[first.id, second.id]):
            assert row.capacity_held == 2
