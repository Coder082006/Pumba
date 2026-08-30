"""The day-sequencing algorithm — SRS §10.4.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§10.1 asks for something unusual and states why: the engine is "deterministic:
the same inputs, the same catalogue state and the same tariff configuration
always produce the same itinerary and the same total. It performs no
optimisation search and no learning." §10.4 then writes the algorithm out in
twenty-one numbered lines "so that two implementations produce identical
output". This module is one of those two implementations, and the line numbers
in the comments below refer to it.

**Nothing here fetches anything, and that is the design rather than an
accident.** Line 12 of §10.4 calls `travel_time(origin, target)`. Here that is
a function the caller supplies. Three things follow, and all three are worth
having:

* The algorithm is testable with no database, no network, no port and no fake.
  Every case below is exercised against a travel-time function a test wrote,
  which is the only way to check what the sequencer does with a 90-minute gap
  and a 100-minute drive.
* ADR 0019's rule stays enforceable. The caller decides whether a duration came
  from a provider, the nightly matrix or a haversine estimate, and the quality
  travels with it into the transfer this module emits. The domain cannot
  launder it, because the domain never chooses it.
* §8.2's layer 3 forbids I/O here anyway.

**Locations are opaque.** The algorithm compares them (line 11, `origin !=
target`) and hands them to `travel_time`. It never needs a coordinate, so it
does not have one — geometry lives in the application layer with the catalogue
rows it comes from. A `LocationKey` is any hashable the caller can resolve
again; in practice a string like `"accommodation:41"` or `"point:39.19,-6.16"`.

**Flexible placement is deterministic, which §10.4 leaves open.** Line 17 says
"the largest remaining gap that can contain (travel_in + visit_minutes +
travel_out)". Two gaps can tie, and two flexible items can compete for one gap,
and §10.1 requires the same answer every time regardless. So: flexible items
are placed in tie-break-rank then id order, and among equally large gaps the
earliest wins. Both choices are arbitrary in themselves and neither is
arbitrary in effect — what matters is that they are fixed and written down.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import IntEnum
from itertools import pairwise

from apps.trip.domain.findings import Finding, Severity, SuggestedAction

__all__ = [
    "Kind",
    "LocationKey",
    "TravelEstimate",
    "TravelTime",
    "Buffers",
    "PlannedItem",
    "SequenceResult",
    "NO_SLOT_FOR_ATTRACTION",
    "sequence_day",
    "sequence_trip",
]

#: An opaque, hashable handle for somewhere the planner can route to or from.
LocationKey = str

NO_SLOT_FOR_ATTRACTION = "NO_SLOT_FOR_ATTRACTION"


class Kind(IntEnum):
    """Item kinds, valued by §10.4's tie-break rank.

    §10.4: "Tie-break rank for identical starts_at: TRANSFER (0) < STAY
    check-out (1) < ACTIVITY (2) < ATTRACTION (3) < STAY check-in (4) <
    FREE_TIME (5). This guarantees a total order and satisfies principle A7."

    The rank *is* the value, so sorting cannot pick up a different order than
    the one specified — a separate rank table beside the enum is a second
    place for the ordering to live, and A7 is about there being one.

    A STAY appears twice because a stay spans nights: on the day it begins it
    is a check-in and sorts late, and on the day it ends it is a check-out and
    sorts early. The application layer expands one `itinerary_item` row into
    whichever of the two anchors fall on the day being sequenced.
    """

    TRANSFER = 0
    STAY_CHECK_OUT = 1
    ACTIVITY = 2
    ATTRACTION = 3
    STAY_CHECK_IN = 4
    FREE_TIME = 5


#: §10.4 line 5: what may be moved. Everything else has a time somebody else
#: chose — a departure, a flight, a check-in hour — and the planner works
#: around it rather than with it.
FLEXIBLE_KINDS = frozenset({Kind.ATTRACTION, Kind.FREE_TIME})


@dataclass(frozen=True, slots=True)
class TravelEstimate:
    """What `travel_time` returns: a duration, a distance, and its provenance.

    `quality` is a plain string here rather than the model's `EstimateQuality`,
    because a domain module may not import Django. It is passed through
    untouched — this module never inspects it, and never sets it.
    """

    seconds: int
    metres: int
    quality: str

    def __post_init__(self) -> None:
        if self.seconds < 0 or self.metres < 0:
            raise ValueError("a travel estimate cannot be negative")
        if not self.quality:
            raise ValueError("a travel estimate must say where it came from — ADR 0019, §12.6")


#: §10.4 line 12. Supplied by the caller; see the module docstring.
TravelTime = Callable[[LocationKey, LocationKey], TravelEstimate]


@dataclass(frozen=True, slots=True)
class Buffers:
    """§10.4: "buffer_before is configuration, not code".

    Read from `system_setting` by the caller (NFR-M07) and handed in, so this
    module holds no business constant. The defaults here match Appendix B and
    exist only so a test can construct one without naming every field; the
    application layer always passes explicit values.
    """

    activity_minutes: int = 15
    airport_departure_minutes: int = 180
    check_in_minutes: int = 0

    def before(self, kind: Kind, *, is_airport_departure: bool = False) -> timedelta:
        """How much slack the planner leaves before an item starts.

        An airport departure is the one case where the buffer is enormous and
        the consequence of getting it wrong is a missed flight, so it is a
        separate flag rather than a kind: a transfer *to* a departure gateway
        is an ACTIVITY-shaped item as far as ordering goes and a three-hour
        problem as far as timing goes.
        """
        if is_airport_departure:
            return timedelta(minutes=self.airport_departure_minutes)
        if kind is Kind.ACTIVITY:
            return timedelta(minutes=self.activity_minutes)
        if kind is Kind.STAY_CHECK_IN:
            return timedelta(minutes=self.check_in_minutes)
        return timedelta(0)


@dataclass(frozen=True, slots=True)
class PlannedItem:
    """One item as the sequencer sees it.

    Not an ORM row and deliberately not shaped like one: it carries what §10.4
    reads and nothing else. The application layer maps `itinerary_item` onto
    this and back, which is also what keeps a `TRANSFER` the sequencer invented
    distinguishable from one that was already there — an invented one has
    `item_id < 0`.
    """

    item_id: int
    kind: Kind
    title: str
    day_number: int

    #: `None` for a flexible item, which is precisely what makes it flexible.
    starts_at: datetime | None = None
    ends_at: datetime | None = None

    #: Where the item begins and ends. Equal for everything except a transfer.
    #: `None` where the item has no place — FREE_TIME at the hotel, say — in
    #: which case no transfer is ever inserted around it.
    start_location: LocationKey | None = None
    end_location: LocationKey | None = None

    #: §10.4 line 18, for flexible items: how long it takes to do the thing.
    visit_minutes: int = 0

    #: §10.3: a locked item is never rewritten. The sequencer may still insert
    #: transfers *around* one, which is the whole point of §10.3's example —
    #: adding day 4 to a confirmed trip must not disturb days 1 to 3.
    is_locked: bool = False

    #: Set only on a transfer this module inserted.
    distance_m: int | None = None
    travel_seconds: int | None = None
    estimate_quality: str | None = None
    sequence_no: int = 0

    #: A transfer whose target is a departure gateway. Carried on the item so
    #: the buffer decision is made from data rather than from a name.
    is_airport_departure: bool = False

    @property
    def is_flexible(self) -> bool:
        return self.starts_at is None

    @property
    def is_inserted(self) -> bool:
        """A transfer this module created, rather than one already stored."""
        return self.item_id < 0


@dataclass(frozen=True, slots=True)
class SequenceResult:
    items: tuple[PlannedItem, ...]
    findings: tuple[Finding, ...]

    @property
    def unscheduled(self) -> tuple[PlannedItem, ...]:
        """Flexible items no gap could hold. §10.4 line 19 leaves them in the
        itinerary rather than dropping them, so the tourist can see what did
        not fit and move something."""
        return tuple(item for item in self.items if item.is_flexible)


def _total_order(item: PlannedItem) -> tuple[datetime, int, int]:
    """§10.4 lines 6-7, verbatim: starts_at, then rank, then id.

    `assert` rather than a guard: `_fixed` has already filtered on
    `starts_at is None`, so a None here would be a bug in this module.
    """
    assert item.starts_at is not None
    return (item.starts_at, int(item.kind), item.item_id)


def _next_inserted_id(used: Iterable[int]) -> int:
    """Negative, descending, so inserted transfers are stably ordered among
    themselves and never collide with a stored row's id."""
    lowest = min([*used, 0])
    return lowest - 1


