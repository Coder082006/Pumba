# Tourism Journey Orchestration Platform

A mobile-first tourism planning, reservation and travel-management platform.
A tourist plans and pays for their entire journey — airport transfer,
accommodation, activities, inter-town transport — before leaving their home
country, and receives one confirmed, day-by-day itinerary. Supply side is
verified local providers: drivers, hotels, activity operators.

The architectural centre of the system is the itinerary orchestration layer:
**`Trip → Itinerary → ItineraryItem → Booking`**. Everything else serves it.

> **Specification.** `docs/srs/SRS-ZTJOP-001.pdf` is the authoritative source
> for scope, data model, API design, state machines and business rules. Read it
> before changing anything. Where this README abbreviates, the SRS governs.
>
> **Current phase.** Phase 1 (Foundation) is complete. There is no feature code
> yet — see `docs/IMPLEMENTATION-PLAN.md` for the phase plan and the open
> decisions.

---

## Quick start

**Prerequisites:** Docker Desktop, Node 20, [pnpm](https://pnpm.io) 9,
[uv](https://docs.astral.sh/uv/). Python 3.12 is installed by `uv` — you do not
need it on your PATH.

```bash
git clone <repo> && cd Pumba
cp .env.example .env
make dev          # or: pnpm dev   (Windows without GNU make)
```

That builds and starts everything:

| Service | URL | Notes |
|---|---|---|
| API | http://localhost:8000/api/v1/health | Django + DRF under ASGI |
| API docs | http://localhost:8000/api/docs/ | Swagger UI over the generated schema |
| Tourist site | http://localhost:3000 | Next.js 15 |
| Console | http://localhost:5173 | Vite — provider and admin route trees |
| PostgreSQL | localhost:5432 | 16 + PostGIS 3.4 |
| Redis | localhost:6379 | cache, locks, Celery broker, Channels layer |

Celery worker and beat run alongside; they have no ports.

To work without containers, install dependencies with `make install` and point
`POSTGRES_HOST` and `REDIS_URL` at your own services.

---

## Commands

Every `make` target has a `pnpm` equivalent, because GNU make is not installed
on Windows by default.

| make | pnpm | Does |
|---|---|---|
| `make dev` | `pnpm dev` | Start the whole stack |
| `make down` | `pnpm down` | Stop it |
| `make reset` | `pnpm reset` | Stop it and **drop the local database** |
| `make check` | `pnpm check` | Everything CI runs |
| `make lint` | `pnpm lint` | ruff + eslint |
| `make typecheck` | `pnpm typecheck` | mypy + tsc |
| `make boundaries` | `pnpm boundaries` | Module dependency contracts |
| `make test` | `pnpm test` | Backend and frontend suites |
| `make coverage` | `pnpm coverage` | Tests with both coverage gates |
| `make contracts` | `pnpm contracts` | Regenerate OpenAPI **and** the TS types |
| `make format` | `pnpm format` | Apply formatting |

`make check` runs exactly what CI runs, so a CI failure reproduces locally.

---

## Layout

```
apps/
  api/              Django 5.1 · Python 3.12 · the only backend
    config/         settings (base/dev/ci/staging/prod), asgi, wsgi, celery
    apps/
      common/       shared kernel: base model, Money, errors, events,
                    state machine, settings port, pagination
      <module>/     the 14 modules of SRS section 6.4
    ports/          external integration protocols + fakes
    tests/          cross-module and architecture tests
  web-tourist/      Next.js 15 · public tourist site
  web-console/      Vite 5 · provider portal + admin console, two route trees
packages/
  contracts/        OpenAPI spec (generated from Django) + generated TS types
  ui/               shared components, Tailwind preset, design tokens
  config/           shared tsconfig / eslint
infrastructure/
  docker/           Dockerfiles
  terraform/        skeleton only — later phase
docs/
  srs/              the specification
  adr/              architecture decision records
  IMPLEMENTATION-PLAN.md
```

This differs from SRS section 36 — see
[ADR 0001](docs/adr/0001-monorepo-layout.md) for the mapping and the reason.

---

## Architecture

### Modular monolith

One deployable Django application, fourteen modules with **mechanically
enforced** boundaries (SRS sections 6.2, 6.5). The dependency graph is a DAG,
read strictly and non-transitively:

```
L0  identity · location · notify        (no intra-platform dependencies)
L1  catalogue -> location               provider -> identity
L2  inventory -> catalogue              transport -> location, provider
L3  trip      -> catalogue, transport
L4  booking   -> inventory, trip, provider
L5  payment · messaging · review        -> booking
L6  finance   -> payment, booking
L7  administration                      -> all (read via interfaces)
```

`inventory` depends on `catalogue`, and `catalogue` depends on `location`, but
**`inventory` may not import `location`** — it goes through the `catalogue`
service interface. That non-transitivity is what keeps the seams extractable
later (SRS section 44.2).

31 import-linter contracts encode this in `apps/api/.importlinter`, and
`apps/api/tests/test_architecture.py` proves they actually fail CI by
introducing real violations and asserting the linter catches them.

### Layers

Every module has the same five layers (SRS section 8.2):

```
1. interface       views · serializers · permissions · urls
2. application     services.py          <- the only cross-module entry point
3. domain          domain/*.py          <- pure; no ORM, no I/O; 95% coverage
4. data access     models · repositories · selectors
5. infrastructure  adapters/ · tasks.py <- the only place a vendor SDK appears
```

Pricing, policy evaluation, validation rules and state machine guards live in
the domain layer as pure functions. That is the audit-sensitive logic, and
keeping it free of I/O is what makes it testable without a database and
reviewable without a debugger.

### Non-negotiables

1. **No AI/ML anywhere.** Every ranking, matching, dispatch and pricing
   decision is deterministic and rule-driven (SRS section 3.5). Do not add an
   ML dependency to any manifest.
2. **No destination-specific code.** Never branch on the destination name.
   Destinations, corridors, tariffs and policies are database rows
   (SRS section 4.2).
3. **No hard-coded business constants.** Every threshold, rate, weight and TTL
   is a `system_setting` row. Read them through
   `apps.common.config.get_setting` — the full register is SRS Appendix B.
4. **All money is `Decimal`** with an explicit currency, `ROUND_HALF_UP`, at
   the currency's own minor unit. Use `apps.common.money.Money`; it refuses
   floats and refuses cross-currency arithmetic.
5. **All timestamps `TIMESTAMPTZ`, stored UTC**, rendered in the destination's
   timezone.
6. **External IDs are UUIDs.** `public_id`, never the sequential integer.
7. **Idempotency keys** on every mutating POST that creates a booking, payment
   or assignment.
8. **The ledger is append-only.** Corrections are new reversing entries.

### The API is client-agnostic

The backend serves the tourist site, the console and — later — the Flutter
tourist and driver apps. No web-specific or Next.js-specific concern may leak
into it. Adding Flutter must require zero backend change.

---

## Changing the API

The OpenAPI specification is generated from the Django source and
**committed**, so that a breaking change is visible as a diff in the pull
request (SRS section 36.2).

```bash
make contracts    # regenerates openapi.yaml AND packages/contracts/src/schema.d.ts
```

CI regenerates both and fails if either differs from what you committed. Never
hand-edit `openapi/openapi.yaml` or `src/schema.d.ts`.

---

## Testing

```bash
make test        # everything
make coverage    # with the SRS section 35.3 gates
```

Coverage gates: **80% overall, 95% on the domain layer.** The domain gate is
higher because that code decides prices and refunds, and disputes are resolved
against it.

Tests needing PostgreSQL and Redis are marked `@pytest.mark.integration` and
are **skipped automatically when Docker is unavailable**, so the pure-domain
suite runs everywhere. Run them with Docker up.

---

## Conventions

- Trunk-based development, short-lived branches, protected `main`.
- [Conventional commits](https://www.conventionalcommits.org/).
- Commit one logical change at a time.
- Cite the SRS section when implementing a rule from it. The codebase does this
  throughout — it is how a reviewer checks the code against the spec.

---

## Known gaps

Honest list, current as of the end of Phase 1.

| Gap | Detail |
|---|---|
| **Compose stack unverified** | Written but never executed — Docker is not installed on the machine Phase 1 was built on. YAML structure is validated; the stack has not been brought up. |
| **GeoDjango not enabled** | PostGIS and GDAL are provisioned; `django.contrib.gis` waits for Phase 3's first geometry column. [ADR 0004](docs/adr/0004-geodjango-deferred-to-phase-3.md). |
| **Tourist website is a scope change** | SRS section 38.2 puts it after the MVP. Needs a product decision. [ADR 0002](docs/adr/0002-web-first-tourist-client.md). |
| **`AUTH_USER_MODEL` not set** | Phase 2 introduces `identity.User`. The local database must be recreated when it lands. |
| **`updated_at` maintained in Python** | SRS section 7.2 specifies a database trigger, which also covers raw SQL and bulk updates. Phase 2 migration task. |
| **No rate limiting** | SRS section 9.6 limits are Phase 2, alongside authentication. |
| **No role guards in the console** | RBAC is Phase 2. No placeholder guard is stubbed — one that always allows would be worse than none. |

See `docs/IMPLEMENTATION-PLAN.md` section 7 for the open decisions.
