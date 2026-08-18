# Implementation Plan — Zanzibar Tourism Journey Orchestration Platform

**Status:** Draft for approval — no application code written yet
**Source of truth:** `docs/srs/SRS-ZTJOP-001.pdf` (v1.0, 146 pp., "Approved for Development")
**Author:** Claude (engineering)
**Date:** 2026-08-18

---

## 0. Preliminaries — what I actually found

Three things differed from the kickoff brief before I read a line of spec. Flagging them
because two affect provenance:

| Brief said | Reality | Action taken |
|---|---|---|
| SRS is at `docs/srs/SRS-ZTJOP-001.docx` | It is a **PDF**, and it was in `~/Downloads/Documents/`, not in the repo | Copied to `docs/srs/SRS-ZTJOP-001.pdf` |
| Repo already `git init`-ed | Directory was empty, not a repo | Ran `git init` |
| — | No `.docx` exists anywhere on the machine | Working from the PDF |

**Extraction caveat that matters.** I extracted text twice, because `pdftotext -layout`
interleaves the columns of every multi-column table in this document — including §6.4,
the module dependency table that the entire import-linter contract set derives from.
Under `-layout`, §6.4 reads as though `trip` owns `transfer_corridor` and `booking`
depends on `catalogue`. Both are false. The `-raw` extraction reconstructs the tables
correctly and is what I used for every table cited below.

Both extractions are committed (`docs/srs/srs.txt`, `docs/srs/srs-raw.txt`) so the
derivation is auditable, but **the PDF is normative** — if a table below looks wrong,
check the PDF page, not my text dump.

---

## 1. Conflicts between the kickoff brief and the SRS

The brief says: *"Where this prompt and the SRS disagree, tell me; do not silently pick
one."* There are six real disagreements. C1 is the one that needs a decision before I
write anything.

### C1 — The tourist website is post-MVP in the SRS. The brief makes it the primary v1 client. ⚠️ **BLOCKING**

This is not a build-order difference. It is a scope difference.

- **SRS §38.2** lists *"a public web booking front end"* under **SHOULD HAVE —
  Immediately After MVP**. Not in §38.1 MUST HAVE.
- **SRS §23–25** specify the tourist client as a **Flutter application**, with 32
  screens fully specified in §24. There is no web tourist client anywhere in the
  document.
- **SRS §34.5** explicitly rejects Next.js: *"Alternative: Next.js if server rendering or
  SEO becomes a requirement — **it is not, since both applications are behind
  authentication**."* That sentence is scoped to the provider portal and admin console,
  and it is correct for them. It becomes wrong the moment a public tourist site exists,
  because that site's catalogue pages are the SEO surface.
- **SRS §38.1** MUST HAVE requires `Offline` — *"Confirmed itinerary, driver details, PIN
  and support number available offline."* This is an acceptance criterion (§41.10). A web
  app can approximate it with a service worker and IndexedDB; it cannot match a Flutter
  app holding the itinerary in platform secure storage. **A web-first v1 either ships a
  materially weaker offline guarantee or drops §41.10.**

The brief's build order is defensible — the API is genuinely client-agnostic, web ships
faster, and Flutter later costs zero backend change if we hold the line on §6.4. But it
changes what "v1" means, and §38.1/§41.10 are written against the Flutter app.

**I need you to pick one. I recommend (a).**

| Option | What it means |
|---|---|
| **(a) Re-baseline the MVP** (recommended) | `apps/web-tourist` becomes the v1 tourist client. Amend §38.1 to move the Flutter tourist app to SHOULD HAVE and §41.10's offline criterion to a PWA-scoped equivalent. Record as an ADR. Honest, and keeps the SRS trustworthy. |
| **(b) Web is an additional surface** | Flutter tourist app stays MUST HAVE. Web ships first but v1 does not launch without Flutter. Bigger v1, no SRS amendment. |
| **(c) Keep §38.1 as written** | Build Flutter first. Contradicts the brief. I mention it only for completeness. |

Whichever you choose, **nothing in Phase 1 changes** — the foundation is identical either
way. I can start on your approval of this plan and take the C1 decision in parallel, as
long as it lands before Phase 2 exit. It only becomes blocking at the point where we
commit to a v1 launch definition.

