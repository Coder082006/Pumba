# ADR 0027 — The payment schema the SRS names but never specifies

**Status:** Accepted · **Date:** 2026-09-15 · **Phase:** 8a

## Context

§7.5.13 gives `payment` a column list. Three of the four tables §6.4 puts in
this module — `payment_transaction`, `refund`, `payment_webhook_event` — have
**no column specification anywhere in the document**. They are named in §6.4,
related in §7.4 (R28, R29), exercised in §9.4.8 and §21, traced in §42, and
never defined. This is the same gap ADR 0007 and ADR 0025 record for earlier
phases, and it is settled the same way: design against §7.2's conventions and
write down what was chosen and why.

Even the table that *is* specified is short of what §21 requires of it. §7.5.13
has no `created_at`/`updated_at`, though §7.7 applies the `updated_at` trigger
generally; no `expires_at`, though §21.4 has an `EXPIRED` state and §17.2 gives
the payment window 30 minutes; no CHECK enumerating the ten §21.4 states,
though §7.7 claims "enum domains | Invalid states impossible at storage layer";
and its own prose (§7.5.13, immediately after the table) names a column the
table omits — "Only the PSP token, last four digits and card brand may be
stored, in `payment_instrument_summary` JSONB".

BR-062 — "a trip may hold at most one non-terminal payment at a time" — has no
constraint or index in §7.6, while §7.4 R27 says `trip : payment` is `1 : 0..*`
because "retries create new payment rows". Both are true at once only if the
uniqueness is conditional, which the SRS never states.

And §21.6 specifies refunds in prose — initiated by the Platform, always
against an original payment, full or partial, carrying a `booking_id` so the
ledger can reverse the right accrual — while giving the `refund` row **no
lifecycle at all**. A refund is asynchronous at the PSP: it is requested, and
it settles later, or fails. §33.9 requires a test for "refund exceeding
capture". None of those three moments is representable in a table with no
status.

## Decision

### 1. `payment` gains the five columns §21 needs and §7.5.13 omits

`created_at`, `updated_at` (trigger-maintained, as everywhere else),
`expires_at` (§17.2's payment window, so an `EXPIRED` transition has something
to read), `payment_instrument_summary JSONB` (the column §7.5.13's own prose
names — token, brand, last four, and nothing else, ever), and a CHECK
constraint enumerating §21.4's ten states. `public_id`, `version` and the
§7.2 conventions apply as they do to every other table.

**No column here may hold a PAN, a CVV, an expiry date or a full cardholder
name.** PM1 puts the platform in SAQ A scope and `ports/payment.py` already
refuses to carry card data; the schema refuses to store it.

### 2. BR-062 is a partial unique index, not a unique constraint

```sql
CREATE UNIQUE INDEX uniq_payment_live_per_trip ON payment (trip_id)
WHERE status NOT IN ('CAPTURED','PARTIALLY_REFUNDED','REFUNDED',
                     'FAILED','EXPIRED','CHARGEBACK_LOST');
```

This is the only reading that satisfies BR-062 and R27 together: a declined
card leaves a terminal `FAILED` row and the tourist may try again, and two
live intents on one trip are impossible at the storage layer rather than by
convention. `DISPUTED` is deliberately **not** terminal here — a disputed
payment is still the trip's live payment.

### 3. `payment_transaction` is append-only, one row per state change

