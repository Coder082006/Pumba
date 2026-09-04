"""Activity schedule recurrence — SRS §16.2.

    activity_schedule is a recurring rule: weekday mask, start time, capacity,
    validity window.
    activity_departure is a concrete, sellable instance at an exact instant
    with its own capacity counters.

The nightly `materialise_activity_departures` job that turns the first into the
second is **Phase 5**. What belongs here now is the meaning of the recurrence,
because Phase 3's §27.8 console creates schedules and has to validate and
display them, and because the materialiser will be far easier to get right
against a mask whose semantics are already pinned down and tested.

Two decisions worth stating.

**The mask is Monday-first**, matching `date.weekday()` — bit 0 is Monday, bit 6
is Sunday. Sunday-first is equally defensible and is what several calendar
libraries use, which is exactly why it is written down: a mask read with the
wrong convention shifts every departure by a day and produces a schedule that
looks plausible in the console and is wrong in the boat.

**Occurrence is evaluated against a local date, not an instant.** A schedule
says "Monday to Saturday at 08:30" in the destination's own time. Converting to
an instant belongs to materialisation, which needs the destination's timezone
and its DST history; this module answers the calendar question and refuses to
pretend it can answer the other one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time, timedelta

__all__ = [
    "WeekdayMask",
    "ScheduleRule",
    "ScheduleError",
    "EVERY_DAY",
    "WEEKDAY_NAMES",
    "mask_of",
    "names_of",
    "occurs_on",
    "occurrence_dates",
]


class ScheduleError(ValueError):
    """A recurrence rule that cannot produce departures."""


#: Bit 0 is Monday, matching `date.weekday()`. See the module docstring.
WeekdayMask = int

_ALL_DAYS = 0b1111111
EVERY_DAY: WeekdayMask = _ALL_DAYS

#: Bit order, so index 0 is Monday. The names are the mask's public spelling:
#: a seed file, a console form and an audit entry all say `["mon", "wed"]`
#: rather than `5`, because the point of the mask being data is that a person
#: can check it against a provider's timetable without doing binary arithmetic.
WEEKDAY_NAMES: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def mask_of(days: Sequence[str]) -> WeekdayMask:
    """Day names to a mask. Case-insensitive; refuses a name it cannot place.

    Raises `ScheduleError` for an unknown day and for a set that selects none,
    so both are refused at the boundary that names the input rather than by the
    CHECK constraint, which names only the row.
    """
    mask = 0
    for day in days:
        try:
            mask |= 1 << WEEKDAY_NAMES.index(day.lower())
        except ValueError as exc:
            raise ScheduleError(
                f"{day!r} is not a weekday; expected one of {list(WEEKDAY_NAMES)}."
            ) from exc
    if mask == 0:
        raise ScheduleError("a schedule must run on at least one day")
    return mask


def names_of(mask: WeekdayMask) -> tuple[str, ...]:
    """A mask back to day names, Monday first.

    The inverse of `mask_of` over every legal mask, which is what the round-trip
    test asserts — a display helper that disagreed with the parser would put one
    set of days in the console and a different set in the boat.
    """
    return tuple(name for index, name in enumerate(WEEKDAY_NAMES) if mask & (1 << index))


@dataclass(frozen=True, slots=True)
class ScheduleRule:
    """§16.2's recurring rule, minus the capacity the materialiser needs.

    `valid_to` is `None` for an open-ended schedule, which is what a provider
    running a year-round tour actually has. Requiring an end date would make
    every such provider invent one.
    """

    weekday_mask: WeekdayMask
    start_time: time
    valid_from: date
    valid_to: date | None = None

    def __post_init__(self) -> None:
        if not 0 < self.weekday_mask <= _ALL_DAYS:
            # A zero mask is a schedule that never runs — almost certainly a
            # console default that was never filled in, and it would produce a
            # listing with no departures and no error anywhere.
            raise ScheduleError(
                f"weekday_mask must select at least one day, got {self.weekday_mask}"
            )
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ScheduleError("valid_to cannot precede valid_from")


def occurs_on(rule: ScheduleRule, day: date) -> bool:
    """Does this schedule run on `day`? A local calendar date, not an instant."""
    if day < rule.valid_from:
        return False
    if rule.valid_to is not None and day > rule.valid_to:
        return False
    return bool(rule.weekday_mask & (1 << day.weekday()))


def occurrence_dates(rule: ScheduleRule, *, start: date, horizon_days: int) -> tuple[date, ...]:
    """Every local date this schedule runs, within `horizon_days` of `start`.

    `horizon_days` is `departures.horizon_days` from `system_setting` — 180 by
    default per §16.2 — passed in rather than read, like every other threshold.
    """
    if horizon_days < 1:
        raise ValueError("horizon_days must be at least 1")
    days = (start + timedelta(days=offset) for offset in range(horizon_days))
    return tuple(day for day in days if occurs_on(rule, day))
