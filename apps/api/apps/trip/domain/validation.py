"""The itinerary validation rules — SRS §10.6, VR-01 to VR-17.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§10.6: validation runs on every generate and again inside quote. ERROR blocks
quoting; WARNING is advisory.

**The rules ask questions; they do not go looking for answers.** Every fact a
rule needs — is this listing active, was the attraction open at that hour, how
long is the drive — arrives on `ItemFacts`, resolved by the application layer
from catalogue and from the routing resolver. Two reasons, and the second is
the important one:

* A domain module may not perform I/O (§8.2 layer 3).
* Opening hours are `catalogue`'s to evaluate, in the destination's own zone
  (§15.2). A copy of that logic here would be a second implementation of a rule
  that already exists and is tested, and the two would drift. So the caller
  answers "was it open", and this module decides what that means.

**Three rules are not what a first reading of §10.6 suggests, and the SRS says
so itself in its v1.2 amendments.** They are implemented as amended:

* **VR-04** — "Every night in the trip range is covered by exactly one STAY
  anchor, or explicitly marked as 'own arrangement'. *Amended v1.2: 'own
  arrangement' is the normal case, since the Platform does not sell the room.*"
  So the ERROR is a night covered **twice**, not a night uncovered. An
  uncovered night is VR-16's warning, which is what makes §10.9's "day trip
  with no accommodation — supported" true.
* **VR-05** — "*Amended v1.2: the room occupancy half of this rule is deferred
  to v2 with room_type (ADR 0013); a stay anchor asserts no occupancy.*" Only
  the activity half is v1.
* **VR-11** — "*Deferred to v2 (ADR 0013).*" There is no `room_type` in the v1
  schema, so there is no minimum-nights requirement to satisfy. It is declared
  in `DEFERRED_RULES` rather than omitted, so its absence is a decision a
  reviewer can see rather than a gap they have to notice.

**VR-09 is half-built, and that is recorded rather than hidden.** "Every item's
provider and listing are active and not suspended". The listing half is checked
now. The provider half cannot be: `provider` is a Phase 1 skeleton, so nothing
can answer the question. `provider_is_active` is therefore `bool | None`, and
`None` means *unknown* rather than *fine* — the rule records the difference and
`test_domain_validation.py` asserts that an unknown provider does not silently
pass as an active one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo

from apps.trip.domain.findings import Finding, Severity, SuggestedAction
from apps.trip.domain.sequencing import Buffers, Kind, LocationKey

__all__ = [
    "Limits",
    "PartyFacts",
    "FlightFacts",
    "ItemFacts",
    "TripFacts",
    "DEFERRED_RULES",
    "ALL_RULES",
    "validate",
]

#: Rules the v1 schema cannot answer, and why. Declared rather than omitted:
#: the same pattern as `test_ports_registry.DELIBERATELY_UNREGISTERED`, so
#: re-adding one is an edit somebody makes in a file a reviewer reads.
DEFERRED_RULES: dict[str, str] = {
    "VR-11": (
        "Deferred to v2 with room_type (ADR 0013, and the SRS v1.2 amendment "
        "to §10.6). There is no room type in the v1 schema, so there is no "
        "minimum-nights requirement to satisfy. A stay anchor asserts no "
        "occupancy and books no room."
    ),
}

#: Every rule this module emits. Stated as a set so that a rule quietly
#: disappearing — the failure mode of a long if-chain — fails a test.
ALL_RULES: frozenset[str] = frozenset(
    {
        "VR-01",
        "VR-02",
        "VR-03",
        "VR-04",
        "VR-05",
        "VR-06",
        "VR-07",
        "VR-08",
        "VR-09",
        "VR-10",
        "VR-12",
        "VR-13",
        "VR-14",
        "VR-15",
        "VR-16",
        "VR-17",
    }
)


@dataclass(frozen=True, slots=True)
class Limits:
    """Appendix B thresholds, read from `system_setting` by the caller.

    NFR-M07: no business constant in code. The defaults match Appendix B and
    exist so a test can build one without naming every field.
    """

    items_per_day: int = 5
    travel_minutes_per_day: int = 240
    arrival_processing_minutes: int = 45
    #: VR-17's "within 3 hours of landing". §10.6 states it inline rather than
    #: as a named setting, so it is named here — a threshold a market might
    #: reasonably disagree with should not need a deployment to change.
    arrival_activity_grace_hours: int = 3


@dataclass(frozen=True, slots=True)
class PartyFacts:
    """§7.5.10's adults / children / infants.

    `size` counts adults and children and **excludes infants**, because
    §16.3's capacity is seats and a lap infant does not occupy one. The trip
    table stores counts and not ages, which is why VR-15 is deliberately
    conservative — see its rule below.
    """

    adults: int = 2
    children: int = 0
    infants: int = 0

    @property
    def size(self) -> int:
        return self.adults + self.children

    @property
    def includes_children(self) -> bool:
        return self.children > 0 or self.infants > 0


@dataclass(frozen=True, slots=True)
class FlightFacts:
    """§7.5's `trip_flight`, as the rules need it."""

    scheduled_at: datetime
    gateway_location: LocationKey
    #: §11.2: the tourist or driver may update the actual arrival, and the
    #: system re-times the transfer. When present it is the truth VR-07 works
    #: from; when absent the schedule is.
    actual_at: datetime | None = None

    @property
    def at(self) -> datetime:
        return self.actual_at or self.scheduled_at


