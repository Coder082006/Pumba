"""Activity requirements and schedule recurrence — SRS §16.2, §16.4.

`min_age` and `swimming_ability_required` are safety controls, so the tests
that matter here are the ones proving a typo cannot produce a listing that
looks restricted in the console and enforces nothing at booking.
"""

from __future__ import annotations

from datetime import date, time

import pytest

from apps.catalogue.domain.requirements import (
    ActivityRequirements,
    RequirementsError,
    parse_requirements,
)
from apps.catalogue.domain.schedules import (
    EVERY_DAY,
    ScheduleError,
    ScheduleRule,
    occurrence_dates,
    occurs_on,
)

MON, TUE, WED, THU, FRI, SAT, SUN = (1 << n for n in range(7))
MON_TO_SAT = MON | TUE | WED | THU | FRI | SAT


class TestParsingTheSrsExample:
    def test_the_srs_example_parses(self) -> None:
        got = parse_requirements(
            {
                "min_age": 8,
                "max_age": None,
                "swimming_ability_required": True,
                "medical_declarations": ["pregnancy", "heart_condition"],
                "what_to_bring": ["swimwear", "towel", "sunscreen"],
                "not_suitable_for": ["reduced_mobility"],
            }
        )
        assert got.min_age == 8
        assert got.max_age is None
        assert got.swimming_ability_required is True
        assert got.medical_declarations == ("pregnancy", "heart_condition")
        assert got.what_to_bring == ("swimwear", "towel", "sunscreen")
        assert got.not_suitable_for == ("reduced_mobility",)

    def test_absent_requirements_are_unrestricted(self) -> None:
        for empty in (None, {}):
            got = parse_requirements(empty)
            assert got.is_unrestricted is True
            assert got.what_to_bring == ()


class TestUnknownKeysAreRejected:
    """The safety control. A typo here enforces nothing and reports nothing."""

    def test_a_misspelt_min_age_is_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="unknown requirement keys"):
            parse_requirements({"minimum_age": 8})

    def test_the_error_names_the_keys_that_would_have_worked(self) -> None:
        with pytest.raises(RequirementsError, match="min_age"):
            parse_requirements({"minimum_age": 8})

    def test_a_plausible_but_unsupported_key_is_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="unknown requirement keys"):
            parse_requirements({"min_age": 8, "max_weight_kg": 100})


class TestAgeBounds:
    def test_a_zero_min_age_is_not_the_same_as_no_min_age(self) -> None:
        # A provider stating infants are welcome is different from a provider
        # stating nothing, and collapsing the two loses that.
        explicit = parse_requirements({"min_age": 0})
        absent = parse_requirements({})
        assert explicit.min_age == 0
        assert absent.min_age is None
        assert explicit.is_unrestricted is False
        assert absent.is_unrestricted is True

    def test_an_explicit_null_is_unrestricted(self) -> None:
        assert parse_requirements({"min_age": None}).min_age is None

    def test_a_negative_age_is_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="cannot be negative"):
            parse_requirements({"min_age": -1})

    def test_an_implausible_age_is_rejected(self) -> None:
        # A transposed year of birth becomes a validation error rather than a
        # restriction that silently matches everyone.
        with pytest.raises(RequirementsError, match="implausible"):
            parse_requirements({"min_age": 1987})

    def test_a_boolean_is_not_an_age(self) -> None:
        # bool is an int in Python, so this needs its own check.
        with pytest.raises(RequirementsError, match="whole number"):
            parse_requirements({"min_age": True})

    def test_a_string_age_is_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="whole number"):
            parse_requirements({"min_age": "8"})

    def test_a_max_below_the_min_is_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="below min_age"):
            parse_requirements({"min_age": 18, "max_age": 8})

    def test_equal_bounds_are_allowed(self) -> None:
        got = parse_requirements({"min_age": 18, "max_age": 18})
        assert got.admits_age(18) is True


