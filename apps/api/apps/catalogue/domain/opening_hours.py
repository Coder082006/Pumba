"""Attraction opening hours — SRS §15.2.

The stored shape is fixed by the SRS:

    {
      "mon": [["09:00","18:00"]],
      "fri": [["09:00","12:00"],["14:00","18:00"]],
      "sun": [],
      "exceptions": [
        { "date": "2027-08-14", "closed": true, "reason": "Public holiday" }
      ]
    }

and §15.2 requires it be *"evaluated in the destination's timezone"*.

**That requirement is enforced by the signatures, not by remembering it.**
Every public function here takes `tz` as a required keyword argument. There is
no overload, no default, and no module-level fallback to the server clock, so
there is no way to call this module correctly-looking and get server-local
answers. A destination stores its own IANA zone precisely so that a platform
running in one region can serve attractions in another; a `datetime.now()`
anywhere in this file would quietly delete that property, and the deletion
would be invisible for as long as the server happened to share a zone with the
destination — which, during Zanzibar-only development, is always.

For the same reason `is_open_at` rejects naive datetimes rather than assuming
UTC. An assumption here is a wrong answer in a table a tourist plans a day
around.

**Overnight ranges.** §15.2's schema does not mention them, but a sunset dhow
that runs 20:00-02:00 is an ordinary case in this catalogue, and writing
`["20:00","02:00"]` is what an administrator will do. A range whose close is at
or before its open is therefore read as running into the next day, and it is
tested. The alternative — rejecting it — would push administrators into
splitting one session across two day keys, which is worse data.

**Exceptions outrank the weekly pattern**, closed or open. §15.2 shows a
closure, but the same mechanism has to express a one-off opening (a site open
on a normally-closed Sunday for a festival), so `closed: false` with explicit
`ranges` is supported and replaces that day entirely.
"""

from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

__all__ = [
    "Weekday",
    "TimeRange",
    "ClosureException",
    "OpeningHours",
    "DaySchedule",
    "OpeningHoursError",
    "parse_opening_hours",
    "is_open_at",
    "week_view",
    "next_open_at",
    "ranges_on",
]


class OpeningHoursError(ValueError):
    """The stored JSON does not match the §15.2 schema."""


#: §15.2 keys, in `date.weekday()` order so indexing needs no lookup table.
Weekday = int
_DAY_KEYS: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


@dataclass(frozen=True, slots=True, order=True)
class TimeRange:
    opens: time
    closes: time

    @property
    def crosses_midnight(self) -> bool:
        """`20:00-02:00` runs into the next day; `09:00-18:00` does not."""
        return self.closes <= self.opens

    def contains(self, moment: time) -> bool:
        """Is `moment` inside this range, treating the close as exclusive?

        Exclusive because an attraction that closes at 18:00 is not open at
        18:00, and an inclusive bound would tell a tourist to arrive as the
        gate shuts.
        """
        if self.crosses_midnight:
            return moment >= self.opens or moment < self.closes
        return self.opens <= moment < self.closes


@dataclass(frozen=True, slots=True)
class ClosureException:
    """A dated override. `ranges` is empty when `closed` is true."""

    date: date
    closed: bool
    reason: str | None = None
    ranges: tuple[TimeRange, ...] = ()


@dataclass(frozen=True, slots=True)
class OpeningHours:
    week: Mapping[Weekday, tuple[TimeRange, ...]]
    exceptions: Mapping[date, ClosureException]


@dataclass(frozen=True, slots=True)
class DaySchedule:
    """One row of the §24.9 "opening hours for the coming week" table."""

    date: date
    ranges: tuple[TimeRange, ...]
    is_closed: bool
    exception_reason: str | None = None


def parse_opening_hours(raw: Mapping[str, object] | None) -> OpeningHours:
    """Validate and structure the stored JSONB.

    `None` and `{}` both mean "no hours recorded", which renders as *unknown*
    rather than as *closed* — an attraction with no data is not an attraction
    that is shut, and saying so would be a fabrication of the same kind the
    distance chip has to avoid.
    """
    if not raw:
        return OpeningHours(week={}, exceptions={})

    unknown = set(raw) - set(_DAY_KEYS) - {"exceptions"}
    if unknown:
        raise OpeningHoursError(f"unknown keys: {sorted(unknown)}")

    week: dict[Weekday, tuple[TimeRange, ...]] = {}
    for index, key in enumerate(_DAY_KEYS):
        if key not in raw:
            continue
        week[index] = _parse_ranges(raw[key], where=key)

    exceptions: dict[date, ClosureException] = {}
    for item in _as_sequence(raw.get("exceptions", ()), where="exceptions"):
        exception = _parse_exception(item)
        if exception.date in exceptions:
            raise OpeningHoursError(f"duplicate exception for {exception.date.isoformat()}")
        exceptions[exception.date] = exception

    return OpeningHours(week=week, exceptions=exceptions)


def ranges_on(hours: OpeningHours, day: date) -> tuple[TimeRange, ...]:
    """The ranges in force on `day`, exceptions taking precedence."""
    exception = hours.exceptions.get(day)
    if exception is not None:
        return () if exception.closed else exception.ranges
    return hours.week.get(day.weekday(), ())


