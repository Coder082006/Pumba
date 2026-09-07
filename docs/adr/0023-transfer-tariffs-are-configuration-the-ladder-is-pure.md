# ADR 0023 — Transfer tariffs are configuration; the resolution ladder is pure

**Status:** Accepted · **Date:** 2026-09-07 · **Phase:** 6

## Context

§37.6 asks for "corridors and tariffs with the resolution order; the metered
pricing function; vehicle classes; the transport quoting API; the degraded-mode
rules". §12.4 specifies the pricing model. Between them sit five gaps that have
to be closed by decision rather than by reading, because the specification does
not close them.

**1. Neither table has a schema.** §7.5 runs `7.5.1 user` through
`7.5.15 audit_log` and contains neither `transfer_corridor` nor
`transfer_tariff`. Neither appears in the §7.3 ERD, in §7.4's relationship
register (R1–R43), or in §7.6's indexing strategy. The only normative column
facts anywhere are §12.4's two field tables, which give names and meanings and
no types, nullability, constraints or indexes.

**2. `transport` cannot see `catalogue`.** §6.4 gives `transport -> location,
provider`. But §12.4 step 3 resolves "the region of origin", step 4 the
"country default", and the metered formula adds `airport_surcharge` "IF origin
or target is a gateway destination". Region, country and `is_gateway` are all
`catalogue` facts, on the `destination` row. The module that owns the ladder
may not read the data the ladder branches on.

**3. `booking_transfer.tariff_id` is one column for two tables.** §12.4: "the
matched rule id is stored on `booking_transfer.tariff_id` so the price is
reproducible forever". Steps 1 and 2 match a `transfer_corridor`; steps 3 and 4
match a `transfer_tariff`. A single FK cannot address both.

**4. Whether a market-level step exists.** ADR 0018 introduced `market` between
country and region and explicitly deferred the pricing question: "whoever
builds transfer pricing should ask whether step 3.5 exists." §4.2 as amended
says the same thing and leaves it open.

**5. Where a leg's vehicle class is stored.** §12.2: "A transfer leg is defined
by an origin, a target, a departure instant, a party size, a luggage count and
a vehicle class ... and the binding is stored on the itinerary item so that the
leg can be re-priced identically later." §7.5.11's column list for
`itinerary_item` contains neither `vehicle_class` nor `luggage_count`.

## Decision

### 1. Design both tables against §7.2, and record the invention

The ADR 0007 precedent: "Design all five against the §7.2 conventions, and
record the reasoning per table so the invention is visible rather than buried
in a migration."

Both are `BaseModel + SoftDeleteModel`. They are curated commercial
configuration, in the same family as `cancellation_policy` — an administrator
withdraws a corridor and later wants it back, and a hard delete would take the
audit trail with it. They are **not** `VersionedModel`: nothing takes a row
lock on a tariff, because §12.4 reads them and BR-054 freezes the result onto
the booking rather than mutating the rule.

Cross-module references — `origin_destination_id`, `target_destination_id`,
`region_id`, `country_id` — are plain indexed `BigIntegerField` with **no SQL
foreign key**. ADR 0012: `transport` is L2 and a database-level dependency on
`catalogue` would be the edge §6.4 forbids, written in DDL where import-linter
cannot see it.

Effective dating (`valid_from`, `valid_to`) on both, per §12.4. A partial
unique index covers the active window so two overlapping corridors for one
(origin, target, class) cannot both be live — the ladder says "first match
wins", and two simultaneous first matches is a coin flip on a price.

### 2. `quote_transfer()` takes resolved facts, and the view lives in `trip`

`transport.services.quote_transfer()` receives a `LegEndpoint` carrying the
destination id, region id, country id, `is_gateway`, coordinate and timezone —
already resolved. It performs no catalogue read and needs no catalogue import.
The ladder becomes a pure function over facts and rows, which is what §8.2
layer 3 wants of it anyway.

Somebody must do the resolving. `.importlinter` gives `trip -> catalogue,
transport`, and `trip` is the only module that may see both. So **the view for
`POST /transport/quotes` lives in `trip`**, resolves each of §12.2's four
bindings through `catalogue.services`, and calls `transport.services`.

The URL is unchanged: `POST /api/v1/transport/quotes`, exactly as §9.4.4 gives
it. Only the code moves. This is ADR 0022's pattern and ADR 0022's argument —
the same reason `POST /trips/{id}/quote` is served by `booking`: the use case
belongs to the only module allowed to see both halves of it.

