"""Schema helpers shared by every module's migrations — SRS §7.2.

Migration modules are named `0001_…`, which is not an importable identifier,
so anything a later migration needs to reuse has to live outside them. That
is the whole reason this module exists.
"""

from __future__ import annotations

from django.db import migrations

__all__ = ["UPDATED_AT_FUNCTION_SQL", "DROP_UPDATED_AT_FUNCTION_SQL", "attach_updated_at_trigger"]

UPDATED_AT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_UPDATED_AT_FUNCTION_SQL = "DROP FUNCTION IF EXISTS set_updated_at() CASCADE;"


def attach_updated_at_trigger(table: str) -> migrations.RunSQL:
    """Put one table under the §7.2 `updated_at` trigger.

    `TimestampedModel.save()` already maintains the column, and that covers
    ordinary ORM writes. It does not cover `QuerySet.update()`, `bulk_update`,
    a data migration, a `COPY`, or a hand-run correction — all of which bypass
    `save()` and leave `updated_at` stale exactly when someone is trying to
    establish what changed and when. The trigger puts the invariant somewhere
    nothing can route around it.

    `DROP … IF EXISTS` runs first so the operation is safe to re-apply to a
    database that has been partially migrated by hand.
    """
    name = f"{table}_set_updated_at"
    return migrations.RunSQL(
        sql=(
            f'DROP TRIGGER IF EXISTS {name} ON "{table}";\n'
            f'CREATE TRIGGER {name} BEFORE UPDATE ON "{table}"\n'
            f"FOR EACH ROW EXECUTE FUNCTION set_updated_at();"
        ),
        reverse_sql=f'DROP TRIGGER IF EXISTS {name} ON "{table}";',
    )
