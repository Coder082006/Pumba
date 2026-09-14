"""`booking`, `booking_activity`, `booking_transfer`, `booking_status_history`.

SRS §7.5.12 and ADR 0025. The first tables `booking` has ever had: until now the
module quoted trips (ADR 0022) without being able to record that anybody bought
one.

**`booking_status_history` is append-only in the database.** R26 says so, and
the trigger below is what makes it true for every writer — the ORM, a shell, a
data migration, a hand-run correction. An UPDATE or DELETE raises — including
the delete Django issues on the way to deleting a booking, because the ORM
cascades in Python row by row. So a booking with history cannot be deleted at
all, which is exactly §7.2's "never soft-deleted or hard-deleted" enforced
rather than hoped for. Test databases are unaffected: they roll back or TRUNCATE,
and neither fires a row trigger.

`booking_accommodation` is not created. ADR 0013 reserves it.
"""

import uuid
from decimal import Decimal

import django.contrib.gis.db.models.fields
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

from apps.common.db import attach_updated_at_trigger

APPEND_ONLY_SQL = """
CREATE OR REPLACE FUNCTION booking_status_history_is_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'booking_status_history is append-only (SRS R26): % refused', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS booking_status_history_append_only ON booking_status_history;
CREATE TRIGGER booking_status_history_append_only
    BEFORE UPDATE OR DELETE ON booking_status_history
    FOR EACH ROW EXECUTE FUNCTION booking_status_history_is_append_only();
"""

