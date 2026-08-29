# ADR 0018 — Market is a tier between country and region

**Status:** Accepted · **Date:** 2026-08-29 · **Phase:** 3.5

## Context

The landing page must ask the tourist where they are going, offer Zanzibar,
and answer "Arusha" with *not open yet* rather than with a 404 or with silence.
That is a product requirement, and it is also the first screen that makes §4.1's
promise — *"enables scheduled market launch without a deployment"* — visible to
anyone who is not reading the schema.

**The tier it needs does not exist.** §4.2 specifies a five-level hierarchy:

```
Country → Region → Destination → Attraction / Accommodation → Activity
```

and gives its Region examples as *"Zanzibar Urban/West, Zanzibar North,
Arusha"*. Read that list slowly: two of the three are parts of Zanzibar and the
third is a whole. The SRS's own example is where the strain shows. In the
database as seeded, **"Zanzibar" is not a row**. It is three regions —
`zanzibar-urban-west`, `zanzibar-north`, `zanzibar-central-south` — with Pemba
contributing two more. Arusha would be one. There is no level at which
"Zanzibar" and "Arusha" are the same kind of thing, so there is no level a
selector can be built from.

### Why not `country`

Zanzibar and Arusha are both `TZ`. One country, two markets. Using `country`
would mean either inventing an ISO code Tanzania does not have, or the selector
having exactly one entry forever.

The word is admittedly already taken: `Country`'s docstring calls its bounding
box *"the rectangle every coordinate in this market must fall inside"*, and
`is_publicly_visible` says *"a market that launches on the 12th is open on the
12th"*. That usage was loose, and this ADR is what makes it precise. **A market
is a `market` row.** The country's bounding box stays where it is and keeps its
job; only the prose is corrected.

### Why not `region`

Because region granularity is load-bearing for money. §12.4 resolves a transfer
price down a four-step ladder:

```
1. Active transfer_corridor for (origin_dest, target_dest, class)
2. Active transfer_corridor for the reverse pair when is_bidirectional
3. Active transfer_tariff for (region of origin, class)      ← here
4. Active transfer_tariff for (country default, class)
```

Step 3 is scoped to the region. Collapsing Zanzibar's three regions into one
"Zanzibar" row to make the selector work would erase the distinction the
metered fallback prices on, in a subsystem Phase 6 has not built yet and cannot
argue back. Widening `region` to mean both things means it means neither.

## Decision

**A `market` tier is inserted between `country` and `region`.** The hierarchy
becomes six levels.

```
Country → Market → Region → Destination → Attraction / Accommodation → Activity
```

`market`: `country_id`, `name`, `slug`, `summary`, `is_active`, `launch_date`,
`deleted_at`. `region` gains a `market_id` foreign key — nullable, then a data
migration, then NOT NULL, which is the ADR 0013 pattern.

Seeded: **Zanzibar** (open) and **Pemba** (`is_active = false`, matching §4.1's
"record created but is_active = false"). **Arusha is not seeded** — see
"§41.12" below.

### Two predicates, deliberately different

This is the part that carries the risk, so it is stated as a rule rather than
left to the call sites.

| | Means | Reads |
|---|---|---|
| **`is_listed`** | appears in the selector | active, not deleted. **Ignores `launch_date`.** |
| **`is_open`** | its catalogue is browsable | active, not deleted, **and** `launch_date` reached |

A listed-but-unopened market is exactly the state the landing page needs: the
tile is there, it says *not open yet*, and **nothing beneath it is reachable**.
Its regions, destinations, attractions and activities stay invisible on every
endpoint, absent from the sitemap, and 404 on direct URL.

`is_open` is not new logic. It is `domain.visibility.is_publicly_visible`
applied to a market node, and `market` joins the existing chain in
`visible_chain` and in `selectors._CHAINS` as one more level with
`has_launch_date = True`. An unopened market hides its subtree by exactly the
mechanism a deactivated region already uses. `is_listed` is the *only* new
predicate, it applies to `market` alone, and the selector endpoint is the only
thing permitted to use it.

**The failure this is written against.** Get the chain wrong and Pemba's
attractions become reachable by direct URL and reappear in the sitemap — the
precise regression `domain/visibility.py` exists to prevent, and one that
produces no error and no odd-looking output. The regression tests are therefore
written and failing **before** the model lands, not after.

### The market owns its imagery

`MediaOwnerType` gains `MARKET`, and each market carries an ordered gallery
exactly as a destination does.

The landing page hero must show what the selected place actually looks like —
Zanzibar's beaches and Stone Town; a waterfall or wildlife for somewhere that
is not Zanzibar. §4.2 forbids that mapping living in code, and
`if market == "zanzibar": beach_photo` is the same prohibited construct as
`if destination == "Zanzibar":` wearing different clothes. So it is data.
Opening a market is creating a row and attaching pictures.

## §41.12 is amended, and its pass condition is not weakened

The destination-independence acceptance test has an administrator create *"a new
country, region and destination (for example Arusha)"* with the pass condition
**"no application code change, no deployment, and no database migration"**.

That test now has one more step: the administrator creates a **market** as well.
The pass condition is unchanged and still holds — creating a market is an
`INSERT`, not a migration. This ADR's own migrations run once, now, before the
test is executed, exactly as `region` and `destination` were migrated into
existence before the test could reference them.

**Arusha is deliberately not seeded.** §41.12 requires an administrator to
create it through the console. Seeding it here would pre-empt the acceptance
test and turn a passing criterion into a fixture. The selector shows two markets
until somebody exercises the mechanism the selector exists to advertise.

## Consequences

**`launch_date` now lives at two levels.** §4.2's flag table attributes it to
`destination`. It is now also on `market`, which is the level the phrase
"scheduled market launch" was always describing. `VisibilityNode` already
carried `launch_date` for any node — *"a future entity gaining a launch date
needs no new code"* — so this is the case that docstring anticipated.

**Every catalogue URL gains a market segment**: `/[market]/explore`,
`/[market]/destinations/[slug]`. The sitemap has been live, so the old paths
redirect rather than break.

**The §12.4 tariff ladder is left alone.** A market-level fallback between
region and country is arguable, and it is a *pricing* decision belonging to
Phase 6 and §12.4, not a geography one. Recording it here rather than deciding
it: whoever builds transfer pricing should ask whether step 3.5 exists. Until
they do, the ladder resolves exactly as it does today, because `market` sits
above `region` and changes nothing below it.

**Trip remains single-destination.** §44.4 already records `trip.destination_id`
as the known structural limitation. A market tier neither fixes nor worsens it.

**Cost.** Five modules join a table they did not join before, and the visibility
chain is one level deeper on four models — a join whose cost is a single index
lookup, against a table with two rows.