### C2 — Repository layout: brief and SRS §36 are structurally different

The brief says *"scaffold exactly as specified in SRS §36"* but then gives paths that are
not §36's.

| SRS §36 | Brief |
|---|---|
| `backend/` | `apps/api` |
| `web/provider-portal/` + `web/admin-console/` (two apps) | `apps/web-console` (one app, two route trees) |
| `web/shared-ui/` | `packages/ui` |
| `contracts/` | `packages/contracts` |
| `mobile/` | *(deferred)* |
| `database/`, `documentation/`, `infrastructure/`, `tests/` | *(not mentioned)* |
| — | `packages/config` |

Note §36 and §34.5 already disagree with each other: §36 shows two web directories,
§34.5 mandates *"one code base with two role-scoped route trees."* The brief agrees with
§34.5. **I follow the brief's paths** (they are pnpm/Turborepo-idiomatic and the brief is
more specific), keep §36's `infrastructure/`, `database/`, `docs/` top-level directories
because they hold non-workspace assets, and record the mapping in an ADR. Proposed tree
in §5 below. **Flag if you want §36's literal layout instead.**

### C3 — `administration` vs `admin`

§6.4 calls the module `admin`; §36 calls the directory `administration`; the brief says
`administration`. Not a real conflict — `admin` collides with `django.contrib.admin` and
is a bad package name. **Using `administration`.** Import-linter contract IDs will say
`administration`.

### C4 — React 18 vs React 19, Vite 5

§34.11 says React 18 + Vite. Brief says React 19 + Vite 5. Version bump on a
recommendation, not a requirement. **Following the brief.** No architectural
consequence.

### C5 — MapLibre GL JS vs Mapbox

§34.6 recommends *"Mapbox or a self-hosted OSRM… The decision is commercial and
reversible."* Brief mandates MapLibre GL (vendor-neutral, tiles configurable). MapLibre
is strictly more consistent with the `RoutingPort` abstraction §12.3 requires — the SRS's
own principle A3 argues for the brief's choice. **Following the brief.** This is rendering
only; `RoutingPort` (route/matrix/geocode) remains a separate, unselected decision
(Appendix D-2).

### C6 — Django Channels is in the SRS MUST-HAVE set but not in Phase 1

§6.3/§9.5 require a WebSocket gateway; §37.1 does not list it as Phase 1 work; the brief
lists Channels in the stack but the Phase 1 task list (item 5) asks Compose to bring up
api/worker/beat only. **Resolution:** Phase 1 installs Channels, configures the ASGI
entrypoint and the Redis channel layer, and runs the API under ASGI — but ships **no
consumers**. That satisfies "the stack is real" without doing Phase 10 work. Compose gets
no separate WS service in Phase 1; one ASGI process serves both.

---

## 2. Problems inside the SRS itself

These are internal inconsistencies and gaps, not disagreements with the brief. Four of
them (S1, S2, S5, S6) change Phase 1 code. I need decisions on S1 and S2 before I write
`common/`.

### S1 — The module dependency graph has a cycle: `administration` ⇄ everything ⚠️ **needs decision**

- §6.4 assigns `system_setting` to the `administration` module, and lists
  administration's dependencies as **"all (read via interfaces)."**
- **NFR-M07** (§29.6) and brief rule #5: *"No business constant is hard-coded; all
  thresholds, rates, weights and TTLs live in `system_setting`."*
- Therefore **every** module must read from `administration` — pricing reads
  `platform_fee_rate`, transport reads `dispatch.weights`, inventory reads
  `quote.ttl_minutes`, and so on across all 30 keys in Appendix B.

So `administration → all` and `all → administration`. As stated, §6.4 is not
satisfiable and import-linter cannot encode it.

**Recommended fix:** split read from write.
- `common/config.py` exposes `get_setting(key)` — a typed, cached read port. `common` is
  a leaf that every module may import, so no cycle.
- `administration` retains the `system_setting` **table**, the write path, the audit
  trail (§30.12) and the console UI, and depends on all modules as §6.4 says.
- The read port is backed by the administration-owned table but is not an import of
  `apps.administration`.

This preserves the §6.4 intent (one owner, audited writes) and NFR-M07, and keeps the
graph a DAG. **Confirm before I build `common/`,** because it determines whether
`get_setting` lives in `common` or `administration` — and every subsequent module imports
it.