DROP_APPEND_ONLY_SQL = """
DROP TRIGGER IF EXISTS booking_status_history_append_only ON booking_status_history;
DROP FUNCTION IF EXISTS booking_status_history_is_append_only();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0001_periodic_jobs"),
    ]

    operations = [
        migrations.CreateModel(
            name="Booking",
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
                ("reference", models.CharField(editable=False, max_length=20, unique=True)),
                ("trip_id", models.BigIntegerField(db_index=True)),
                ("tourist_id", models.BigIntegerField()),
                ("provider_id", models.BigIntegerField()),
                (
                    "booking_type",
                    models.CharField(
                        choices=[
                            ("ACCOMMODATION", "Accommodation"),
                            ("ACTIVITY", "Activity"),
                            ("TRANSFER", "Transfer"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("PENDING", "Pending"),
                            ("AWAITING_PROVIDER", "Awaiting provider"),
                            ("CONFIRMED", "Confirmed"),
                            ("IN_PROGRESS", "In progress"),
                            ("COMPLETED", "Completed"),
                            ("CANCELLED", "Cancelled"),
                            ("REFUNDED", "Refunded"),
                            ("NO_SHOW", "No-show"),
                            ("FAILED", "Failed"),
                        ],
                        default="PENDING",
                        max_length=20,
                    ),
                ),
                ("starts_at", models.DateTimeField()),
                ("ends_at", models.DateTimeField()),
                ("pax_count", models.SmallIntegerField()),
                ("gross_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "fee_amount",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14),
                ),
                (
                    "tax_amount",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=14),
                ),
                ("currency", models.CharField(max_length=3)),
                (
                    "commission_rate",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
                ),
                (
                    "commission_amount",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
                ),
                (
                    "net_amount",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
                ),
                (
                    "cancellation_policy_id",
                    models.BigIntegerField(blank=True, default=None, null=True),
                ),
                ("cancellation_policy_snapshot", models.JSONField()),
                ("confirmed_at", models.DateTimeField(blank=True, default=None, null=True)),
                ("cancelled_at", models.DateTimeField(blank=True, default=None, null=True)),
                (
                    "cancelled_by",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("TOURIST", "Tourist"),
                            ("PROVIDER", "Provider"),
                            ("DRIVER", "Driver"),
                            ("PLATFORM", "Platform"),
                        ],
                        default=None,
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "cancellation_reason",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("TOURIST_REQUEST", "Tourist request"),
                            ("TRIP_CANCELLED", "Whole trip cancelled"),
                            ("PROVIDER_DECLINED", "Provider declined"),
                            ("PROVIDER_RESPONSE_TIMEOUT", "Provider did not respond"),
                            ("PROVIDER_UNAVAILABLE", "Provider cannot fulfil"),
                            ("ADMIN_ACTION", "Administrative action"),
                        ],
                        default=None,
                        max_length=40,
                        null=True,
                    ),
                ),
                ("completed_at", models.DateTimeField(blank=True, default=None, null=True)),
                ("response_due_at", models.DateTimeField(blank=True, default=None, null=True)),
            ],
            options={
                "db_table": "booking",
                "ordering": ["starts_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="BookingStatusHistory",
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
                    "from_status",
                    models.CharField(blank=True, default=None, max_length=20, null=True),
                ),
                ("to_status", models.CharField(max_length=20)),
                ("actor_user_id", models.BigIntegerField(blank=True, default=None, null=True)),
                ("actor_role", models.CharField(max_length=40)),
                ("reason", models.CharField(blank=True, default="", max_length=500)),
                ("occurred_at", models.DateTimeField()),
            ],
            options={
                "db_table": "booking_status_history",
                "ordering": ["occurred_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="BookingActivity",
            fields=[
                (
                    "booking",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="activity",
                        serialize=False,
                        to="booking.booking",
                    ),
                ),
                ("activity_id", models.BigIntegerField(db_index=True)),
                ("activity_departure_id", models.BigIntegerField(db_index=True)),
                ("pax_adult", models.SmallIntegerField()),
                ("pax_child", models.SmallIntegerField(default=0)),
                ("meeting_at", models.DateTimeField()),
            ],
            options={
                "db_table": "booking_activity",
            },
        ),
        migrations.CreateModel(
            name="BookingTransfer",
            fields=[
                (
                    "booking",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="transfer",
                        serialize=False,
                        to="booking.booking",
                    ),
                ),
                (
                    "origin_destination_id",
                    models.BigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "target_destination_id",
                    models.BigIntegerField(blank=True, default=None, null=True),
                ),
                (
                    "pickup_point",
                    django.contrib.gis.db.models.fields.PointField(geography=True, srid=4326),
                ),
                (
                    "dropoff_point",
                    django.contrib.gis.db.models.fields.PointField(geography=True, srid=4326),
                ),
                ("pickup_at", models.DateTimeField()),
                ("distance_m", models.IntegerField(blank=True, default=None, null=True)),
                ("travel_seconds", models.IntegerField(blank=True, default=None, null=True)),
                ("estimate_quality", models.CharField(max_length=20)),
                ("vehicle_class", models.CharField(max_length=20)),
                ("luggage_count", models.SmallIntegerField(default=0)),
                ("is_airport_transfer", models.BooleanField(default=False)),
                ("trip_flight_id", models.BigIntegerField(blank=True, default=None, null=True)),
                ("corridor_id", models.BigIntegerField(blank=True, default=None, null=True)),
                ("tariff_id", models.BigIntegerField(blank=True, default=None, null=True)),
            ],
            options={
                "db_table": "booking_transfer",
            },
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["provider_id", "status", "starts_at"], name="booking_provider_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(fields=["tourist_id", "status"], name="booking_tourist_idx"),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(fields=["status", "starts_at"], name="booking_status_starts_idx"),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                condition=models.Q(("status", "AWAITING_PROVIDER")),
                fields=["response_due_at"],
                name="booking_response_due_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(("booking_type__in", ["ACTIVITY", "TRANSFER"])),
                name="booking_type_is_a_v1_type",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    (
                        "status__in",
                        [
                            "DRAFT",
                            "PENDING",
                            "AWAITING_PROVIDER",
                            "CONFIRMED",
                            "IN_PROGRESS",
                            "COMPLETED",
                            "CANCELLED",
                            "REFUNDED",
                            "NO_SHOW",
                            "FAILED",
                        ],
                    )
                ),
                name="booking_status_known",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(("ends_at__gte", models.F("starts_at"))),
                name="booking_ends_after_it_starts",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(("pax_count__gte", 1)), name="booking_pax_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("gross_amount__gte", 0), ("fee_amount__gte", 0), ("tax_amount__gte", 0)
                ),
                name="booking_amounts_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("commission_rate__isnull", True),
                    models.Q(("commission_rate__gte", 0), ("commission_rate__lte", 100)),
                    _connector="OR",
                ),
                name="booking_commission_rate_is_a_percentage",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("commission_amount__isnull", True), ("net_amount__isnull", True)),
                    models.Q(
                        ("commission_amount__isnull", False),
                        ("net_amount__isnull", False),
                        ("commission_rate__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="booking_commission_settles_together",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("cancelled_at__isnull", True),
                        ("cancelled_by__isnull", True),
                        ("cancellation_reason__isnull", True),
                    ),
                    models.Q(
                        ("cancelled_at__isnull", False),
                        ("cancelled_by__isnull", False),
                        ("cancellation_reason__isnull", False),
                    ),
                    _connector="OR",
                ),
                name="booking_cancellation_is_whole",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("status", "AWAITING_PROVIDER"), _negated=True),
                    ("response_due_at__isnull", False),
                    _connector="OR",
                ),
                name="booking_awaiting_provider_has_a_deadline",
            ),
        ),
        migrations.AddConstraint(
            model_name="booking",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("booking_type", "ACTIVITY"), _negated=True),
                    ("cancellation_policy_id__isnull", False),
                    _connector="OR",
                ),
                name="booking_activity_names_its_policy",
            ),
        ),
        migrations.AddField(
            model_name="bookingstatushistory",
            name="booking",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="history",
                to="booking.booking",
            ),
        ),
        migrations.AddConstraint(
            model_name="bookingactivity",
            constraint=models.CheckConstraint(
                condition=models.Q(("pax_adult__gte", 0), ("pax_child__gte", 0)),
                name="booking_activity_pax_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="bookingtransfer",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("corridor_id__isnull", False), ("tariff_id__isnull", True)),
                    models.Q(("corridor_id__isnull", True), ("tariff_id__isnull", False)),
                    _connector="OR",
                ),
                name="booking_transfer_one_pricing_rule",
            ),
        ),
        migrations.AddConstraint(
            model_name="bookingtransfer",
            constraint=models.CheckConstraint(
                condition=models.Q(("estimate_quality__in", ["ROUTED", "MATRIX", "APPROXIMATE"])),
                name="booking_transfer_quality_known",
            ),
        ),
        migrations.AddConstraint(
            model_name="bookingtransfer",
            constraint=models.CheckConstraint(
                condition=models.Q(("luggage_count__gte", 0)),
                name="booking_transfer_luggage_non_negative",
            ),
        ),
        migrations.AddIndex(
            model_name="bookingstatushistory",
            index=models.Index(fields=["booking", "occurred_at"], name="booking_history_idx"),
        ),
        migrations.AddConstraint(
            model_name="bookingstatushistory",
            constraint=models.CheckConstraint(
                condition=models.Q(("actor_role", ""), _negated=True),
                name="booking_history_names_an_actor",
            ),
        ),
        attach_updated_at_trigger("booking"),
        migrations.RunSQL(APPEND_ONLY_SQL, DROP_APPEND_ONLY_SQL),
    ]