Admin CRUD is the mirror image and goes elsewhere again. A corridor row names
two destinations and a tariff row names a region; validating that they exist is
a catalogue read. `administration` has `-> all (read via interfaces)` and is
already the designated cross-module assembly point — `seed.py` lives there for
precisely this reason and its docstring says so. So `/admin/transport/corridors`,
`/admin/transport/tariffs` and `/admin/transport/quote-preview` are served by
`administration`.

### 3. A match carries its kind

`quote_transfer()` returns `TariffMatch(kind, rule_id, step)` where `kind` is
`CORRIDOR` or `TARIFF` and `step` is 1–4. Phase 7's `booking_transfer` stores
both columns with a CHECK that exactly one is populated, rather than one
ambiguous `tariff_id`.

The `step` is not decoration. §27.11 asks for "a quote-preview tool for any
origin-destination-class combination", and an administrator debugging a price
needs to know *which rung answered*, not only what it said. A price that came
from the country-default fallback when a corridor was expected is a
misconfiguration that looks identical to a correct answer without it.

### 4. No market-level step 3.5

The ladder is four steps, exactly as §12.4 states.

A market fallback between region and country would price Zanzibar's three
regions identically, which is the distinction ADR 0018 created `market`
specifically to preserve: "collapsing Zanzibar's three regions into one
'Zanzibar' row to make the selector work would erase the distinction the
metered fallback prices on". Adding the step back one level up erases it just
as thoroughly, one rung later.

The country default already exists as the catch-all. A market that genuinely
needs its own rates has regions, and the regions can carry them.

Recorded here so the next reader finds an answer rather than the question.

### 5. `itinerary_item` gains `vehicle_class` and `luggage_count`

Under ADR 0007's rule, and following ADR 0019, which added `estimate_quality`
to the same table for the same reason.

NOT NULL exactly when `item_type = 'TRANSFER'`, enforced by a CHECK, in the
shape every other transfer column on that table already uses. Without them
§12.2's promise — that a leg can be "re-priced identically later" — is
unkeepable: the inputs to the price would not survive the request that computed
it.

A planner-inserted transfer defaults to the cheapest class whose seats are at
least the party size and whose luggage capacity is at least the luggage count.
§12.4 is explicit that capacity **filters** rather than selects — "the quote
returns only classes whose seats >= pax and luggage >= luggage_count" — and
§24.17 gives the tourist per-leg selection. The default exists because the
planner must insert something; it is a starting point, not the answer.

### 6. `vehicle_class` is a seeded table

§6.4 does not name it among `transport`'s tables. Appendix C counts it as a
seeded entity anyway — "Vehicle class | 4 | STANDARD, COMFORT, VAN, MINIBUS" —
and hard rule 5 forbids seat and luggage capacities as literals in code. Four
rows, seeded like every other piece of reference data, served by
`GET /transport/vehicle-classes`, and available for `vehicle.vehicle_class` to
point at when Phase 11 registers real vehicles.

## Consequences

**The ladder is testable without a database.** `resolve()` and `metered()` are
pure functions over rows and facts, so §12.4's worked example is a unit test
asserting `12.00 + 0.40 x 57.4 + 0.00 x 85 = 34.96`, plus `3.04`, equals
`USD 38.00` — with no fixtures, no routing and no network. §41.6 requires that
example to "reproduce exactly", and this is what makes the requirement cheap
enough to assert on every run.

**The seed prices in TZS and the worked example is in USD, and that is not a
contradiction.** `country.default_currency` is TZS, every seeded destination is
TZS, `trip.currency` comes from the destination and VR-10 requires uniformity.
So the seeded corridors are TZS rows and the USD worked example lives in the
domain test where no trip currency applies.

**Three modules serve transport routes, and a reader will find that odd.** The
public reads are `transport`'s, the tourist quote is `trip`'s, the admin CRUD
is `administration`'s. Each placement follows from what the module is allowed
to see, and the alternative — one module serving all of them — requires an
import §6.4 forbids. The URLs give no sign of the split, which is the point.

**The failure mode to watch is a catalogue read creeping into `transport`.**
The moment `quote_transfer()` resolves an id itself rather than being handed
the fact, the contract breaks and import-linter says so. That is the guard, and
it is why the DTO carries `is_gateway` as a boolean rather than a destination
id the callee could be tempted to look up.
