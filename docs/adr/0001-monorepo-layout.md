# ADR 0001 — Monorepo layout deviates from SRS §36

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 1

## Context

SRS §36.1 specifies a tree rooted at `backend/`, `web/`, `mobile/`,
`contracts/`, `infrastructure/`, `database/`, `documentation/` and `tests/`.
The kickoff brief specifies `apps/api`, `apps/web-tourist`, `apps/web-console`,
`packages/contracts`, `packages/ui` and `packages/config`, while also
instructing us to "scaffold the monorepo exactly as specified in SRS §36".

These cannot both be satisfied. Two further points bear on the choice:

- §36 shows `web/provider-portal/` and `web/admin-console/` as separate
  applications, but §34.5 mandates "one code base with two role-scoped route
  trees". **The SRS contradicts itself here**; the brief agrees with §34.5.
- §36 predates the decision to build a tourist website at all, so it has no
  place for one.

## Decision

Follow the brief's `apps/` + `packages/` layout. Retain §36's `infrastructure/`,
`database/` and `docs/` as top-level directories, because they hold assets that
are not pnpm workspaces.

Mapping:

| SRS §36 | This repository |
|---|---|
| `backend/` | `apps/api/` |
| `web/provider-portal/` + `web/admin-console/` | `apps/web-console/` (one app, two route trees) |
| — | `apps/web-tourist/` (new; see ADR 0002) |
| `web/shared-ui/` | `packages/ui/` |
| `contracts/` | `packages/contracts/` |
| `mobile/` | deferred |
| `infrastructure/`, `database/`, `documentation/` | `infrastructure/`, `database/`, `docs/` |

The internal structure of `apps/api` follows §36 exactly: `config/`, `apps/<module>/`
with the full layer file set, `common/`, `scripts/`, `tests/`.

## Consequences

`apps/`+`packages/` is the pnpm/Turborepo convention, so workspace globbing,
`--filter` and remote caching work without special configuration. Anyone
reading §36 alongside the repository needs this table; it is referenced from
`README.md`. The §36/§34.5 contradiction should be corrected in the next SRS
revision.
