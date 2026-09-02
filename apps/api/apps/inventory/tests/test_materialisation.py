"""Schedules become departures — SRS §16.2, §8.8.

    activity_schedule is a recurring rule … activity_departure is a concrete,
    sellable instance at an exact instant with its own capacity counters.
    A nightly job (materialise_activity_departures) expands schedules into
    departures across a rolling horizon (default 180 days).

Two properties carry the weight, and neither is about how many rows appear.

**The instant is right.** A `start_time` is a local wall time. Resolving it in
the wrong zone puts every departure an hour out for half the year, in a way
that looks correct in the database and is wrong at the jetty. The test activity
lives in `Pacific/Auckland` — a zone that observes DST, unlike Zanzibar —
precisely so that a UTC-resolving implementation fails here rather than in
production somewhere §4.2 says the code is not allowed to know about.

**Running it twice changes nothing.** §8.8 marks the job idempotent, and
`acks_late` means a worker lost mid-run redelivers. A second pass that created
duplicates would be caught by the unique constraint; one that *reset counters*
would silently return capacity somebody had paid for.
"""

from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from apps.inventory import services
from apps.inventory.models import ActivityDeparture
from apps.inventory.tests.catalogue_rows import ZONE, make_activity_id, make_activity_schedule_id

pytestmark = pytest.mark.django_db

#: `make_activity_schedule_id` is Mon-Sat, 08:30, capacity 12, all of 2027.
MONDAY = dt.date(2027, 3, 1)


def _schedule(**overrides: object) -> tuple[int, int]:
    activity = make_activity_id()
    schedule = make_activity_schedule_id(activity)
    if overrides:
        for key, value in overrides.items():
            setattr(schedule, key, value)
        schedule.save()
    return activity.pk, schedule.pk


class TestItExpandsTheRule:
    def test_a_six_day_week_produces_six_departures_a_week(self) -> None:
        _schedule()
        created = services.materialise_departures(start=MONDAY, horizon_days=7)
        assert created == 6

    def test_the_excluded_day_is_the_one_the_mask_excludes(self) -> None:
        """Bit 0 is Monday. A mask read Sunday-first shifts every departure by
        a day and looks entirely plausible in a console."""
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=7)
        zone = ZoneInfo(ZONE)
        weekdays = {
            row.departs_at.astimezone(zone).weekday() for row in ActivityDeparture.objects.all()
        }
        assert weekdays == {0, 1, 2, 3, 4, 5}  # Monday to Saturday; no Sunday

    def test_the_horizon_bounds_it(self) -> None:
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=14)
        assert ActivityDeparture.objects.count() == 12

    def test_the_departure_carries_the_schedule_s_capacity(self) -> None:
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=2)
        assert {row.capacity_total for row in ActivityDeparture.objects.all()} == {12}

    def test_the_departure_knows_which_rule_made_it(self) -> None:
        """§7.5.9: `schedule_id` is null only for the ad-hoc departures a
        provider creates directly."""
        _, schedule_id = _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=2)
        assert {row.schedule_id for row in ActivityDeparture.objects.all()} == {schedule_id}

    def test_a_new_departure_starts_with_nothing_held_or_sold(self) -> None:
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=2)
        row = ActivityDeparture.objects.first()
        assert row is not None
        assert (row.capacity_held, row.capacity_sold, row.status) == (0, 0, "OPEN")


class TestTheInstantIsLocal:
    def test_the_wall_time_is_the_provider_s(self) -> None:
        """08:30 means half past eight where the boat leaves from."""
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=1)
        row = ActivityDeparture.objects.get()
        local = row.departs_at.astimezone(ZoneInfo(ZONE))
        assert (local.hour, local.minute) == (8, 30)

    def test_it_is_stored_in_utc(self) -> None:
        """§7.2: TIMESTAMPTZ in UTC, rendered in the destination's zone."""
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=1)
        assert ActivityDeparture.objects.get().departs_at.tzinfo is not None

    def test_the_wall_time_survives_a_daylight_saving_change(self) -> None:
        """The reason the test destination is in a DST zone at all.

        Auckland leaves daylight time on 2027-04-04. A departure the day
        before and the day after must both read 08:30 locally, and their UTC
        offsets must differ by an hour. An implementation that resolved the
        wall time in UTC would keep the offsets identical and move the boat.
        """
        _schedule(valid_from=dt.date(2027, 1, 1))
        services.materialise_departures(start=dt.date(2027, 4, 1), horizon_days=10)
        zone = ZoneInfo(ZONE)
        rows = {
            row.departs_at.astimezone(zone).date(): row.departs_at.astimezone(zone)
            for row in ActivityDeparture.objects.all()
        }
        before = rows[dt.date(2027, 4, 2)]
        after = rows[dt.date(2027, 4, 6)]
        assert (before.hour, before.minute) == (8, 30)
        assert (after.hour, after.minute) == (8, 30)
        assert before.utcoffset() != after.utcoffset()


