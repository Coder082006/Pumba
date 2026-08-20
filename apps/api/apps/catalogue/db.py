"""Schema helpers for catalogue migrations — SRS §4.1, §7.2.

Migration modules are named `0001_...`, which is not an importable identifier,
so anything two migrations share has to live outside them. That is the whole
reason this module exists, exactly as `apps.common.db` does.

What it holds is triggers, and each one is here for the same reason: the rule
it enforces has a path that no validator sits on.

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

from typing import Any

from django.contrib.postgres.operations import CreateExtension
from django.db import migrations

__all__ = [
    "PostgisExtension",
    "IANA_TIMEZONE_FUNCTION_SQL",
    "DROP_IANA_TIMEZONE_FUNCTION_SQL",
    "attach_timezone_check",
    "KNOWN_TAGS_FUNCTION_SQL",
    "DROP_KNOWN_TAGS_FUNCTION_SQL",
    "attach_known_tags_check",
]


class PostgisExtension(CreateExtension):
    """`CREATE EXTENSION postgis`, with no reverse.

    `CreateExtension` drops the extension when the migration is reversed, and
    that fails here: the `postgis/postgis` image installs `postgis_topology`
    and `postgis_tiger_geocoder` on top of it, so `migrate catalogue zero`
    stops with "cannot drop extension postgis because other objects depend on
    it".

    Reversing it would be the wrong thing even if it worked. `DROP EXTENSION
    ... CASCADE` takes every geography column with it, and the extension is
    infrastructure this migration found rather than property it owns.
    """

    def __init__(self) -> None:
        super().__init__("postgis")

    def database_backwards(self, *args: Any, **kwargs: Any) -> None:
        return None

    def describe(self) -> str:
        return "Create extension postgis (not dropped on reverse)"


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


#: The tag vocabulary is a table (Q7), and `attraction.tags` / `activity.tags`
#: are `text[]` because §16.5 filters with the `&&` overlap operator, which
#: wants an array and not a join. An array is not a foreign key, so nothing in
#: the schema otherwise stops a typo: a misspelt slug produces a row that no
#: chip ever matches, and the failure is invisible - the attraction simply
#: never appears under the filter somebody expected it under.
#:
#: The column is named `tags` on both tables, so the function names it directly.
KNOWN_TAGS_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION assert_known_tags() RETURNS trigger AS $$
DECLARE
    unknown text[];
BEGIN
    SELECT array_agg(candidate) INTO unknown
    FROM unnest(NEW.tags) AS candidate
    WHERE NOT EXISTS (
        SELECT 1 FROM tag WHERE tag.slug = candidate AND tag.deleted_at IS NULL
    );
    IF unknown IS NOT NULL THEN
        RAISE EXCEPTION 'unknown tag slugs: %', unknown
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

DROP_KNOWN_TAGS_FUNCTION_SQL = "DROP FUNCTION IF EXISTS assert_known_tags() CASCADE;"


def attach_known_tags_check(table: str) -> migrations.RunSQL:
    """Refuse a tag slug that is not in the vocabulary, on insert and update."""
    name = f"{table}_tags_are_known"
    return migrations.RunSQL(
        sql=(
            f'DROP TRIGGER IF EXISTS {name} ON "{table}";\n'
            f'CREATE TRIGGER {name} BEFORE INSERT OR UPDATE OF "tags" ON "{table}"\n'
            f"FOR EACH ROW EXECUTE FUNCTION assert_known_tags();"
        ),
        reverse_sql=f'DROP TRIGGER IF EXISTS {name} ON "{table}";',
    )
