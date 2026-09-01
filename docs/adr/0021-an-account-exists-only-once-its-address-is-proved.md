# ADR 0021 — An account exists only once its address is proved

**Status:** Accepted · **Date:** 2026-09-01 · **Phase:** 4

## Context

§28.2.1's SD-01 sequences registration like this:

```
  |        |--POST /auth/register->|             |
  |        |          |--check email unique----->|
  |        |          |--INSERT user(PENDING)--->|
  |        |          |--INSERT tourist_profile->|
  |        |          |--INSERT audit_log------->|
  |        |          |⇢ queue verification email|
  |        |<--201 {user, verification_required}-|
```

The row is written first and the address is proved afterwards. §7.5.1's
`UserStatus.PENDING` exists to name the gap between the two.

**What that leaves behind.** Every abandoned signup, every mistyped address,
every bot that finds the form becomes a permanent row in `user` — with a
profile, an audit entry and a password hash — for an account nobody has proved
they own and, in most of those cases, nobody will ever use. The local database
had nine users after a few days of manual testing; two were real.

The rows are not merely untidy. A `user` table where most addresses are
unproved is one that cannot be trusted for anything that counts accounts, and
an address somebody typed by mistake — someone else's address — sits there
having never been consented to by its owner, which is a Personal Data
Protection Act question and not only an engineering one.

## Decision

**Nothing about an unverified registration is written to the database.**

`POST /auth/register` validates the password against §30.2 and the breach
corpus, hashes it with Argon2id, and stores the details in Redis under a TTL —
`apps/identity/pending.py`. It answers **202 Accepted with no user object**,
because there is no account to describe.

The `user`, `tourist_profile`, role and audit rows are created in one
transaction at the moment the emailed code or link is proved, in
`services._promote`. The account is created **verified**: it has just been
verified, and a row inserted PENDING and corrected a line later would describe
a state nobody was ever in.

### Why a cache and not a staging table

A staging table needs a sweeper, and a sweeper that fails leaves exactly the
rows this decision exists to prevent. Redis forgets on its own: an abandoned
registration is gone when the code expires, with nothing scheduled and nothing
to go wrong.

The cost is that a Redis restart loses in-flight registrations. The person
registers again — the same thing they would do if the email had not arrived —
and that is a better failure than a durable row for an account that may never
exist.

### What changes for a caller

| | Before | After |
|---|---|---|
| `POST /auth/register` | `201 {user, verification_required}` | `202 {email, verification_required}` |
| Duplicate address | 409 against a PENDING row | 409 against a **verified account** only |
| Second attempt, same address | refused | **allowed**, superseding the first code |
| `UserStatus.PENDING` | the state after registering | unreachable by registration |

The second-attempt change is deliberate. Somebody who mistypes their address,
or whose first email never arrives, registers again; refusing would lock them
out of their own address for the length of the TTL over a mistake they are
trying to correct — and there is no account to protect, because none exists.

## Consequences

**§28.2.1's SD-01 should be amended**, and this is recorded rather than done
silently for the reason ADR 0020 gives: a specification the code has quietly
diverged from is worse than one that is out of date, because the next reader
trusts it.

**Non-enumeration gets stronger for free.** An address that was registered but
never verified is now indistinguishable from one nobody has ever typed —
there is no row either way. §30.3 asked for that at the endpoint; it is now
true of the data.

**The audit trail moves rather than disappears.** §41.13's `user.register`
entry is written at promotion, alongside `user.email_verified`, against the
account that actually exists. An entry naming an account that was never
created could not carry an entity id and would describe nothing.

**`EMAIL_NOT_VERIFIED` at login is now defensive rather than routine.**
Registration can no longer produce an unverified account. The check stays,
because an administrator or an import could, and
`test_services.py::TestTc013NonEnumeration` builds one through the repository
so the path is exercised rather than assumed unreachable.

**Tests that needed a user stopped getting one.** `signed_in_as`,
`verified_user` and three other helpers registered and then marked the account
verified; they now call `repositories.create_tourist` directly. That is the
right shape regardless — those suites are about authorisation and trips, and
routing them through the verification flow made every one of them depend on it.

**A live credential no longer sits in the database at rest.** The password hash
waits in Redis for minutes instead of in Postgres indefinitely. Argon2id is
applied before anything is stored, so the exposure is no worse than the
password column — but it is also much shorter-lived.
