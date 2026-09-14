# ADR 0025 — The booking schema the SRS names but never specifies

**Status:** Accepted · **Date:** 2026-09-14 · **Phase:** 7

## Context

§37.7 asks for "booking and subtype models; the state machine and its guards;
status history; basket creation; the confirmation and release routines;
cancellation policy evaluation with preview; provider accept/decline for
on-request activities". The specification is strong on behaviour and thin on
structure, and in several places it contradicts itself. Everything below is a
gap that has to be closed by decision rather than by reading.

**1. Four of the five tables have no specification.** §7.5 specifies
`7.5.12 booking` and nothing else in the module. `booking_activity`,
`booking_transfer`, `booking_status_history` and `booking_accommodation` exist
only as box contents in the §7.3 ERD — names, with no types, nullability,
defaults or indexes.

**2. There is nobody to book from.** §7.5.12 makes `booking.provider_id`
NOT NULL, and BR-037 requires the provider to be "VERIFIED and unsuspended at
the moment of confirmation". There is no `provider` table, the module is stubs,
and all twelve seeded activities carry `provider_id = NULL`. §7.5.3 does specify
`provider` in full — it was simply never built.

**3. The service fee has two homes.** §22.1's commercial model charges the
platform service fee *on top of* each component's gross, and computes commission
on the gross alone. §20.9's worked example retains the fee *out of* the gross, so
the provider's compensation shrinks by it (USD 110 → 55.00 refund, 5.50 fee,
49.50 provider). Both cannot be true of one booking.

**4. The policy is both a foreign key and a snapshot.** The §7.3 ERD shows
`cancel_policy_id FK`; §7.5.12 shows `cancellation_policy_snapshot JSONB` and
no FK; R43 says "policy snapshot copied onto booking" with cardinality
`cancellation_policy 1 : 0..* booking`.

**5. A hold belongs to a trip, and the routine iterates holds of a booking.**
R42: "holds are keyed to trip during checkout". §20.8 step 7: "FOR each
inventory_hold of this booking". No column links the two.

**6. Snapshot timing is stated twice, differently.** §9.4.6 and TC-060 snapshot
policy and commission at `POST /trips/{id}/confirm`, when the booking is
PENDING. BR-070 and §20.8 step 14 snapshot commission rate, amount and net at
capture.

**7. The on-request timeout has no job.** §14.4 and §20.2 require an automatic
cancellation after `provider_response_hours`. No beat entry, cadence or TC
number is given — contrast `release_expired_holds`, which §8 schedules at 60 s.

