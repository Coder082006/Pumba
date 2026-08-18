"""Install the §7.2 `updated_at` trigger function and apply it to `common`.

The function is created once, here; every later module attaches its own
tables with `apps.common.db.attach_updated_at_trigger`.
"""

from __future__ import annotations

from django.db import migrations

from apps.common.db import (
    DROP_UPDATED_AT_FUNCTION_SQL,
    UPDATED_AT_FUNCTION_SQL,
    attach_updated_at_trigger,
)


class Migration(migrations.Migration):
    dependencies = [("common", "0001_initial")]

    operations = [
        migrations.RunSQL(sql=UPDATED_AT_FUNCTION_SQL, reverse_sql=DROP_UPDATED_AT_FUNCTION_SQL),
        attach_updated_at_trigger("idempotency_record"),
    ]
