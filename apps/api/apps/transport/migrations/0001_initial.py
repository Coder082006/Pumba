"""`vehicle_class`, `transfer_corridor`, `transfer_tariff` — SRS §12.4, ADR 0023.

The first tables `transport` has ever had. §12.4 gives the two tariff tables a
field name and a one-line meaning each; everything structural below that is
decided in ADR 0023 and explained in `apps/transport/models.py`.

**`btree_gist` is created here.** The exclusion constraints below put an
equality test on three scalar columns beside an overlap test on a date range,
in one index. GiST cannot index the scalars without it, and PostgreSQL will
refuse the constraint with "data type bigint has no default operator class for
access method gist" — a failure that appears only when the migration runs
against a real database, which is why it is created rather than assumed.

Those constraints are the point of this migration as much as the columns are.
§12.4's ladder says "first match wins"; two live corridors for one (origin,
target, class) on the same day would make the winner an accident of primary key
order. A CHECK cannot see across rows, and an application check would be a
race. This makes the state unrepresentable.

No SQL foreign key leaves this module. §6.4 gives `transport -> location,
provider`, and a reference to `destination` or `region` in DDL is that
forbidden edge written where import-linter cannot read it (ADR 0012).
"""

import uuid
from decimal import Decimal

import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.contrib.postgres.operations import BtreeGistExtension
from django.db import migrations, models

