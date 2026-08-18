# CLAUDE.md — Project Rules

> Save this at the repository root. Claude Code reads it automatically at the start of
> every session, so these rules survive context resets and new conversations. Without it,
> long builds drift.

## Project

Zanzibar Tourism Journey Orchestration Platform. A mobile-first tourism planning,
reservation and travel-management platform for international tourists visiting Zanzibar,
Tanzania. Tourists plan and pay for the whole journey — airport transfer, accommodation,
activities, transport — before travelling, and receive one confirmed day-by-day itinerary.
Supply side is verified local drivers, hotels and activity operators.

**Authoritative specification:**
`docs/srs/SRS-ZTJOP-001 Zanzibar Tourism Platform SRS v1.0.docx`, **baselined to v1.1**
— the filename still says v1.0; the revision-history table inside the document is
authoritative for the version. Read it. It defines the data model, API, state machines
and business rules. If this file and the SRS conflict, raise it with me rather than
picking one.

`docs/srs/srs-docx.txt` is a lossless text extraction of that file — regenerate it with
`scripts/extract_srs.py` after any amendment. Prefer it over `srs.txt` and `srs-raw.txt`,
which are superseded v1.0 PDF extractions kept only for provenance; `srs.txt` in
particular column-scrambles every multi-column table.

**Core aggregate:** `Trip → Itinerary → ItineraryItem → Booking`.

## Build order and MVP client scope

Backend API → tourist website (Next.js) → provider portal + admin (Vite SPA) → Flutter
driver app. The API is client-agnostic; adding a mobile client must require zero backend
changes.

**MVP:** backend API, tourist web, provider portal, admin console, Flutter driver app.
**v1.1:** Flutter tourist app.

Decided in [ADR 0002](adr/0002-web-first-tourist-client.md) and recorded as SRS v1.1.
Three consequences that bind day-to-day work:

- **The Flutter tourist app is out of scope.** Do not scaffold the §23–24 tourist
  screens. The driver app is *in* MVP and is not substitutable by mobile web — a
  90-second offer TTL needs high-priority push, and `ARRIVED`/`COMPLETED` need
  background location inside a geofence.
- **The emailed PDF itinerary and calendar export are MVP, not polish.** Under §41.10 as
  amended they are the web client's entire answer to the offline requirement, so they
  belong to the trip-confirmation path.
- **The web client makes no offline guarantee and must not imply one.** No "available
  offline" copy, no misleading install prompt, no service-worker caching that suggests
  the itinerary survives a lost connection.

## Stack

| Layer | Technology |
|---|---|
| Monorepo | pnpm workspaces + Turborepo |
| Backend | Python 3.12, Django 5.1, DRF 3.15, uv |
| Database | PostgreSQL 16 + PostGIS 3.4 (GeoDjango) |
| Cache/queue | Redis 7; Celery 5 + Beat; queues `default`/`realtime`/`notify`/`payments`/`finance` |
| Realtime | Django Channels 4 (ASGI) |
| Auth | SimpleJWT — 15 min access, 30 day rotating refresh with reuse detection; Argon2id |
| API contract | drf-spectacular → OpenAPI 3.1, committed to `packages/contracts/openapi/` |
| Tourist web | Next.js 15 App Router, React 19, TypeScript strict |
| Console web | Vite 5 + React 19 + React Router v6 (provider portal + admin, role-scoped routes) |
| UI | Tailwind 3 + shadcn/ui, shared via `packages/ui` |
| Data fetching | TanStack Query v5; generated client from OpenAPI |
| Forms | react-hook-form + zod |
| Maps | MapLibre GL JS, tiles configurable |
| Python QA | ruff, mypy (strict on `domain/`), pytest, factory-boy, testcontainers |
| TS QA | eslint, vitest, React Testing Library, Playwright |
| Infra | Docker Compose local; Terraform for cloud; GitHub Actions CI |

## Hard rules

1. **No AI/ML in the product.** No chatbot, recommender, ML matching, AI pricing or
   generative features. All ranking, dispatch, matching and pricing is deterministic and
   rule-driven (SRS §3.5). Never add an ML dependency to a manifest.
2. **No destination-specific code.** Never branch on `"Zanzibar"`. Destinations,
   corridors, tariffs, policies and currencies are data. Zanzibar is the first
   configured destination, nothing more (SRS §4.2).
3. **Modular monolith.** One deployable Django app. Module boundaries enforced by
   import-linter per SRS §6.4. Cross-module access goes through `services.py` and DTOs —
   never another module's `models.py`.
4. **Layering** (SRS §8.2): interface → application → domain → data access →
   infrastructure. `domain/` is pure functions: no ORM, no I/O, no Django imports.
   Pricing, cancellation policy, validation rules and state machine guards live there and
   carry 95% coverage.
5. **No hard-coded business constants.** Thresholds, rates, weights, buffers and TTLs are
   `system_setting` rows (SRS Appendix B).
6. **Money is `Decimal`**, always paired with a currency column, `ROUND_HALF_UP`. Never
   float, anywhere, for any reason.
7. **Timestamps are `TIMESTAMPTZ` stored in UTC**, rendered in the destination timezone.
   No naive datetimes.
8. **External identifiers are UUIDs.** Sequential integers never leave the database.
9. **Idempotency keys** on every mutating POST creating a booking, payment or assignment.
10. **Ledger is append-only.** No UPDATE or DELETE on financial rows; corrections are new
    reversing entries.
11. **Never hold a DB transaction open across an external HTTP call.**
12. **Acquire row locks in ascending primary-key order** to avoid deadlock.
13. **Vendor SDKs only inside `adapters/`**, behind a port interface, with a fake for tests.
14. Availability may be read from cache for search, but the **authoritative check happens
    under row lock inside the committing transaction**. A cached figure never confirms a
    booking.

## Working agreement

- Plan before large changes; show the plan and wait for approval.
- Small, reviewable commits; conventional commit messages.
- Stop and ask when the spec is ambiguous — especially on money, availability,
  cancellation policy or state transitions. Never invent a business rule.
- Do not scaffold future-phase code to "save time."
- Every business rule gets a positive and a negative test.
- Every endpoint gets an authorisation test proving a foreign principal receives 404,
  not 403.
- Update the OpenAPI spec in the same commit as any API change.

## Commands

```
make dev          # bring up the whole stack
make test         # backend + frontend test suites
make lint         # ruff, mypy, import-linter, eslint, tsc
make openapi      # regenerate the OpenAPI spec and TS client
make seed         # load the Zanzibar catalogue seed data
```
