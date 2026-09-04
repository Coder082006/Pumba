"""inventory module — SRS §6.4.

    Owns:       activity_departure, inventory_hold
    Interface:  check_availability(), hold(), commit(), release()
    Depends on: catalogue
    Layer:      L2

Application layer (SRS §8.2 layer 2). The only module boundary — §6.5 rule 1.

**One transaction per use case, and `hold()` is the one that matters.** §17.3's
critical section is `repositories.lock_departures` followed by an assertion and
an update; this module is what wraps it in `transaction.atomic`, decides what
the assertion is, and turns a failure into the `409` §9.4.5 specifies. Splitting
those across two calls would let a caller take the lock and then do something
else with it.

**Party rules come from `catalogue`, through its service interface.** §16.3's
"bookable iff" needs `min_pax`, `max_pax` and `booking_cutoff_hours`, and those
are the activity's rather than the departure's. `inventory -> catalogue` is a
legal edge (§6.4) and `catalogue.activity_facts` already answers in one query;
copying the three columns onto `activity_departure` would be a second home for
a number a provider edits.

**Nothing here reads a clock or a setting.** `now` and `ttl_minutes` arrive as
arguments, so the boundary cases are testable exactly and hard rule 5's
"thresholds are `system_setting` rows" is satisfied at the caller rather than
scattered through the module. §8.4 also forbids anything that could block
inside the transaction, and a settings read is exactly the kind of thing that
grows a cache round trip later.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction

from apps.catalogue import services as catalogue
from apps.common.errors import ConflictError, InventoryUnavailableError, ValidationError
from apps.inventory import repositories as repo
from apps.inventory.domain.capacity import (
    PartyRules,
    Unbookable,
    reduction_conflicts,
    sellable,
    why_not_bookable,
)
from apps.inventory.domain.lifecycle import HOLD_MACHINE, HoldState, returns_capacity
from apps.inventory.dto import (
    AvailabilityBasis,
    DepartureDTO,
    DepartureEdit,
    DriftDTO,
    HoldDTO,
    HoldRequest,
    ProviderDepartureDTO,
)
from apps.inventory.models import ActivityDeparture, HoldStatus, InventoryHold

__all__ = [
    "materialise_departures",
    "list_departures",
    "provider_calendar",
    "CapacityReductionError",
    "edit_departures",
    "check_availability",
    "resolve_departure_at",
    "hold",
    "commit",
    "release",
    "release_expired",
    "reconcile",
]

#: §17.5: "in batches of 200".
SWEEP_BATCH = 200

#: Schedules read per page by the materialiser. Not a business constant — it
#: trades memory against round trips and nothing observable depends on it.
SCHEDULE_PAGE = 500


def materialise_departures(*, start: date, horizon_days: int) -> int:
    """§16.2's nightly expansion of schedules into sellable departures.

    Returns the number of departures created.

    **Idempotent by the unique constraint, not by a lookup.**
    `UNIQUE(activity_id, departs_at)` already says a departure happens once,
    and `ignore_conflicts=True` lets the database enforce that instead of a
    read-then-write that races itself when two runs overlap. §8.8 requires
    idempotence of this job; this is the cheapest honest way to get it.

    **An existing departure is never touched.** A schedule whose capacity a
    provider raised from twelve to sixteen produces sixteen-seat departures
    from tomorrow and leaves next Tuesday's twelve-seat one exactly as it is —
    which is §26.4's rule that *"changes to price, cancellation policy or
    capacity take effect only for new bookings, never for existing ones"*,
    obtained for free rather than implemented separately.

    **The wall time is resolved in the destination's zone.** §16.2's
    `start_time` is local — "Monday to Saturday at 08:30" means half past eight
    where the boat leaves from, and resolving it in UTC puts every departure
    an hour out for half the year in any destination that observes DST.
    Zanzibar does not, which is exactly why this must be right for reasons
    nobody here will ever see: §4.2 forbids the code from knowing which
    destination it is serving.

    A local time that does not exist — the hour a spring-forward skips — is
    resolved by `zoneinfo`'s `fold` rules rather than raised on. There is no
    good answer for "08:30 on a day with no 08:30", the alternative is a
    missing departure nobody is told about, and the provider can cancel or
    move the one date affected.
    """
    created = 0
    after = 0
    while True:
        page = catalogue.active_schedules(
            start=start, horizon_days=horizon_days, after=after, limit=SCHEDULE_PAGE
        )
        if not page:
            return created
        after = max(fact.schedule_id for fact in page)

        wanted: dict[int, set[datetime]] = {}
        capacity: dict[tuple[int, datetime], tuple[int, int]] = {}
        for fact in page:
            zone = ZoneInfo(fact.timezone)
            for day in fact.local_dates:
                instant = datetime.combine(day, fact.start_time, tzinfo=zone)
                wanted.setdefault(fact.activity_id, set()).add(instant)
                capacity[(fact.activity_id, instant)] = (fact.capacity, fact.schedule_id)

        # What already exists, in one query per page. Without this the job
        # would re-send every row it has ever created, every night: after the
        # first run almost all of them are duplicates, and `ignore_conflicts`
        # discards them *after* they have crossed the wire and been parsed.
        existing: set[tuple[int, datetime]] = set(
            ActivityDeparture.objects.filter(activity_id__in=wanted).values_list(
                "activity_id", "departs_at"
            )
        )

        rows = [
            ActivityDeparture(
                activity_id=activity_id,
                schedule_id=schedule_id,
                departs_at=instant,
                capacity_total=seats,
            )
            for (activity_id, instant), (seats, schedule_id) in capacity.items()
            if (activity_id, instant) not in existing
        ]
        if rows:
            # `ignore_conflicts` stays as the backstop, not the mechanism. The
            # filter above is what makes the count meaningful and the job
            # cheap; the flag is what keeps two overlapping runs — a nightly
            # beat and an operator's manual catch-up — from colliding on the
            # unique constraint.
            ActivityDeparture.objects.bulk_create(rows, ignore_conflicts=True)
            created += len(rows)


def _rules(activity_id: int) -> PartyRules:
    facts = catalogue.activity_facts([activity_id]).get(activity_id)
    if facts is None:
        # The SQL foreign key makes an orphaned departure impossible, so this
        # is a soft-deleted or filtered activity rather than a missing row.
        raise ValidationError("That activity is not available.")
    return PartyRules(
        min_pax=facts.min_pax,
        max_pax=facts.max_pax,
        booking_cutoff_hours=facts.booking_cutoff_hours,
    )


def _dto(
    row: ActivityDeparture,
    *,
    basis: AvailabilityBasis,
    rules: PartyRules | None = None,
    pax: int | None = None,
    now: datetime | None = None,
) -> DepartureDTO:
    facts = repo.facts_of(row)
    unbookable: Unbookable | None = None
    if rules is not None and pax is not None and now is not None:
        unbookable = why_not_bookable(facts, rules, pax=pax, now=now)
    return DepartureDTO(
        public_id=row.public_id,
        departs_at=row.departs_at,
        status=row.status,
        remaining=sellable(facts),
        basis=basis,
        price_override=row.price_override,
        unbookable=unbookable,
    )


def list_departures(
    activity_id: int,
    *,
    since: datetime,
    until: datetime,
    now: datetime,
    pax: int | None = None,
) -> list[DepartureDTO]:
    """`GET /activities/{id}/departures?from&to&pax` — SD-06, §24.10.

    **Indicative, and says so.** §17.1 I3 and §8.10 both draw the line here:
    this figure may be stale by the time it is read and may never confirm a
    booking. It is honest about that in the payload rather than in a comment.

    Cancelled departures are returned rather than filtered out. §24.10 shows a
    calendar, and a date that silently vanishes reads as a bug to somebody who
    was looking at it a minute ago; a date marked cancelled reads as weather.
    """
    rules = _rules(activity_id) if pax is not None else None
    rows = ActivityDeparture.objects.filter(
        activity_id=activity_id, departs_at__gte=since, departs_at__lte=until
    ).order_by("departs_at")
    return [
        _dto(row, basis=AvailabilityBasis.INDICATIVE, rules=rules, pax=pax, now=now) for row in rows
    ]


class CapacityReductionError(ConflictError):
    """BR-023 — the reduction would strand seats that are already committed.

    §26.5 requires the refusal to name *"the specific dates"*, so `details`
    carries one entry per offending departure with the instant, what was asked
    for and what is already held or sold. A provider given only "some dates
    conflict" has to find them by hand across a month grid.
    """

    code = "CAPACITY_BELOW_COMMITTED"
    default_message = (
        "Capacity cannot be reduced below the seats already held or sold on these dates."
    )


def provider_calendar(
    activity_id: int, *, since: datetime, until: datetime
) -> list[ProviderDepartureDTO]:
    """§26.5's month grid: every departure in a window, with all three counters.

    Unlocked, like `list_departures` and for the same reason — this is a screen,
    not a decision. What differs is who is reading it, and therefore what it may
    show: an operator deciding whether to cancel Tuesday's boat needs to know
    that four of its eight taken seats are holds rather than sales, because one
    of those numbers will resolve itself in twenty minutes and the other will
    not.

    Ordered by `departs_at`, which is how a calendar is read. The edit path
    below orders by primary key instead, for deadlock avoidance (§8.4) — the
    two orderings are for different readers and neither is the other's default.
    """
    return [
        ProviderDepartureDTO(
            public_id=row.public_id,
            departs_at=row.departs_at,
            status=row.status,
            capacity_total=row.capacity_total,
            capacity_held=row.capacity_held,
            capacity_sold=row.capacity_sold,
            remaining=sellable(repo.facts_of(row)),
            price_override=row.price_override,
        )
        for row in ActivityDeparture.objects.filter(
            activity_id=activity_id, departs_at__gte=since, departs_at__lte=until
        ).order_by("departs_at")
    ]


@transaction.atomic
def edit_departures(activity_id: int, edit: DepartureEdit, *, timezone_name: str) -> int:
    """§26.5's bulk edit, with BR-023 enforced under the lock that makes it true.

    Returns how many departures were changed.

    **The check runs after the lock, not before it.** BR-023 is a statement
    about `capacity_held` and `capacity_sold`, and both move under other
    people's transactions — a quote holding seats is doing exactly that. A
    reduction validated against an unlocked read is validated against a number
    that was true once, which is the same mistake §17.1 I2 exists to name. So
    the rows are locked first, the conflict set is computed from the locked
    values, and the write happens without releasing them.

    **Every offending date is reported, not the first.** §26.5 says the dates
    plural, and a provider fixing a month one rejection at a time would need a
    month of submissions to discover the six that block it.

    **The weekday mask is applied in the destination's zone.** A provider
    closing "every Sunday" means Sunday where the boat is; the same instant is
    Saturday for anybody west of the Atlantic. `timezone_name` arrives as an
    argument because resolving it needs the geography tables, which `inventory`
    may not read (contract `private-catalogue`).

    **Lowering capacity does not cancel anything, and cancelling does not
    return seats.** They are separate operations because they are separate
    decisions: a smaller boat still sails, and a cancelled departure still has
    passengers who booked it. Releasing their money is §14.6's refund path in
    Phase 8, and doing it silently here would be the worst possible place for
    it to happen.
    """
    zone = ZoneInfo(timezone_name)
    rows = repo.lock_departures_between(
        activity_id,
        since=datetime.combine(edit.since, time.min, tzinfo=zone),
        until=datetime.combine(edit.until, time.max, tzinfo=zone),
    )
    if edit.weekday_mask is not None:
        rows = [
            row
            for row in rows
            if edit.weekday_mask & (1 << row.departs_at.astimezone(zone).date().weekday())
        ]

    if edit.capacity_total is not None:
        conflicts = reduction_conflicts(
            (repo.facts_of(row) for row in rows), capacity_total=edit.capacity_total
        )
        if conflicts:
            raise CapacityReductionError(
                details=[
                    {
                        "departs_at": conflict.departs_at.isoformat(),
                        "requested": conflict.requested,
                        "committed": conflict.committed,
                    }
                    for conflict in conflicts
                ]
            )

    return repo.apply_departure_edit(
        rows,
        capacity_total=edit.capacity_total,
        price_override=edit.price_override,
        clear_price=edit.clear_price,
        status=edit.status,
    )


def check_availability(
    requests: Sequence[HoldRequest], *, now: datetime
) -> dict[int, Unbookable | None]:
    """§6.4's `check_availability()`. Indicative — no lock is taken.

    For a screen that wants to warn before the tourist commits, and for
    `booking` to report every unavailable item at once rather than the first.
    §17.4 is explicit that this layer is *"indicative only; never
    authoritative"*: the answer here is what was true a moment ago, and `hold`
    asks again under lock.
    """
    if not requests:
        return {}
    rows = {row.id: row for row in repo.departures_by_id(r.departure_id for r in requests)}
    rules = _rules_for(rows.values())

    out: dict[int, Unbookable | None] = {}
    for request in requests:
        row = rows.get(request.departure_id)
        if row is None:
            raise ValidationError("That departure is not available.")
        out[request.departure_id] = why_not_bookable(
            repo.facts_of(row), rules[row.activity_id], pax=request.pax, now=now
        )
    return out


def _rules_for(rows: Iterable[ActivityDeparture]) -> dict[int, PartyRules]:
    """Party rules for every activity behind a set of departures, in one query."""
    activity_ids = {row.activity_id for row in rows}
    facts = catalogue.activity_facts(sorted(activity_ids))
    missing = activity_ids - set(facts)
    if missing:
        raise ValidationError("That activity is not available.")
    return {
        activity_id: PartyRules(
            min_pax=fact.min_pax,
            max_pax=fact.max_pax,
            booking_cutoff_hours=fact.booking_cutoff_hours,
        )
        for activity_id, fact in facts.items()
    }


def resolve_departure_at(activity_id: int, *, departs_at: datetime) -> int | None:
    """The departure an itinerary item's start instant names, or None.

    `UNIQUE(activity_id, departs_at)` (§7.5.9) is what makes this a lookup
    rather than a search, and it is how `booking` binds
    `itinerary_item.activity_departure_id` at quote time without `trip` ever
    reaching into this module. The tourist chose the instant from a list of
    real departures; this is the other half of that round trip.
    """
    row = ActivityDeparture.objects.filter(
        activity_id=activity_id, departs_at=departs_at
    ).values_list("id", flat=True)
    return next(iter(row), None)


def _alternatives(
    row: ActivityDeparture, *, pax: int, now: datetime, limit: int = 3
) -> list[dict[str, object]]:
    """Other departures of the same activity that would work.

    §9.4.5 requires the 409 to carry *"where the catalogue offers them,
    alternative departures"*. Offering them is the difference between an error
    that ends a booking and one that redirects it.
    """
    rules = _rules(row.activity_id)
    candidates = (
        ActivityDeparture.objects.filter(activity_id=row.activity_id, departs_at__gt=now)
        .exclude(id=row.id)
        .order_by("departs_at")[: limit * 4]
    )
    out: list[dict[str, object]] = []
    for candidate in candidates:
        if why_not_bookable(repo.facts_of(candidate), rules, pax=pax, now=now) is None:
            out.append(
                {
                    "public_id": str(candidate.public_id),
                    "departs_at": candidate.departs_at.isoformat(),
                    "remaining": sellable(repo.facts_of(candidate)),
                }
            )
        if len(out) == limit:
            break
    return out


@transaction.atomic
def hold(
    *,
    trip_id: int,
    requests: Sequence[HoldRequest],
    ttl_minutes: int,
    now: datetime,
) -> list[HoldDTO]:
    """§17.3's critical section. The routine the oversell guarantee rests on.

    Sequence, all inside one transaction:

        1. release this trip's prior holds (§9.4.5 step 2)
        2. lock every departure, ascending by primary key (§8.4)
        3. assert `sellable >= pax` for each, from the locked read
        4. `capacity_held += pax`, `version += 1`
        5. insert a hold row per request

    **Step 1 is not an optimisation.** A re-quote that took new holds without
    releasing the old ones would double-count the same trip's own seats against
    itself, and the second quote of an unchanged itinerary would fail as sold
    out — by the tourist's own hand.

    Raises `InventoryUnavailableError` naming *every* unavailable departure and
    its alternatives, not the first. §9.4.5's `details` array is a list because
    a tourist told about one sold-out activity at a time, over three attempts,
    gives up before the third.
    """
    if not requests:
        return []

    # Same-trip release first, so its seats are available to re-take below.
    for prior in repo.live_holds_of_trip(trip_id, for_update=True):
        _finish(prior, state=HoldState.RELEASED)

    wanted: dict[int, int] = {}
    for request in requests:
        if request.pax <= 0:
            raise ValidationError("A hold must be for at least one person.")
        # Two items on the same departure are one claim on it. Summing rather
        # than locking twice is what keeps step 3's assertion true of the
        # whole request instead of each half separately.
        wanted[request.departure_id] = wanted.get(request.departure_id, 0) + request.pax

    locked = repo.lock_departures(sorted(wanted))
    if len(locked) != len(wanted):
        raise ValidationError("That departure is not available.")

    rules = _rules_for(locked)
    unavailable: list[dict[str, object]] = []
    for row in locked:
        pax = wanted[row.id]
        reason = why_not_bookable(repo.facts_of(row), rules[row.activity_id], pax=pax, now=now)
        if reason is not None:
            unavailable.append(
                {
                    "departure": str(row.public_id),
                    "reason": reason.value,
                    "alternatives": _alternatives(row, pax=pax, now=now),
                }
            )

    if unavailable:
        raise InventoryUnavailableError(details=unavailable)

    expires_at = now + timedelta(minutes=ttl_minutes)
    held: list[HoldDTO] = []
    for row in locked:
        repo.add_held(row.id, quantity=wanted[row.id])
    for request in requests:
        created = repo.create_hold(
            trip_id=trip_id,
            departure_id=request.departure_id,
            quantity=request.pax,
            expires_at=expires_at,
        )
        held.append(_hold_dto(created))
    return held


def _hold_dto(row: InventoryHold) -> HoldDTO:
    return HoldDTO(
        hold_token=row.hold_token,
        quantity=row.quantity,
        expires_at=row.expires_at,
        status=row.status,
    )


def _finish(row: InventoryHold, *, state: HoldState) -> None:
    """Move one hold to a terminal state and correct its counter.

    The machine validates the edge (§17.2) and `returns_capacity` decides the
    arithmetic, so neither is restated at the three call sites that need them.
    """
    HOLD_MACHINE.transition(HoldState(row.status), state)
    if returns_capacity(state):
        repo.release_held(row.resource_id, quantity=row.quantity)
    else:
        repo.move_held_to_sold(row.resource_id, quantity=row.quantity)
    repo.finish_hold(row, state=state)


@transaction.atomic
def commit(*, trip_id: int, now: datetime) -> int:
    """§20.8, step 9: `*_held -= qty ; *_sold += qty`; hold → COMMITTED.

    Called from the confirmation routine on payment capture, which is Phase 7.
    It is built and tested now because §37.5 names *"the concurrency-safe hold
    and commit routines"* as this phase's feature, and because a commit written
    later, against holds taken by code that had shipped months earlier, is how
    the two halves stop agreeing about what a hold means.

    **An expired hold is refused** — BR-026, and §17.1 I5's defensive re-check.
    The counter row is locked first so the check and the move are one act; a
    hold that expired between them would otherwise sell a seat the sweeper had
    already given back.
    """
    live = repo.live_holds_of_trip(trip_id, for_update=True)
    if not live:
        return 0
    repo.lock_departures(sorted({row.resource_id for row in live}))

    expired = [row for row in live if not row.is_live(now=now)]
    if expired:
        raise InventoryUnavailableError(
            "The hold on this trip expired before payment completed.",
            code="HOLD_EXPIRED",
            details=[{"hold": str(row.hold_token)} for row in expired],
        )

    for row in live:
        _finish(row, state=HoldState.COMMITTED)
    return len(live)


@transaction.atomic
def release(*, trip_id: int) -> int:
    """Give a trip's held capacity back — §9.4.5 step 2, §8.9's PaymentFailed.

    Idempotent: a trip with no live holds releases nothing and says so with a
    zero rather than an error. §8.8 requires that of every job, and the two
    callers most likely to repeat this are a retrying task and a tourist
    pressing a button twice.
    """
    live = repo.live_holds_of_trip(trip_id, for_update=True)
    if not live:
        return 0
    repo.lock_departures(sorted({row.resource_id for row in live}))
    for row in live:
        _finish(row, state=HoldState.RELEASED)
    return len(live)


def release_expired(*, now: datetime, limit: int = SWEEP_BATCH) -> list[int]:
    """§17.5's sweeper, over this module's rows. Returns the trips affected.

    Returns rather than acts on the trips, because moving a trip back to DRAFT
    is `trip`'s to do and `inventory` may not reach it (§6.4, ADR 0022). The
    Beat task in `booking` is what joins the two halves.

    **One transaction per hold, not one for the batch.** A batch-wide
    transaction would hold locks on up to two hundred departures for the length
    of the sweep, which is exactly the contention a quote is trying to get
    through; and a single bad row would roll back the other hundred and
    ninety-nine, which then wait another sixty seconds.
    """
    affected: list[int] = []
    for candidate in repo.expired_holds(now=now, limit=limit):
        with transaction.atomic():
            # Re-read under lock: two sweepers overlapping would otherwise both
            # act on the same row, and the second would decrement a counter
            # for capacity the first had already returned.
            row = repo.lock_hold(candidate.id)
            if row is None or row.status != HoldStatus.HELD:
                continue
            # `expires_at` is re-read too, not only `status`. §9.4.6 extends a
            # hold to the payment window when a basket is confirmed, so a hold
            # this batch selected sixty seconds ago may since have been given
            # more time — and sweeping it would release capacity out from
            # under a payment that is in flight.
            if row.expires_at > now:
                continue
            repo.lock_departures([row.resource_id])
            _finish(row, state=HoldState.EXPIRED)
            affected.append(row.trip_id)
    return affected


def reconcile(*, limit: int = 500) -> list[DriftDTO]:
    """§17.4's reconciliation, over the half of the system that exists.

    The SRS defines it as *"compares Σ confirmed bookings against `*_sold`"*.
    There are no bookings until Phase 7 and nothing can move `capacity_sold`
    until §20.8's confirmation routine exists, so a job written to that letter
    would compare zero against zero, pass every night, and be indistinguishable
    from a working checker on the day it stopped being one (ADR 0022).

    What it checks instead is the same class of drift over the half Phase 5
    owns: `capacity_held` against the live holds that justify it. The
    `capacity_sold` half arrives with the routine that first moves it.

    Read without locks, deliberately. A reconciliation that locked every
    departure it examined would contend with the quotes it exists to watch, and
    a figure that was true a moment ago is enough to raise an alert with — the
    investigation re-reads.
    """
    rows = list(ActivityDeparture.objects.exclude(capacity_held=0).order_by("id")[:limit])
    if not rows:
        return []
    live = repo.held_by_departure([row.id for row in rows])
    return [
        DriftDTO(
            departure_public_id=row.public_id,
            capacity_held=row.capacity_held,
            held_by_live_holds=live.get(row.id, 0),
        )
        for row in rows
        if live.get(row.id, 0) != row.capacity_held
    ]