def is_open_at(hours: OpeningHours, instant: datetime, *, tz: ZoneInfo) -> bool:
    """Is the attraction open at `instant`, read in its own timezone?

    A range that crosses midnight is also checked against the previous local
    day, because 01:00 on Tuesday belongs to Monday's 20:00-02:00 session.
    """
    if instant.tzinfo is None:
        raise ValueError("naive datetime; opening hours are evaluated in the destination timezone")

    local = instant.astimezone(tz)
    moment = local.time()
    today = local.date()

    for candidate in ranges_on(hours, today):
        if not candidate.crosses_midnight and candidate.contains(moment):
            return True
        if candidate.crosses_midnight and moment >= candidate.opens:
            return True

    for candidate in ranges_on(hours, today - timedelta(days=1)):
        if candidate.crosses_midnight and moment < candidate.closes:
            return True

    return False


def week_view(
    hours: OpeningHours, *, from_date: date, days: int, tz: ZoneInfo
) -> tuple[DaySchedule, ...]:
    """The §24.9 table: `days` consecutive local days from `from_date`.

    `tz` is required and unused in the arithmetic — deliberately. The caller
    must have resolved `from_date` in the destination's zone to call this at
    all, and taking the zone here is what makes an accidental
    `date.today()` from the server visible at the call site rather than
    hidden inside a helper.
    """
    if days < 1:
        raise ValueError("days must be at least 1")
    _ = tz

    schedule: list[DaySchedule] = []
    for offset in range(days):
        day = from_date + timedelta(days=offset)
        exception = hours.exceptions.get(day)
        ranges = ranges_on(hours, day)
        schedule.append(
            DaySchedule(
                date=day,
                ranges=ranges,
                is_closed=not ranges,
                exception_reason=exception.reason if exception is not None else None,
            )
        )
    return tuple(schedule)


def next_open_at(
    hours: OpeningHours, *, after: datetime, tz: ZoneInfo, horizon_days: int = 14
) -> datetime | None:
    """The next local instant the attraction opens, or `None` within horizon.

    Returns `after` itself when it is already inside opening hours, because
    "when does it next open" for somebody standing at the gate is now.
    """
    if after.tzinfo is None:
        raise ValueError("naive datetime; opening hours are evaluated in the destination timezone")
    if is_open_at(hours, after, tz=tz):
        return after

    local = after.astimezone(tz)
    for offset in range(horizon_days + 1):
        day = local.date() + timedelta(days=offset)
        for candidate in sorted(ranges_on(hours, day)):
            opens_at = datetime.combine(day, candidate.opens, tzinfo=tz)
            if opens_at > local:
                return opens_at
    return None


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _as_sequence(value: object, *, where: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        raise OpeningHoursError(f"{where}: expected a list, got {type(value).__name__}")
    return value


def _parse_ranges(value: object, *, where: str) -> tuple[TimeRange, ...]:
    parsed = tuple(_parse_range(item, where=where) for item in _as_sequence(value, where=where))
    for first, second in itertools.pairwise(parsed):
        if (
            not first.crosses_midnight
            and not second.crosses_midnight
            and second.opens < first.opens
        ):
            raise OpeningHoursError(f"{where}: ranges must be in ascending order")
    return parsed


def _parse_range(item: object, *, where: str) -> TimeRange:
    pair = _as_sequence(item, where=where)
    if len(pair) != 2:
        raise OpeningHoursError(f"{where}: expected [open, close], got {len(pair)} values")
    return TimeRange(_parse_time(pair[0], where=where), _parse_time(pair[1], where=where))


def _parse_time(value: object, *, where: str) -> time:
    if not isinstance(value, str):
        raise OpeningHoursError(f"{where}: expected 'HH:MM', got {type(value).__name__}")
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise OpeningHoursError(f"{where}: {value!r} is not a valid time") from exc


def _parse_exception(item: object) -> ClosureException:
    if not isinstance(item, Mapping):
        raise OpeningHoursError("exceptions: each entry must be an object")
    unknown = set(item) - {"date", "closed", "reason", "ranges"}
    if unknown:
        raise OpeningHoursError(f"exceptions: unknown keys {sorted(unknown)}")

    raw_date = item.get("date")
    if not isinstance(raw_date, str):
        raise OpeningHoursError("exceptions: 'date' is required and must be a string")
    try:
        on = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise OpeningHoursError(f"exceptions: {raw_date!r} is not a valid date") from exc

    closed = item.get("closed", True)
    if not isinstance(closed, bool):
        raise OpeningHoursError("exceptions: 'closed' must be a boolean")

    reason = item.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise OpeningHoursError("exceptions: 'reason' must be a string")

    ranges = () if closed else _parse_ranges(item.get("ranges", ()), where="exceptions.ranges")
    if closed and item.get("ranges"):
        raise OpeningHoursError("exceptions: a closed day cannot carry ranges")

    return ClosureException(date=on, closed=closed, reason=reason, ranges=ranges)