### S2 — No owner for the idempotency store ⚠️ **needs decision**

§9.1 and principle A6 require `Idempotency-Key` on every POST creating a booking,
payment or assignment, with *"server stores key → response for 24 h."* That is one
mechanism spanning three modules. But §6.4 assigns no table for it — the only
idempotency storage in the schema is `payment.idempotency_key UNIQUE` (§7.5, line 2257),
which covers payments only.

**Recommended fix:** a shared `idempotency_record` table owned by `common`, with a
DRF mixin/middleware that captures and replays the response envelope. Same reasoning as
S1: it is infrastructure, not a business module. **This is Phase 1 work** (brief item 9),
so I need the call now.

### S3 — `payment` depends on `trip` and `booking` synchronously, contradicting §6.4

§6.4 says `payment → booking (via events)`. But:
- §9.4.7 `POST /payments/intents` *"assert `trip.status = PENDING_PAYMENT` and holds are
  live; compute the charge amount from `trip.total_amount`"* — a **synchronous read of
  `trip`**, and `trip` is not in payment's dependency list at all.
- §9.4.8 webhook handler on `CAPTURED` *"run the confirmation routine of Section 20.8
  (commit holds, confirm bookings, emit events)"* — synchronous writes into `booking` and
  `inventory`, inside the payment module's transaction, not "via events."

The §6.4 table is aspirational here; §9.4 and §20.8 are the implementable spec.
**Not Phase 1** (no payment code yet), but it changes the import-linter contract for
`payment`, which *is* Phase 1. I will encode payment's allowed set as
`{booking, trip, inventory}` with a `# SRS §6.4 says events-only; §9.4.7/§20.8 require
sync. Revisit at Phase 8.` comment on the contract, rather than write a contract I know
Phase 8 will have to loosen. Alternative is to write the strict contract now and take the
CI failure in Phase 8 as a forcing function — **tell me if you prefer that**; it is the
more disciplined choice and I would not argue against it.

### S4 — `cancellation_policy` has no owning module

Referenced by `accommodation` and `activity` (catalogue, §7.5), snapshotted onto
`booking` (§9.4.6), evaluated in `apps/booking/domain/policies.py` (§8.5), and managed in
the admin console (§27.12). §6.4 assigns it to none of the four. Seeded as 4 rows
(Appendix C).

**Recommendation:** `booking` owns the table (it owns the evaluation logic and the
snapshot), catalogue holds an FK without traversing it, per §6.5 rule 3. **Not Phase 1** —
no models yet. Raising it now so it is not discovered in Phase 7.

### S5 — Money precision: §7.2 and §18.5 don't quite agree

§7.2 mandates `NUMERIC(14,2)` for every money column. §18.5 mandates
*"ROUND_HALF_UP at the currency's minor-unit precision (2 for USD, 2 for TZS as used by
the PSP, **0 for currencies without minor units**)."*

These coexist — a 0-decimal amount stores fine in `NUMERIC(14,2)` — but only if the
`Money` value object owns a per-currency exponent table and never assumes 2. If Phase 1
hardcodes `quantize(Decimal("0.01"))`, every zero-decimal currency added later is a
silent money bug. **This is a Phase 1 design consequence:** `common/money.py` carries an
ISO 4217 exponent map, and `Money.quantize()` is currency-driven. No decision needed, but
it is why the Money object is not a two-line dataclass.

### S6 — Appendix B is absent from the table of contents

Appendices A–E exist (pp. 143–146) but no appendix appears in the TOC, which ends at
§44. The brief cites "SRS Appendix B" for the settings register — it is real, p. 144, 30
keys. Cosmetic, but if anyone regenerates the TOC the appendices need including.

### S7 — Minor

- **§3.1** the P1–P6 failure table has its rows and consequences misaligned in the source
  document (not an extraction artefact — the cells are genuinely offset by one).
  Cosmetic.
- **§2.2.2** cites *"Section 50 of the concept brief"* for the No-AI constraint; no
  concept brief is in scope and §50 does not exist in a 44-section document. Dead
  reference; §3.5 is the live one.