@dataclass(frozen=True, slots=True)
class ItemFacts:
    """One item, with every fact the rules need already resolved.

    Wider than `PlannedItem` on purpose: sequencing needs times and places,
    validation needs the commercial and catalogue state behind them, and
    keeping them apart stops the sequencer growing fields it never reads.
    """

    item_id: int
    kind: Kind
    day_number: int
    title: str
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    start_location: LocationKey | None = None
    end_location: LocationKey | None = None

    #: VR-10.
    currency: str | None = None

    #: VR-05, VR-06, VR-15 — activity facts from catalogue.
    min_pax: int | None = None
    max_pax: int | None = None
    booking_cutoff_hours: int | None = None
    departs_at: datetime | None = None
    min_age: int | None = None

    #: VR-12. `None` means hours are not published (§15.2), which is not the
    #: same as closed and does not warn.
    is_open_at_scheduled_time: bool | None = None

    #: VR-09. `provider_is_active` is `None` while `provider` is a skeleton —
    #: unknown, not fine.
    listing_is_active: bool = True
    provider_is_active: bool | None = None

    #: §10.9: "Trip entirely without transfers (tourist self-drives) —
    #: supported; VR-03 is skipped between items marked own_transport = true".
    own_transport: bool = False

    #: VR-03 and VR-14. Present on a TRANSFER the planner produced.
    travel_seconds: int | None = None

    #: VR-07 / VR-08. A transfer bound to a flight leg.
    is_airport_arrival: bool = False
    is_airport_departure: bool = False

    #: VR-04 / VR-16: the nights a STAY anchor covers, as dates.
    covered_nights: tuple[date, ...] = ()


@dataclass(frozen=True, slots=True)
class TripFacts:
    start_date: date
    end_date: date
    timezone: str
    currency: str
    party: PartyFacts = field(default_factory=PartyFacts)
    arrival: FlightFacts | None = None
    departure: FlightFacts | None = None

    @property
    def nights(self) -> tuple[date, ...]:
        """The nights a trip contains.

        A trip from the 10th to the 15th has five nights, not six: the last
        date is a departure day, not a night. A day trip has none, which is
        why §10.9 can call a trip with no accommodation supported.
        """
        span = (self.end_date - self.start_date).days
        return tuple(self.start_date + timedelta(days=n) for n in range(span))

    def window(self) -> tuple[datetime, datetime]:
        """VR-01's range: the trip dates in local time, extended by flights.

        §10.6 says "extended by the arrival and departure flight times", and
        the extension only ever widens: a flight landing at 23:40 on the first
        night does not shorten the day, and an inbound flight that lands the
        day *before* the trip starts legitimately moves the boundary back.
        """
        zone = ZoneInfo(self.timezone)
        start = datetime.combine(self.start_date, time.min, tzinfo=zone)
        end = datetime.combine(self.end_date, time.max, tzinfo=zone)
        if self.arrival is not None:
            start = min(start, self.arrival.at)
        if self.departure is not None:
            end = max(end, self.departure.at)
        return start, end


