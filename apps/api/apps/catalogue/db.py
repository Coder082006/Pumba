"""Schema helpers for catalogue migrations — SRS §4.1, §7.2.

Migration modules are named `0001_...`, which is not an importable identifier,
so anything two migrations share has to live outside them. That is the whole
reason this module exists, exactly as `apps.common.db` does.

What it holds is one trigger, and the reason is worth stating.

`Destination.timezone` and `Country.default_timezone` carry an IANA zone name.
A field validator refuses a bad one on the console and the API path, which is
where an administrator types it. It does not refuse one arriving through a data
migration, a `QuerySet.update()`, a fixture load or a hand-run correction, and
`Africa/Zanzibar` is the shape of mistake that survives all four: it looks like
a zone, and nothing complains until an opening-hours table somewhere in that
destination fails to render, a long way from where it was typed.

So the invariant goes where nothing can route around it. `pg_timezone_names` is
PostgreSQL's own view of the tz database, which is the same IANA source
`zoneinfo` reads. The two can differ by a release, and when they do this
rejects a name Python would accept - the conservative direction, and loud.
"""

from __future__ import annotations

from django.db import migrations

__all__ = [
    "IANA_TIMEZONE_FUNCTION_SQL",
    "DROP_IANA_TIMEZONE_FUNCTION_SQL",
    "attach_timezone_check",
]

#: The column is passed as a trigger argument rather than named here, so one
#: function serves `country.default_timezone` and `destination.timezone` and
#: any zone column a later table adds.
IANA_TIMEZONE_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION assert_iana_timezone() RETURNS trigger AS $$
DECLARE
    zone text := row_to_json(NEW) ->> TG_ARGV[0];
BEGIN
    IF zone IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM pg_timezone_names WHERE name = zone) THEN
        RAISE EXCEPTION '% is not a known IANA time zone', zone
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_IANA_TIMEZONE_FUNCTION_SQL = "DROP FUNCTION IF EXISTS assert_iana_timezone() CASCADE;"


def attach_timezone_check(table: str, column: str) -> migrations.RunSQL:
    """Refuse a zone name PostgreSQL does not know, on insert and on update.

    `DROP ... IF EXISTS` runs first so the operation is safe to re-apply to a
    database that has been partially migrated by hand.
    """
    name = f"{table}_{column}_is_iana"
    return migrations.RunSQL(
        sql=(
            f'DROP TRIGGER IF EXISTS {name} ON "{table}";\n'
            f'CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OF "{column}" ON "{table}"\n'
            f"FOR EACH ROW EXECUTE FUNCTION assert_iana_timezone('{column}');"
        ),
        reverse_sql=f'DROP TRIGGER IF EXISTS {name} ON "{table}";',
    )
