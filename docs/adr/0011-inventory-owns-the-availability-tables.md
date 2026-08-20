# ADR 0011 — `room_availability` and `activity_departure` belong to `inventory`, not `catalogue`

**Status:** Accepted · **Date:** 2026-08-20 · **Phase:** 3

## Context

The Phase 3 plan lists thirteen tables "all in `catalogue`", and two of them are
`room_availability` and `activity_departure`. That is wrong, and SRS §6.4 says
so in its own ownership column:

```
catalogue | country, region, destination, attraction, activity,
            activity_schedule, accommodation, room_type, media | ... | location
inventory | room_availability, activity_departure, inventory_hold | ... | catalogue
```

The two tables are named for `inventory`, and `inventory` is the module that
depends on `catalogue` rather than the other way round.

The error came from reading §7.5.8 and §7.5.9 — which sit inside the data model
section, next to `room_type` and `activity` — without re-reading §6.4. Nothing
in the build would have caught it: `tests/test_srs_module_catalogue.py`
re-derives the §6.4 *dependency* column from the lossless docx extraction, but
not the *ownership* column, so a table in the wrong module breaks no contract
and fails no test. It would have surfaced in Phase 5, as a table that has to
move after two phases of code has been written against its current home.

Question Q2 of the Phase 3 plan was answered "forward-declare
`room_availability` now, read-only", with the guard:

> No write path from any endpoint. [...] A test that asserts the catalogue
> module exposes no mutation of `room_availability` — the same shape as your
> import-linter violation tests. Prove the constraint, do not document it.

Putting the table in its §6.4 home makes that guard structural rather than
bespoke, which is a better outcome than the test that was planned.

## Decision

`room_availability` and `activity_departure` are created in `apps/inventory/`,
referencing `catalogue.room_type` and `catalogue.activity` by id, with the
`FOREIGN KEY` added in SQL by inventory's own migration. The reference is by id
rather than by `ForeignKey` per [ADR
0012](0012-cross-module-row-references-are-by-id.md), whose correction note
records why the permitted DAG direction does not license the import.

Three consequences follow, and all three are wanted.

**Q2's guard is enforced by import-linter.** `catalogue` cannot import
`inventory`, so no catalogue service, serializer, view or repository can reach
the availability rows at all, let alone mutate them. The planned assertion test
remains, but it now asserts something the linter already makes impossible —
which is the belt to the linter's braces, not the only fastening.

**The `CHECK (rooms_held + rooms_sold <= rooms_open)` constraint ships now**, as
Q2 requires, in `inventory`'s first migration. Nothing writes those columns in
Phase 3; the constraint exists so that when something does, the oversell it
prevents was never possible.

**The two endpoints that report indicative availability move up a layer.**
`GET /accommodations/{id}/room-types?check_in=…` and
`GET /activities/{id}/departures` compose catalogue data with inventory counts.
`catalogue` may not do that composition. `inventory` may — it depends on
`catalogue` — so the availability-aware variants of those endpoints are served
from `apps/inventory/views.py`, reading catalogue through its service
interface. The figures remain indicative and are labelled as such in the
serializer contract; the authoritative check happens under row lock inside the
committing transaction, in Phase 5.

## Consequences

Phase 5 gains the materialisation job and the capacity mutation in the module
that already owns the rows, rather than beginning with a table move.

`tests/test_srs_module_catalogue.py` should be extended to derive the ownership
column as well as the dependency column, so the next misplacement fails the
build instead of waiting two phases. That is deliberately not done in this
commit: the derivation needs the §7.5 table names normalised against the ORM's
`db_table` values, which is a piece of work in its own right and would bury
this decision inside it.
