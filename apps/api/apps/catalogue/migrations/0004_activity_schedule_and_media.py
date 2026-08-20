"""Activities, their recurring schedules, and polymorphic media.

§16.2 keeps `activity_schedule` (a rule) separate from `activity_departure` (a
sellable instant). Only the rule is created here: the departures table belongs
to `inventory` (ADR 0011), and the nightly materialisation that connects them
is Phase 5.

`activity.tags` is put under the same vocabulary trigger as `attraction.tags`.
"""

import uuid
from decimal import Decimal

import django.contrib.gis.db.models.fields
import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import apps.catalogue.validators
from apps.catalogue.db import attach_known_tags_check
from apps.common.db import attach_updated_at_trigger

TABLES = ["activity", "activity_schedule", "media"]


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0003_accommodation_and_room_type"),
    ]

    operations = [
        migrations.CreateModel(
            name="Activity",
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
                    "coordinates",
                    django.contrib.gis.db.models.fields.PointField(geography=True, srid=4326),
                ),
                ("meeting_point_text", models.CharField(blank=True, default="", max_length=255)),
                ("duration_minutes", models.SmallIntegerField()),
                ("price_per_person", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "price_per_group",
                    models.DecimalField(
                        blank=True, decimal_places=2, default=None, max_digits=14, null=True
                    ),
                ),
                (
                    "currency",
                    models.CharField(
                        max_length=3,
                        validators=[apps.catalogue.validators.validate_iso_currency_code],
                    ),
                ),
                ("min_pax", models.SmallIntegerField(default=1)),
                ("max_pax", models.SmallIntegerField()),
                ("min_age", models.SmallIntegerField(blank=True, default=None, null=True)),
                (
                    "requirements",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        validators=[apps.catalogue.validators.validate_activity_requirements],
                    ),
                ),
                ("inclusions", models.JSONField(blank=True, default=list)),
                ("exclusions", models.JSONField(blank=True, default=list)),
                (
                    "tags",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.SlugField(max_length=64),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                ("booking_cutoff_hours", models.SmallIntegerField(default=24)),
                (
                    "confirmation_mode",
                    models.CharField(
                        choices=[
                            ("INSTANT", "Confirms immediately"),
                            ("ON_REQUEST", "Confirmed by the provider"),
                        ],
                        default="INSTANT",
                        max_length=20,
                    ),
                ),
                (
                    "rating_avg",
                    models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=3),
                ),
                ("rating_count", models.IntegerField(default=0)),
                ("feature_rank", models.SmallIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "attraction",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activities",
                        to="catalogue.attraction",
                    ),
                ),
                (
                    "cancellation_policy",
                    models.ForeignKey(
                        blank=True,
                        default=None,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activities",
                        to="catalogue.cancellationpolicy",
                    ),
                ),
                (
                    "destination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="activities",
                        to="catalogue.destination",
                    ),
                ),
            ],
            options={
                "db_table": "activity",
                "ordering": ["feature_rank", "name"],
            },
        ),
        migrations.CreateModel(
            name="ActivitySchedule",
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
                ("weekday_mask", models.SmallIntegerField()),
                ("start_time", models.TimeField()),
                ("capacity", models.SmallIntegerField()),
                ("valid_from", models.DateField()),
                ("valid_to", models.DateField(blank=True, default=None, null=True)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "activity",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="schedules",
                        to="catalogue.activity",
                    ),
                ),
            ],
            options={
                "db_table": "activity_schedule",
                "ordering": ["activity", "start_time", "id"],
            },
        ),
        migrations.CreateModel(
            name="Media",
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
                    "owner_type",
                    models.CharField(
                        choices=[
                            ("destination", "Destination"),
                            ("attraction", "Attraction"),
                            ("activity", "Activity"),
                            ("accommodation", "Accommodation"),
                            ("room_type", "Room type"),
                        ],
                        max_length=20,
                    ),
                ),
                ("owner_id", models.BigIntegerField()),
                ("file_key", models.CharField(max_length=255)),
                ("alt_text", models.CharField(blank=True, default="", max_length=255)),
                ("width", models.IntegerField(blank=True, default=None, null=True)),
                ("height", models.IntegerField(blank=True, default=None, null=True)),
                ("sort_order", models.SmallIntegerField(default=0)),
                ("is_primary", models.BooleanField(default=False)),
            ],
            options={
                "db_table": "media",
                "ordering": ["owner_type", "owner_id", "-is_primary", "sort_order", "id"],
                "indexes": [
                    models.Index(fields=["owner_type", "owner_id"], name="media_owner_idx")
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("width__isnull", True), ("width__gt", 0), _connector="OR"
                        ),
                        name="media_width_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("height__isnull", True), ("height__gt", 0), _connector="OR"
                        ),
                        name="media_height_positive",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("is_primary", True)),
                        fields=("owner_type", "owner_id"),
                        name="media_one_primary_per_owner",
                    ),
                ],
            },
        ),
        migrations.AddIndex(
            model_name="activity",
            index=django.contrib.postgres.indexes.GistIndex(
                fields=["coordinates"], name="activity_coordinates_gist"
            ),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["tags"], name="activity_tags_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(
                fields=["destination", "is_active", "feature_rank", "price_per_person"],
                name="activity_dest_active_rank",
            ),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=models.Index(fields=["attraction"], name="activity_attraction_idx"),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("slug",),
                name="activity_slug_unique_alive",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("price_per_person__gte", 0)), name="activity_price_non_negative"
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("price_per_group__isnull", True), ("price_per_group__gte", 0), _connector="OR"
                ),
                name="activity_group_price_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("duration_minutes__gt", 0)), name="activity_duration_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("max_pax__gte", 1), ("min_pax__gte", 1), ("min_pax__lte", models.F("max_pax"))
                ),
                name="activity_pax_range_is_satisfiable",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("min_age__isnull", True), ("min_age__gte", 0), _connector="OR"),
                name="activity_min_age_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("booking_cutoff_hours__gte", 0)),
                name="activity_booking_cutoff_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("rating_avg__gte", 0), ("rating_avg__lte", 5)),
                name="activity_rating_avg_in_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("rating_count__gte", 0)),
                name="activity_rating_count_non_negative",
            ),
        ),
        migrations.AddConstraint(
            model_name="activity",
            constraint=models.CheckConstraint(
                condition=models.Q(("feature_rank__gte", 1)), name="activity_feature_rank_positive"
            ),
        ),
        migrations.AddIndex(
            model_name="activityschedule",
            index=models.Index(
                fields=["activity", "is_active"], name="activity_schedule_active_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="activityschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(("weekday_mask__gte", 1), ("weekday_mask__lte", 127)),
                name="activity_schedule_weekday_mask_in_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="activityschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(("capacity__gt", 0)), name="activity_schedule_capacity_positive"
            ),
        ),
        migrations.AddConstraint(
            model_name="activityschedule",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("valid_to__isnull", True),
                    ("valid_to__gte", models.F("valid_from")),
                    _connector="OR",
                ),
                name="activity_schedule_window_is_ordered",
            ),
        ),
        *[attach_updated_at_trigger(table) for table in TABLES],
        attach_known_tags_check("activity"),
    ]
