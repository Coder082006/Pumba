"""The capacity counters — SRS §7.5.8, §7.5.9, §14.3, §16.3, §17.1, and Q2.

Two things are being proved here, and only one of them is about columns.

**Oversell is impossible in the schema.** §17.1 I2 and §16.3 both say the
database constraint is what stops a race, not the application logic above it.
Phase 3 writes no counter at all, so these constraints are asserted directly:
when Phase 5 adds the first `UPDATE`, the invariant it must respect is already
there and already tested.

**`catalogue` has no path to these rows.** Q2 asked for a test asserting the
catalogue module exposes no mutation of `room_availability`. ADR 0011 put the
table in its §6.4 home and ADR 0012 made the references ids rather than
`ForeignKey`s, which finished the job: there is no reverse accessor, no
relation and no import, so catalogue cannot read a capacity counter at all, let
alone write one. The assertions below are the belt to that braces, and they are
deliberately about *absence*, because absence is what a future reader will be
tempted to "fix".

The parent rows come from `catalogue_rows`, which uses `apps.get_model` rather
than an import - the contract is not relaxed for tests.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, models
from django.utils import timezone

from apps.inventory.models import ActivityDeparture, DepartureStatus, RoomAvailability
from apps.inventory.tests.catalogue_rows import (
    make_activity_id,
    make_activity_schedule_id,
    make_room_type_id,
)

NIGHT = dt.date(2027, 8, 12)


def _availability(**overrides: object) -> RoomAvailability:
    values: dict[str, object] = {
        "room_type_id": overrides.pop("room_type_id", None) or make_room_type_id().pk,
        "stay_date": NIGHT,
        "rooms_open": 5,
    }
    values.update(overrides)
    return RoomAvailability.objects.create(**values)  # type: ignore[arg-type]


def _departure(**overrides: object) -> ActivityDeparture:
    values: dict[str, object] = {
        "activity_id": overrides.pop("activity_id", None) or make_activity_id().pk,
        "departs_at": timezone.now() + dt.timedelta(days=7),
        "capacity_total": 12,
    }
    values.update(overrides)
    return ActivityDeparture.objects.create(**values)  # type: ignore[arg-type]


class TestCatalogueHasNoPathToTheseRows:
    """Q2's guard, and the reason ADRs 0011 and 0012 exist."""

    def test_the_tables_belong_to_inventory(self) -> None:
        assert RoomAvailability._meta.app_label == "inventory"
        assert ActivityDeparture._meta.app_label == "inventory"

    def test_a_room_type_has_no_reverse_accessor_to_its_calendar(self) -> None:
        """With a `ForeignKey` this would be `room_type.availability`, and
        catalogue code could write capacity with no import to catch."""
        room_type = django_apps.get_model("catalogue", "RoomType")
        assert not hasattr(room_type, "availability")

    def test_an_activity_has_no_reverse_accessor_to_its_departures(self) -> None:
        activity = django_apps.get_model("catalogue", "Activity")
        assert not hasattr(activity, "departures")

    def test_no_catalogue_model_can_reach_an_inventory_model(self) -> None:
        for name in ("RoomType", "Activity", "ActivitySchedule", "Accommodation"):
            model = django_apps.get_model("catalogue", name)
            reachable = {
                f.related_model._meta.app_label
                for f in model._meta.get_fields()
                if f.is_relation and getattr(f, "related_model", None) is not None
            }
            assert "inventory" not in reachable, f"{name} can reach inventory"

    def test_these_models_hold_no_relation_into_catalogue_either(self) -> None:
        """The references are ids. import-linter enforces the import ban; this
        catches the Django-level relation that would not need one."""
        for model in (RoomAvailability, ActivityDeparture):
            relations = [f for f in model._meta.get_fields() if f.is_relation]
            assert relations == [], f"{model.__name__} declares {relations}"

    def test_the_module_imports_nothing_from_catalogue(self) -> None:
        """Contract `private-catalogue` says this across the whole module; this
        says it about the one file that would need the import."""
        import inspect
        import io
        import tokenize

        from apps.inventory import models as inventory_models

        executable = " ".join(
            token.string
            for token in tokenize.generate_tokens(
                io.StringIO(inspect.getsource(inventory_models)).readline
            )
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        )
        assert "catalogue" not in executable


class TestSchemaMatchesSection758And759:
    def test_the_tables_are_named_as_the_srs_names_them(self) -> None:
        assert RoomAvailability._meta.db_table == "room_availability"
        assert ActivityDeparture._meta.db_table == "activity_departure"

    def test_every_section_758_column_exists(self) -> None:
        expected = {
            "room_type_id",
            "stay_date",
            "rooms_open",
            "rooms_held",
            "rooms_sold",
            "rate_override",
            "min_nights",
            "is_closed",
            "version",
        }
        assert {f.name for f in RoomAvailability._meta.get_fields()} >= expected

    def test_every_section_759_column_exists(self) -> None:
        expected = {
            "activity_id",
            "schedule_id",
            "departs_at",
            "capacity_total",
            "capacity_held",
            "capacity_sold",
            "price_override",
            "status",
            "version",
        }
        assert {f.name for f in ActivityDeparture._meta.get_fields()} >= expected

    def test_both_carry_the_optimistic_lock_column(self) -> None:
        """§7.2 names both tables; §32.3's VERSION_CONFLICT is the failure."""
        assert RoomAvailability._meta.get_field("version").default == 0
        assert ActivityDeparture._meta.get_field("version").default == 0

    def test_the_rate_override_is_a_decimal(self) -> None:
        rate = RoomAvailability._meta.get_field("rate_override")
        assert isinstance(rate, models.DecimalField)
        assert (rate.max_digits, rate.decimal_places) == (14, 2)

    def test_the_departure_statuses_are_the_four_the_srs_names(self) -> None:
        assert {c.value for c in DepartureStatus} == {"OPEN", "FULL", "CANCELLED", "CLOSED"}