import apps.transport.models
import apps.transport.validators
from apps.common.db import attach_updated_at_trigger


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        # `set_updated_at()` is created there and attached below.
        ("common", "0002_updated_at_trigger"),
    ]

    operations = [
        # Must precede every ExclusionConstraint in this file.
        BtreeGistExtension(),
        migrations.CreateModel(
            name="VehicleClass",
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
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, default=None, editable=False, null=True),
                ),
                ("code", models.CharField(max_length=20)),
                ("name", models.CharField(max_length=60)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "seats",
                    models.PositiveSmallIntegerField(
                        validators=[django.core.validators.MinValueValidator(1)]
                    ),
                ),
                ("luggage_capacity", models.PositiveSmallIntegerField(default=0)),
                ("has_air_conditioning", models.BooleanField(default=False)),
                ("display_order", models.PositiveSmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "vehicle_class",
                "ordering": ["display_order", "code"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("code",),
                        name="vehicle_class_code_unique_alive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("seats__gte", 1)), name="vehicle_class_seats_positive"
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TransferTariff",
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
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, default=None, editable=False, null=True),
                ),
                (
                    "scope",
                    models.CharField(
                        choices=[("REGION", "Region"), ("COUNTRY", "Country")], max_length=10
                    ),
                ),
                (
                    "region_id",
                    models.BigIntegerField(blank=True, db_index=True, default=None, null=True),
                ),
                (
                    "country_id",
                    models.BigIntegerField(blank=True, db_index=True, default=None, null=True),
                ),
                ("base_fare", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "per_km_rate",
                    models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=14),
                ),
                (
                    "per_minute_rate",
                    models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=14),
                ),
                (
                    "minimum_fare",
                    models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14),
                ),
                (
                    "night_surcharge_pct",
                    models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=5),
                ),
                ("night_from", models.TimeField(blank=True, default=None, null=True)),
                ("night_to", models.TimeField(blank=True, default=None, null=True)),
                (
                    "airport_surcharge",
                    models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14),
                ),
                (
                    "waiting_rate_per_minute",
                    models.DecimalField(decimal_places=4, default=Decimal("0"), max_digits=14),
                ),
                (
                    "currency",
                    models.CharField(
                        max_length=3,
                        validators=[apps.transport.validators.validate_iso_currency_code],
                    ),
                ),
                ("valid_from", models.DateField()),
                ("valid_to", models.DateField(blank=True, default=None, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "vehicle_class",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="tariffs",
                        to="transport.vehicleclass",
                    ),
                ),
            ],
            options={
                "db_table": "transfer_tariff",
                "ordering": ["scope", "vehicle_class"],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("country_id__isnull", True),
                                ("region_id__isnull", False),
                                ("scope", "REGION"),
                            ),
                            models.Q(
                                ("country_id__isnull", False),
                                ("region_id__isnull", True),
                                ("scope", "COUNTRY"),
                            ),
                            _connector="OR",
                        ),
                        name="transfer_tariff_scope_columns_coherent",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("night_from__isnull", True), ("night_to__isnull", True)),
                            models.Q(("night_from__isnull", False), ("night_to__isnull", False)),
                            _connector="OR",
                        ),
                        name="transfer_tariff_night_window_coherent",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("night_surcharge_pct", Decimal("0")),
                            ("night_from__isnull", False),
                            _connector="OR",
                        ),
                        name="transfer_tariff_night_surcharge_needs_a_window",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("base_fare__gte", Decimal("0")),
                            ("per_km_rate__gte", Decimal("0")),
                            ("per_minute_rate__gte", Decimal("0")),
                            ("minimum_fare__gte", Decimal("0")),
                            ("night_surcharge_pct__gte", Decimal("0")),
                            ("airport_surcharge__gte", Decimal("0")),
                            ("waiting_rate_per_minute__gte", Decimal("0")),
                        ),
                        name="transfer_tariff_rates_non_negative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("valid_to__isnull", True),
                            ("valid_to__gte", models.F("valid_from")),
                            _connector="OR",
                        ),
                        name="transfer_tariff_window_ordered",
                    ),
                    django.contrib.postgres.constraints.ExclusionConstraint(
                        condition=models.Q(
                            ("deleted_at__isnull", True), ("is_active", True), ("scope", "REGION")
                        ),
                        expressions=[
                            ("region_id", "="),
                            ("vehicle_class", "="),
                            (
                                apps.transport.models.DateRange(
                                    "valid_from",
                                    "valid_to",
                                    django.contrib.postgres.fields.ranges.RangeBoundary(),
                                ),
                                "&&",
                            ),
                        ],
                        name="transfer_tariff_no_overlapping_region_window",
                    ),
                    django.contrib.postgres.constraints.ExclusionConstraint(
                        condition=models.Q(
                            ("deleted_at__isnull", True), ("is_active", True), ("scope", "COUNTRY")
                        ),
                        expressions=[
                            ("country_id", "="),
                            ("vehicle_class", "="),
                            (
                                apps.transport.models.DateRange(
                                    "valid_from",
                                    "valid_to",
                                    django.contrib.postgres.fields.ranges.RangeBoundary(),
                                ),
                                "&&",
                            ),
                        ],
                        name="transfer_tariff_no_overlapping_country_window",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="TransferCorridor",
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
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, default=None, editable=False, null=True),
                ),
                ("origin_destination_id", models.BigIntegerField(db_index=True)),
                ("target_destination_id", models.BigIntegerField(db_index=True)),
                (
                    "fixed_price",
                    models.DecimalField(
                        decimal_places=2,
                        max_digits=14,
                        validators=[django.core.validators.MinValueValidator(Decimal("0.01"))],
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        max_length=3,
                        validators=[apps.transport.validators.validate_iso_currency_code],
                    ),
                ),
                ("is_bidirectional", models.BooleanField(default=True)),
                ("valid_from", models.DateField()),
                ("valid_to", models.DateField(blank=True, default=None, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "vehicle_class",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="corridors",
                        to="transport.vehicleclass",
                    ),
                ),
            ],
            options={
                "db_table": "transfer_corridor",
                "ordering": ["origin_destination_id", "target_destination_id", "vehicle_class"],
                "indexes": [
                    models.Index(
                        fields=["origin_destination_id", "target_destination_id", "vehicle_class"],
                        name="transfer_corridor_lookup",
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(("fixed_price__gt", Decimal("0"))),
                        name="transfer_corridor_price_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("valid_to__isnull", True),
                            ("valid_to__gte", models.F("valid_from")),
                            _connector="OR",
                        ),
                        name="transfer_corridor_window_ordered",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("origin_destination_id", models.F("target_destination_id")),
                            _negated=True,
                        ),
                        name="transfer_corridor_endpoints_differ",
                    ),
                    django.contrib.postgres.constraints.ExclusionConstraint(
                        condition=models.Q(("deleted_at__isnull", True), ("is_active", True)),
                        expressions=[
                            ("origin_destination_id", "="),
                            ("target_destination_id", "="),
                            ("vehicle_class", "="),
                            (
                                apps.transport.models.DateRange(
                                    "valid_from",
                                    "valid_to",
                                    django.contrib.postgres.fields.ranges.RangeBoundary(),
                                ),
                                "&&",
                            ),
                        ],
                        name="transfer_corridor_no_overlapping_window",
                    ),
                ],
            },
        ),
        # §7.2: the ORM maintains `updated_at` on save, the trigger maintains
        # it on everything that is not a save. An administrator closing a
        # corridor in bulk is exactly a path that bypasses `Model.save()`.
        attach_updated_at_trigger("vehicle_class"),
        attach_updated_at_trigger("transfer_corridor"),
        attach_updated_at_trigger("transfer_tariff"),
    ]
