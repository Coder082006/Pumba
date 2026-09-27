# ADR 0029 — The dispatch tables the SRS names but never specifies

**Status:** Accepted · **Date:** 2026-09-27 · **Phase:** 9a

## Context

§7.5 specifies `driver` (§7.5.4) and `vehicle` (§7.5.5) column by column, and
Phase 9a builds both exactly as written. It specifies none of the four tables
the dispatcher actually runs on.

`driver_assignment` and `driver_offer` are assigned to `transport` by §6.4,
drawn in the §7.3 ERD, given a relationship cardinality in R2
("booking_transfer : driver_assignment, 1 : 0..1, assignment created at
confirmation or by dispatch"), given an EXCLUDE constraint in §7.6, given
optimistic locking in §7.6, given two jobs in §8.8 (`dispatch_driver_offer`,
`expire_driver_offer`), given a nine-state machine in §11.7 and a scoring
function in §11.6 — and never given a column list. `driver_location` and
`geofence` are assigned to `location` by §6.4 and named in §7.6's index list,
§8.8's `purge_driver_locations`, §13 and §31.5, with the same absence.

This is the fourth time: ADR 0007 (Phase 2), ADR 0025 (booking), ADR 0027
(payment) and ADR 0028 (finance) record the same gap. It is settled the same
way — design the tables from the behaviour the SRS *does* specify, and say so
in writing rather than in a migration nobody reads.

Two of the four also raise a question the earlier ADRs did not: whether they
should be created in 9a at all.

## Decision

### 1. `driver_assignment`

One row per transfer booking, created PENDING at confirmation (R2), advanced
only along §11.7's declared edges.

| Column | Why |
|---|---|
| `id`, `public_id` | House convention |
| `booking_id` | The transfer it serves. `UNIQUE` — R2's `0..1` |
| `driver_id`, `vehicle_id` | Null until ASSIGNED |
| `status` | §11.7's nine: PENDING, OFFERED, ASSIGNED, EN_ROUTE, ARRIVED, STARTED, COMPLETED, CANCELLED, UNFULFILLED. INCIDENT is a tenth, from §11.8 |
| `starts_at`, `ends_at` | **The buffered window, not the pickup time.** §7.6 writes the constraint over `tstzrange(starts_at, ends_at)` and §11.6 rule 5 defines the window as `[pickup_at - pre_buffer, expected_end + post_buffer]`. Storing the buffered window is the only way both readings are true at once, so these columns mean "when this driver is unavailable", and `pickup_at` stays on `booking_transfer` where it already is |
| `pickup_pin_hash` | §11.9: generated at assignment, stored hashed. Never a column that can be read back |
| `assigned_at`, `en_route_at`, `arrived_at`, `started_at`, `completed_at` | The timestamps §25 and §11.8's detections read. A no-show is "no ARRIVED within 30 minutes of pickup time", which needs a column, not an inference from history |
| `override_reason` | §11.7's guards are overridable; §13 (line 4782) requires the reason and an audit flag |
| `cancellation_reason`, `incident_reason` | §11.8's paths |
| `version` | §7.6 names `driver_assignment` for optimistic locking |

**The EXCLUDE constraint is the rule, not a check.** §7.6 verbatim:
`EXCLUDE USING gist (driver_id WITH =, tstzrange(starts_at, ends_at) WITH &&)`,
partial on the non-terminal statuses so a completed or cancelled assignment
stops blocking the driver's diary. `btree_gist` already exists — this module's
own `0001_initial.py` creates it for the tariff constraints.

The eligibility filter of §11.6 checks overlap too. That is deliberate
duplication: the filter produces a candidate list, the constraint produces a
guarantee, and TC-084 asserts the second one.

History is append-only by trigger, as `booking_status_history` is.

### 2. `driver_offer`

One row per offer to one candidate. `assignment_id`, `driver_id`, `rank`,
`score`, `score_components` (JSONB), `expires_at`, `status`
(SENT / ACCEPTED / DECLINED / EXPIRED / SUPERSEDED), `responded_at`,
`decline_reason`.

`score_components` is on the row and not only in the audit log because §11.6
ends by requiring that "a provider dispute can be answered with the exact
computation", and a dispute is about *one driver's* offer. The audit entry
holds the whole candidate list; the offer holds its own arithmetic.

A partial unique index allows at most one `SENT` offer per assignment: §11.5
offers to `candidate[i]`, singular, and advances. Two live offers would make
TC-083's race a data error rather than a contended one.

### 3. `driver_location` lands minimally

§11.6 needs one fact: `last_known_location if online and fresh (< 15 min)`.
That is the whole of 9a's requirement, and 9a builds the whole of it —
`driver_id`, `point`, `accuracy_m`, `recorded_at`, `speed_kph`, `heading` — and
nothing else.

Deferred to Phase 10, with reasons rather than as an oversight: daily
partitioning and the 30-day retention of §31.5 and `purge_driver_locations`
(§8.8), because partitioning an empty table is a guess at its access pattern;
ingest validation and the ETA plausibility checks of §33; and the WebSocket
fan-out, which is Phase 10's deliverable and TC-092's subject.

### 4. `geofence` is **not** created in 9a

§11.7's guards read "inside geofence (300 m)" and "inside drop-off geofence".
Both are a distance from a point the system already stores —
`booking_transfer.pickup_point` and `dropoff_point`, both
`geography(Point,4326)`. In 9a the guard is therefore a computation, and there
is nothing to put in a `geofence` row that is not already in `booking_transfer`
plus a radius from `system_setting`.

What genuinely needs the table is Phase 10: §19.1's `DRIVER_NEARBY` at 1,500 m
and the geofence-*triggered* events, which fire on entering a named region that
belongs to no single booking. A table created now would have no writer, and
ADR 0028 has already recorded what this project thinks of those: a promise
rather than a record. `fx_rate` was left out for the same reason and the same
sentence applies here.

### 5. Dispatch is wired through `administration`, because the modules cannot see each other

`.importlinter` forbids `apps.transport → apps.booking`, and `deps-booking`
forbids the reverse. `subscribe()` is keyed by event *class*, so transport
cannot subscribe to a booking event without importing the module that defines
it.

So the wiring lives in `apps/administration/handlers.py`, which
`AdministrationConfig.ready()` already registers and which already fans payment
events out to notifications. `booking` publishes `TransferBookingConfirmed`
carrying the dispatch facts as primitives (§8.9); `administration` calls
`transport.services.open_assignment()`. Transport's own events travel back the
same way, to `booking`, to `notify`, and to the operations ticket of §11.8.

This is not a new pattern and not a new dependency. It is the existing
composition root doing the job a composition root exists for.

### 6. `support_ticket` is the minimum §11.8 needs

§6.4 assigns `support_ticket` to `administration`. 9a creates it at the size of
its only caller: `severity`, `kind`, `subject_type` and `subject_id`, `status`,
`opened_at`, `closed_at`, `note`. TC-090 asserts a HIGH ticket exists; nothing
in the MVP asserts a helpdesk, and §27 gives the console its own screens in
Phase 12.

## Consequences

A driver cannot hold two overlapping jobs, and that is true because PostgreSQL
refuses it — not because a service remembered to look. The same constraint
resolves TC-083's race for free: two simultaneous acceptances contend on the
same exclusion, one wins, the other becomes a 409.

Every offer carries the arithmetic that produced it, so §11.6's promise to
providers is a query rather than a reconstruction from logs that may have
rotated.

The cost is that `starts_at`/`ends_at` on an assignment mean something slightly
different from what their names suggest — they are an availability window, not
the service window — and a later reader could mistake them for the pickup and
drop-off times. The column comments say so, `booking_transfer.pickup_at`
remains the single source of the real time, and the services never compute one
from the other.

Leaving `geofence` out means Phase 10 creates a table that §6.4 assigned to a
module built in Phase 9. That is visible and intended. The alternative was an
empty table whose shape had been guessed a phase early.
