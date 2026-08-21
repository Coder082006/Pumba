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

from django.db import models

from apps.common.models import BaseModel, VersionedModel

__all__ = ["DepartureStatus", "ActivityDeparture"]


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