**8. Smaller silences.** No enumeration of `cancellation_reason` ("coded
reason"); no convention for `actor_user_id` on a System transition; no
cancellation policy for a TRANSFER, which has no catalogue listing to carry one;
and §20.5 draws "payment failed → back to PRICED" while the implemented trip
machine has no `PENDING_PAYMENT → PRICED` edge.

## Decision

### 1. Design the four tables against §7.2, in ADR 0007's shape

The same rule ADR 0007, 0019 and 0023 used: a table the SRS names but does not
specify is designed against §7.2's conventions, and the reasoning is recorded
here so the invention is visible rather than buried in a migration. Every
column the ERD names is kept under its ERD name. Cross-module references are
plain indexed `BIGINT` with no SQL foreign key (ADR 0012).

**`booking`** — §7.5.12 as specified, plus five columns it needs and does not
name:

| Added column | Why |
|---|---|
| `fee_amount NUMERIC(14,2)` | This component's share of the trip's service fee. Decision 3 needs it, and it cannot be recomputed later once `platform_fee_rate` moves |
| `tax_amount NUMERIC(14,2)` | The same, for tax. Zero while no tax rule is configured |
| `cancellation_policy_id BIGINT NULL` | Decision 4 |
| `cancelled_by VARCHAR(20) NULL` | TOURIST / PROVIDER / DRIVER / PLATFORM. BR-045 turns on *who* cancelled; `cancellation_reason` says why, not who |
| `response_due_at TIMESTAMPTZ NULL` | Decision 7 |

`booking_type` keeps all three §7.5.12 values in its enum and a CHECK that
admits only `ACTIVITY` and `TRANSFER`. ADR 0013 reserves `ACCOMMODATION`
without renumbering, so reviving it in v2 is a CHECK change, not a rename.
`reference` is `BKG-YYYY-NNNNNNN` from the existing `common/reference.py`,
random rather than sequential, placed by insert-and-retry on the UNIQUE
constraint — the allocator `trip` already uses.

**`booking_activity`** — `booking_id` (PK, FK, CASCADE per R24),
`activity_id`, `activity_departure_id`, `pax_adult`, `pax_child`, `meeting_at`.

**`booking_transfer`** — `booking_id` (PK, FK, CASCADE), `origin_destination_id`,
`target_destination_id`, `pickup_point` and `dropoff_point` as geography points
(the ERD's `lat/lng` pairs, stored the way `itinerary_item` already stores
them), `pickup_at`, `distance_m`, `travel_seconds`, **`estimate_quality`**,
`vehicle_class`, `luggage_count`, `is_airport_transfer`, `trip_flight_id NULL`,
and **two** rule columns, `corridor_id` and `tariff_id`, with
`CHECK (num_nonnulls(corridor_id, tariff_id) = 1)` — the instruction ADR 0023
wrote down, because one `tariff_id` cannot address two tables.
`estimate_quality` is added so that a corridor-priced leg whose distance was
estimated cannot launder that estimate into a confirmed record (ADR 0019).

**`booking_status_history`** — `id`, `booking_id` (FK, CASCADE per R26),
`from_status NULL` (null on creation), `to_status`, `actor_user_id NULL`,
`actor_role`, `reason`, `occurred_at`. **Append-only** by trigger: an UPDATE or
DELETE raises. R26 says append-only, and an application convention is the kind
that erodes one "just this once" repair at a time.

A System transition writes `actor_user_id = NULL` and `actor_role = 'SYSTEM'`.
Null alone would be indistinguishable from a lost actor.

**`booking_accommodation`** is not created. ADR 0013 reserves it.

### 2. `provider` is built now, as §7.5.3 specifies it, and administered

Nothing about the table is invented. What Phase 7 adds is the smallest thing
that makes a booking payable to somebody: an administrator creates a provider,
walks it to VERIFIED, and points listings at it. Self-registration, document
upload and the provider principal remain Phase 11.

`verify_status` is a state machine, declared in `provider/domain`, with §26.2's
edges: DRAFT → SUBMITTED → UNDER_REVIEW → VERIFIED | REJECTED; REJECTED →
SUBMITTED (resubmit); VERIFIED → SUSPENDED → VERIFIED. The administrator's
"verify" action walks the declared edges and audits each, rather than jumping
DRAFT → VERIFIED along an edge §26.2 does not draw.

The console lives in `administration`. `provider -> identity` is the whole of
its allowed graph, and a provider names a `catalogue` region and owns
`catalogue` listings — the placement argument ADR 0023 made for the tariff
console. §7.5.3's "a provider may not own listings of a type inconsistent with
provider_type" is enforced in that application service; the trigger half would
be a cross-module trigger, which ADR 0012 forbids, and is not built.

`activity.provider_id` is backfilled from seeded providers but **stays
nullable**, which the first draft of this record got wrong. The catalogue console
creates activities, and `catalogue` may not import `provider`, so a NOT NULL
column would make an activity impossible to create until somebody could name its
seller in a module that cannot see sellers. Instead a listing with no provider is
a listing nobody can book: basket creation refuses it, and the seed test asserts
no seeded activity is without one.

### 3. The service fee sits on top of gross

§22.1 is the commercial model and §20.9 is one example of it; where they
disagree, the model wins. So:

- `booking.gross_amount` is the component's price — its `itinerary_item.line_total`.
- `booking.fee_amount` is its share of `trip.fee_amount`, allocated pro-rata by
  gross and settled to the cent by largest remainder, so the shares sum to the
  trip's fee exactly. `tax_amount` is allocated the same way.
- Commission is `gross_amount × commission_rate` and never touches the fee.

§20.9 is then evaluated with the fee outside the price:

```
refund_of_price        = round(gross × refund_percent / 100, 2)
fee_refunded           = fee   if cancelled by PROVIDER/DRIVER/PLATFORM (BR-045)
                                or more than 7 days before start
                         else 0
tax_refunded           = round(tax × refund_percent / 100, 2)
provider_compensation  = gross − refund_of_price
refund_amount          = refund_of_price + fee_refunded + tax_refunded
```

For §20.9's own inputs — gross 110.00, fee 5.50 on top, MODERATE_7D, cancelled
by the tourist three days out — the tourist receives **55.00**, the platform
retains **5.50**, and the provider keeps **55.00**. The example's 49.50 is not
reproduced, deliberately. Recorded so the next reader does not "fix" the test
back to the example.

This also removes a defect in §20.9 as written. Under its formula a 100%-refund
tier inside seven days — FLEX_48H cancelled three days out — gives
`provider_compensation = gross − gross − fee`, which is negative. With the fee
outside the price the provider's figure is `gross − refund_of_price`, which
cannot go below zero.

### 4. Keep the policy id *and* the snapshot

`cancellation_policy_id` records which policy the component was sold under, for
reporting and for §27.12's "which listings use each policy". The JSONB snapshot
is what BR-040 evaluates, and nothing ever reads the live row to price a refund.
The id is nullable because a transfer has no listing (decision 8).

### 5. A booking's holds are found by resource, not by column

`inventory.hold` sums two items on one departure into a single claim, so one
hold may cover two bookings, and a hold row is released and re-created on every
re-quote. A `hold_id` column on a booking would be wrong in the first case and
stale in the second. §20.8's "holds of this booking" is therefore resolved as
*the trip's live holds on this booking's `activity_departure_id`*, and a
transfer has none. Commitment itself calls the existing `inventory.commit()`,
which already works per trip and already refuses a dead hold with `HOLD_EXPIRED`.

### 6. Snapshot the rate at the basket, the amounts at capture

At `POST /trips/{id}/confirm`: the policy snapshot, the policy id and the
commission **rate** — which is what TC-060 checks ("policy and commission
snapshotted"). At capture, §20.8 step 14: `commission_amount` and `net_amount`,
computed from the rate already frozen. The rate cannot move between the two, so
this satisfies BR-070 and §20.8 at once rather than choosing between them.

The rate is resolved through §22.2's order. `commission_rule` is Phase 8, so in
Phase 7 every scope above GLOBAL is empty and the rate is
`commission.default_percent`. The snapshot is taken either way; Phase 8 changes
what is resolved, not where it is stored.

The hold window is extended to `payment.window_minutes` at basket creation, as
§9.4.6 says. §17.2 anchors the same window to payment-intent creation; Phase 8
may extend it again there, and extending twice is harmless while extending too
late is not.

### 7. The on-request timeout is a scheduled sweep against a stored deadline

Entering AWAITING_PROVIDER stores `response_due_at = now + provider_response_hours`.
`booking.expire_provider_responses` runs every five minutes and cancels, with
`cancelled_by = PROVIDER`, `cancellation_reason = PROVIDER_RESPONSE_TIMEOUT`
and a full refund under BR-045, every booking still awaiting past its deadline.

**The deadline is the rule; the sweep is housekeeping.** An acceptance arriving
after `response_due_at` is refused even if the sweep has not run yet, so the
five-minute cadence never becomes five extra minutes of provider grace.

### 8. The smaller silences

**`cancellation_reason` codes:** `TOURIST_REQUEST`, `TRIP_CANCELLED`,
`PROVIDER_DECLINED`, `PROVIDER_RESPONSE_TIMEOUT`, `PROVIDER_UNAVAILABLE`,
`ADMIN_ACTION`. The column stays `VARCHAR(40)` with a CHECK, so adding a code is
a migration with a reason attached.

**A transfer's cancellation policy** is the one named by a new setting,
`transfer.cancellation_policy_code`, default `FLEX_48H`. §26.4 makes transfer
pricing platform-managed, so its policy is platform-managed too; a setting keeps
it out of code (hard rule 5) and out of any provider's hands.

**The booking machine's closure.** §20.2's fourteen edges and no others.
`AWAITING_PROVIDER → IN_PROGRESS` and `AWAITING_PROVIDER → NO_SHOW` are illegal;
`REFUNDED` is reachable only from `CANCELLED`. Terminal states are COMPLETED,
REFUNDED, NO_SHOW and FAILED — CANCELLED is not terminal, because it moves to
REFUNDED. §20.10's NO_SHOW reversal is a dispute outcome with a compensating
refund, which arrives with the driver phases; it is not an edge.

**Force-transition** (BR-038) bypasses *guards*, never the *table*. A
SUPER_ADMIN may move a booking along any declared edge whose guard would refuse;
an undeclared edge is refused for them too. An exceptional control that could
invent an edge would make §20.2 advisory.

**The trip machine gains `PENDING_PAYMENT → PRICED`.** §20.5 draws it for a
failed payment and TC-072 expects it; the implemented table has only
`PENDING_PAYMENT → DRAFT`, which is the quote-expired path. Both edges are kept.

**The tier boundary stays strict.** §14.6's prose says "> 7 days"; §20.9's
pseudocode says `hours_before >= T.hours_before`. They differ at exactly
168.000000 hours. `catalogue.domain.cancellation.refund_percent_at` already
implements `>`, and TC-100/TC-101 test one second either side, where both
readings agree. Evaluation reuses that function rather than re-deciding.

## Consequences

**A booking row is reproducible from itself.** Price, fee share, tax share,
policy tiers, commission rate, tariff rule and distance quality are all on the
row or its subtype. BR-040, BR-054 and BR-070 are satisfied by storage, not by
discipline.

**The §20.9 worked example is not a test fixture as printed.** A test uses its
inputs and asserts decision 3's outputs, with a comment pointing here.

**Phase 8 changes resolution, not schema.** `commission_rule` fills §22.2's
upper scopes; the webhook calls the confirmation routine this phase writes; the
payment window may be extended once more at intent creation.

**Two cross-module invariants are held by application code**, not the
database: a provider's listings match its type, and a booking's provider is
VERIFIED at confirmation. Both have a positive and a negative test, because
nothing else would notice them failing.

## Addendum — 2026-09-15: who sells a transfer

Building the basket found a ninth gap. §7.5.12 makes `booking.provider_id`
NOT NULL, and a transfer booking has no listing to take a provider from: §20.3
names the fulfilment actor as "assigned driver", and assignment is dispatch,
which happens after payment and arrives in Phases 9–10.

**Decision:** a transfer booking is sold by the VERIFIED `TRANSPORT` provider
operating in the leg's origin region — `provider.region_id` is §7.5.3's
"operating region" — and when a region has several, the oldest. The rule is
stable, so a given leg books against the same operator on every attempt, and
the tie-break favours nobody by name. A region with none refuses the leg at
basket creation rather than selling a drive nobody can be paid for.

This is a placeholder for dispatch, not a substitute for it. When Phase 9
assigns a driver, the assignment may belong to a different operator; whether
`booking.provider_id` then follows the assignment is Phase 9's decision, and
the column is written only by this module either way (§20.1).

The seed gives each of the three live Zanzibar regions one transport provider,
and `tests/test_seed.py` asserts every region with a live destination has one.

## Addendum — 2026-09-15: the confirmation mode is frozen at the basket

§20.8 step 12 sets a booking CONFIRMED "(or AWAITING_PROVIDER)", and §14.4 makes
the choice turn on `activity.confirmation_mode`. Step 12 runs at payment capture;
the listing is `catalogue`'s and may change between the basket and the capture.

**Decision:** `booking_activity.confirmation_mode` snapshots the mode at basket
creation, with a CHECK admitting INSTANT and ON_REQUEST. The tourist is shown at
checkout whether a component confirms instantly, and an operator switching the
listing afterwards must not turn that promise into a wait nobody agreed to — the
same reasoning BR-041 and §26.4 apply to policy and price.

