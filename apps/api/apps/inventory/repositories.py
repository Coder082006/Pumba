"""inventory module — SRS §6.4.

Data-access layer (SRS §8.2 layer 4). All ORM writes, and one of them is the
most safety-critical routine in the system.

**§17.3's critical section, rewritten for departures.** The SRS gives the
routine against `room_availability`, and amends it in prose rather than in
code:

    Amended v1.2. V1 holds inventory for activity departures only. The routine
    below is written against room_availability, which does not exist in the v1
    schema; read activity_departure, departs_at and capacity_held for
    room_availability, stay_date and rooms_held. The lock discipline, the
    ascending primary-key ordering, the application assertion and the CHECK
    backstop are unchanged.

Substituted, it is:

    BEGIN;
    SELECT id, capacity_total, capacity_held, capacity_sold, version
      FROM activity_departure
     WHERE id = ANY(:ids)
     ORDER BY id                  -- ascending PK order: deadlock avoidance
       FOR UPDATE;
    -- application asserts sellable >= pax for every departure
    UPDATE activity_departure
       SET capacity_held = capacity_held + :pax, version = version + 1
     WHERE id = ANY(:ids);
    INSERT INTO inventory_hold (...) VALUES (...);
    COMMIT;

Four disciplines, each of which is load-bearing on its own.

**The lock comes before the read that decides.** §17.1 I2: *"every capacity
change happens inside a transaction that holds a row lock on the counter row"*.
A `sellable` computed before `FOR UPDATE` is a number that was true once.

**Ascending primary-key order** (§8.4). Two quotes touching the same two
departures in opposite orders deadlock; ordering by `id` is what makes that
impossible rather than unlikely.

**The `CHECK` is the backstop, not the check.** §17.3: *"If it ever fires, that
is a defect and the transaction aborts rather than overselling."* The
application assertion above it is what produces a `409` a client can act on; a
routine relying on the constraint alone would be correct and would report an
`IntegrityError` to a tourist.

**No external call inside the transaction** (§8.4, hard rule 11). Nothing here
does I/O beyond the database, and the settings these functions need arrive as
arguments so that not even a cache read can appear between the lock and the
commit.

Nothing here checks ownership. The trip a hold belongs to was proved by the
caller — `booking` — before any of this ran.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime

from django.db import transaction
from django.db.models import F, QuerySet, Sum

from apps.inventory.domain.capacity import Departure, DepartureState
from apps.inventory.domain.lifecycle import HoldState
from apps.inventory.models import ActivityDeparture, HeldResource, HoldStatus, InventoryHold

__all__ = [
    "departures_by_id",
    "lock_departures",
    "facts_of",
    "add_held",
    "move_held_to_sold",
    "release_held",
    "create_hold",
    "live_holds_of_trip",
    "expired_holds",
    "lock_hold",
    "finish_hold",
    "held_by_departure",
]


def departures_by_id(ids: Iterable[int]) -> QuerySet[ActivityDeparture]:
    """Unlocked, for search and display. §17.1 I3: indicative only."""
    return ActivityDeparture.objects.filter(id__in=list(ids))


def lock_departures(ids: Sequence[int]) -> list[ActivityDeparture]:
    """The `SELECT … FOR UPDATE` of §17.3, in ascending primary-key order.

    The sort is on the database side rather than in Python, because it is the
    order rows are *locked* in that avoids the deadlock, and that is decided by
    the query plan and not by what the caller does with the result afterwards.

    Asserts a transaction is open. `select_for_update` outside one raises in
    Django anyway, but a lock that is released at the end of the statement
    rather than the end of the transaction is exactly the failure this module
    exists to prevent, so it is worth naming rather than inheriting.
    """
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError(
            "lock_departures must run inside a transaction: a lock released at "
            "the end of the statement protects nothing (SRS §17.1 I2)."
        )
    return list(
        ActivityDeparture.objects.select_for_update().filter(id__in=list(ids)).order_by("id")
    )


def facts_of(row: ActivityDeparture) -> Departure:
    """The counter row as the domain's value object."""
    return Departure(
        departs_at=row.departs_at,
        capacity_total=row.capacity_total,
        capacity_held=row.capacity_held,
        capacity_sold=row.capacity_sold,
        status=DepartureState(row.status),
    )


def add_held(departure_id: int, *, quantity: int) -> None:
    """`capacity_held += quantity`, and bump the version.

    `F()` rather than a read-modify-write in Python: the arithmetic happens in
    the database, on the row the caller has already locked, so the value that
    is incremented is the value that was read under that lock.

    The version bump is §7.7's optimistic-locking column. It has no reader in
    v1 — the pessimistic lock above is what actually serialises this — but a
    counter that moved without it would make `version` a number that means
    "some changes" rather than "all changes", which is worse than not having it.
    """
    ActivityDeparture.objects.filter(id=departure_id).update(
        capacity_held=F("capacity_held") + quantity, version=F("version") + 1
    )