@pytest.mark.django_db
class TestOversellIsImpossible:
    def test_a_calendar_row_saves_and_reads_back(self) -> None:
        row = _availability()
        row.refresh_from_db()
        assert (row.rooms_open, row.rooms_held, row.rooms_sold) == (5, 0, 0)

    def test_held_plus_sold_may_equal_open(self) -> None:
        row = _availability(rooms_open=5, rooms_held=2, rooms_sold=3)
        assert row.sellable == 0

    def test_held_plus_sold_may_not_exceed_open(self) -> None:
        """The §14.3 constraint Q2 asked to ship now, before any writer."""
        with pytest.raises(IntegrityError):
            _availability(rooms_open=5, rooms_held=3, rooms_sold=3)

    def test_a_negative_counter_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            _availability(rooms_held=-1)

    def test_one_row_per_room_type_per_night(self) -> None:
        room = make_room_type_id()
        _availability(room_type_id=room.pk)
        with pytest.raises(IntegrityError):
            _availability(room_type_id=room.pk)

    def test_the_same_night_for_two_room_types_is_fine(self) -> None:
        first = make_room_type_id()
        second = make_room_type_id(accommodation=first.accommodation, name="Suite")
        _availability(room_type_id=first.pk)
        _availability(room_type_id=second.pk)

    def test_a_negative_rate_override_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            _availability(rate_override=Decimal("-1.00"))

    def test_a_zero_minimum_stay_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            _availability(min_nights=0)

    def test_a_calendar_row_for_no_such_room_type_is_refused(self) -> None:
        """The FOREIGN KEY the migration adds in SQL. The column is a plain
        integer in the model, and integrity is not therefore optional."""
        with pytest.raises(IntegrityError):
            _availability(room_type_id=999_999)


@pytest.mark.django_db
class TestSellableIsIndicativeOnly:
    def test_sellable_is_open_minus_held_minus_sold(self) -> None:
        row = _availability(rooms_open=5, rooms_held=1, rooms_sold=2)
        assert row.sellable == 2

    def test_a_closed_night_sells_nothing_even_with_rooms_open(self) -> None:
        """§7.5.8 stop-sell. A closed night is a decision; an empty one is an
        outcome, and §24.13 tells the tourist which it is."""
        row = _availability(rooms_open=5, is_closed=True)
        assert row.sellable == 0

    def test_a_cancelled_departure_sells_nothing(self) -> None:
        assert _departure(status=DepartureStatus.CANCELLED).sellable == 0

    def test_a_full_departure_sells_nothing_even_if_the_arithmetic_allows(self) -> None:
        departure = _departure(capacity_total=12, capacity_sold=6, status=DepartureStatus.FULL)
        assert departure.sellable == 0


@pytest.mark.django_db
class TestDepartures:
    def test_a_departure_saves_and_reads_back(self) -> None:
        departure = _departure()
        departure.refresh_from_db()
        assert departure.status == DepartureStatus.OPEN
        assert departure.capacity_held == 0

    def test_capacity_may_not_be_oversold(self) -> None:
        with pytest.raises(IntegrityError):
            _departure(capacity_total=12, capacity_held=6, capacity_sold=7)

    def test_one_departure_per_activity_per_instant(self) -> None:
        activity = make_activity_id()
        when = timezone.now() + dt.timedelta(days=3)
        _departure(activity_id=activity.pk, departs_at=when)
        with pytest.raises(IntegrityError):
            _departure(activity_id=activity.pk, departs_at=when)

    def test_an_ad_hoc_departure_has_no_schedule(self) -> None:
        """§16.2: providers may create departures directly."""
        assert _departure().schedule_id is None

    def test_a_departure_may_come_from_a_schedule(self) -> None:
        activity = make_activity_id()
        schedule = make_activity_schedule_id(activity)
        departure = _departure(activity_id=activity.pk, schedule_id=schedule.pk)
        assert departure.schedule_id == schedule.pk

    def test_retiring_a_schedule_leaves_its_departures_standing(self) -> None:
        """`ON DELETE SET NULL`. A departure somebody bought must not vanish
        because the recurring rule that produced it was retired."""
        activity = make_activity_id()
        schedule = make_activity_schedule_id(activity)
        departure = _departure(activity_id=activity.pk, schedule_id=schedule.pk)
        schedule.hard_delete()
        departure.refresh_from_db()
        assert departure.schedule_id is None

    def test_a_departure_for_no_such_activity_is_refused(self) -> None:
        with pytest.raises(IntegrityError):
            _departure(activity_id=999_999)

    def test_departs_at_is_stored_in_utc(self) -> None:
        """§7.2: TIMESTAMPTZ in UTC, rendered in the destination's zone."""
        departure = _departure(departs_at=timezone.now() + dt.timedelta(days=1))
        departure.refresh_from_db()
        assert departure.departs_at.utcoffset() == dt.timedelta(0)