- **Appendix D** lists 8 commercial decisions (payment provider, routing provider,
  hosting region, tax treatment, provider terms, consumer terms, insurance, support
  hours). **None block Phase 1** — all sit behind ports or config rows, exactly as the
  appendix claims. D-2 (routing provider) blocks *Phase 4 completion*; D-1 (payment
  provider) blocks *Phase 8 start*. Worth starting those conversations now given lead
  times.

---

## 3. Module dependency order

Derived from §6.4 (`-raw` extraction, PDF pp. 21–22), with `administration`'s settings
read-path resolved per **S1**.

```
identity      → ∅
location      → ∅                      (external port only)
notify        → ∅                      (external ports only)
catalogue     → location
provider      → identity
inventory     → catalogue
transport     → location, provider
trip          → catalogue, transport
booking       → inventory, trip, provider
payment       → booking                (see S3 — likely + trip, inventory)
messaging     → booking
review        → booking
finance       → payment, booking
administration→ all                    (write side; read side via common/config)
```

This is a DAG. Topological layers — and therefore the **only** valid build order for
backend modules:

| Layer | Modules | Buildable when |
|---|---|---|
| **L0** | `identity`, `location`, `notify` | immediately |
| **L1** | `catalogue`, `provider` | L0 done |
| **L2** | `inventory`, `transport` | L1 done |
| **L3** | `trip` | L2 done |
| **L4** | `booking` | L3 done |
| **L5** | `payment`, `messaging`, `review` | L4 done |
| **L6** | `finance` | L5 done |
| **L7** | `administration` | all done |

`common` sits beneath L0 and is importable by everything. Nothing imports upward.

**Note the SRS phase plan violates its own graph in one place.** §37.4 (Phase 4, Trip)
admits it: *"for transfer insertion, [depends on] the routing port from Phase 6 — the
port interface is delivered here and the tariff logic in Phase 6."* `trip → transport` is
an L3→L2 edge, so Phase 4 cannot complete before Phase 6's tariff work. The SRS resolves
it by splitting the port interface from its implementation. That is the right call and I
will keep it, but it means **Phase 4 and Phase 6 overlap and cannot be sequenced
strictly.**

### Layering within each module (§8.2)

```
1. interface       views.py, consumers.py, serializers.py, permissions.py, urls.py
2. application     services.py, use_cases/          ← only layer other modules may call
3. domain          domain/*.py                      ← pure; no ORM, no I/O; 95% coverage
4. data access     models.py, repositories.py, selectors.py
5. infrastructure  adapters/, tasks.py              ← only place a vendor SDK may appear
```

---

## 4. Phase plan (re-ordered for web-first)

SRS §37 is 14 phases / 32–38 weeks for a team of six including two mobile engineers.
Re-ordering for web-first mainly means **Flutter work (SRS Phases 9-client, and the
tourist/driver app slices threaded through Phases 2–10) moves to the end**, and the two
web phases (SRS 11, 12) move earlier and merge.

| # | Phase | SRS ref | Notes on the change |
|---|---|---|---|
| 1 | Architecture & Foundation | §37.1 | **This session.** Unchanged. |
| 2 | Auth & User Management | §37.2 | Client slice = web, not Flutter |
| 3 | Catalogue | §37.3 | + tourist-site catalogue pages (SEO/server components) |
| 4 | Trip Planner & Itinerary | §37.4 | Overlaps 6 by design (see §3) |
| 5 | Accommodation & Inventory | §37.5 | |
| 6 | Transportation & Tariffs | §37.6 | |
| 7 | Booking Engine | §37.7 | |
| 8 | Payments & Finance | §37.8 | Gated on Appendix D-1 |
| 9 | Provider Portal + Admin Console | §37.11 + §37.12 | **Merged and moved earlier** — one Vite app per §34.5, and providers must exist before drivers have employers |
| 10 | Driver System | §37.9 | Driver *API* + admin/provider-side management. Driver *app* deferred |
| 11 | Real-Time Tracking & Notifications | §37.10 | Channels consumers land here |
| 12 | Flutter tourist + driver apps | §23–25 | **Moved to end.** Zero backend change is the acceptance test |
| 13 | Hardening, Testing, UAT | §37.13 | |
| 14 | Production Deployment & Launch | §37.14 | Gated on Appendix D-3, D-6, D-7 |

