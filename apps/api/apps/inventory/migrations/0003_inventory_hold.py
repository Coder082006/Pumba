"""`inventory_hold` — SRS §7.3, §7.6, §17.2.

The first table in the schema that *writes* a capacity counter's justification.
`activity_departure` has said how much capacity is spoken for since Phase 3;
this says why, and §17.4's reconciliation is the job of noticing when the two
stop agreeing.

**Two references leave this table and neither gets a FOREIGN KEY**, for
different reasons.

`trip_id` points at `trip.trip`, which is L3 while `inventory` is L2 (ADR
0022). Migration 0001 added SQL foreign keys into `catalogue` precisely because
that direction is downhill and a migration dependency is a string rather than
an import; this one would point uphill, and the module graph forbids it in DDL
for the same reason it forbids it in Python.

`resource_id` is polymorphic (§7.3): it addresses whichever counter table
`resource_type` names. A column that means two things cannot carry a constraint
that means one, so integrity for it is the `hold()` routine's, which resolves
the row under lock before writing anything that references it.
"""

import uuid

import django.utils.timezone
from django.db import migrations, models

from apps.common.db import attach_updated_at_trigger


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_room_availability_deferred_to_v2"),
    ]

    operations = [
        migrations.CreateModel(
            name="InventoryHold",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now, editable=False
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(default=django.utils.timezone.now, editable=False),
                ),
                ("version", models.IntegerField(default=0, editable=False)),
                ("hold_token", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("trip_id", models.BigIntegerField(db_index=True)),
                (
                    "resource_type",
                    models.CharField(
                        choices=[("ACTIVITY_DEPARTURE", "Activity departure")], max_length=32
                    ),
                ),
                ("resource_id", models.BigIntegerField()),
                ("date_from", models.DateField(blank=True, default=None, null=True)),
                ("date_to", models.DateField(blank=True, default=None, null=True)),
                ("quantity", models.SmallIntegerField()),
                ("expires_at", models.DateTimeField()),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("HELD", "Held"),
                            ("COMMITTED", "Committed"),
                            ("RELEASED", "Released"),
                            ("EXPIRED", "Expired"),
                        ],
                        default="HELD",
                        max_length=20,
                    ),
                ),
            ],
            options={
                "db_table": "inventory_hold",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(
                        condition=models.Q(("status", "HELD")),
                        fields=["expires_at"],
                        name="inventory_hold_expiry_idx",
                    ),
                    models.Index(fields=["trip_id", "status"], name="inventory_hold_trip_idx"),
                    models.Index(
                        fields=["resource_type", "resource_id", "status"],
                        name="inventory_hold_resource_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("quantity__gt", 0)),
                        name="inventory_hold_quantity_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("date_from__isnull", True), ("date_to__isnull", True)),
                            models.Q(
                                ("date_from__isnull", False),
                                ("date_to__gte", models.F("date_from")),
                                ("date_to__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="inventory_hold_dates_are_whole_and_ordered",
                    ),
                ],
            },
        ),
        # §7.2: the ORM maintains `updated_at` on save, the trigger maintains
        # it on everything that is not a save. The sweeper updates holds in
        # bulk, which is exactly a path that bypasses `Model.save()`.
        attach_updated_at_trigger("inventory_hold"),
    ]
