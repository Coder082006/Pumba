"""The §24.7 tag vocabulary and §15.1 attractions.

`tag` is created before `attraction`, because the trigger at the end of this
migration reads it on every write to `attraction.tags`.
"""

import uuid

import django.contrib.gis.db.models.fields
import django.contrib.postgres.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models

import apps.catalogue.validators
from apps.catalogue.db import (
    DROP_KNOWN_TAGS_FUNCTION_SQL,
    KNOWN_TAGS_FUNCTION_SQL,
    attach_known_tags_check,
)
from apps.common.db import attach_updated_at_trigger

TABLES = ["tag", "attraction"]


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Tag",
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
                ("slug", models.SlugField(max_length=64)),
                ("label", models.CharField(max_length=80)),
                ("sort_order", models.SmallIntegerField(default=0)),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "tag",
                "ordering": ["sort_order", "id"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("slug",),
                        name="tag_slug_unique_alive",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Attraction",
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
                ("name", models.CharField(max_length=140)),
                ("slug", models.SlugField(max_length=140)),
                ("summary", models.TextField(blank=True, default=None, null=True)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "coordinates",
                    django.contrib.gis.db.models.fields.PointField(geography=True, srid=4326),
                ),
                ("opening_hours", models.JSONField(blank=True, default=None, null=True)),
                (
                    "entrance_fee",
                    models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
                ),
                (
                    "fee_currency",
                    models.CharField(
                        blank=True,
                        default=None,
                        max_length=3,
                        null=True,
                        validators=[apps.catalogue.validators.validate_iso_currency_code],
                    ),
                ),
                ("visit_minutes", models.SmallIntegerField(blank=True, default=None, null=True)),
                (
                    "tags",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.SlugField(max_length=64),
                        blank=True,
                        default=list,
                        size=None,
                    ),
                ),
                ("accessibility_notes", models.TextField(blank=True, default="")),
                ("feature_rank", models.SmallIntegerField(default=100)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "destination",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="attractions",
                        to="catalogue.destination",
                    ),
                ),
            ],
            options={
                "db_table": "attraction",
                "ordering": ["feature_rank", "name"],
                "indexes": [
                    django.contrib.postgres.indexes.GistIndex(
                        fields=["coordinates"], name="attraction_coordinates_gist"
                    ),
                    django.contrib.postgres.indexes.GinIndex(
                        fields=["tags"], name="attraction_tags_gin"
                    ),
                    models.Index(
                        fields=["destination", "is_active", "feature_rank"],
                        name="attraction_dest_active_rank",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("slug",),
                        name="attraction_slug_unique_alive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("entrance_fee__isnull", True), ("fee_currency__isnull", True)
                            ),
                            models.Q(
                                ("entrance_fee__isnull", False), ("fee_currency__isnull", False)
                            ),
                            _connector="OR",
                        ),
                        name="attraction_fee_has_a_currency",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("entrance_fee__isnull", True),
                            ("entrance_fee__gte", 0),
                            _connector="OR",
                        ),
                        name="attraction_fee_non_negative",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            ("visit_minutes__isnull", True),
                            ("visit_minutes__gt", 0),
                            _connector="OR",
                        ),
                        name="attraction_visit_minutes_positive",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("feature_rank__gte", 1)),
                        name="attraction_feature_rank_positive",
                    ),
                ],
            },
        ),
        *[attach_updated_at_trigger(table) for table in TABLES],
        migrations.RunSQL(sql=KNOWN_TAGS_FUNCTION_SQL, reverse_sql=DROP_KNOWN_TAGS_FUNCTION_SQL),
        attach_known_tags_check("attraction"),
    ]