def validate(
    items: Sequence[ItemFacts],
    *,
    trip: TripFacts,
    buffers: Buffers,
    limits: Limits,
) -> tuple[Finding, ...]:
    """Run VR-01 to VR-17 and return every finding, in rule order.

    Rule order rather than item order, because §24.14 groups the banner by
    rule and a tourist reading "three things are wrong" wants them grouped by
    what is wrong, not by which row happens to come first.
    """
    findings: list[Finding] = []
    for rule in (
        _vr01_within_the_trip_window,
        _vr02_no_overlaps,
        _vr03_reachable,
        _vr04_no_night_covered_twice,
        _vr05_party_fits_the_activity,
        _vr06_booking_cutoff,
        _vr07_arrival_transfer_not_too_early,
        _vr08_departure_transfer_not_too_late,
        _vr09_listings_are_active,
        _vr10_one_currency,
        _vr12_attraction_opening_hours,
        _vr13_items_per_day,
        _vr14_travel_minutes_per_day,
        _vr15_age_requirement,
        _vr16_nights_without_a_stay,
        _vr17_activity_too_soon_after_landing,
    ):
        findings.extend(rule(items, trip=trip, buffers=buffers, limits=limits))
    return tuple(findings)


# ---------------------------------------------------------------------------
# The rules. Each takes the same arguments so `validate` can iterate them, and
# each returns a list rather than yielding, so a rule that finds nothing is
# visibly a rule that ran.
# ---------------------------------------------------------------------------


def _timed(items: Sequence[ItemFacts]) -> list[ItemFacts]:
    return [i for i in items if i.starts_at is not None and i.ends_at is not None]


def _order(item: ItemFacts) -> tuple[datetime, int, int]:
    """§10.4's total order, reused so validation reads a day in the same
    sequence the planner wrote it. `_timed` has already excluded unscheduled
    items, so the assert is a claim about this module rather than a guard."""
    assert item.starts_at is not None
    return (item.starts_at, int(item.kind), item.item_id)


def _by_day(items: Sequence[ItemFacts]) -> dict[int, list[ItemFacts]]:
    days: dict[int, list[ItemFacts]] = {}
    for item in _timed(items):
        days.setdefault(item.day_number, []).append(item)
    for day_items in days.values():
        day_items.sort(key=_order)
    return days


