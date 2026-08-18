# ADR 0007 — Schemas for the identity tables the SRS never specifies

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 2 · **Issue:** Q4

## Context

SRS §6.4 lists six tables `identity` owns: `user`, `role`, `user_role`,
`tourist_profile`, `session`, `device`.

§7.5 specifies two of them. §7.5.1 gives `user` column by column and §7.5.2
gives `tourist_profile`. Then §7.5.3 moves on to `provider`.

- **`role` and `user_role`** appear only as boxes in the §7.3 ERD diagram:
  `role(id, code, name)` and `user_role(role_id, user_id, granted_at)`. No
  types, no constraints, no delete rules.
- **`session` and `device`** appear nowhere at all — not in §7.5, not in the
  ERD, not in the indexing strategy of §7.6. They are named in §6.4 and in
  §37.2's deliverables ("device registration") and never described.

A seventh table is needed and named nowhere: email verification and password
reset both require a one-time token store. §9.4.1 sends a verification email
and §24.5 completes a reset from a deep link, so the rows have to exist
somewhere.

## Decision

Design all five against the §7.2 conventions, and record the reasoning per
table so the invention is visible rather than buried in a migration.

### `role`, `user_role`

Straight from the ERD, plus the §7.2 timestamps. Two decisions the ERD does
not make:

- **`user_role.granted_by` is `SET_NULL`, not `CASCADE`.** Removing an
  administrator must not delete the record of what they granted. It is
  nullable because registration and the seed loader grant `TOURIST` with no
  human actor.
- **`role` is `PROTECT`.** A role still granted to somebody cannot be deleted
  out from under them.

`role.code` is the shared vocabulary of `apps.common.authz.Role` (ADR 0005),
and a test asserts the seeded rows and the enum match exactly — a role in one
and not the other either grants nothing at runtime or cannot be checked at
all.

### `session`

**One row per issued refresh token, not one per login.** Rotation appends a
row and stamps `superseded_by` on its predecessor, so a family is an
append-only chain and the reuse detection of §30.2 is a lookup rather than an
inference. Columns: `jti`, `family_id`, `user`, `issued_at`, `expires_at`,
`revoked_at`, `superseded_by`, `ip`, `user_agent`.

Not a `BaseModel`. `jti` is already the external identifier, it travels inside
a signed token rather than in a URL, and a second UUID would only raise the
question of which one addresses the row.

`ip` and `user_agent` are there because §30.2 requires alerting the user on a
reuse detection, and an alert that cannot say *where from* is alarming rather
than actionable.

### `device`

Columns: `user`, `platform`, `push_token`, `device_name`, `app_version`,
`last_seen_at`, `revoked_at`. §25.3 registers the token "at login and on token
rotation", so writes are frequent and idempotent by token.

**A live push token is unique across all users.** A token identifies a
physical handset; two live rows for one token would deliver one user's
itinerary to another user's phone after a device changes hands. The constraint
is partial on `revoked_at IS NULL` so the history survives.

### `one_time_token`

Columns: `user`, `purpose`, `token_hash`, `expires_at`, `consumed_at`.

**Only the SHA-256 is stored.** The plaintext exists in the email and nowhere
else, for the same reason a password is not stored: a database disclosure must
not hand over the ability to take over every account with a pending
verification or reset.

Single-use is `consumed_at`, not deletion, so a replayed link can be
distinguished from an expired one — §24.5 requires the expired case to offer a
restart.

## Consequences

**These five tables are ours, not the SRS's.** If the document is reissued
with its own definitions, they must be reconciled deliberately rather than
assumed compatible.

**`session` grows one row per refresh, not per login.** A 30-day refresh
rotated every 15 minutes is ~2,900 rows per active session. A sweeper for
expired and superseded rows is required before launch; it is not Phase 2 work
but it is not optional either, and there is a partial index on `expires_at`
for live rows to make it cheap.

**Neither `session` nor `device` is readable by any investigative role.**
`apps/common/authz/ownership.py` maps both to `NONE` for `SUPPORT_AGENT` and
`COMPLIANCE_ADMIN`: a session row is a live credential handle and a push token
is a delivery address for private content.

## Related

- ADR 0005 — where `Role` lives and why.
- `docs/PHASE-2-PLAN.md` §0 Q4.
