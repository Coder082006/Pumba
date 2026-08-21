"""The capacity counter — SRS §7.5.9, §16.3, §17.1, and Q2.

Two things are being proved here, and only one of them is about columns.

**Oversell is impossible in the schema.** §17.1 I2 and §16.3 both say the
database constraint is what stops a race, not the application logic above it.
Phase 3 writes no counter at all, so these constraints are asserted directly:
when Phase 5 adds the first `UPDATE`, the invariant it must respect is already
there and already tested.

**`catalogue` has no path to these rows.** Q2 asked for a test asserting the
catalogue module exposes no mutation of a capacity counter. ADR 0011 put the
table in its §6.4 home and ADR 0012 made the references ids rather than
`ForeignKey`s, which finished the job: there is no reverse accessor, no
relation and no import, so catalogue cannot read a capacity counter at all, let
alone write one. The assertions below are the belt to that braces, and they are
deliberately about *absence*, because absence is what a future reader will be
tempted to "fix".

The parent rows come from `catalogue_rows`, which uses `apps.get_model` rather
than an import - the contract is not relaxed for tests.

`room_availability` was the second counter table until ADR 0013 made
accommodation a location reference. Its absence is asserted below rather than
left implicit: a dropped table that nothing checks for is a table somebody
reintroduces by copying a v2 branch.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError, connection, models
from django.utils import timezone

from apps.inventory.models import ActivityDeparture, DepartureStatus
from apps.inventory.tests.catalogue_rows import make_activity_id, make_activity_schedule_id


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

    def test_the_table_belongs_to_inventory(self) -> None:
        assert ActivityDeparture._meta.app_label == "inventory"

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
        for model in (ActivityDeparture,):
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


class TestSchemaMatchesSection759:
    def test_the_table_is_named_as_the_srs_names_it(self) -> None:
        assert ActivityDeparture._meta.db_table == "activity_departure"

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

    def test_it_carries_the_optimistic_lock_column(self) -> None:
        """§7.2 names the table; §32.3's VERSION_CONFLICT is the failure."""
        assert ActivityDeparture._meta.get_field("version").default == 0

    def test_the_price_override_is_a_decimal(self) -> None:
        price = ActivityDeparture._meta.get_field("price_override")
        assert isinstance(price, models.DecimalField)
        assert (price.max_digits, price.decimal_places) == (14, 2)

    def test_the_departure_statuses_are_the_four_the_srs_names(self) -> None:
        assert {c.value for c in DepartureStatus} == {"OPEN", "FULL", "CANCELLED", "CLOSED"}


@pytest.mark.django_db
class TestSellableIsIndicativeOnly:
    def test_sellable_is_total_minus_held_minus_sold(self) -> None:
        assert _departure(capacity_total=12, capacity_held=1, capacity_sold=2).sellable == 9

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
        """The §16.3 constraint Q2 asked to ship before any writer exists."""
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


@pytest.mark.django_db
class TestRoomAvailabilityHasLeftTheV1Schema:
    """ADR 0013. A stay anchor has no rooms, so there is nothing to count.

    Asserted rather than assumed. Both the model and the table are cheap to
    reintroduce by copying a v2 branch, and nothing else in the suite would
    notice if one came back.
    """

    def test_the_model_is_gone(self) -> None:
        with pytest.raises(LookupError):
            django_apps.get_model("inventory", "RoomAvailability")

    def test_the_table_is_gone(self) -> None:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('room_availability')")
            assert cursor.fetchone()[0] is None

    def test_the_only_counter_table_is_the_activity_one(self) -> None:
        """§17.1 I1 as amended in v1.2: one place per resource type, and in v1
        there is one resource type carrying capacity."""
        counters = {
            model._meta.db_table
            for model in django_apps.get_app_config("inventory").get_models()
            if any(f.name.endswith("_held") for f in model._meta.get_fields())
        }
        assert counters == {"activity_departure"}
