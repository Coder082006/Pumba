"""Full-text search columns - SRS §7.6.

Generated **stored** columns rather than triggers: `to_tsvector(regconfig, text)`
is immutable, so PostgreSQL maintains them itself. There is no application code
that can forget to update one, and no window in which a row and its index
disagree - which a trigger would still leave open for `COPY` and for any writer
that disables triggers.
"""

import django.contrib.postgres.indexes
import django.contrib.postgres.search
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("catalogue", "0004_activity_schedule_and_media"),
    ]

    operations = [
        migrations.AddField(
            model_name="accommodation",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.SearchVector(
                    "name", "summary", "description", config="english"
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        migrations.AddField(
            model_name="activity",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.SearchVector(
                    "name", "summary", "description", config="english"
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        migrations.AddField(
            model_name="attraction",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.SearchVector(
                    "name", "summary", "description", config="english"
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        migrations.AddField(
            model_name="destination",
            name="search_vector",
            field=models.GeneratedField(
                db_persist=True,
                expression=django.contrib.postgres.search.SearchVector(
                    "name", "summary", "description", config="english"
                ),
                output_field=django.contrib.postgres.search.SearchVectorField(),
            ),
        ),
        migrations.AddIndex(
            model_name="accommodation",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="accommodation_search_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="activity",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="activity_search_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="attraction",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="attraction_search_gin"
            ),
        ),
        migrations.AddIndex(
            model_name="destination",
            index=django.contrib.postgres.indexes.GinIndex(
                fields=["search_vector"], name="destination_search_gin"
            ),
        ),
    ]
