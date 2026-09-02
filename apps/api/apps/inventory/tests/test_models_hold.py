"""`inventory_hold` — SRS §7.3, §7.6, §17.1, §17.2.

§17.1 I4 is the sentence this table exists to satisfy:

    Holds are explicit, time-boxed rows — never implicit reservations inferred
    from booking status.

So these tests are mostly about the row being *explicit*: it has a status that
distinguishes why it ended, a TTL that is a column rather than a convention,
and enough index support for the sweeper to find it. An inferred reservation
would satisfy none of those, and would be indistinguishable from a booking that
failed halfway.

The counter is not touched here. Moving `capacity_held` is the repository's
work, under lock, and it is tested where the lock is.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from django.db import IntegrityError, connection
from django.utils import timezone

from apps.inventory.models import HeldResource, HoldStatus, InventoryHold

pytestmark = pytest.mark.django_db


def _hold(**overrides: object) -> InventoryHold:
    values: dict[str, object] = {
        "trip_id": 1,
        "resource_type": HeldResource.ACTIVITY_DEPARTURE,
        "resource_id": 1,
        "quantity": 2,
        "expires_at": timezone.now() + dt.timedelta(minutes=20),
    }
    values.update(overrides)
    return InventoryHold.objects.create(**values)  # type: ignore[arg-type]


class TestItIsAnExplicitRow:
    """§17.1 I4."""

    def test_a_hold_starts_held(self) -> None:
        assert _hold().status == HoldStatus.HELD

    def test_a_hold_carries_its_own_expiry(self) -> None:
        """Time-boxed by a column. A TTL that lived only in configuration
        could be changed underneath a hold that was already taken."""
        assert _hold().expires_at is not None

    def test_the_four_states_are_the_ones_section_17_2_draws(self) -> None:
        assert set(HoldStatus.values) == {"HELD", "COMMITTED", "RELEASED", "EXPIRED"}

    def test_a_hold_is_addressed_by_token_outside_the_module(self) -> None:
        """§7.2 keeps the BIGSERIAL inside the database; §7.3 names
        `hold_token` as what everybody else uses."""
        assert isinstance(_hold().hold_token, uuid.UUID)

    def test_two_holds_never_share_a_token(self) -> None:
        token = _hold().hold_token
        with pytest.raises(IntegrityError):
            _hold(hold_token=token)

    def test_it_is_versioned_for_optimistic_locking(self) -> None:
        """§7.2 names this table; §32.3's VERSION_CONFLICT is the failure."""
        assert _hold().version == 0


class TestLiveness:
    """§17.1 I5: expiry is swept *and* re-checked, so a late sweeper is safe."""

    def test_a_fresh_hold_is_live(self) -> None:
        assert _hold().is_live(now=timezone.now())

    def test_a_hold_past_its_ttl_is_dead_before_the_sweeper_reaches_it(self) -> None:
        """The defensive half of I5. A hold that has expired is expired
        whether or not anything has got round to marking it."""
        stale = _hold(expires_at=timezone.now() - dt.timedelta(seconds=1))
        assert stale.status == HoldStatus.HELD
        assert not stale.is_live(now=timezone.now())

    @pytest.mark.parametrize(
        "status", [HoldStatus.COMMITTED, HoldStatus.RELEASED, HoldStatus.EXPIRED]
    )
    def test_a_terminal_hold_is_never_live(self, status: HoldStatus) -> None:
        assert not _hold(status=status).is_live(now=timezone.now())


class TestTheSchemaRefusesNonsense:
    def test_a_hold_for_no_capacity_is_rejected(self) -> None:
        """Zero seats held is not a hold; it is a row that decrements nothing
        and would still be swept, alerted on and reconciled."""
        with pytest.raises(IntegrityError):
            _hold(quantity=0)

    def test_a_negative_hold_is_rejected(self) -> None:
        with pytest.raises(IntegrityError):
            _hold(quantity=-1)

    def test_a_v1_hold_carries_no_dates(self) -> None:
        """§7.3's range covered a span of nights. A departure is an instant."""
        hold = _hold()
        assert hold.date_from is None
        assert hold.date_to is None

    def test_half_a_date_range_is_rejected(self) -> None:
        """Not a shorter range — a row nobody can interpret."""
        with pytest.raises(IntegrityError):
            _hold(date_from=dt.date(2027, 8, 12))

    def test_a_backwards_date_range_is_rejected(self) -> None:
        with pytest.raises(IntegrityError):
            _hold(date_from=dt.date(2027, 8, 12), date_to=dt.date(2027, 8, 11))


class TestTheSweeperCanFindItsWork:
    """§7.6: "INDEX(expires_at) WHERE status='HELD' — expiry sweeper"."""

    def test_the_expiry_index_is_partial(self) -> None:
        """Within a day of launch the terminal holds outnumber the live ones,
        and the sweeper reads only the live ones."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT indexdef FROM pg_indexes WHERE indexname = %s",
                ["inventory_hold_expiry_idx"],
            )
            row = cursor.fetchone()
        assert row is not None
        assert "WHERE" in row[0].upper()

    def test_the_prior_holds_of_a_trip_are_indexed(self) -> None:
        """§9.4.5 step 2 releases them on every re-quote."""
        assert any(index.name == "inventory_hold_trip_idx" for index in InventoryHold._meta.indexes)

    def test_holds_are_indexed_by_the_counter_they_justify(self) -> None:
        """§17.4's reconciliation sums live holds per departure."""
        assert any(
            index.name == "inventory_hold_resource_idx" for index in InventoryHold._meta.indexes
        )


class TestTheModuleBoundaryIsIntact:
    def test_the_table_belongs_to_inventory(self) -> None:
        assert InventoryHold._meta.app_label == "inventory"

    def test_it_declares_no_relation_to_anything(self) -> None:
        """ADR 0012 and ADR 0022: `trip_id` and `resource_id` are ids, not
        `ForeignKey`s — one because the reference points up the module graph,
        the other because it is polymorphic."""
        assert [f.name for f in InventoryHold._meta.get_fields() if f.is_relation] == []

    def test_it_is_not_soft_deleted(self) -> None:
        """§7.2: booking records are never soft-deleted. A hold that vanished
        from the default manager would take its capacity with it and leave a
        counter nothing accounts for."""
        assert not hasattr(InventoryHold, "deleted_at")

    def test_the_hold_has_no_second_identifier(self) -> None:
        """`hold_token` is §7.3's. A `public_id` beside it would be two names
        for one row, which is one more than anybody keeps straight."""
        assert not hasattr(InventoryHold, "public_id")

    def test_trip_id_carries_no_foreign_key(self) -> None:
        """ADR 0022. `inventory` is L2 and `trip` is L3; a constraint pointing
        uphill is the dependency §6.4 forbids, written in DDL."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'inventory_hold'::regclass AND contype = 'f'"
            )
            assert cursor.fetchall() == []


class TestTheCounterTableIsStillTheOnlyOne:
    def test_a_hold_holds_no_counter_of_its_own(self) -> None:
        """§17.1 I1: capacity lives in exactly one place per resource type. A
        `*_held` column here would be a second copy of the same number, and
        the first thing to drift."""
        assert not [f for f in InventoryHold._meta.get_fields() if f.name.endswith("_held")]