def _vr01_within_the_trip_window(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    start, end = trip.window()
    out: list[Finding] = []
    for item in _timed(items):
        assert item.starts_at is not None and item.ends_at is not None
        if item.starts_at < start or item.ends_at > end:
            out.append(
                Finding(
                    code="VR-01",
                    severity=Severity.ERROR,
                    message=f"{item.title} falls outside the trip's dates.",
                    item_ids=(item.item_id,),
                    suggested_action=SuggestedAction.EDIT_TRIP_DATES,
                    context={"day_number": item.day_number},
                )
            )
    return out


def _vr02_no_overlaps(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "No two non-STAY items on the same day overlap in time."

    Stays are excluded because they are anchors that span the whole night and
    would overlap everything by construction (ADR 0013).
    """
    out: list[Finding] = []
    for day_items in _by_day(items).values():
        timed = [i for i in day_items if i.kind not in (Kind.STAY_CHECK_IN, Kind.STAY_CHECK_OUT)]
        for earlier, later in pairwise(timed):
            assert earlier.ends_at is not None and later.starts_at is not None
            if later.starts_at < earlier.ends_at:
                out.append(
                    Finding(
                        code="VR-02",
                        severity=Severity.ERROR,
                        message=f"{earlier.title} and {later.title} overlap.",
                        item_ids=(earlier.item_id, later.item_id),
                        suggested_action=SuggestedAction.RESCHEDULE_ITEM,
                        context={"day_number": earlier.day_number},
                    )
                )
    return out


def _vr03_reachable(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "gap between A.ends_at and B.starts_at >= travel time + buffer".

    **Applied to every adjacent pair, transfers included.** An earlier draft
    skipped any pair where one side was a TRANSFER, reasoning that the transfer
    already carried the travel. That was wrong in a way the tests caught: on a
    day the sequencer has actually built, *every* pair has a transfer on one
    side of it, so the rule fired on nothing at all. A rule that cannot fire on
    the shape its own planner produces is not a rule.

    The chain a sequenced day produces is `A -> transfer -> B`, and the check
    reads naturally across it. A and the transfer share a location, so the
    requirement between them is the buffer alone — which asserts that the leg
    does not have to set off before A has finished. The transfer and B share a
    location too, and the requirement there is B's own buffer, which is exactly
    what §10.4 line 14 subtracts when it times the leg. The transfer's driving
    time is accounted for by its occupying the hours between them.

    Where two items in different places have *no* transfer between them the
    travel time is unmeasured and reads as zero, so it is the buffer that
    catches the tight gap — and the item ids in the finding are what make it
    fixable.
    """
    out: list[Finding] = []
    for day_items in _by_day(items).values():
        for earlier, later in pairwise(day_items):
            if earlier.own_transport or later.own_transport:
                continue
            assert earlier.ends_at is not None and later.starts_at is not None

            same_place = (
                earlier.end_location is not None and earlier.end_location == later.start_location
            )
            travel = timedelta(0) if same_place else _travel_between(day_items, earlier, later)
            gap = later.starts_at - earlier.ends_at
            needed = travel + buffers.before(later.kind)
            if gap < needed:
                out.append(
                    Finding(
                        code="VR-03",
                        severity=Severity.ERROR,
                        message=(
                            f"There is not enough time to get from {earlier.title} "
                            f"to {later.title}."
                        ),
                        item_ids=(earlier.item_id, later.item_id),
                        suggested_action=SuggestedAction.ADD_TRANSFER,
                        context={
                            "day_number": earlier.day_number,
                            "gap_minutes": int(gap.total_seconds() // 60),
                            "required_minutes": int(needed.total_seconds() // 60),
                        },
                    )
                )
    return out


def _travel_between(
    day_items: Sequence[ItemFacts], earlier: ItemFacts, later: ItemFacts
) -> timedelta:
    """The measured drive between two items, if the planner put one there."""
    for candidate in day_items:
        if (
            candidate.kind is Kind.TRANSFER
            and candidate.start_location == earlier.end_location
            and candidate.end_location == later.start_location
            and candidate.travel_seconds is not None
        ):
            return timedelta(seconds=candidate.travel_seconds)
    return timedelta(0)


def _vr04_no_night_covered_twice(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "covered by exactly one STAY anchor, or explicitly marked as 'own
    arrangement'" — and v1.2 makes own arrangement the normal case.

    So the error is a night claimed twice, which is a real contradiction: two
    anchors say the tourist sleeps in two places, and the planner would route
    transfers to both. An *uncovered* night is VR-16's warning, and that is
    what makes §10.9's "day trip with no accommodation: supported" true.
    """
    seen: dict[date, list[int]] = {}
    for item in items:
        for night in item.covered_nights:
            seen.setdefault(night, []).append(item.item_id)

    return [
        Finding(
            code="VR-04",
            severity=Severity.ERROR,
            # `%-d` is glibc-only; the client renders from `context` anyway.
            message=f"{night:%d %B} is covered by more than one stay.",
            item_ids=tuple(ids),
            suggested_action=SuggestedAction.REMOVE_ITEM,
            context={"night": night.isoformat()},
        )
        for night, ids in sorted(seen.items())
        if len(ids) > 1
    ]


def _vr05_party_fits_the_activity(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """v1.2: the activity half only. A stay anchor asserts no occupancy."""
    size = trip.party.size
    out: list[Finding] = []
    for item in items:
        if item.kind is not Kind.ACTIVITY:
            continue
        below = item.min_pax is not None and size < item.min_pax
        above = item.max_pax is not None and size > item.max_pax
        if below or above:
            out.append(
                Finding(
                    code="VR-05",
                    severity=Severity.ERROR,
                    message=(
                        f"{item.title} takes between {item.min_pax} and "
                        f"{item.max_pax} people; your party is {size}."
                    ),
                    item_ids=(item.item_id,),
                    suggested_action=SuggestedAction.EDIT_PARTY,
                    context={"party_size": size, "min_pax": item.min_pax, "max_pax": item.max_pax},
                )
            )
    return out


def _vr06_booking_cutoff(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "Activity booked at or before its booking_cutoff_hours relative to
    departure" — §16.6: the cutoff exists because a provider cannot staff a
    last-minute booking.

    Evaluated against the item's own `starts_at`, which for an ACTIVITY is
    when the tourist plans to be there. An activity with no bound departure is
    not checked: there is nothing to be late for yet, and the constraint in
    `models.py` deliberately permits that draft state.
    """
    out: list[Finding] = []
    for item in items:
        if item.kind is not Kind.ACTIVITY:
            continue
        if item.departs_at is None or item.booking_cutoff_hours is None:
            continue
        latest = item.departs_at - timedelta(hours=item.booking_cutoff_hours)
        if item.starts_at is not None and item.starts_at > latest:
            out.append(
                Finding(
                    code="VR-06",
                    severity=Severity.ERROR,
                    message=(
                        f"{item.title} must be booked at least "
                        f"{item.booking_cutoff_hours} hours before it departs."
                    ),
                    item_ids=(item.item_id,),
                    suggested_action=SuggestedAction.RESCHEDULE_ITEM,
                    context={"booking_cutoff_hours": item.booking_cutoff_hours},
                )
            )
    return out


def _vr07_arrival_transfer_not_too_early(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "starts no earlier than the flight arrival plus
    buffer.arrival_processing_minutes (default 45)".

    The 45 minutes are immigration, baggage and customs. A pickup timed before
    them is a driver waiting while the meter of the tourist's anxiety runs,
    which §11.1 calls "the single highest-anxiety moment of the journey".
    """
    if trip.arrival is None:
        return []
    earliest = trip.arrival.at + timedelta(minutes=limits.arrival_processing_minutes)
    return [
        Finding(
            code="VR-07",
            severity=Severity.ERROR,
            message=(
                f"The airport pickup is scheduled before the flight has cleared "
                f"arrivals; it must be at least "
                f"{limits.arrival_processing_minutes} minutes after landing."
            ),
            item_ids=(item.item_id,),
            suggested_action=SuggestedAction.RESCHEDULE_ITEM,
            context={"earliest": earliest.isoformat()},
        )
        for item in items
        if item.is_airport_arrival and item.starts_at is not None and item.starts_at < earliest
    ]


def _vr08_departure_transfer_not_too_late(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "arrives at the gateway at least buffer.airport_departure_minutes
    before scheduled departure"."""
    if trip.departure is None:
        return []
    latest = trip.departure.scheduled_at - timedelta(minutes=buffers.airport_departure_minutes)
    return [
        Finding(
            code="VR-08",
            severity=Severity.ERROR,
            message=(
                f"The airport drop-off arrives too late; it must be at least "
                f"{buffers.airport_departure_minutes} minutes before departure."
            ),
            item_ids=(item.item_id,),
            suggested_action=SuggestedAction.RESCHEDULE_ITEM,
            context={"latest": latest.isoformat()},
        )
        for item in items
        if item.is_airport_departure and item.ends_at is not None and item.ends_at > latest
    ]


def _vr09_listings_are_active(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "Every item's provider and listing are active and not suspended".

    Half of this rule is real and half cannot be answered yet. `provider` is a
    Phase 1 skeleton, so `provider_is_active` is `None` for every item today —
    and `None` is treated as *unknown*, never as *active*. The distinction is
    what stops this rule silently becoming a listing-only check the day
    somebody wires a provider up and forgets it.
    """
    out: list[Finding] = []
    for item in items:
        if not item.listing_is_active:
            out.append(
                Finding(
                    code="VR-09",
                    severity=Severity.ERROR,
                    message=f"{item.title} is no longer available.",
                    item_ids=(item.item_id,),
                    suggested_action=SuggestedAction.REMOVE_ITEM,
                    context={"reason": "listing_inactive"},
                )
            )
        elif item.provider_is_active is False:
            out.append(
                Finding(
                    code="VR-09",
                    severity=Severity.ERROR,
                    message=f"{item.title} is provided by a supplier who is not currently active.",
                    item_ids=(item.item_id,),
                    suggested_action=SuggestedAction.REMOVE_ITEM,
                    context={"reason": "provider_inactive"},
                )
            )
    return out


def _vr10_one_currency(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "Currency is uniform across all items in the trip."

    §18.5 does not permit a mixed-currency total, and §7.5.10 locks the
    presentment currency at PRICED. An item priced in something else cannot be
    added into a subtotal without an exchange rate this module has no business
    knowing about.
    """
    offenders = [i for i in items if i.currency is not None and i.currency != trip.currency]
    if not offenders:
        return []
    return [
        Finding(
            code="VR-10",
            severity=Severity.ERROR,
            message=(
                f"Some items are priced in a different currency from the trip's "
                f"{trip.currency}."
            ),
            item_ids=tuple(i.item_id for i in offenders),
            suggested_action=SuggestedAction.CONTACT_SUPPORT,
            context={
                "trip_currency": trip.currency,
                "item_currencies": sorted({i.currency for i in offenders if i.currency}),
            },
        )
    ]


def _vr12_attraction_opening_hours(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "An attraction is scheduled outside its opening hours for that weekday."

    `is_open_at_scheduled_time is None` means the attraction has not published
    hours (§15.2), which is not the same as being closed and does not warn.
    Warning on unpublished hours would put a caution on most of the catalogue
    and teach tourists to ignore the banner.
    """
    return [
        Finding(
            code="VR-12",
            severity=Severity.WARNING,
            message=f"{item.title} may be closed at that time.",
            item_ids=(item.item_id,),
            suggested_action=SuggestedAction.RESCHEDULE_ITEM,
            context={"day_number": item.day_number},
        )
        for item in items
        if item.kind is Kind.ATTRACTION and item.is_open_at_scheduled_time is False
    ]


def _vr13_items_per_day(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "A day contains more than limit.items_per_day (default 5) timed items."

    Transfers are excluded from the count. They are the planner's own work,
    and counting them would warn a tourist about a day the planner made busy
    rather than one they did.
    """
    out: list[Finding] = []
    for day, day_items in sorted(_by_day(items).items()):
        counted = [i for i in day_items if i.kind is not Kind.TRANSFER]
        if len(counted) > limits.items_per_day:
            out.append(
                Finding(
                    code="VR-13",
                    severity=Severity.WARNING,
                    message=f"Day {day} has {len(counted)} things planned, which is a lot.",
                    item_ids=tuple(i.item_id for i in counted),
                    suggested_action=SuggestedAction.RESCHEDULE_ITEM,
                    context={"day_number": day, "item_count": len(counted)},
                )
            )
    return out


def _vr14_travel_minutes_per_day(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "Total travel time in a day exceeds limit.travel_minutes_per_day
    (default 240)"."""
    out: list[Finding] = []
    for day, day_items in sorted(_by_day(items).items()):
        seconds = sum(i.travel_seconds or 0 for i in day_items if i.kind is Kind.TRANSFER)
        minutes = seconds // 60
        if minutes > limits.travel_minutes_per_day:
            out.append(
                Finding(
                    code="VR-14",
                    severity=Severity.WARNING,
                    message=f"Day {day} involves about {minutes} minutes of travelling.",
                    item_ids=tuple(i.item_id for i in day_items if i.kind is Kind.TRANSFER),
                    suggested_action=SuggestedAction.RESCHEDULE_ITEM,
                    context={"day_number": day, "travel_minutes": minutes},
                )
            )
    return out


def _vr15_age_requirement(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "An activity has an age requirement and the party includes children
    below it."

    **Deliberately conservative.** `trip` stores counts, not ages (§7.5.10), so
    the platform cannot tell whether a particular child is below a particular
    minimum. Warning whenever an age-restricted activity meets a party with
    children is the honest reading of what is knowable: it is a WARNING, it
    asks the tourist to check, and it does not block. Inventing an age to
    compare against would be the fabrication this project has declined
    elsewhere.
    """
    if not trip.party.includes_children:
        return []
    return [
        Finding(
            code="VR-15",
            severity=Severity.WARNING,
            message=(
                f"{item.title} has a minimum age of {item.min_age}. "
                "Please check that everyone in your party qualifies."
            ),
            item_ids=(item.item_id,),
            suggested_action=SuggestedAction.NONE,
            context={"min_age": item.min_age},
        )
        for item in items
        if item.kind is Kind.ACTIVITY and item.min_age is not None
    ]


def _vr16_nights_without_a_stay(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "The trip has no accommodation for one or more nights."

    v1.2 keeps this and explains why: "a night with no stay anchor is a night
    whose transfers cannot be planned around it". A warning rather than an
    error, because §10.9 supports a trip with no accommodation at all — the
    tourist may simply have booked elsewhere.
    """
    covered = {night for item in items for night in item.covered_nights}
    uncovered = [night for night in trip.nights if night not in covered]
    if not uncovered:
        return []
    return [
        Finding(
            code="VR-16",
            severity=Severity.WARNING,
            message=(
                f"{len(uncovered)} night(s) have no accommodation recorded, so "
                "transfers cannot be planned around them."
            ),
            item_ids=(),
            suggested_action=SuggestedAction.ADD_STAY,
            context={"nights": [n.isoformat() for n in uncovered]},
        )
    ]


def _vr17_activity_too_soon_after_landing(
    items: Sequence[ItemFacts], *, trip: TripFacts, buffers: Buffers, limits: Limits
) -> list[Finding]:
    """ "Same-day arrival flight and an activity starting within 3 hours of
    landing."

    Not an error: a tourist who wants to go straight from the airport to a
    sunset cruise is allowed to. It is a warning because a delayed flight
    turns it into a missed activity nobody will refund.
    """
    if trip.arrival is None:
        return []
    landing = trip.arrival.at
    grace = landing + timedelta(hours=limits.arrival_activity_grace_hours)
    return [
        Finding(
            code="VR-17",
            severity=Severity.WARNING,
            message=(
                f"{item.title} starts within "
                f"{limits.arrival_activity_grace_hours} hours of your flight landing."
            ),
            item_ids=(item.item_id,),
            suggested_action=SuggestedAction.RESCHEDULE_ITEM,
            context={"landing": landing.isoformat()},
        )
        for item in items
        if item.kind is Kind.ACTIVITY
        and item.starts_at is not None
        and landing <= item.starts_at < grace
    ]
