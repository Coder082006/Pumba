# ADR 0012 — A row in another module is referenced by id, not by foreign key

**Status:** Accepted · **Date:** 2026-08-20 · **Phase:** 3

## Context

SRS §7.5.7 gives `accommodation` a `provider_id` pointing at `provider.id`, and
§7.5.9 gives `activity` the same. SRS §6.4 gives `catalogue` exactly one
dependency, `location`, and puts `provider` in a different module that
`catalogue` may not import.

Both statements are in the specification and they pull in opposite directions.
A Django `ForeignKey` is not only a database constraint: it installs a
traversable attribute, so `accommodation.provider.payout_account` becomes
reachable from catalogue code, `select_related("provider")` starts appearing in
catalogue querysets, and the module boundary the §6.4 DAG exists to protect is
gone — not by an import anybody wrote deliberately, but by an attribute the ORM
created. CLAUDE.md's third rule is explicit: cross-module access goes through
`services.py` and DTOs, never another module's `models.py`.

The immediate trigger is that `provider` does not exist yet. It arrives around
Phase 6, and its absence would block `accommodation` entirely if the reference
had to be a real `ForeignKey` today.

## Decision

A column that references a row owned by another module is a plain
`BigIntegerField`, indexed, named `<module_entity>_id`, and documented as a soft
reference. Catalogue's `accommodation.provider_id` and `activity.provider_id`
are the first two.

Reading the referenced row is a service call — `provider.services.get_provider(…)`
returning a DTO — made from a layer entitled to depend on both modules, never
from a catalogue queryset.

**Referential integrity is not abandoned; it is deferred to the owning side.**
When `provider` lands, its migration adds the constraint with `RunSQL`:

```sql
ALTER TABLE accommodation
    ADD CONSTRAINT accommodation_provider_fk
    FOREIGN KEY (provider_id) REFERENCES provider(id);
```

A migration dependency is a string, not an import, so this couples the schemas
without coupling the code. The direction is right too: `provider` is the module
that knows when its table exists.

Until then the column is nullable, and Phase 3 seeds no accommodation and no
activities — so no row carries a dangling reference in the meantime.

## Consequences

The obvious cost is that between now and Phase 6 nothing stops a bad
`provider_id` being written. The mitigations are that nothing writes one (the
admin catalogue API leaves the field absent, and the seed loader does not set
it) and that the constraint arrives before the first provider-owned listing
does.

The less obvious cost is that this pattern is easy to over-apply. It is for
references that genuinely cross a §6.4 module boundary. Within a module a
`ForeignKey` remains the right tool, and `catalogue.attraction.destination_id`
stays a real one.

## Correction, made the same day

The paragraph originally here said the rule was about the *direction* of the
edge: that `inventory -> catalogue` is a direction §6.4 permits, so a real
`ForeignKey` was correct there. Writing `inventory.models` proved that wrong,
and import-linter said so in one line:

```
catalogue internals are private
apps.inventory is not allowed to import apps.catalogue.models
```

The contract forbids `apps.catalogue.models` to *every* other module, in either
direction, and it is right to. §6.4's dependency is on a module's **service
interface**, not on its tables, and §6.2 and §44.2 want each module extractable
behind that interface. A `ForeignKey` needs the import, installs a relation in
both directions, and would have to be unpicked to extract the seam.

So the rule has no direction clause. **A row owned by another module is
referenced by id, whichever way the DAG edge runs.** `inventory`'s
`room_type_id`, `activity_id` and `schedule_id` are plain indexed integers for
the same reason `accommodation.provider_id` is.

The referential integrity is not lost, and here it does not even have to wait:
`inventory`'s own migration adds the constraints, because a migration
dependency is a string and not an import.

```sql
ALTER TABLE room_availability
    ADD CONSTRAINT room_availability_room_type_fk
    FOREIGN KEY (room_type_id) REFERENCES room_type(id);
```

That combination — id in the model, `FOREIGN KEY` in the migration — is the
general shape. The database keeps its integrity; the module keeps its seam.

It also settles Q2's guard more firmly than the planned test would have. With no
import, no relation and no reverse accessor, `catalogue` cannot read a capacity
counter at all, let alone mutate one. Contract `private-catalogue` and
`apps.get_model` between them mean even the *tests* do not reach through: the
inventory suite builds its catalogue parent rows through Django's model
registry, which is what migrations use for exactly this reason.
