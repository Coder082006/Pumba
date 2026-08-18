# ADR 0006 — Appendix B is extended with this phase's thresholds

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 2 · **Issue:** Q5

## Context

Brief rule 5 and SRS NFR-M07: *"No business constant is hard-coded; all
thresholds, rates, weights and TTLs live in `system_setting`."* Appendix B is
described in the SRS as *"the practical expression of NFR-M07"* and lists 31
keys.

None of them belong to authentication. But the values exist elsewhere in the
document, stated as prose:

- **§30.2** — minimum 12 characters; lockout after 10 failed attempts in 15
  minutes, exponential to 1 hour; access token 15 minutes; refresh token 30
  days.
- **§9.6** — eight rate limits, given as a table with no keys.

So the values are specified and the register does not hold them. Implementing
either §30.2 or §9.6 without extending the register means hard-coding a
business constant, which rule 5 forbids outright.

## Decision

Register twenty new keys in `apps/common/config.py::SETTINGS_REGISTER`, taking
every default verbatim from the section that states it.

| Group | Keys | Source |
|---|---|---|
| Password policy | `auth.password.min_length`, `auth.password.breach_check_enabled` | §30.2, §9.4.1 |
| Lockout | `auth.lockout.{threshold,window_minutes,base_minutes,max_minutes}` | §30.2 |
| Sessions | `auth.{access_token_minutes,refresh_token_days,totp_drift_steps}` | §30.2 |
| One-time links | `auth.{email_verification_ttl_hours,password_reset_ttl_minutes}` | — |
| Rate limits | `ratelimit.*` — nine keys | §9.6 |

Two of these have no value anywhere in the SRS and are engineering defaults,
flagged as such rather than presented as specified: the email-verification and
password-reset link lifetimes, and the TOTP drift window.

Rate limits are stored as `"N/period/scope"` strings — one row per line of the
§9.6 table, keeping the register readable as the table it came from and
letting an administrator retune a single limit without a deployment.

## Consequences

**Every §30.2 and §9.6 value is administrator-tunable with no deployment**,
which is what NFR-M07 asks for and what makes the lockout policy adjustable
during an incident rather than after one.

**The domain layer never sees a default.** `LockoutPolicy` and
`validate_password` take their thresholds as parameters, so the register is
read once at the service boundary and the pure functions stay independent of
configuration entirely.

**Appendix B and the register have diverged**, deliberately and visibly. The
register is now the authority; Appendix B is a subset of it. If the SRS is
reissued, these twenty keys should be folded into the appendix.

**`auth.password.breach_check_enabled` is a kill switch, not a policy.** It
exists so a breach-service outage can be ridden out by an administrator rather
than by a deployment. The failure *policy* — closed on registration, open on
login — is code, not configuration, because it is a security decision and not
an operational dial.

## Related

- ADR 0003 — the settings read port that makes this reachable from `identity`.
- `docs/PHASE-2-PLAN.md` §0 Q5.
