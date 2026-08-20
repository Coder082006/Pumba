"""The capacity counters - SRS §7.5.8, §7.5.9, §17.1.

Tables only. Nothing in Phase 3 writes a counter; the CHECK constraints ship now
so that the invariant Phase 5 must respect is in the schema before the first
writer exists, rather than in a reviewer's memory.

The references into `catalogue` are ids in the model (ADR 0012) and FOREIGN KEYs
here. The migration may say what the model may not, because a migration
dependency is a string and not an import - which is the whole point: the
database keeps its integrity and the module boundary keeps its seam.
"""

import uuid

import django.db.models.expressions
import django.utils.timezone
from django.db import migrations, models

from apps.common.db import attach_updated_at_trigger

TABLES = ["room_availability", "activity_departure"]

#: NO ACTION rather than CASCADE on the two owning references: a room type or
#: an activity with a calendar behind it must not be deletable out from under
#: it. `schedule_id` is the exception - §16.2 lets a recurring rule be retired,
#: and a departure somebody bought must survive that.
FOREIGN_KEYS = """
ALTER TABLE room_availability
    ADD CONSTRAINT room_availability_room_type_fk
    FOREIGN KEY (room_type_id) REFERENCES room_type(id);

ALTER TABLE activity_departure
    ADD CONSTRAINT activity_departure_activity_fk
    FOREIGN KEY (activity_id) REFERENCES activity(id);

ALTER TABLE activity_departure
    ADD CONSTRAINT activity_departure_schedule_fk
    FOREIGN KEY (schedule_id) REFERENCES activity_schedule(id) ON DELETE SET NULL;
"""

DROP_FOREIGN_KEYS = """
ALTER TABLE room_availability
    DROP CONSTRAINT IF EXISTS room_availability_room_type_fk;
ALTER TABLE activity_departure
    DROP CONSTRAINT IF EXISTS activity_departure_activity_fk;
ALTER TABLE activity_departure
    DROP CONSTRAINT IF EXISTS activity_departure_schedule_fk;
"""


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        # By name, not by import. `catalogue` owns room_type, activity and
        # activity_schedule, and they must exist before these keys reference
        # them.
        ("catalogue", "0004_activity_schedule_and_media"),
        ("common", "0002_updated_at_trigger"),
    ]

    operations = [
        migrations.CreateModel(
            name="ActivityDeparture",
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
                (
                    "public_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                ("version", models.IntegerField(default=0, editable=False)),
                ("activity_id", models.BigIntegerField(db_index=True)),
                (
                    "schedule_id",
                    models.BigIntegerField(blank=True, db_index=True, default=None, null=True),
                ),
                ("departs_at", models.DateTimeField()),
                ("capacity_total", models.SmallIntegerField()),
                ("capacity_held", models.SmallIntegerField(default=0)),
                ("capacity_sold", models.SmallIntegerField(default=0)),
                (
                    "price_override",
                    models.DecimalField(
                        blank=True, decimal_places=2, default=None, max_digits=14, null=True
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OPEN", "Open"),
                            ("FULL", "Full"),
                            ("CANCELLED", "Cancelled"),
                            ("CLOSED", "Closed"),
                        ],
                        default="OPEN",
                        max_length=20,
                    ),
                ),
            ],
            options={
                "db_table": "activity_departure",
                "ordering": ["activity_id", "departs_at"],
                "indexes": [
                    models.Index(
                        fields=["activity_id", "departs_at"], name="activity_departure_next_idx"
                    ),
                    models.Index(
                        fields=["departs_at", "status"], name="activity_departure_status_idx"
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("activity_id", "departs_at"),
                        name="activity_departure_one_per_instant",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "capacity_total__gte",
                                django.db.models.expressions.CombinedExpression(
                                    models.F("capacity_held"), "+", models.F("capacity_sold")
                                ),
                            )
                        ),
                        name="activity_departure_no_oversell",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("capacity_held__gte", 0),
                            ("capacity_sold__gte", 0),
                            ("capacity_total__gte", 0),
                        ),
                        name="activity_departure_counters_non_negative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("price_override__isnull", True),
                            ("price_override__gte", 0),
                            _connector="OR",
                        ),
                        name="activity_departure_price_non_negative",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="RoomAvailability",
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
                (
                    "public_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                ("version", models.IntegerField(default=0, editable=False)),
                ("room_type_id", models.BigIntegerField(db_index=True)),
                ("stay_date", models.DateField()),
                ("rooms_open", models.SmallIntegerField()),
                ("rooms_held", models.SmallIntegerField(default=0)),
                ("rooms_sold", models.SmallIntegerField(default=0)),
                (
                    "rate_override",
                    models.DecimalField(
                        blank=True, decimal_places=2, default=None, max_digits=14, null=True
                    ),
                ),
                ("min_nights", models.SmallIntegerField(blank=True, default=None, null=True)),
                ("is_closed", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "room_availability",
                "ordering": ["room_type_id", "stay_date"],
                "indexes": [
                    models.Index(
                        fields=["stay_date", "room_type_id"], name="room_availability_date_idx"
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("room_type_id", "stay_date"),
                        name="room_availability_one_row_per_night",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "rooms_open__gte",
                                django.db.models.expressions.CombinedExpression(
                                    models.F("rooms_held"), "+", models.F("rooms_sold")
                                ),
                            )
                        ),
                        name="room_availability_no_oversell",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("rooms_held__gte", 0), ("rooms_open__gte", 0), ("rooms_sold__gte", 0)
                        ),
                        name="room_availability_counters_non_negative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("rate_override__isnull", True),
                            ("rate_override__gte", 0),
                            _connector="OR",
                        ),
                        name="room_availability_rate_non_negative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("min_nights__isnull", True), ("min_nights__gt", 0), _connector="OR"
                        ),
                        name="room_availability_min_nights_positive",
                    ),
                ],
            },
        ),
        *[attach_updated_at_trigger(table) for table in TABLES],
        migrations.RunSQL(sql=FOREIGN_KEYS, reverse_sql=DROP_FOREIGN_KEYS),
    ]