def sequence_day(
    items: Sequence[PlannedItem],
    *,
    travel_time: TravelTime,
    buffers: Buffers,
) -> SequenceResult:
    """§10.4 lines 2-20, for one day.

    Returns every item that belongs to the day, timed and renumbered, plus
    whatever findings the placement produced. Items are returned even when
    they could not be placed — line 19 is explicit that an unplaceable
    attraction stays in the itinerary and is reported, because silently
    dropping something the tourist chose is worse than telling them it does
    not fit.
    """
    fixed = sorted((i for i in items if not i.is_flexible), key=_total_order)
    flexible = sorted(
        (i for i in items if i.is_flexible),
        key=lambda i: (int(i.kind), i.item_id),
    )

    findings: list[Finding] = []
    inserted: list[PlannedItem] = []
    used_ids = [i.item_id for i in items]

    # -- lines 8-15: a transfer between adjacent items in different places ---
    for earlier, later in pairwise(fixed):
        origin = earlier.end_location
        target = later.start_location
        if origin is None or target is None or origin == target:
            continue

        estimate = travel_time(origin, target)
        assert later.starts_at is not None
        ends_at = later.starts_at - buffers.before(
            later.kind, is_airport_departure=later.is_airport_departure
        )
        starts_at = ends_at - timedelta(seconds=estimate.seconds)

        item_id = _next_inserted_id(used_ids)
        used_ids.append(item_id)
        inserted.append(
            PlannedItem(
                item_id=item_id,
                kind=Kind.TRANSFER,
                title=f"{earlier.title} to {later.title}",
                day_number=later.day_number,
                starts_at=starts_at,
                ends_at=ends_at,
                start_location=origin,
                end_location=target,
                distance_m=estimate.metres,
                travel_seconds=estimate.seconds,
                estimate_quality=estimate.quality,
                is_airport_departure=later.is_airport_departure,
            )
        )

    timed = sorted([*fixed, *inserted], key=_total_order)

    # -- lines 16-19: flexible items into the gaps ---------------------------
    placed_flexible: list[PlannedItem] = []
    still_unplaced: list[PlannedItem] = []
    for candidate in flexible:
        slot = _largest_gap_that_fits(timed, candidate, travel_time=travel_time)
        if slot is None:
            findings.append(
                Finding(
                    code=NO_SLOT_FOR_ATTRACTION,
                    severity=Severity.WARNING,
                    message=(
                        f"{candidate.title} could not be fitted into day "
                        f"{candidate.day_number}; there is no gap long enough."
                    ),
                    item_ids=(candidate.item_id,),
                    suggested_action=SuggestedAction.RESCHEDULE_ITEM,
                    context={
                        "day_number": candidate.day_number,
                        "required_minutes": candidate.visit_minutes,
                    },
                )
            )
            still_unplaced.append(candidate)
            continue
        placed = replace(candidate, starts_at=slot[0], ends_at=slot[1])
        placed_flexible.append(placed)
        timed = sorted([*timed, placed], key=_total_order)

    # -- line 20: renumber from 1 in ascending starts_at order ---------------
    numbered = tuple(
        replace(item, sequence_no=position)
        for position, item in enumerate(sorted(timed, key=_total_order), start=1)
    )
    return SequenceResult(items=(*numbered, *still_unplaced), findings=tuple(findings))


