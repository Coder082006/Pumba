"""inventory module — SRS §6.4.

    Owns:       activity_departure, inventory_hold
    Interface:  check_availability(), hold(), commit(), release()
    Depends on: catalogue
    Layer:      L2

Data-access layer (SRS §8.2 layer 4).

§17.1 states the principles these tables exist to serve, and the first is the
reason they are here rather than in `catalogue`:

    I1  Capacity counters live in exactly one place per resource type

In v1 that is `activity_departure` alone. `room_availability` was here until
ADR 0013 made accommodation a location reference rather than a product; it is
deferred to v2 and dropped by migration 0002.

**Phase 3 creates the tables and nothing else.** No hold, no commit, no
release, no materialisation job, no calendar upsert, and no code path anywhere
that increments a counter. Rows arrive only through test factories and the seed
loader. That restraint is what makes the §24.11 and §24.13 figures honest about
being indicative: there is no writer yet whose staleness could be argued about.

What does ship now is the arithmetic that makes oversell impossible later:

    CHECK (capacity_held + capacity_sold <= capacity_total)

§16.3 and §17.1 both say the constraint is what stops a race, not the
application logic above it. Adding it now costs nothing and means that when
Phase 5 writes the first counter update, the invariant it must respect is
already in the schema rather than in a reviewer's memory.

`VersionedModel` is mixed in for the same reason: §7.2 names the table for
optimistic locking, and §32.3's `VERSION_CONFLICT` is the failure mode. The
column is inert this phase.

**The references into `catalogue` are ids, not `ForeignKey`s** (ADR 0012).
§6.4 lets `inventory` depend on `catalogue`, but the dependency is on its
*service interface*: contract `private-catalogue` forbids importing
`apps.catalogue.models` from any other module, in either direction, because
that import is what a Django `ForeignKey` needs and a real relation is what
§6.2 and §44.2 would have to unpick to extract a seam.

So the columns are plain indexed integers, and the referential integrity is a
constraint added in SQL by the migration below - which depends on catalogue's
migration by name, and a migration dependency is a string, not an import.

The pleasant side effect is that Q2's guard becomes absolute. There is no
`activity.departures` accessor for catalogue code to reach through, no reverse
relation, and no import: `catalogue` cannot read a capacity counter at all, let
alone write one.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.db import models

from apps.common.models import BaseModel, TimestampedModel, VersionedModel

__all__ = [
    "DepartureStatus",
    "ActivityDeparture",
    "HoldStatus",
    "HeldResource",
    "InventoryHold",
]


class DepartureStatus(models.TextChoices):
    """§7.5.9: OPEN/FULL/CANCELLED/CLOSED."""

    OPEN = "OPEN", "Open"
    FULL = "FULL", "Full"
    CANCELLED = "CANCELLED", "Cancelled"
    CLOSED = "CLOSED", "Closed"


class ActivityDeparture(BaseModel, VersionedModel):
    """SRS §7.5.9 and §16.2. A concrete, sellable instant.

    §16.2 separates this from `activity_schedule` deliberately: the schedule is
    a recurring rule, this is the thing with counters. The nightly
    `materialise_activity_departures` job that expands one into the other is
    Phase 5, and `schedule` is null for the ad-hoc departures a provider
    creates directly.

    `departs_at` is `TIMESTAMPTZ` stored in UTC and rendered in the
    destination's zone, per §7.2. The schedule's `start_time` is a local wall
    time; resolving one into the other is the materialisation job's work, and
    doing it in the wrong zone is the mistake that puts every departure an hour
    out twice a year.
    """

    #: -> catalogue.activity.id.
    activity_id = models.BigIntegerField(db_index=True)
    #: -> catalogue.activity_schedule.id, null for the ad-hoc departures §16.2
    #: lets a provider create directly. `ON DELETE SET NULL` in the migration:
    #: retiring a recurring rule must not delete a departure somebody bought.
    schedule_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)

    departs_at = models.DateTimeField()

    capacity_total = models.SmallIntegerField()
    capacity_held = models.SmallIntegerField(default=0)
    capacity_sold = models.SmallIntegerField(default=0)

    price_override = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    status = models.CharField(
        max_length=20, choices=DepartureStatus.choices, default=DepartureStatus.OPEN
    )

    class Meta:
        db_table = "activity_departure"
        ordering = ["activity_id", "departs_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["activity_id", "departs_at"], name="activity_departure_one_per_instant"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    capacity_total__gte=models.F("capacity_held") + models.F("capacity_sold")
                ),
                name="activity_departure_no_oversell",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    capacity_total__gte=0, capacity_held__gte=0, capacity_sold__gte=0
                ),
                name="activity_departure_counters_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(price_override__isnull=True) | models.Q(price_override__gte=0),
                name="activity_departure_price_non_negative",
            ),
        ]
        indexes = [
            # §24.10 shows the next 30 days for one activity.
            models.Index(fields=["activity_id", "departs_at"], name="activity_departure_next_idx"),
            models.Index(fields=["departs_at", "status"], name="activity_departure_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.activity_id}@{self.departs_at.isoformat()}"

    @property
    def sellable(self) -> int:
        """§16.3. Indicative only.

        §17.1 I3: search may read a stale figure; committing a booking may
        not. The authoritative check happens under row lock inside the
        committing transaction, in Phase 5.
        """
        if self.status != DepartureStatus.OPEN:
            return 0
        return self.capacity_total - self.capacity_held - self.capacity_sold


class HoldStatus(models.TextChoices):
    """§17.2's four states.

    `HELD` is the only live one; the other three are terminal, and which one a
    hold ends in is the whole of its history — committed because the money
    arrived, released because something gave it back, expired because nobody
    came. A single `is_active` boolean would lose that distinction, and §17.4's
    reconciliation is exactly the job of noticing when the counters and the
    reasons disagree.
    """

    HELD = "HELD", "Held"
    COMMITTED = "COMMITTED", "Committed"
    RELEASED = "RELEASED", "Released"
    EXPIRED = "EXPIRED", "Expired"


class HeldResource(models.TextChoices):
    """What a hold is against.

    §7.3 draws `inventory_hold` polymorphically — `resource_type` plus
    `resource_id` — because it was written when two counter tables existed. In
    v1 there is one (§17.1 I1, ADR 0013), so this enum has a single member.

    It is kept polymorphic anyway, and that is a deliberate cost. A hold row
    that named `activity_departure_id` directly would have to be migrated when
    `room_availability` returns in v2, and every reader of it rewritten; the
    enum costs one column and a `WHERE` clause now.
    """

    ACTIVITY_DEPARTURE = "ACTIVITY_DEPARTURE", "Activity departure"


class InventoryHold(TimestampedModel, VersionedModel):
    """SRS §7.3, §17.2. Capacity that is spoken for but not yet paid.

    §17.1 I4 is the reason this is a row rather than a flag:

        Holds are explicit, time-boxed rows — never implicit reservations
        inferred from booking status.

    An inferred reservation cannot be swept, cannot be counted, and cannot be
    told apart from a booking that failed halfway. This table is what makes
    §17.4's reconciliation possible at all: the counter says how much capacity
    is spoken for, and these rows say *why*, and a nightly job compares them.

    **No `public_id`, and no `SoftDeleteModel`.** §7.3 names `hold_token` as
    this table's unique external identifier, so `BaseModel` would give it a
    second UUID meaning the same thing — two identifiers for one row is one
    more than anybody can keep straight. And `common.models.SoftDeleteModel` is
    explicit that booking and financial records are excluded (§7.2): a hold
    that vanished from the default manager would take its capacity with it and
    leave a counter nothing accounts for.

    **`trip_id` carries no foreign key** (ADR 0022). `inventory` is L2 and
    `trip` is L3; the SQL foreign keys `inventory` does have all point at
    `catalogue`, which is downhill. A constraint pointing uphill would be the
    dependency §6.4 forbids, written in DDL instead of in an import. `booking`
    is the module that can see both sides, and it is what keeps this column
    honest.
    """

    #: §7.3's `(U)`. The identifier this row is known by outside `inventory` —
    #: DTOs carry it, §7.2 keeps the BIGSERIAL inside the database.
    hold_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)

    #: -> trip.trip.id. No FK; see the class docstring and ADR 0022.
    trip_id = models.BigIntegerField(db_index=True)

    resource_type = models.CharField(max_length=32, choices=HeldResource.choices)
    #: -> activity_departure.id while `resource_type` has one member.
    resource_id = models.BigIntegerField()

    #: §7.3 carries a date range because a room hold covered a span of nights.
    #: A departure is an instant, so both are null for every v1 hold. They stay
    #: on the table for the same reason `itinerary_item.room_type_id` did not:
    #: the column is cheap and re-deriving it in v2 is not.
    date_from = models.DateField(null=True, blank=True, default=None)
    date_to = models.DateField(null=True, blank=True, default=None)

    #: Seats, for an activity departure. Rooms, when v2 returns.
    quantity = models.SmallIntegerField()

    expires_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=HoldStatus.choices, default=HoldStatus.HELD)

    class Meta:
        db_table = "inventory_hold"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="inventory_hold_quantity_positive"
            ),
            # Either both dates or neither, and ordered when present. A half
            # range is not a shorter range; it is a row nobody can interpret.
            models.CheckConstraint(
                condition=(
                    models.Q(date_from__isnull=True, date_to__isnull=True)
                    | models.Q(
                        date_from__isnull=False,
                        date_to__isnull=False,
                        date_to__gte=models.F("date_from"),
                    )
                ),
                name="inventory_hold_dates_are_whole_and_ordered",
            ),
        ]
        indexes = [
            # §7.6, verbatim: "INDEX(expires_at) WHERE status='HELD' — expiry
            # sweeper". Partial because the sweeper reads only live holds and
            # the terminal ones outnumber them within a day of launch.
            models.Index(
                fields=["expires_at"],
                condition=models.Q(status=HoldStatus.HELD),
                name="inventory_hold_expiry_idx",
            ),
            # §9.4.5 step 2: "release any prior holds belonging to this trip".
            models.Index(fields=["trip_id", "status"], name="inventory_hold_trip_idx"),
            # §17.4's reconciliation sums live holds per counter row.
            models.Index(
                fields=["resource_type", "resource_id", "status"],
                name="inventory_hold_resource_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.status} {self.quantity}x {self.resource_type}#{self.resource_id}"

    def is_live(self, *, now: datetime) -> bool:
        """HELD and not yet past its TTL.

        §17.1 I5: expiry is driven by a sweeper *and defensively re-checked at
        commit*, so a delayed sweeper cannot cause an oversell. That second
        check is this method — a hold whose `expires_at` has passed is dead
        whether or not the sweeper has reached it yet.
        """
        return self.status == HoldStatus.HELD and self.expires_at > now
