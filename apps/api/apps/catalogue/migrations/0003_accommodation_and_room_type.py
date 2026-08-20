"""Stays - SRS §7.5.7, §14.

`cancellation_policy` is created first because both `accommodation` and, later,
`activity` reference it. The `tiers` shape is checked by a field validator on
the write path; the CHECK here reaches only as far as "is a JSON array", which
is the gross error a bad import makes.
"""

import uuid
from decimal import Decimal

import django.contrib.gis.db.models.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import apps.catalogue.validators
from apps.common.db import attach_updated_at_trigger

TABLES = ["cancellation_policy", "accommodation", "room_type"]

TIERS_IS_AN_ARRAY = """
ALTER TABLE cancellation_policy
    ADD CONSTRAINT cancellation_policy_tiers_is_an_array
    CHECK (jsonb_typeof(tiers) = 'array');
"""

DROP_TIERS_IS_AN_ARRAY = """
ALTER TABLE cancellation_policy
    DROP CONSTRAINT IF EXISTS cancellation_policy_tiers_is_an_array;
"""


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0002_attraction_and_tag"),
    ]

    operations = [
        migrations.CreateModel(
            name="CancellationPolicy",
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
                ("code", models.CharField(max_length=32)),
                ("name", models.CharField(max_length=120)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "tiers",
                    models.JSONField(
                        blank=True,
                        default=list,
                        validators=[apps.catalogue.validators.validate_cancellation_tiers],
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "cancellation_policy",
                "ordering": ["code"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("code",),
                        name="cancellation_policy_code_unique_alive",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Accommodation",
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
                    "provider_id",
                    models.BigIntegerField(blank=True, db_index=True, default=None, null=True),
                ),
                ("name", models.CharField(max_length=140)),
                ("slug", models.SlugField(max_length=140)),
                ("summary", models.TextField(blank=True, default=None, null=True)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "property_type",
                    models.CharField(
                        choices=[
                            ("HOTEL", "Hotel"),
                            ("RESORT", "Resort"),
                            ("LODGE", "Lodge"),
                            ("GUESTHOUSE", "Guesthouse"),
                            ("APARTMENT", "Apartment"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "coordinates",
                    django.contrib.gis.db.models.fields.PointField(geography=True, srid=4326),
                ),
                ("address_line", models.CharField(blank=True, default="", max_length=255)),
                ("star_rating", models.SmallIntegerField(blank=True, default=None, null=True)),
                ("amenities", models.JSONField(blank=True, default=dict)),
                ("check_in_time", models.TimeField(blank=True, default=None, null=True)),
                ("check_out_time", models.TimeField(blank=True, default=None, null=True)),
                ("child_policy", models.JSONField(blank=True, default=dict)),
                ("booking_cutoff_hours", models.SmallIntegerField(default=4)),
                (
                    "rating_avg",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=3),
                ),
                ("rating_count", models.IntegerField(default=0)),
                ("feature_rank", models.SmallIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "destination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="accommodations",
                        to="catalogue.destination",
                    ),
                ),
                (
                    "cancellation_policy",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="accommodations",
                        to="catalogue.cancellationpolicy",
                    ),
                ),
            ],
            options={
                "db_table": "accommodation",
                "ordering": ["feature_rank", "name"],
            },
        ),
        migrations.CreateModel(
            name="RoomType",
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
                ("name", models.CharField(max_length=120)),
                ("max_adults", models.SmallIntegerField()),
                ("max_children", models.SmallIntegerField(default=0)),
                ("bed_configuration", models.CharField(blank=True, default="", max_length=80)),
                ("size_sqm", models.SmallIntegerField(blank=True, default=None, null=True)),
                ("base_rate", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "currency",
                    models.CharField(
                        max_length=3,
                        validators=[apps.catalogue.validators.validate_iso_currency_code],
                    ),
                ),
                ("total_rooms", models.SmallIntegerField()),
                ("amenities", models.JSONField(blank=True, default=dict)),
                ("min_nights", models.SmallIntegerField(blank=True, default=None, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "accommodation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="room_types",
                        to="catalogue.accommodation",
                    ),
                ),
            ],
            options={
                "db_table": "room_type",
                "ordering": ["accommodation", "base_rate", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="accommodation",
            index=django.contrib.postgres.indexes.GistIndex(
                fields=["coordinates"], name="accommodation_coordinates_gist"
            ),
        ),
        migrations.AddIndex(
            model_name="accommodation",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["amenities"], name="accommodation_amenities_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="accommodation",
            index=models.Index(
                fields=["destination", "is_active", "feature_rank"],
                name="accommodation_dest_active_rank",
            ),
        ),
        migrations.AddConstraint(
            model_name="accommodation",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("slug",),
                name="accommodation_slug_unique_alive",
            ),
        ),
        migrations.AddConstraint(
            model_name="accommodation",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("star_rating__isnull", True),
                    models.Q(("star_rating__gte", 1), ("star_rating__lte", 5)),
                    _connector="OR",
                ),
                name="accommodation_star_rating_in_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="accommodation",
            constraint=models.CheckConstraint(
                condition=models.Q(("booking_cutoff_hours__gte", 0)),
                name="accommodation_booking_cutoff_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="accommodation",
            constraint=models.CheckConstraint(
                condition=models.Q(("rating_avg__gte", 0), ("rating_avg__lte", 5)),
                name="accommodation_rating_avg_in_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="accommodation",
            constraint=models.CheckConstraint(
                condition=models.Q(("rating_count__gte", 0)),
                name="accommodation_rating_count_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="accommodation",
            constraint=models.CheckConstraint(
                condition=models.Q(("feature_rank__gte", 1)),
                name="accommodation_feature_rank_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomtype",
            constraint=models.CheckConstraint(
                condition=models.Q(("base_rate__gte", 0)), name="room_type_base_rate_non_negative"
            ),
        ),
        migrations.AddConstraint(
            model_name="roomtype",
            constraint=models.CheckConstraint(
                condition=models.Q(("total_rooms__gt", 0)), name="room_type_total_rooms_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="roomtype",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_adults__gte", 1)),
                name="room_type_takes_at_least_one_adult",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomtype",
            constraint=models.CheckConstraint(
                condition=models.Q(("max_children__gte", 0)),
                name="room_type_max_children_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomtype",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("size_sqm__isnull", True), ("size_sqm__gt", 0), _connector="OR"
                ),
                name="room_type_size_positive",
            ),
        ),
        migrations.AddConstraint(
            model_name="roomtype",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("min_nights__isnull", True), ("min_nights__gt", 0), _connector="OR"
                ),
                name="room_type_min_nights_positive",
            ),
        ),
        *[attach_updated_at_trigger(table) for table in TABLES],
        migrations.RunSQL(sql=TIERS_IS_AN_ARRAY, reverse_sql=DROP_TIERS_IS_AN_ARRAY),
    ]
