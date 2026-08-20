"""Activities, schedules and media — SRS §16, §15.4, §7.3, §35.7.

§16.1 says activities are entirely database-driven and that no activity type,
category or name appears in application code. That is asserted here the same
way §4.2 is asserted elsewhere: by tokenising the module and looking for the
words.

The constraints that matter are the ones that would otherwise produce a listing
that renders perfectly and can never be booked. An inverted pax range makes
§16.3's `min_pax <= pax <= max_pax` unsatisfiable; a zero weekday mask is a
schedule that recurs on no day. Both are visible and inert, which is a worse
failure than an absent row because nobody goes looking for it.
"""

from __future__ import annotations

from datetime import date, time
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models
from django.db.models.deletion import ProtectedError

from apps.catalogue.domain.schedules import ScheduleRule, WeekdayMask, occurs_on
from apps.catalogue.models import Activity, ActivitySchedule, ConfirmationMode, Media
from apps.catalogue.tests.factories import (
    make_activity,
    make_activity_schedule,
    make_attraction,
    make_destination,
    make_media,
    make_tag,
)


def field(model: type[models.Model], name: str) -> models.Field:
    return model._meta.get_field(name)  # type: ignore[return-value]


class TestSchemaMatchesSection759:
    def test_the_tables_are_named_as_the_srs_names_them(self) -> None:
        assert Activity._meta.db_table == "activity"
        assert ActivitySchedule._meta.db_table == "activity_schedule"
        assert Media._meta.db_table == "media"

    def test_every_section_759_field_exists(self) -> None:
        expected = {
            "provider_id",
            "destination",
            "attraction",
            "name",
            "description",
            "coordinates",
            "meeting_point_text",
            "duration_minutes",
            "price_per_person",
            "price_per_group",
            "currency",
            "min_pax",
            "max_pax",
            "min_age",
            "requirements",
            "inclusions",
            "exclusions",
            "cancellation_policy",
            "booking_cutoff_hours",
            "feature_rank",
            "is_active",
            "deleted_at",
        }
        assert {f.name for f in Activity._meta.get_fields()} >= expected

    def test_the_provider_reference_is_soft_here_too(self) -> None:
        """ADR 0012."""
        assert isinstance(field(Activity, "provider_id"), models.BigIntegerField)
        assert not isinstance(field(Activity, "provider_id"), models.ForeignKey)

    def test_the_confirmation_modes_are_the_two_section_166_names(self) -> None:
        assert {c.value for c in ConfirmationMode} == {"INSTANT", "ON_REQUEST"}

    def test_departures_are_not_this_modules_table(self) -> None:
        """ADR 0011: §6.4 gives `activity_departure` to `inventory`."""
        assert not hasattr(Activity, "departures")

    def test_no_activity_type_or_category_appears_in_the_module(self) -> None:
        """§16.1: "no activity type, category or name appears in application
        code". The seed catalogue is snorkelling, dhow cruises and spice farms
        as rows."""
        import inspect
        import io
        import tokenize

        from apps.catalogue import models as catalogue_models

        executable = " ".join(
            token.string
            for token in tokenize.generate_tokens(
                io.StringIO(inspect.getsource(catalogue_models)).readline
            )
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )
        for word in ("snorkel", "Snorkel", "dhow", "Dhow", "spice", "Spice", "safari", "Safari"):
            assert word not in executable, f"{word} appears in executable code"


@pytest.mark.django_db
class TestActivityConstraints:
    def test_an_activity_saves_and_reads_back(self) -> None:
        activity = make_activity()
        activity.refresh_from_db()
        assert activity.confirmation_mode == ConfirmationMode.INSTANT
        assert activity.price_per_person == Decimal("95.00")

    def test_an_activity_may_have_no_attraction(self) -> None:
        """§15.4: an open-water excursion anchors on its own coordinates."""
        assert make_activity().attraction is None

    def test_an_activity_may_hang_off_an_attraction(self) -> None:
        attraction = make_attraction()
        activity = make_activity(destination=attraction.destination, attraction=attraction)
        assert list(attraction.activities.all()) == [activity]

    def test_many_activities_may_share_one_attraction(self) -> None:
        """§15.4's diagram: three providers, one Stone Town."""
        attraction = make_attraction()
        for index in range(3):
            make_activity(
                destination=attraction.destination,
                attraction=attraction,
                slug=f"tour-{index}",
                name=f"Tour {index}",
            )
        assert attraction.activities.count() == 3

    def test_an_inverted_pax_range_is_refused(self) -> None:
        """§16.3's predicate would be unsatisfiable: the listing renders and
        can never be booked."""
        with pytest.raises(IntegrityError):
            make_activity(min_pax=8, max_pax=4)

    def test_a_single_seat_activity_is_allowed(self) -> None:
        activity = make_activity(min_pax=1, max_pax=1)
        assert (activity.min_pax, activity.max_pax) == (1, 1)

    def test_a_zero_max_pax_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity(min_pax=0, max_pax=0)

    def test_a_zero_duration_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity(duration_minutes=0)

    def test_a_negative_price_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity(price_per_person=Decimal("-1.00"))

    def test_a_group_price_is_optional(self) -> None:
        assert make_activity().price_per_group is None

    def test_a_negative_group_price_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity(price_per_group=Decimal("-1.00"))

    def test_a_negative_minimum_age_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity(min_age=-1)

    def test_a_minimum_age_of_zero_is_a_deliberate_statement(self) -> None:
        """§16.4: zero means infants are explicitly admitted, which is not the
        same as unrestricted."""
        assert make_activity(min_age=0).min_age == 0

    def test_the_tag_vocabulary_is_closed_here_too(self) -> None:
        make_tag(slug="water-sports", label="Water sports")
        make_activity(tags=["water-sports"])
        with pytest.raises(IntegrityError):
            make_activity(slug="other", tags=["watersports"])