class TestAdmitsAge:
    EIGHT_PLUS = ActivityRequirements(min_age=8)
    CHILDREN_ONLY = ActivityRequirements(min_age=4, max_age=12)

    def test_the_boundary_is_inclusive(self) -> None:
        # A provider writing "8+" means an eight-year-old may come.
        assert self.EIGHT_PLUS.admits_age(8) is True

    def test_below_the_minimum_is_refused(self) -> None:
        assert self.EIGHT_PLUS.admits_age(7) is False

    def test_above_the_minimum_is_admitted(self) -> None:
        assert self.EIGHT_PLUS.admits_age(60) is True

    def test_the_upper_boundary_is_inclusive(self) -> None:
        assert self.CHILDREN_ONLY.admits_age(12) is True

    def test_above_the_maximum_is_refused(self) -> None:
        assert self.CHILDREN_ONLY.admits_age(13) is False

    def test_an_unrestricted_activity_admits_everyone(self) -> None:
        unrestricted = ActivityRequirements()
        assert unrestricted.admits_age(0) is True
        assert unrestricted.admits_age(99) is True

    def test_a_negative_age_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot be negative"):
            self.EIGHT_PLUS.admits_age(-1)


class TestFreeTextLists:
    def test_a_string_is_not_a_list(self) -> None:
        with pytest.raises(RequirementsError, match="must be a list of strings"):
            parse_requirements({"what_to_bring": "towel"})

    def test_non_string_entries_are_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="only strings"):
            parse_requirements({"what_to_bring": ["towel", 3]})

    def test_empty_entries_are_rejected(self) -> None:
        with pytest.raises(RequirementsError, match="empty entry"):
            parse_requirements({"what_to_bring": ["towel", "  "]})

    def test_entries_are_trimmed(self) -> None:
        assert parse_requirements({"what_to_bring": ["  towel  "]}).what_to_bring == ("towel",)

    def test_duplicates_collapse_preserving_order(self) -> None:
        got = parse_requirements({"what_to_bring": ["towel", "swimwear", "towel"]})
        assert got.what_to_bring == ("towel", "swimwear")

    def test_the_vocabulary_is_open(self) -> None:
        # §16.1: no activity vocabulary in application code. A market needing
        # "altitude_sickness" adds it as data.
        got = parse_requirements({"medical_declarations": ["altitude_sickness"]})
        assert got.medical_declarations == ("altitude_sickness",)

    def test_swimming_ability_must_be_boolean(self) -> None:
        with pytest.raises(RequirementsError, match="must be a boolean"):
            parse_requirements({"swimming_ability_required": "yes"})


