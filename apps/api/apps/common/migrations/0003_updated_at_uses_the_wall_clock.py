"""Re-create `set_updated_at()` so it reads the wall clock — SRS §7.2.

The function was written with `now()`, which in PostgreSQL is the *transaction*
start time and not the current instant. `created_at` is set from Python at the
moment of the write, so a row inserted and then updated inside one transaction
came back with `updated_at` earlier than `created_at`: a timeline that reads as
though the row was modified before it existed.

`CREATE OR REPLACE` rebinds every trigger already attached, so no table needs
touching. There is no reverse: `now()` is the defect.
"""

from __future__ import annotations

from django.db import migrations

from apps.common.db import UPDATED_AT_FUNCTION_SQL

_PREVIOUS_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("common", "0002_updated_at_trigger")]

    operations = [
        migrations.RunSQL(sql=UPDATED_AT_FUNCTION_SQL, reverse_sql=_PREVIOUS_FUNCTION_SQL),
    ]