def move_held_to_sold(departure_id: int, *, quantity: int) -> None:
    """§20.8: `*_held -= qty ; *_sold += qty`.

    One statement, because the two halves are one fact. Split into two updates
    they would be briefly inconsistent, and the window is exactly where the
    `CHECK` would fire on a row that is not actually oversold.
    """
    ActivityDeparture.objects.filter(id=departure_id).update(
        capacity_held=F("capacity_held") - quantity,
        capacity_sold=F("capacity_sold") + quantity,
        version=F("version") + 1,
    )


def release_held(departure_id: int, *, quantity: int) -> None:
    """`capacity_held -= quantity`. The RELEASED and EXPIRED arithmetic."""
    ActivityDeparture.objects.filter(id=departure_id).update(
        capacity_held=F("capacity_held") - quantity, version=F("version") + 1
    )


def create_hold(
    *, trip_id: int, departure_id: int, quantity: int, expires_at: datetime
) -> InventoryHold:
    """The `INSERT INTO inventory_hold` of §17.3.

    `full_clean` is deliberately not called: this row's every constraint is a
    database `CHECK`, the caller has already asserted capacity under lock, and
    a Python-side validation pass here would add a second place for the rules
    to live without adding a rule.
    """
    return InventoryHold.objects.create(
        trip_id=trip_id,
        resource_type=HeldResource.ACTIVITY_DEPARTURE,
        resource_id=departure_id,
        quantity=quantity,
        expires_at=expires_at,
    )


def live_holds_of_trip(trip_id: int, *, for_update: bool = False) -> list[InventoryHold]:
    """§9.4.5 step 2: "release any prior holds belonging to this trip".

    Ordered by `resource_id` so that the counter rows a re-quote releases are
    reached in the same ascending order the next `lock_departures` will use.
    Two trips releasing and re-taking overlapping departures otherwise have a
    lock order that depends on insertion history.
    """
    rows = InventoryHold.objects.filter(trip_id=trip_id, status=HoldStatus.HELD)
    if for_update:
        rows = rows.select_for_update()
    return list(rows.order_by("resource_id", "id"))


def expired_holds(*, now: datetime, limit: int) -> list[InventoryHold]:
    """§17.5's sweeper query: `status = 'HELD' AND expires_at < now()`.

    `limit` is the batch size — the SRS says 200 — and the ordering is oldest
    first so a backlog drains in the order it accumulated rather than starving
    the holds that have been dead longest.

    Only the ids matter to the caller, which re-reads each row under lock. A
    sweeper that acted on the rows it selected here would be acting on a
    snapshot taken before any lock was held.
    """
    return list(
        InventoryHold.objects.filter(status=HoldStatus.HELD, expires_at__lt=now).order_by(
            "expires_at", "id"
        )[:limit]
    )


def lock_hold(hold_id: int) -> InventoryHold | None:
    """Re-read one hold under lock, for the sweeper and the commit routine.

    Returns None if it is gone, which cannot happen — nothing deletes a hold —
    but the sweeper is the one caller that reads without a lock first, so the
    narrow window in which two sweepers pick up the same batch has to end
    somewhere, and it ends here.
    """
    return InventoryHold.objects.select_for_update().filter(id=hold_id).first()


def finish_hold(hold: InventoryHold, *, state: HoldState) -> None:
    """Move a hold to one of §17.2's three ends.

    Takes the already-validated target rather than validating here: the
    machine is `domain.lifecycle`'s, and a repository that also checked the
    transition would be the second place the rule lives.
    """
    hold.status = HoldStatus(state.value)
    hold.version = hold.version + 1
    hold.save(update_fields=["status", "version"])


def held_by_departure(departure_ids: Sequence[int]) -> dict[int, int]:
    """Σ live HELD quantity per departure — §17.4's reconciliation input.

    Grouped in the database. Summing in Python would need every hold row for
    every departure in memory, and the job runs over the whole catalogue.
    """
    rows = (
        InventoryHold.objects.filter(
            status=HoldStatus.HELD,
            resource_type=HeldResource.ACTIVITY_DEPARTURE,
            resource_id__in=list(departure_ids),
        )
        .values("resource_id")
        .annotate(total=Sum("quantity"))
    )
    return {int(row["resource_id"]): int(row["total"]) for row in rows}