If C1 resolves to **(a)**, Phase 12 moves out of v1 entirely and the plan is 13 phases.

---

## 5. Phase 1 scope — exactly what I propose to build

Mapped to the brief's 10 items. **Nothing here is feature work.** No business model, no
endpoint beyond health, no migration except `common`'s.

### 5.1 Proposed tree

```
Pumba/
├── apps/
│   ├── api/                          # Django 5.1 · Python 3.12 · uv
│   │   ├── config/                   # settings/{base,dev,ci,staging,prod}.py, asgi, wsgi, celery
│   │   ├── apps/
│   │   │   ├── common/               # ← the only module with code in Phase 1
│   │   │   │   ├── models.py         # BaseModel: public_id UUID, created/updated_at, deleted_at
│   │   │   │   ├── money.py          # Money VO, ISO 4217 exponents, ROUND_HALF_UP  (S5)
│   │   │   │   ├── errors.py         # PlatformError hierarchy (§8.7)
│   │   │   │   ├── exception_handler.py  # → error envelope (§9.2, §32)
│   │   │   │   ├── middleware.py     # RequestId, Locale, AuditContext
│   │   │   │   ├── events.py         # domain event bus, transaction.on_commit (§8.9)
│   │   │   │   ├── state_machine.py  # table-driven transition validator (§36.2)
│   │   │   │   ├── pagination.py     # cursor pagination (§9.1)
│   │   │   │   ├── config.py         # get_setting() read port            (S1 — pending)
│   │   │   │   ├── idempotency.py    # Idempotency-Key store + replay     (S2 — pending)
│   │   │   │   └── health.py         # GET /api/v1/health
│   │   │   ├── identity/  catalogue/  provider/  inventory/  trip/
│   │   │   ├── booking/   transport/  location/   payment/   finance/
│   │   │   └── notify/    messaging/  review/     administration/
│   │   │        └─ each: models.py repositories.py selectors.py services.py
│   │   │           domain/ serializers.py views.py permissions.py urls.py
│   │   │           tasks.py adapters/ tests/          ← skeleton only, no logic
│   │   ├── ports/                    # RoutingPort, PaymentGatewayPort, Push/Email/Sms/StoragePort
│   │   │                             #   + fakes  (protocols only — no real adapters)
│   │   ├── tests/                    # cross-module + architecture tests
│   │   ├── pyproject.toml            # uv · ruff · mypy · pytest
│   │   └── .importlinter             # §6.4 contracts
│   ├── web-tourist/                  # Next.js 15 · React 19 · App Router
│   └── web-console/                  # Vite 5 · React 19 · two role-scoped route trees
├── packages/
│   ├── contracts/openapi/            # generated from Django, committed, diffed in CI
│   ├── ui/                           # shadcn/ui + Tailwind preset + design tokens
│   └── config/                       # shared tsconfig / eslint / prettier
├── infrastructure/
│   ├── docker/                       # Dockerfiles + compose
│   └── terraform/                    # scaffold only, per brief
├── database/seeds/                   # empty in Phase 1 (Appendix C is Phase 3)
├── docs/                             # srs/, adr/, IMPLEMENTATION-PLAN.md, runbooks/
├── .github/workflows/
├── turbo.json  ·  pnpm-workspace.yaml  ·  Makefile  ·  README.md
```

The 13 non-`common` modules ship as **skeletons**: the file set exists, `domain/` and
`adapters/` and `tests/` exist, `services.py` is empty, `models.py` has no models. They
exist so the import-linter contracts are real and so no one has to invent structure in
Phase 2.

### 5.2 Item-by-item

1. **This document.** ✅
2. **Monorepo** — pnpm workspaces + Turborepo, Node 20, Python 3.12/uv, conventional
   commits, `.gitignore`, `.editorconfig`.
3. **Django + 14 module skeletons** — as above.
4. **`common/`** — base model, Money, errors + handler + envelope, request-id middleware,
   event bus, state machine core, pagination. Plus `config.py` and `idempotency.py`
   pending S1/S2.
5. **Docker Compose** — `postgres:16-postgis-3.4`, `redis:7`, `api` (ASGI), `worker`,
   `beat`, `web-tourist`, `web-console`. One command, healthchecks, named volumes.
   Celery queues declared: `default`, `realtime`, `notify`, `payments`, `finance`.
