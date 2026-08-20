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

Contrast with [ADR 0011](0011-inventory-owns-the-availability-tables.md), where
the reference runs `inventory -> catalogue` — a direction §6.4 permits — and a
real `ForeignKey` is therefore correct. The rule is about the direction of the
edge in the DAG, not about crossing a module boundary as such.