class TestScheduleRecurrence:
    RULE = ScheduleRule(
        weekday_mask=MON_TO_SAT,
        start_time=time(8, 30),
        valid_from=date(2027, 1, 1),
        valid_to=date(2027, 12, 31),
    )

    def test_the_mask_is_monday_first(self) -> None:
        # date.weekday() is Monday=0. Sunday-first is equally defensible and is
        # what several calendar libraries use — which is exactly why this is
        # asserted: the wrong convention shifts every departure by a day.
        monday = date(2027, 8, 9)
        assert monday.weekday() == 0
        assert occurs_on(ScheduleRule(MON, time(8), date(2027, 1, 1)), monday) is True
        assert occurs_on(ScheduleRule(SUN, time(8), date(2027, 1, 1)), monday) is False

    def test_the_sunday_bit_is_the_seventh(self) -> None:
        sunday = date(2027, 8, 15)
        assert sunday.weekday() == 6
        assert occurs_on(ScheduleRule(SUN, time(8), date(2027, 1, 1)), sunday) is True

    def test_it_runs_on_a_selected_day(self) -> None:
        assert occurs_on(self.RULE, date(2027, 8, 12)) is True  # Thursday

    def test_it_does_not_run_on_an_unselected_day(self) -> None:
        assert occurs_on(self.RULE, date(2027, 8, 15)) is False  # Sunday

    def test_it_does_not_run_before_the_validity_window(self) -> None:
        assert occurs_on(self.RULE, date(2026, 12, 31)) is False

    def test_it_does_not_run_after_the_validity_window(self) -> None:
        assert occurs_on(self.RULE, date(2028, 1, 1)) is False

    def test_the_window_boundaries_are_inclusive(self) -> None:
        rule = ScheduleRule(EVERY_DAY, time(8), date(2027, 1, 1), date(2027, 12, 31))
        assert occurs_on(rule, date(2027, 1, 1)) is True
        assert occurs_on(rule, date(2027, 12, 31)) is True

    def test_an_open_ended_schedule_runs_indefinitely(self) -> None:
        # A year-round tour has no end date, and demanding one would make every
        # such provider invent one.
        rule = ScheduleRule(EVERY_DAY, time(8), date(2027, 1, 1))
        assert occurs_on(rule, date(2099, 1, 1)) is True

    def test_a_zero_mask_is_rejected(self) -> None:
        # Almost certainly a console default never filled in, which would
        # produce a listing with no departures and no error anywhere.
        with pytest.raises(ScheduleError, match="at least one day"):
            ScheduleRule(0, time(8), date(2027, 1, 1))

    def test_a_mask_beyond_seven_days_is_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="weekday_mask"):
            ScheduleRule(0b10000000, time(8), date(2027, 1, 1))

    def test_an_inverted_validity_window_is_rejected(self) -> None:
        with pytest.raises(ScheduleError, match="cannot precede"):
            ScheduleRule(EVERY_DAY, time(8), date(2027, 12, 31), date(2027, 1, 1))

    def test_a_single_day_window_is_allowed(self) -> None:
        rule = ScheduleRule(EVERY_DAY, time(8), date(2027, 8, 12), date(2027, 8, 12))
        assert occurs_on(rule, date(2027, 8, 12)) is True


class TestOccurrenceDates:
    RULE = ScheduleRule(MON_TO_SAT, time(8, 30), date(2027, 8, 9))

    def test_a_week_of_mon_to_sat_yields_six_dates(self) -> None:
        got = occurrence_dates(self.RULE, start=date(2027, 8, 9), horizon_days=7)
        assert len(got) == 6
        assert date(2027, 8, 15) not in got  # the Sunday

    def test_dates_are_ascending(self) -> None:
        got = occurrence_dates(self.RULE, start=date(2027, 8, 9), horizon_days=30)
        assert list(got) == sorted(got)

    def test_the_horizon_is_a_parameter(self) -> None:
        # departures.horizon_days, 180 by default per §16.2.
        short = occurrence_dates(self.RULE, start=date(2027, 8, 9), horizon_days=7)
        long = occurrence_dates(self.RULE, start=date(2027, 8, 9), horizon_days=180)
        assert len(long) > len(short)

    def test_a_horizon_of_one_day_yields_at_most_one_date(self) -> None:
        got = occurrence_dates(self.RULE, start=date(2027, 8, 9), horizon_days=1)
        assert got == (date(2027, 8, 9),)

    def test_a_zero_horizon_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            occurrence_dates(self.RULE, start=date(2027, 8, 9), horizon_days=0)

    def test_it_stops_at_the_validity_end(self) -> None:
        rule = ScheduleRule(EVERY_DAY, time(8), date(2027, 8, 9), date(2027, 8, 11))
        got = occurrence_dates(rule, start=date(2027, 8, 9), horizon_days=30)
        assert got == (date(2027, 8, 9), date(2027, 8, 10), date(2027, 8, 11))

    def test_it_skips_dates_before_the_validity_start(self) -> None:
        rule = ScheduleRule(EVERY_DAY, time(8), date(2027, 8, 12))
        got = occurrence_dates(rule, start=date(2027, 8, 9), horizon_days=5)
        assert got == (date(2027, 8, 12), date(2027, 8, 13))