class TestItIsIdempotent:
    def test_a_second_run_creates_nothing(self) -> None:
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=7)
        assert services.materialise_departures(start=MONDAY, horizon_days=7) == 0

    def test_a_second_run_leaves_the_counters_alone(self) -> None:
        """The failure that would matter. A re-run that reset `capacity_sold`
        would return capacity somebody had paid for, and nothing would say
        so."""
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=7)
        ActivityDeparture.objects.update(capacity_held=2, capacity_sold=6)

        services.materialise_departures(start=MONDAY, horizon_days=7)
        row = ActivityDeparture.objects.first()
        assert row is not None
        assert (row.capacity_held, row.capacity_sold) == (2, 6)

    def test_a_raised_capacity_reaches_new_departures_only(self) -> None:
        """§26.4: *"changes to price, cancellation policy or capacity take
        effect only for new bookings — never for existing ones."* Obtained
        from the unique constraint rather than implemented separately."""
        _, schedule_id = _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=7)

        from django.apps import apps as django_apps

        django_apps.get_model("catalogue", "ActivitySchedule").objects.filter(
            id=schedule_id
        ).update(capacity=20)
        services.materialise_departures(start=MONDAY, horizon_days=14)

        old = ActivityDeparture.objects.filter(departs_at__lt=_utc(MONDAY + dt.timedelta(days=7)))
        new = ActivityDeparture.objects.filter(departs_at__gte=_utc(MONDAY + dt.timedelta(days=7)))
        assert {row.capacity_total for row in old} == {12}
        assert {row.capacity_total for row in new} == {20}

    def test_extending_the_horizon_adds_only_the_new_dates(self) -> None:
        _schedule()
        services.materialise_departures(start=MONDAY, horizon_days=7)
        assert services.materialise_departures(start=MONDAY, horizon_days=14) == 6


def _utc(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(0, 0), tzinfo=ZoneInfo(ZONE))


class TestWhichSchedulesRun:
    def test_an_inactive_schedule_produces_nothing(self) -> None:
        """§16.2 lets a provider retire a rule without touching the departures
        it already produced."""
        _schedule(is_active=False)
        assert services.materialise_departures(start=MONDAY, horizon_days=7) == 0

    def test_a_schedule_outside_its_window_produces_nothing(self) -> None:
        _schedule(valid_from=dt.date(2028, 1, 1), valid_to=dt.date(2028, 12, 31))
        assert services.materialise_departures(start=MONDAY, horizon_days=7) == 0

    def test_an_open_ended_schedule_keeps_producing(self) -> None:
        """`valid_to` is None for the year-round tour a real provider runs;
        requiring an end date would make every one of them invent one."""
        _schedule(valid_to=None, valid_from=dt.date(2027, 1, 1))
        assert services.materialise_departures(start=dt.date(2030, 3, 4), horizon_days=7) == 6

    def test_two_schedules_on_one_activity_both_run(self) -> None:
        """A morning and an afternoon departure of the same tour."""
        activity = make_activity_id()
        make_activity_schedule_id(activity)
        second = make_activity_schedule_id(activity)
        second.start_time = dt.time(14, 0)
        second.save()

        assert services.materialise_departures(start=MONDAY, horizon_days=7) == 12


class TestTheTask:
    """The shell §8.8 schedules. It decides nothing."""

    def test_it_reports_what_it_did(self) -> None:
        """Counts rather than nothing, so the result backend carries something
        an operator can read when asked whether last night's run did anything.

        180 days from a Monday contains 25 Sundays, and the schedule runs on
        the other 155.
        """
        from apps.inventory.tasks import materialise_activity_departures

        _schedule(valid_to=None)
        result = materialise_activity_departures(today=MONDAY.isoformat())
        assert result == {"created": 155, "horizon_days": 180}

    def test_it_takes_the_horizon_from_configuration(self) -> None:
        """NFR-M07: the 180 days of §16.2 is `departures.horizon_days`, not a
        number in a task body."""
        from apps.common.config import get_setting

        assert get_setting("departures.horizon_days") == 180

    def test_the_date_crosses_the_queue_as_a_string(self) -> None:
        """Celery serialises arguments as JSON. A `date` object could not
        survive the queue, so passing one would work in a test and fail in a
        worker — the difference that only shows up in production."""
        import json

        json.dumps({"today": MONDAY.isoformat()})


class TestItIsScheduled:
    """§8.8: "Beat, nightly". The cadence is a row, not a dict."""

    def test_the_beat_entry_exists(self) -> None:
        """`CELERY_BEAT_SCHEDULER` is the database scheduler, so a schedule
        declared in code would never run: the scheduler reads
        `django_celery_beat_periodictask` and nothing else."""
        from django_celery_beat.models import PeriodicTask

        assert PeriodicTask.objects.filter(
            task="inventory.materialise_activity_departures"
        ).exists()

    def test_it_is_scheduled_nightly_rather_than_on_an_interval(self) -> None:
        from django_celery_beat.models import PeriodicTask

        row = PeriodicTask.objects.get(task="inventory.materialise_activity_departures")
        assert row.crontab is not None
        assert row.interval is None

    def test_the_scheduled_name_is_one_a_worker_can_resolve(self) -> None:
        """A `PeriodicTask` naming a task no worker registers fires on
        schedule and is discarded every time — a log full of `NotRegistered`
        and a job an operator believes is running."""
        from django_celery_beat.models import PeriodicTask

        from apps.inventory.tasks import materialise_activity_departures

        row = PeriodicTask.objects.get(task="inventory.materialise_activity_departures")
        assert row.task == materialise_activity_departures.name