def _largest_gap_that_fits(
    timed: Sequence[PlannedItem],
    candidate: PlannedItem,
    *,
    travel_time: TravelTime,
) -> tuple[datetime, datetime] | None:
    """§10.4 lines 17-18.

    "the largest remaining gap that can contain (travel_in + visit_minutes +
    travel_out)". A gap exists between two consecutive timed items; a day with
    fewer than two of them has nowhere bounded to put a flexible item, and the
    answer is that it does not fit rather than that it fits anywhere. That is
    deliberate: an attraction placed in an unbounded stretch of a day has no
    relationship to anything, and §24.14 renders it as though the planner had
    decided something.

    Ties are broken by earliest, so the placement is reproducible (§10.1).
    """
    if len(timed) < 2:
        return None

    visit = timedelta(minutes=candidate.visit_minutes)
    best: tuple[timedelta, datetime, datetime] | None = None

    for earlier, later in pairwise(timed):
        assert earlier.ends_at is not None and later.starts_at is not None
        gap = later.starts_at - earlier.ends_at
        if gap <= timedelta(0):
            continue

        travel_in = _travel(earlier.end_location, candidate.start_location, travel_time)
        travel_out = _travel(candidate.end_location, later.start_location, travel_time)
        needed = travel_in + visit + travel_out
        if gap < needed:
            continue

        if best is None or gap > best[0]:
            starts_at = earlier.ends_at + travel_in
            best = (gap, starts_at, starts_at + visit)

    if best is None:
        return None
    return best[1], best[2]


def _travel(
    origin: LocationKey | None, target: LocationKey | None, travel_time: TravelTime
) -> timedelta:
    if origin is None or target is None or origin == target:
        return timedelta(0)
    return timedelta(seconds=travel_time(origin, target).seconds)


def sequence_trip(
    items: Sequence[PlannedItem],
    *,
    day_numbers: Sequence[int],
    travel_time: TravelTime,
    buffers: Buffers,
) -> SequenceResult:
    """§10.4 lines 1-2 and 21: every day of the trip, in order.

    `day_numbers` is passed in rather than derived from the items, because
    §10.4 line 1 builds the day list from the *trip dates*. A trip whose middle
    day has nothing planned still has that day, and an item sitting on a day
    outside the range is a VR-01 error rather than a day this function should
    invent.
    """
    known = set(day_numbers)
    all_items: list[PlannedItem] = []
    all_findings: list[Finding] = []

    for day in day_numbers:
        result = sequence_day(
            [i for i in items if i.day_number == day],
            travel_time=travel_time,
            buffers=buffers,
        )
        all_items.extend(result.items)
        all_findings.extend(result.findings)

    # Items outside the trip's days are returned untouched. Discarding them
    # here would hide the very thing VR-01 exists to report.
    all_items.extend(i for i in items if i.day_number not in known)

    return SequenceResult(items=tuple(all_items), findings=tuple(all_findings))
