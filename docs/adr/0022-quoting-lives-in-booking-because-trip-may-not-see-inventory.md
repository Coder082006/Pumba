# ADR 0022 — Quoting lives in `booking`, because `trip` may not see `inventory`

**Status:** Accepted · **Date:** 2026-09-02 · **Phase:** 5

## Context

§9.4.5 calls `POST /trips/{id}/quote` *"the most consequential endpoint in the
system"* and gives it a single transaction that reads an itinerary, locks
capacity counters, writes hold rows and moves the trip to PRICED. It is the
whole of Phase 5's "hold mechanics" deliverable and the only place TC-050 to
TC-053 can be exercised.

It is also, as written, an endpoint no module is allowed to implement.

§6.4's dependency table gives `trip -> catalogue, transport`. `inventory` is
absent, and `.importlinter`'s `deps-trip` contract enforces the absence:

```
trip           -> catalogue, transport
booking        -> inventory, trip, provider
```

`tests/test_srs_module_catalogue.py` re-derives that table from
`docs/srs/srs-docx.txt` on every run, so the contract cannot be quietly widened
without the SRS being amended first. And the direction is not arbitrary:
`inventory` is L2 and `trip` is L3, so a `trip -> inventory` edge would be the
first downward-pointing dependency in the graph.

The endpoint's URL says `trip`. Its work says `inventory`. One of those has to
give.

## Decision

**The quote use case is `booking`'s, and the URL is unchanged.**

`booking` already depends on `inventory`, `trip` and `provider` — §6.4 gives it
that triple for no other reason, since nothing else in the system needs all
three. §43 is blunter still:

> X. Booking + inventory + payment — **DO NOT EXTRACT.** These three share the
> atomic transaction that makes the basket correct. Splitting them converts a
> database transaction into a saga and is the single most expensive mistake
> available to this system.

The quote *is* that atomic transaction, one phase early. So
`apps/booking/services.py` gains `quote_trip()`, `apps/booking/urls.py` is
mounted at `trips/<uuid:public_id>/quote`, and the composition reads:

```
trip.services       -> the itinerary and its validation state
inventory.services  -> hold(), release(), commit()
trip.services       -> mark_priced()
```

all three of them legal edges.

**No booking row is created.** `booking`'s own models, its state machine and
its basket remain Phase 7. What ships here is the module's first use case, not
the module.

### Which URL, and why not a new one

`/trips/{id}/quote` is what §9.4.5, §42's FR-030 and every sequence diagram
name. Routing is an interface concern and `config/urls.py` composes the whole
API from several modules already; moving the *path* to match the *package*
would make the specification wrong to protect an implementation detail.

## Three consequences that had to be decided with it

### `inventory_hold.trip_id` gets no foreign key

§7.4's R42 relates `booking` to `inventory_hold` *"via trip"*, and the hold row
carries `trip_id`. A real `FOREIGN KEY` would point from an L2 table to an L3
one — the dependency the module graph exists to forbid, expressed in DDL
instead of in an import. ADR 0011 added SQL foreign keys from `inventory` into
`catalogue` precisely because that direction is legal; this one is not.

So `trip_id` is a plain indexed `BigIntegerField` with no constraint behind it,
and `booking` — the one module that can see both sides — is what keeps it
honest. This is the first cross-module reference in the schema with no
referential integrity at all, and it is recorded here rather than left for a
reader to discover as an omission.

### The expiry sweeper lives in `booking.tasks`

§17.5's sweeper does two things: it releases capacity, and TC-052 requires that
*"trip returns to DRAFT-equivalent"*. Those are two modules' rows.
`inventory.services.release_expired()` handles its own — counters and hold
status, under lock. The Beat task composes it with `trip.services.expire_quote()`,
and composition is `booking`'s job for the same reason the quote is.

§8.8 registers the job as `release_expired_holds` without naming a module, so
nothing is contradicted.

### The reconciliation checker compares holds to counters, not bookings

§17.4 defines it as *"Nightly job compares Σ confirmed bookings against `*_sold`
and alerts on drift"*. There are no bookings until Phase 7, and no code path
that can move `capacity_sold` until the confirmation routine of §20.8 exists.
A job written to the letter of §17.4 would compare zero against zero, pass
every night, and be indistinguishable from a working checker on the day it
stopped being one.

So in v1 it asserts the invariant Phase 5 actually owns:

```
for each departure:  Σ quantity of live HELD holds  ==  capacity_held
```

which is the same class of drift — a counter that has diverged from the rows
that justify it — over the half of the system that exists. The `capacity_sold`
half is added in Phase 7 alongside the routine that first moves it, and the
job's docstring says so, so that the gap is a stated scope rather than a silent
one.

## Consequences

**§6.4 is unamended.** No contract in `.importlinter` changes, no SRS text
changes, and `test_srs_module_catalogue.py` keeps passing for the reason it was
written rather than because it was adjusted.

**`booking` acquires a use case three phases before its models.** The
alternative was to defer the quote to Phase 7, which would leave Phase 5 unable
to pass its own acceptance tests — TC-050 to TC-053 are all about the quote
placing holds — and would ship a departure calendar nobody can hold against.

**`trip` gains two service functions rather than a dependency.**
`mark_priced()` and `expire_quote()` are the DRAFT ↔ PRICED edges of §20.5's
machine, called by `booking`, so trip state is still only ever written by
`trip`.

**VR-06 becomes reachable.** `trip/services.py`'s `DEFERRED_INPUTS` records the
rule as inert *"because §6.4 does not permit trip -> inventory"*. It still does
not; the departure's `departs_at` arrives as a fact supplied by the caller,
which is the same shape `catalogue.opening_status` already uses to answer a
question `trip` may not answer for itself.
