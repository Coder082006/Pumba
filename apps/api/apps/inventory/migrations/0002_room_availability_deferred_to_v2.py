"""room_availability leaves the v1 schema — ADR 0013, SRS v1.2 §7.5.8, §17.1.

Accommodation stopped being a bookable product. A stay anchor has no rooms, so
there is nothing to count, and an unused counter table is not free: it is a live
table with a live foreign key that every `migrate`, every `pytest --create-db`
and every reverse round-trip still pays for, and that every serializer and
OpenAPI schema has to explicitly suppress.

**This is additive, not a rewrite of `0001_availability_tables`.** That
migration is pushed, CI applies it from zero on every run, and ADR 0011 and ADR
0012 both quote it by name including the `room_availability_room_type_fk` SQL.
Editing it would falsify two accepted records; dropping forward keeps every one
of them true and makes the v2 revival the plain reverse of this file.

The drop order across the two modules is forced by that foreign key: this runs
first, and catalogue's `0006` — which removes `room_type` — depends on it. A
migration dependency is a string, not an import, so ADR 0012 still holds.
"""

from django.db import migrations

from apps.common.db import attach_updated_at_trigger

#: Dropping the table takes the constraint with it. Naming it anyway makes the
#: reverse honest: recreating the table would otherwise bring it back without
#: the foreign key, and a silently unconstrained column is worse than none.
DROP_FK = """
ALTER TABLE room_availability
    DROP CONSTRAINT IF EXISTS room_availability_room_type_fk;
"""

RESTORE_FK = """
ALTER TABLE room_availability
    ADD CONSTRAINT room_availability_room_type_fk
    FOREIGN KEY (room_type_id) REFERENCES room_type(id);
"""


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_availability_tables")]

    operations = [
        # First forward is last backward. `DeleteModel` reversed recreates the
        # table from migration state, and state does not carry triggers, so the
        # re-attach has to run after it — which means it has to be written
        # before it.
        migrations.RunSQL(
            sql=migrations.RunSQL.noop,
            reverse_sql=attach_updated_at_trigger("room_availability").sql,
        ),
        migrations.RunSQL(sql=DROP_FK, reverse_sql=RESTORE_FK),
        migrations.DeleteModel(name="RoomAvailability"),
    ]
