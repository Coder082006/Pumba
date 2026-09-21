# ADR 0028 — A ledger that can express a double entry

**Status:** Accepted · **Date:** 2026-09-21 · **Phase:** 8b

## Context

§22.3 is unambiguous: "The ledger is **double-entry** and append-only. Every
booking generates a balanced set of entries." §7.5.14 then specifies
`ledger_entry` — and the table it specifies cannot represent a double entry.

Three things are missing, and each of them matters:

1. **There is no `account` column.** §22.3's own table has an Account column
   with five values — Platform clearing, Provider payable, Platform revenue,
   Platform expense, Platform FX — and none of them reaches the schema. The
   account would therefore be derivable only by looking up `entry_type` in
   application code, which makes the chart of accounts a dictionary in a Python
   file rather than data. §21.9 makes that concrete: an amount mismatch is
   "posted to a dedicated ledger account", and there would be no column to post
   it to.

2. **There is no journal id.** Every row is a single leg. Nothing groups the
   two or more legs of one economic event, so "debits equal credits" can only
   ever be asserted in aggregate per `(booking_id, currency)` — which is exactly
   what BR-064 settles for, and exactly why a single unbalanced posting would be
   invisible until the nightly sweep found a booking that no longer added up,
   with no way to say which posting broke it.

3. **Every foreign key is nullable**, `booking_id` included, while BR-064
   reconciles per booking. A `PSP_FEE` or `FX_DIFFERENCE` row carrying no
   booking falls outside the invariant entirely — it is not that such rows
   break the check, it is that the check cannot see them.

Four more tables §6.4 assigns to this module have **no specification at all**:
`commission_rule` (referenced by `provider.commission_rule_id` and resolved in
§22.2), `provider_balance` (named in BR-064 as holding `available_amount` and
`pending_amount`), `payout` and `payout_item`. This is the same gap ADR 0007,
ADR 0025 and ADR 0027 record for earlier phases, and it is settled the same way.

## Decision

### 1. `ledger_entry` gains `account` and `journal_id`

`account` is one of §22.3's five, stored rather than inferred. `journal_id` is
a UUID shared by every leg of one economic event — a capture, an accrual, a
reversal, a payout.

Everything else in §7.5.14 stands as written: `entry_type`, `direction`,
`amount` always positive, `currency`, the four nullable references,
`reversal_of_id`, `memo`, `occurred_at`.

With those two columns the invariant BR-064 asks for becomes checkable at three
levels instead of one: per journal (immediately, before the rows are written),
per booking and currency (nightly, as §22.3 specifies), and per account (which
is what a finance report is).

### 2. One writer, and it refuses an unbalanced journal

`finance.services.post_journal(entries)` is the only function that inserts into
`ledger_entry`. It refuses a set whose debits and credits do not match, refuses
a zero-amount leg, and assigns the `journal_id`.

This is the whole reason the column is worth having. A ledger where any caller
may write one row is a ledger that goes out of balance at 4pm on a Friday and
is discovered on Sunday; a ledger with one door that checks before it opens
cannot.

### 3. Immutability is the database's, not the application's

§7.5.14: "UPDATE and DELETE are revoked at the database role level."
A trigger refusing both, as `booking_status_history`, `payment_transaction` and
`payment_webhook_event` already have. A correction is a new entry naming
`reversal_of_id` (BR-077).

### 4. The four unspecified tables

- **`commission_rule`** — §22.2's resolution needs `scope`
  (LISTING/PROVIDER/TYPE/GLOBAL), the id it matches on, `priority`, `method`
  (PERCENT/FLAT/TIERED), `percent`, `flat_amount`, `tiers` (JSONB, for TIERED's
  monthly bands), `min_fee`, `max_fee`, `currency`, `valid_from`, `valid_to`,
  `is_active`. Resolution takes the highest-priority active rule in scope order.
- **`provider_balance`** — one row per `(provider_id, currency)`, with
  `pending_amount` and `available_amount`, which BR-064 names, plus
  `available_from` so the hold in §22.4 is a column rather than a calculation
  repeated in three places.
- **`payout`** — `provider_id`, `currency`, `amount`, `status`, `period_start`,
  `period_end`, `approved_by_user_id`, `approved_at`, `rail_reference`,
  `paid_at`, `failure_reason`.
- **`payout_item`** — `payout_id`, `booking_id`, `amount`, `memo`: §22.5's
  "line items referencing each booking", which is what makes a provider
  statement auditable back to a service.

### 5. `fx_rate` is deliberately **not** created

§6.4 assigns it to this module and §18.4 describes hourly rates with a markup.
But ADR 0024 settled that a trip is priced, locked and charged in the
destination's own currency, and the display conversion is indicative only and
explicitly not promotable. Nothing on the paying path converts anything.

A table with no writer is a promise, not a record. It arrives with presentment
currency, which is a scope decision and not a schema one.

### 6. A payout is released, never sent

§22.5 says release "calls the payout rail (bank transfer or mobile money) via
an adapter", and the SRS never writes that port's Protocol. More to the point,
the rail moves real money to real companies under terms that do not exist yet:
Appendix D-5 puts the provider agreement — commission bands, chargeback
allocation, cancellation compensation — in Phase 11.

So `PayoutRailPort` is declared and **has no adapter and no default**, exactly
as `payment` did in 8a (ADR 0027 decision 7). Releasing a payout writes the
`PAYOUT_SETTLEMENT` entry and records a **manual reference** — the transfer an
operator made at their bank, typed in afterwards. That is what actually happens
in a business's first year, and it is honest in a way that a "paid" flag set by
software that moved nothing would not be.

### 7. Accrual reads the booking's snapshot, never the rule

BR-070 freezes the commission rule and rate on the booking at confirmation, and
Phase 7 already writes `commission_rate`, `commission_amount` and `net_amount`
there. `finance.accrue` reads those three columns.

This is most of TC-110 by construction: changing a rule to 20% cannot alter a
booking confirmed at 15%, because the accrual never consults a rule at all.
§22.2's resolution runs exactly once, at basket creation, where Phase 7 already
put it.

## Consequences

The financial position of the platform becomes a query rather than a
reconstruction: what a provider is owed is `provider_balance`, what Pumba
earned is the sum of a revenue account, and both are derived from entries that
nobody can edit. §22.7's requirement that reports come "from the ledger, never
from the booking table" is satisfiable because the ledger holds the account
each figure belongs to.

The cost is a discipline: every new economic event has to be expressed as a
balanced journal, and a phase that invents one leg without its counterpart will
fail at `post_journal` rather than at a reviewer's judgement. That is the
intended trade.

The `account` column duplicates information derivable from `entry_type`, and
they could drift. They cannot drift silently: the mapping lives in
`finance.domain.accounts`, `post_journal` stamps the account from it, and
nothing else may write a row.

`DISPUTED` and `CHARGEBACK_LOST` remain unreachable (ADR 0027), so `CHARGEBACK`
is a declared entry type with no producer. It stays declared for the same
reason those states do: §22.3 lists it, and a gap that is visible is a gap
somebody can close.
