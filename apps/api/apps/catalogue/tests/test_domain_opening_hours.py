"""Opening hours — SRS §15.2.

The timezone tests are the point of this file. §15.2 says hours are "evaluated
in the destination's timezone", and the failure mode is invisible during
Zanzibar-only development because the server and the destination agree. So the
same JSON is evaluated in three zones and asserted to give different answers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from apps.catalogue.domain.opening_hours import (
    ClosureException,
    OpeningHours,
    OpeningHoursError,
    TimeRange,
    is_open_at,
    next_open_at,
    parse_opening_hours,
    ranges_on,
    week_view,
)

DAR = ZoneInfo("Africa/Dar_es_Salaam")  # UTC+3, no DST
AUCKLAND = ZoneInfo("Pacific/Auckland")  # UTC+12/+13
LOS_ANGELES = ZoneInfo("America/Los_Angeles")  # UTC-8/-7

NINE_TO_SIX = {day: [["09:00", "18:00"]] for day in ("mon", "tue", "wed", "thu", "fri", "sat")}


class TestParsing:
    def test_the_srs_example_parses(self) -> None:
        hours = parse_opening_hours(
            {
                "mon": [["09:00", "18:00"]],
                "tue": [["09:00", "18:00"]],
                "fri": [["09:00", "12:00"], ["14:00", "18:00"]],
                "sun": [],
                "exceptions": [{"date": "2027-08-14", "closed": True, "reason": "Public holiday"}],
            }
        )
        assert hours.week[0] == (TimeRange(time(9), time(18)),)
        assert hours.week[4] == (
            TimeRange(time(9), time(12)),
            TimeRange(time(14), time(18)),
        )
        assert hours.week[6] == ()
        assert hours.exceptions[date(2027, 8, 14)].reason == "Public holiday"

    def test_no_data_is_unknown_not_closed(self) -> None:
        # An attraction with no recorded hours is not an attraction that is
        # shut. Saying "closed" would be a fabrication.
        for empty in (None, {}):
            hours = parse_opening_hours(empty)
            assert hours.week == {}
            assert hours.exceptions == {}

    def test_a_missing_day_is_absent_rather_than_empty(self) -> None:
        hours = parse_opening_hours({"mon": [["09:00", "18:00"]]})
        assert 1 not in hours.week
        assert ranges_on(hours, date(2027, 8, 10)) == ()  # a Tuesday

    def test_an_unknown_top_level_key_is_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="unknown keys"):
            parse_opening_hours({"monday": [["09:00", "18:00"]]})

    def test_a_malformed_time_is_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="not a valid time"):
            parse_opening_hours({"mon": [["9am", "18:00"]]})

    def test_a_range_needs_exactly_two_values(self) -> None:
        with pytest.raises(OpeningHoursError, match=r"expected \[open, close\]"):
            parse_opening_hours({"mon": [["09:00"]]})

    def test_a_day_must_be_a_list(self) -> None:
        with pytest.raises(OpeningHoursError, match="expected a list"):
            parse_opening_hours({"mon": "09:00-18:00"})

    def test_out_of_order_ranges_are_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="ascending order"):
            parse_opening_hours({"mon": [["14:00", "18:00"], ["09:00", "12:00"]]})

    def test_a_duplicate_exception_date_is_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="duplicate exception"):
            parse_opening_hours({"exceptions": [{"date": "2027-08-14"}, {"date": "2027-08-14"}]})

    def test_an_exception_defaults_to_closed(self) -> None:
        hours = parse_opening_hours({"exceptions": [{"date": "2027-08-14"}]})
        assert hours.exceptions[date(2027, 8, 14)].closed is True

    def test_a_closed_exception_cannot_carry_ranges(self) -> None:
        with pytest.raises(OpeningHoursError, match="closed day cannot carry ranges"):
            parse_opening_hours(
                {
                    "exceptions": [
                        {"date": "2027-08-14", "closed": True, "ranges": [["09:00", "12:00"]]}
                    ]
                }
            )

    def test_an_exception_can_open_a_normally_closed_day(self) -> None:
        # A site opening on a normally-closed Sunday for a festival. §15.2
        # shows only closures, but one mechanism has to express both.
        hours = parse_opening_hours(
            {
                "sun": [],
                "exceptions": [
                    {
                        "date": "2027-08-15",
                        "closed": False,
                        "ranges": [["10:00", "16:00"]],
                        "reason": "Festival",
                    }
                ],
            }
        )
        assert ranges_on(hours, date(2027, 8, 15)) == (TimeRange(time(10), time(16)),)

    def test_an_unknown_exception_key_is_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="unknown keys"):
            parse_opening_hours({"exceptions": [{"date": "2027-08-14", "shut": True}]})

    def test_an_exception_without_a_date_is_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="'date' is required"):
            parse_opening_hours({"exceptions": [{"closed": True}]})

    def test_a_malformed_exception_date_is_rejected(self) -> None:
        with pytest.raises(OpeningHoursError, match="not a valid date"):
            parse_opening_hours({"exceptions": [{"date": "14/08/2027"}]})

    def test_an_exception_must_be_an_object(self) -> None:
        with pytest.raises(OpeningHoursError, match="must be an object"):
            parse_opening_hours({"exceptions": ["2027-08-14"]})


class TestTheTimezoneRule:
    """The same JSON, three zones, different answers. §15.2."""

    HOURS = parse_opening_hours(NINE_TO_SIX)

    def test_naive_datetimes_are_refused(self) -> None:
        # Assuming UTC here would be a wrong answer in a table a tourist plans
        # a day around.
        with pytest.raises(ValueError, match="naive datetime"):
            is_open_at(self.HOURS, datetime(2027, 8, 12, 10), tz=DAR)  # noqa: DTZ001

    def test_one_instant_is_open_in_dar_and_shut_in_auckland(self) -> None:
        # 2027-08-12 08:00Z is 11:00 in Dar (open) and 20:00 in Auckland
        # (shut). One instant, one JSON document, two answers.
        instant = datetime(2027, 8, 12, 8, 0, tzinfo=UTC)
        assert is_open_at(self.HOURS, instant, tz=DAR) is True
        assert is_open_at(self.HOURS, instant, tz=AUCKLAND) is False

    def test_the_same_instant_is_shut_in_los_angeles(self) -> None:
        # 01:00 local — before opening, and on the previous calendar day.
        instant = datetime(2027, 8, 12, 8, 0, tzinfo=UTC)
        assert is_open_at(self.HOURS, instant, tz=LOS_ANGELES) is False

    def test_the_local_weekday_is_what_counts_not_the_utc_one(self) -> None:
        # 2027-08-15 is a Sunday, closed. 22:00Z on Saturday the 14th is
        # already Sunday 01:00 in Auckland, so Auckland says shut for a
        # different reason than the hour: it is a different day there.
        instant = datetime(2027, 8, 14, 22, 0, tzinfo=UTC)
        assert instant.astimezone(AUCKLAND).date() == date(2027, 8, 15)
        assert is_open_at(self.HOURS, instant, tz=AUCKLAND) is False

    def test_a_zone_west_of_utc_can_still_be_open_on_the_previous_day(self) -> None:
        # 2027-08-16 01:00Z is 2027-08-15 18:00 in LA — Sunday, closed —
        # while Dar is on Monday at 04:00, also closed. Different reasons.
        instant = datetime(2027, 8, 16, 1, 0, tzinfo=UTC)
        assert is_open_at(self.HOURS, instant, tz=LOS_ANGELES) is False
        assert is_open_at(self.HOURS, instant, tz=DAR) is False

    def test_a_dst_transition_does_not_change_local_opening_time(self) -> None:
        # Auckland leaves DST on 2027-04-04. 10:00 local is inside 09:00-18:00
        # on both sides of the transition, which is the property a stored
        # local time must have.
        before = datetime(2027, 4, 2, 10, 0, tzinfo=AUCKLAND)
        after = datetime(2027, 4, 6, 10, 0, tzinfo=AUCKLAND)
        assert is_open_at(self.HOURS, before, tz=AUCKLAND) is True
        assert is_open_at(self.HOURS, after, tz=AUCKLAND) is True


class TestIsOpenAt:
    HOURS = parse_opening_hours(
        {**NINE_TO_SIX, "fri": [["09:00", "12:00"], ["14:00", "18:00"]], "sun": []}
    )

    def test_open_inside_a_range(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 10, tzinfo=DAR), tz=DAR) is True

    def test_open_exactly_at_the_opening_minute(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 9, tzinfo=DAR), tz=DAR) is True

    def test_shut_exactly_at_the_closing_minute(self) -> None:
        # Exclusive close: an attraction closing at 18:00 is not open at 18:00,
        # and telling a tourist to arrive as the gate shuts is worse than
        # telling them it is closed.
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 18, tzinfo=DAR), tz=DAR) is False

    def test_shut_before_opening(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 8, 59, tzinfo=DAR), tz=DAR) is False

    def test_shut_in_a_midday_gap(self) -> None:
        # Friday 13:00 falls between the two Friday ranges.
        assert is_open_at(self.HOURS, datetime(2027, 8, 13, 13, tzinfo=DAR), tz=DAR) is False

    def test_open_in_the_afternoon_range(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 13, 15, tzinfo=DAR), tz=DAR) is True

    def test_shut_on_a_day_with_no_ranges(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 15, 12, tzinfo=DAR), tz=DAR) is False

    def test_shut_when_no_hours_are_recorded(self) -> None:
        assert (
            is_open_at(parse_opening_hours(None), datetime(2027, 8, 12, 12, tzinfo=DAR), tz=DAR)
            is False
        )


class TestOvernightRanges:
    """A sunset dhow running 20:00-02:00 is ordinary in this catalogue."""

    HOURS = parse_opening_hours({"thu": [["20:00", "02:00"]]})

    def test_open_late_on_the_stated_day(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 23, tzinfo=DAR), tz=DAR) is True

    def test_open_after_midnight_on_the_following_day(self) -> None:
        # 01:00 Friday belongs to Thursday's session.
        assert is_open_at(self.HOURS, datetime(2027, 8, 13, 1, tzinfo=DAR), tz=DAR) is True

    def test_shut_after_the_overnight_close(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 13, 2, 1, tzinfo=DAR), tz=DAR) is False

    def test_shut_before_the_evening_open(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 19, tzinfo=DAR), tz=DAR) is False

    def test_the_range_reports_itself_as_crossing_midnight(self) -> None:
        assert TimeRange(time(20), time(2)).crosses_midnight is True
        assert TimeRange(time(9), time(18)).crosses_midnight is False

    def test_a_zero_length_range_is_treated_as_crossing(self) -> None:
        # 09:00-09:00 is degenerate data; reading it as a full 24 hours is the
        # only interpretation that does not silently drop the row.
        assert TimeRange(time(9), time(9)).crosses_midnight is True


class TestExceptions:
    HOURS = parse_opening_hours(
        {
            **NINE_TO_SIX,
            "exceptions": [
                {"date": "2027-08-12", "closed": True, "reason": "Public holiday"},
                {
                    "date": "2027-08-15",
                    "closed": False,
                    "ranges": [["10:00", "16:00"]],
                    "reason": "Festival",
                },
            ],
        }
    )

    def test_a_closure_outranks_the_weekly_pattern(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 12, 12, tzinfo=DAR), tz=DAR) is False

    def test_an_opening_outranks_a_closed_weekday(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 15, 12, tzinfo=DAR), tz=DAR) is True

    def test_an_exception_replaces_the_day_rather_than_merging(self) -> None:
        # 09:00 is inside the weekly pattern but outside the festival hours.
        assert is_open_at(self.HOURS, datetime(2027, 8, 15, 9, tzinfo=DAR), tz=DAR) is False

    def test_a_neighbouring_day_is_unaffected(self) -> None:
        assert is_open_at(self.HOURS, datetime(2027, 8, 13, 12, tzinfo=DAR), tz=DAR) is True


class TestWeekView:
    HOURS = parse_opening_hours(
        {
            **NINE_TO_SIX,
            "sun": [],
            "exceptions": [{"date": "2027-08-14", "closed": True, "reason": "Public holiday"}],
        }
    )

    def test_it_returns_the_requested_number_of_days(self) -> None:
        assert len(week_view(self.HOURS, from_date=date(2027, 8, 9), days=7, tz=DAR)) == 7

    def test_days_are_consecutive_from_the_start_date(self) -> None:
        view = week_view(self.HOURS, from_date=date(2027, 8, 9), days=3, tz=DAR)
        assert [d.date for d in view] == [date(2027, 8, 9), date(2027, 8, 10), date(2027, 8, 11)]

    def test_a_closed_day_is_marked_closed(self) -> None:
        view = week_view(self.HOURS, from_date=date(2027, 8, 15), days=1, tz=DAR)
        assert view[0].is_closed is True
        assert view[0].ranges == ()

    def test_an_exception_reason_is_carried_for_display(self) -> None:
        view = week_view(self.HOURS, from_date=date(2027, 8, 14), days=1, tz=DAR)
        assert view[0].is_closed is True
        assert view[0].exception_reason == "Public holiday"

    def test_an_ordinary_day_carries_no_reason(self) -> None:
        view = week_view(self.HOURS, from_date=date(2027, 8, 15), days=1, tz=DAR)
        assert view[0].exception_reason is None

    def test_split_ranges_survive_into_the_view(self) -> None:
        hours = parse_opening_hours({"fri": [["09:00", "12:00"], ["14:00", "18:00"]]})
        view = week_view(hours, from_date=date(2027, 8, 13), days=1, tz=DAR)
        assert len(view[0].ranges) == 2
        assert view[0].is_closed is False

    def test_zero_days_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            week_view(self.HOURS, from_date=date(2027, 8, 9), days=0, tz=DAR)


class TestNextOpenAt:
    HOURS = parse_opening_hours({**NINE_TO_SIX, "sun": []})

    def test_it_returns_now_when_already_open(self) -> None:
        now = datetime(2027, 8, 12, 10, tzinfo=DAR)
        assert next_open_at(self.HOURS, after=now, tz=DAR) == now

    def test_it_finds_the_same_day_opening(self) -> None:
        got = next_open_at(self.HOURS, after=datetime(2027, 8, 12, 7, tzinfo=DAR), tz=DAR)
        assert got == datetime(2027, 8, 12, 9, tzinfo=DAR)

    def test_it_skips_a_closed_day(self) -> None:
        # Sunday the 15th is closed; the answer is Monday the 16th.
        got = next_open_at(self.HOURS, after=datetime(2027, 8, 15, 8, tzinfo=DAR), tz=DAR)
        assert got == datetime(2027, 8, 16, 9, tzinfo=DAR)

    def test_it_finds_tomorrow_after_closing_time(self) -> None:
        got = next_open_at(self.HOURS, after=datetime(2027, 8, 12, 19, tzinfo=DAR), tz=DAR)
        assert got == datetime(2027, 8, 13, 9, tzinfo=DAR)

    def test_it_returns_none_when_never_open_within_the_horizon(self) -> None:
        never = OpeningHours(week={}, exceptions={})
        assert next_open_at(never, after=datetime(2027, 8, 12, 9, tzinfo=DAR), tz=DAR) is None

    def test_naive_datetimes_are_refused(self) -> None:
        with pytest.raises(ValueError, match="naive datetime"):
            next_open_at(self.HOURS, after=datetime(2027, 8, 12, 9), tz=DAR)  # noqa: DTZ001

    def test_it_answers_in_the_destination_zone_not_the_callers(self) -> None:
        # Asked from an Auckland-shaped instant, the answer is still Dar's
        # 09:00 and still carries Dar's offset.
        after = datetime(2027, 8, 12, 20, tzinfo=AUCKLAND)  # 11:00 in Dar, open
        got = next_open_at(self.HOURS, after=after, tz=DAR)
        assert got == after


class TestExceptionsInteractWithClosureDataclass:
    def test_a_closure_exposes_its_reason(self) -> None:
        exception = ClosureException(date=date(2027, 8, 14), closed=True, reason="Holiday")
        assert exception.ranges == ()
        assert exception.reason == "Holiday"