PM6: "Every payment state change writes an immutable `payment_transaction`
row." Columns: `payment_id`, `from_status`, `to_status`, `psp_reference`,
`amount`/`currency`, `failure_code` (§21.8's normalised taxonomy),
`raw_reference` to the webhook event that caused it where there was one,
`occurred_at`, and the actor — `SYSTEM` for a webhook or poll, a user id for
an administrative action.

Append-only is enforced by a trigger that refuses UPDATE and DELETE, the way
`booking_status_history` already is. It is a trigger rather than a convention
because §7.7 lists "ledger immutability" as a database-level mechanism and a
payment history nobody can edit is worth exactly as much as a ledger nobody
can edit.

### 4. `payment_webhook_event` stores the payload before anything reads it

`psp_name`, `psp_event_id` (**UNIQUE** — §21.5's deduplication key),
`event_type`, `payload` (JSONB, the raw body as received), `signature_verified`,
`received_at`, `processed_at` (null until applied), `payment_id` (nullable —
an event may arrive for a reference we do not know), and `outcome`
(`APPLIED | DUPLICATE | IGNORED_NOT_ADVANCING | UNMATCHED`).

The row is written **before** the transition is attempted (§21.5: "The raw
payload is persisted before any processing, so a processing bug never loses an
event"), and the unique `psp_event_id` is what makes TC-071 — a replayed
webhook — a 200 with no second effect.

`outcome` exists because §21.5's ordering rule produces a legitimate
non-action: "the handler applies a state transition only if it is legal and
advances the state, otherwise it records and ignores". A row that records
nothing about why it did nothing would make an out-of-order event
indistinguishable from a bug.

### 5. `refund` gets the lifecycle the SRS does not give it

`REQUESTED → SETTLED` or `REQUESTED → FAILED`. Nothing else; there is no
cancellation of a refund, because the money is either back or it is not.

Columns: `payment_id`, `booking_id` (nullable only for a trip-level
discretionary refund; §21.6 requires it for a partial so the ledger can reverse
the correct accrual), `amount`/`currency`, `reason_code`, `requested_by`
(nullable — `NULL` means the platform, automatically, under a cancellation
policy), `approved_by` (BR-047's FINANCE_OFFICER above
`refund.auto_approve_limit`), `psp_refund_reference`, `failure_code`,
`requested_at`, `settled_at`.

**A refund is a row before it is an API call.** §8.9's bus dispatches after
commit and swallows a handler's failure so one consumer cannot roll back its
publisher — which is right, and which means a refund issued *inside* a handler
would be lost silently if the PSP call raised. There is no sweeper behind
refunds the way §17.5's sweeper stands behind holds. So the handler subscribing
to `BookingCancelled` writes a `REQUESTED` row and does nothing else, and a
retried Celery task settles it. BR-043 is preserved by construction: the amount
on the row is the amount the event carried, which is the amount the preview
showed.

### 6. S3 is resolved: `payment → {booking, trip, inventory}` stands

`.importlinter` has carried "Revisit at Phase 8 — issue S3" since Phase 1.
Revisited: §6.4's "booking (via events)" is not implementable as written.
§9.4.7 asserts `trip.status = PENDING_PAYMENT` and computes the charge from
`trip.total_amount` synchronously, and §9.4.8 runs §20.8's confirmation routine
— which commits holds and confirms bookings — inside the webhook transaction.
Events cannot express either without inventing an asynchronous confirmation the
SRS does not describe and §20.4's invariant would not survive.

The contract keeps its three modules and its comment becomes the decision
rather than a deferral. The dependency remains one-way: `booking` still may not
import `payment`, so the confirmation routine is *called by* the webhook and
knows nothing about who called it.

### 7. The port becomes reachable, and never by accident

`payment` has had no entry in `ports_registry._FAKES` and no accessor, and
`test_ports_registry.py` asserts both — "A fake gateway that reports success is
the most dangerous double in the system". That protection is kept and made
precise rather than removed: `get_payment_port()` exists, and resolves
**only** what `PORT_ADAPTERS["payment"]` names. There is no fallback. A
deployment that forgets to configure a gateway raises at first use; it does not
quietly start taking imaginary money.

The fake remains reachable exactly one way: by naming it, which `ci.py` does
and no production settings module does. The registry test's exact-set
assertions move `payment` from "has no accessor" to "has no default", which is
the invariant that actually mattered.

## Consequences

A payment's whole history is reconstructible from rows that cannot be edited:
what the PSP said (`payment_webhook_event`), what we did about it
(`payment_transaction`), and what it cost (`refund`). §21.9's reconciliation in
8b joins the PSP settlement report to those rows by `psp_reference`.

A refund survives a crash, a PSP outage and a bad deploy, because it is a row
with a status and a task that retries. The cost is that "refunded" is two
states rather than one, and every screen that reports a refund must say which:
requested is not settled, and telling a tourist their money is back before it
is would be worse than telling them it is on its way.

The `DISPUTED` and `CHARGEBACK_LOST` states exist in the machine (§21.4) and
are unreachable in 8a: no chargeback ingestion is built, because §22.6 defers
allocation to the provider agreement, which is Appendix D-5 and Phase 11. They
are declared rather than omitted so the machine matches §21.4 and the gap is
visible instead of implied.

§20.4's linkage table — the invariant that a confirmed booking must have a
captured payment — has no rule for `DISPUTED` or `CHARGEBACK_LOST`. 8b's
nightly consistency job inherits that gap and will need one; it is recorded
here so it is not discovered by a P2 alert firing on a state nobody wrote a
rule for.
