"""The geography hierarchy - SRS §7.3 (R6), §7.5.6.

`CreateExtension("postgis")` runs ahead of the first geography column so a
fresh database bootstraps itself; §7.1 already specifies the PostGIS image, but
an extension present in the image is not an extension created in the database.

The two trigger families at the end are the §7.2 `updated_at` maintenance and
the IANA zone check described in `apps.catalogue.db`.
"""

import uuid

import django.contrib.gis.db.models.fields
import django.contrib.postgres.indexes
import django.db.models.deletion
import django.utils.timezone
from django.contrib.postgres.operations import CreateExtension
from django.db import migrations, models

import apps.catalogue.validators
from apps.catalogue.db import (
    DROP_IANA_TIMEZONE_FUNCTION_SQL,
    IANA_TIMEZONE_FUNCTION_SQL,
    attach_timezone_check,
)
from apps.common.db import attach_updated_at_trigger

TABLES = ["country", "region", "destination"]


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        # `set_updated_at()` is created there and attached here.
        ("common", "0002_updated_at_trigger"),
    ]

    operations = [
        CreateExtension("postgis"),
        migrations.CreateModel(
            name="Country",
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
                    "iso_code",
                    models.CharField(
                        max_length=2,
                        validators=[apps.catalogue.validators.validate_iso_country_code],
                    ),
                ),
                ("name", models.CharField(max_length=80)),
                (
                    "default_currency",
                    models.CharField(
                        max_length=3,
                        validators=[apps.catalogue.validators.validate_iso_currency_code],
                    ),
                ),
                (
                    "default_timezone",
                    models.CharField(
                        max_length=60, validators=[apps.catalogue.validators.validate_iana_timezone]
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
            ],
            options={
                "db_table": "country",
                "ordering": ["name"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("deleted_at__isnull", True)),
                        fields=("iso_code",),
                        name="country_iso_code_unique_alive",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Region",
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
                ("slug", models.SlugField(max_length=140)),
                ("is_active", models.BooleanField(default=True)),
                (
                    "country",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="regions",
                        to="catalogue.country",
                    ),
                ),
            ],
            options={
                "db_table": "region",
                "ordering": ["country__iso_code", "name"],
            },
        ),
        migrations.CreateModel(
            name="Destination",
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
                ("slug", models.SlugField(max_length=140)),
                ("summary", models.TextField(blank=True, default=None, null=True)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "centroid",
                    django.contrib.gis.db.models.fields.PointField(geography=True, srid=4326),
                ),
                ("is_gateway", models.BooleanField(default=False)),
                (
                    "gateway_type",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("AIRPORT", "Airport"),
                            ("SEAPORT", "Seaport"),
                            ("LAND_BORDER", "Land border"),
                        ],
                        default=None,
                        max_length=20,
                        null=True,
                    ),
                ),
                (
                    "gateway_code",
                    models.CharField(blank=True, default=None, max_length=10, null=True),
                ),
                (
                    "timezone",
                    models.CharField(
                        max_length=60, validators=[apps.catalogue.validators.validate_iana_timezone]
                    ),
                ),
                (
                    "default_currency",
                    models.CharField(
                        max_length=3,
                        validators=[apps.catalogue.validators.validate_iso_currency_code],
                    ),
                ),
                ("launch_date", models.DateField(blank=True, default=None, null=True)),
                ("feature_rank", models.SmallIntegerField(default=100)),
                ("is_active", models.BooleanField(default=False)),
                (
                    "region",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="destinations",
                        to="catalogue.region",
                    ),
                ),
            ],
            options={
                "db_table": "destination",
                "ordering": ["feature_rank", "name"],
            },
        ),
        migrations.AddIndex(
            model_name="region",
            index=models.Index(fields=["country", "is_active"], name="region_country_active_idx"),
        ),
        migrations.AddConstraint(
            model_name="region",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("country", "slug"),
                name="region_slug_unique_alive_per_country",
            ),
        ),
        migrations.AddIndex(
            model_name="destination",
            index=django.contrib.postgres.indexes.GistIndex(
                fields=["centroid"], name="destination_centroid_gist"
            ),
        ),
        migrations.AddIndex(
            model_name="destination",
            index=models.Index(
                fields=["region", "is_active"], name="destination_region_active_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="destination",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True)),
                fields=("slug",),
                name="destination_slug_unique_alive",
            ),
        ),
        migrations.AddConstraint(
            model_name="destination",
            constraint=models.UniqueConstraint(
                condition=models.Q(("deleted_at__isnull", True), ("is_gateway", True)),
                fields=("gateway_code",),
                name="destination_gateway_code_unique_alive",
            ),
        ),
        migrations.AddConstraint(
            model_name="destination",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(
                        ("gateway_code__isnull", False),
                        ("gateway_type__isnull", False),
                        ("is_gateway", True),
                    ),
                    models.Q(
                        ("gateway_code__isnull", True),
                        ("gateway_type__isnull", True),
                        ("is_gateway", False),
                    ),
                    _connector="OR",
                ),
                name="destination_gateway_columns_coherent",
            ),
        ),
        migrations.AddConstraint(
            model_name="destination",
            constraint=models.CheckConstraint(
                condition=models.Q(("feature_rank__gte", 1)),
                name="destination_feature_rank_positive",
            ),
        ),
        *[attach_updated_at_trigger(table) for table in TABLES],
        migrations.RunSQL(
            sql=IANA_TIMEZONE_FUNCTION_SQL, reverse_sql=DROP_IANA_TIMEZONE_FUNCTION_SQL
        ),
        attach_timezone_check("country", "default_timezone"),
        attach_timezone_check("destination", "timezone"),
    ]