6. **import-linter** — §6.4 as `forbidden` contracts per module; §8.2 as a `layers`
   contract; `domain/` must not import `django.db`; vendor SDKs confined to `adapters/`.
   Plus a pytest architecture test for the "services.py returns DTOs, never ORM
   instances" rule (§6.5 rule 5), which import-linter cannot express. **A deliberately
   forbidden import is committed on a scratch branch to prove CI fails.**
7. **`/api/v1/health`** + drf-spectacular → `packages/contracts/openapi/openapi.yaml`,
   committed.
8. **Web apps** — both scaffolded, Tailwind + shadcn/ui from `packages/ui`, TanStack
   Query, generated client, both rendering live health output.
9. **CI** — ruff, mypy (strict on `domain/`), import-linter, pytest + coverage gate
   (80% overall / 95% domain, per §35.3), tsc, eslint, vitest, OpenAPI diff.
10. **`make dev`** + `README.md`.

### 5.3 Explicitly NOT in Phase 1

No models outside `common`. No auth (Phase 2). No real port adapters — protocols and
fakes only. No Channels consumers (see C6). No seed data (Phase 3). No Terraform beyond
an empty module skeleton. No Playwright suites beyond one smoke test. No Flutter.

### 5.4 Commit sequence

Roughly 12 conventional commits, reviewable independently:
`chore(repo)` scaffold → `chore(api)` Django+uv → `feat(common)` base model+money →
`feat(common)` errors+envelope → `feat(common)` middleware+events+state machine →
`feat(api)` module skeletons → `feat(api)` ports+fakes → `feat(api)` health+OpenAPI →
`chore(ci)` import-linter → `chore(docker)` compose → `feat(web)` ×2 → `ci` workflows →
`docs` README+Makefile.

---

## 6. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | C1 unresolved → v1 launch definition ambiguous | Decide before Phase 2 exit; Phase 1 unaffected |
| R2 | S1/S3 → import-linter contracts get loosened later, eroding the point of having them | Decide S1 now; comment S3's contract with its revisit phase |
| R3 | 95% domain coverage is easy at zero LOC and hard at Phase 7 | Gate enforced from commit 1 so it is never retrofitted |
| R4 | Appendix D-1/D-2 lead times (payment, routing) | Start commercial conversations now — D-2 blocks Phase 4 completion |
| R5 | `trip`↔`transport` circular pressure at Phase 4/6 | Port interface delivered Phase 4, implementation Phase 6, per §37.4 |
| R6 | PostGIS + GeoDjango on Windows dev machines | All dev via Compose; no host-native GDAL dependency |

---

## 7. Decisions I need from you

**Before I write `common/`:**

1. **S1** — `get_setting()` in `common/config.py` (recommended) or in
   `apps/administration/services.py` accepting the cycle?
2. **S2** — shared `idempotency_record` table owned by `common` (recommended)?
3. **S3** — encode `payment → {booking, trip, inventory}` now with a comment
   (recommended), or the strict §6.4 events-only contract and take the Phase 8 CI
   failure as a forcing function?

**Before Phase 2 exit:**

4. **C1** — (a) re-baseline MVP to web, (b) web additional to Flutter, or (c) Flutter
   first?

**Confirm or correct:**

5. **C2** — brief's `apps/` + `packages/` layout over §36's literal tree?
6. **C5** — MapLibre over §34.6's Mapbox recommendation?

---

## 8. Proposed Phase 2 scope

Stated now so the boundary is visible; formal proposal follows Phase 1 completion.

SRS §37.2 — `identity` module only (L0). User/role/profile models; register, verify
email, login, refresh with rotation **and reuse detection**, logout, password reset;
Argon2id (§30.2); TOTP enrolment for provider/admin; RBAC permission classes and the
ownership predicate pattern; device registration; audit logging of every auth event; the
authorisation-matrix test harness. Client slice: web-tourist auth screens + web-console
login. **Acceptance:** TC-001…TC-013 pass; the authorisation matrix runs green across
every endpoint then existing; no endpoint unintentionally public.

Explicitly not in Phase 2: any catalogue model, any provider model (L1, Phase 3+).

---

**I have not written application code. Awaiting approval of this plan and answers to §7.**
