# ADR 0005 — The authorisation vocabulary and mechanism live in `common`

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 2 · **Issue:** S9

## Context

SRS §30.3 requires two checks on **every** request in **every** module:

```
ROLE CHECK      does this principal's role permit this operation class?
OWNERSHIP CHECK does this principal own this specific row?
                (queryset filtered by principal at the repository layer —
                 never "fetch then compare", which leaks existence)
```

So all fourteen modules need `Principal`, `Role`, `Permission`, the ownership
rule table and the filter mechanism.

But §6.4 gives `identity` exactly one dependant. `provider` depends on it;
nothing else does. `catalogue` depends on `location` alone, `inventory` on
`catalogue` alone, `trip` on `catalogue` and `transport`. Under the §6.5
contracts, `apps.catalogue.views` importing `apps.identity.domain.principal`
breaks both `deps-catalogue` and `private-identity`.

`apps.common` cannot import `identity` either — `common-is-a-leaf` forbids it,
and rightly so: a shared kernel that reaches into a business module is a back
door through which any module reaches any other.

This was discovered while writing `apps/common/scoping.py`, with the rule table
already built inside `apps/identity/domain/`. It is the same family of problem
as **S1** (`system_setting` owned by `administration`, read by everyone) and
**S2** (idempotency spanning three modules with no owner).

## Decision

Resolve it the same way S1 was resolved — separate the **mechanism** from the
**ownership**:

| Lives in | What |
|---|---|
| `apps/common/authz/roles.py` | `Role`, `Permission`, `ROLE_PERMISSIONS`, `mfa_mandatory` |
| `apps/common/authz/principal.py` | `Principal` |
| `apps/common/authz/ownership.py` | `Resource`, `Scope`, `OwnershipRule`, `OWNERSHIP`, `Filter`, `ownership_filter` |
| `apps/common/scoping.py` | the one place a `Filter` becomes a Django `Q` |
| `apps/identity` | the `role` and `user_role` **tables**, principal **construction** (`get_principal()` in the §6.4 interface), authentication |

`identity` keeps everything §6.4 assigns it. What moved is the shared
vocabulary and the decision function — neither of which touches a table.

### The purity guarantee moves with it

`apps.common.authz` is a domain layer that happens not to sit under a
`domain/` path, so it is held to the domain rules explicitly rather than by
naming convention:

- added to the `domain-layer-is-pure` import-linter contract — no Django, no
  DRF, no Celery, no Redis, no HTTP client, no `config`;
- added to the mypy `strict` + `disallow_any_explicit` override;
- added to the CI 95% coverage gate alongside `apps/*/domain/*`.

`apps/common/scoping.py` is deliberately outside that set: it imports
`django.db.models` because translating to a `Q` is precisely its job. It is the
only module in the codebase that builds an authorisation query.

## Consequences

**The 404-not-403 property becomes structural.** `DENY_ALL` maps to
`queryset.none()`, so `get_object()` raises `Http404` on its own. There is no
code path where a foreign principal reaches a row and is then rejected, and no
per-module implementation that could get it wrong.

**A module still cannot read another module's rows.** `common.authz` exposes no
table and no query — it answers "which rows may this principal reach", and the
module's own repository applies the answer to its own queryset.

**`Role` is now shared vocabulary.** A new role is a change to the shared
kernel and to every module's authorisation matrix at once. That is the correct
blast radius: a new role that only some modules know about is a hole.

**The rule table grows per phase, not up front.** `Resource` lists only
resources whose models exist — declaring `BOOKING` before `booking` has models
would be future-phase scaffolding. The totality test over `Role × Resource` is
what makes adding one later safe: the build fails until every role's access to
the new resource is stated.

## Alternatives rejected

**Duplicate the primitives per module.** Fourteen implementations of the
control that OWASP ranks #1 for APIs. The §30.3 wording — "the ownership check
*is* the control" — does not survive being reimplemented fourteen times.

**Widen `deps-*` so every module may import `identity`.** Makes `identity` a
universal dependency, contradicting §6.4 and destroying the extractable seams
of §6.2 and §44.2 for the sake of one import.

**Pass an untyped principal (a dict, or `frozenset[str]` roles).** Keeps the
graph clean and throws away every guarantee that makes the rule table
reviewable. The totality test cannot be written against `str`.

## Related

- ADR 0003 — the settings read port in `common` (issue S1), the precedent.
- `apps/api/.importlinter` — the purity contract, extended here.
- `docs/PHASE-2-PLAN.md` §1 — the four ownership semantics this protects.