@pytest.mark.django_db
class TestTheTwoMinimumAgesMustAgree:
    """§7.5.9 has the column, §16.4 has the key, and both are safety controls."""

    def test_agreeing_values_pass(self) -> None:
        activity = make_activity(min_age=8, requirements={"min_age": 8})
        activity.full_clean(exclude=["slug"])

    def test_disagreeing_values_are_refused_on_the_console_path(self) -> None:
        activity = make_activity(min_age=8, requirements={"min_age": 12})
        with pytest.raises(ValidationError) as exc:
            activity.full_clean(exclude=["slug"])
        assert "min_age" in exc.value.message_dict

    def test_either_may_be_absent(self) -> None:
        make_activity(min_age=8, requirements={}).full_clean(exclude=["slug"])

    def test_an_unknown_requirements_key_is_refused(self) -> None:
        """§16.4: typing `minimum_age` would create a listing whose age
        restriction silently does not exist."""
        activity = make_activity(requirements={"minimum_age": 8})
        with pytest.raises(ValidationError) as exc:
            activity.full_clean(exclude=["slug"])
        assert "requirements" in exc.value.message_dict


@pytest.mark.django_db
class TestSchedulesAreRulesNotDepartures:
    def test_a_schedule_saves_and_reads_back(self) -> None:
        schedule = make_activity_schedule()
        schedule.refresh_from_db()
        assert schedule.start_time == time(8, 30)
        assert schedule.capacity == 12

    def test_the_stored_mask_is_read_by_the_domain_unchanged(self) -> None:
        """Bit 0 is Monday, matching `date.weekday()`. If the column and
        `domain.schedules` disagreed, every departure would materialise a day
        out and nothing would raise."""
        schedule = make_activity_schedule()
        rule = ScheduleRule(
            weekday_mask=WeekdayMask(schedule.weekday_mask),
            start_time=schedule.start_time,
            valid_from=schedule.valid_from,
            valid_to=schedule.valid_to,
        )
        assert occurs_on(rule, date(2027, 8, 9)) is True  # a Monday
        assert occurs_on(rule, date(2027, 8, 15)) is False  # the Sunday after

    def test_a_mask_that_recurs_on_no_day_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity_schedule(weekday_mask=0)

    def test_a_mask_beyond_seven_days_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity_schedule(weekday_mask=0b10000000)

    def test_a_zero_capacity_schedule_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity_schedule(capacity=0)

    def test_an_open_ended_schedule_is_allowed(self) -> None:
        assert make_activity_schedule(valid_to=None).valid_to is None

    def test_an_inverted_validity_window_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_activity_schedule(valid_from=date(2027, 12, 31), valid_to=date(2027, 1, 1))

    def test_a_single_day_window_is_allowed(self) -> None:
        schedule = make_activity_schedule(valid_from=date(2027, 6, 1), valid_to=date(2027, 6, 1))
        assert schedule.valid_from == schedule.valid_to

    def test_deleting_an_activity_with_schedules_is_refused(self) -> None:
        schedule = make_activity_schedule()
        with pytest.raises(ProtectedError):
            schedule.activity.hard_delete()


@pytest.mark.django_db
class TestMedia:
    def test_media_hangs_off_any_owner_type(self) -> None:
        destination = make_destination()
        item = make_media(destination)
        assert (item.owner_type, item.owner_id) == ("destination", destination.pk)

    def test_dimensions_are_stored_for_the_cls_budget(self) -> None:
        """`next/image` reserves space from them; without them the page
        reflows when the image loads."""
        item = make_media()
        assert (item.width, item.height) == (1920, 1080)

    def test_a_zero_dimension_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            make_media(width=0)

    def test_one_hero_per_owner(self) -> None:
        destination = make_destination()
        make_media(destination, file_key="a", is_primary=True)
        with pytest.raises(IntegrityError):
            make_media(destination, file_key="b", is_primary=True)

    def test_two_owners_may_each_have_a_hero(self) -> None:
        first = make_destination()
        second = make_destination(region=first.region, slug="elsewhere", name="Elsewhere")
        make_media(first, is_primary=True)
        make_media(second, is_primary=True)

    def test_the_same_owner_id_in_two_tables_does_not_collide(self) -> None:
        """The polymorphic pair is the key, not the id: an attraction and a
        destination can both be row 1."""
        destination = make_destination()
        attraction = make_attraction(destination=destination)
        make_media(destination, is_primary=True)
        make_media(attraction, is_primary=True)
        assert Media.objects.filter(is_primary=True).count() == 2

    def test_ordering_is_hero_then_sort_order_then_id(self) -> None:
        destination = make_destination()
        third = make_media(destination, file_key="c", sort_order=20)
        second = make_media(destination, file_key="b", sort_order=10)
        first = make_media(destination, file_key="a", is_primary=True, sort_order=99)
        ordered = list(Media.objects.filter(owner_type="destination", owner_id=destination.pk))
        assert ordered == [first, second, third]

    def test_media_is_not_soft_deleted(self) -> None:
        """A removed image is removed: there is no re-registration case, and
        §7.7's uniqueness argument does not apply."""
        assert "deleted_at" not in {f.name for f in Media._meta.get_fields()}
