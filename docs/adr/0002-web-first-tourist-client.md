# ADR 0002 — Tourist web is the v1 booking client; the Flutter tourist app is v1.1

**Status:** Accepted · **Date:** 2026-08-18 · **Phase:** 2 · **Decided by:** Product Owner
**Amends:** SRS v1.0 §38.1, §38.2, §41.10 → recorded as SRS v1.1

## Context

The brief orders delivery web-first: API, tourist website, provider/admin
console, then Flutter. The SRS v1.0 baseline said something different.

- **§38.2** placed "a public web booking front end" in **SHOULD HAVE —
  Immediately After MVP**, not in §38.1 MUST HAVE.
- **§23–25** specify the tourist client as a Flutter application with 32
  screens. No web tourist client appeared anywhere in the document.
- **§34.5** rejected Next.js explicitly: *"Alternative: Next.js if server
  rendering or SEO becomes a requirement — it is not, since both applications
  are behind authentication."* That reasoning holds for the portal and console
  and fails for a public tourist site, which is not behind authentication.
- **§38.1** required offline access to the confirmed itinerary, driver details,
  PIN and support number, with acceptance criterion **§41.10**. A PWA can
  approximate this; it cannot match a Flutter app using platform secure storage
  and background execution.

This was recorded in Phase 1 as conflict **C1** in
`docs/IMPLEMENTATION-PLAN.md`, deliberately left open because Phase 1 is
identical under every option.

## Decision

The two tourist clients serve different phases of the journey, and the MVP is
re-baselined on that basis.

**Tourist web (Next.js) is the v1 booking client.** It owns pre-arrival:
discovery, planning, quoting, payment and booking management. This is where SEO
and desktop conversion matter, and it is where the transaction happens.

**The Flutter tourist app becomes the in-destination companion and moves to
v1.1.** It owns offline itinerary, driver tracking, pickup PIN and push.

**The Flutter driver app stays in MVP scope.** It cannot be replaced by mobile
web. A 90-second offer TTL needs high-priority push, and the `ARRIVED` and
`COMPLETED` transitions need background location inside a geofence. Airport
transfer — the platform's anchor use case — does not function without it.

### Revised MVP client scope

| Client | Scope |
|---|---|
| Backend API | MVP |
| Tourist web (Next.js) | MVP |
| Provider portal (Vite console) | MVP |
| Admin console (Vite console) | MVP |
| Flutter driver app | MVP |
| Flutter tourist app | **v1.1** |

### §41.10 is amended honestly, not stretched to fit

The offline acceptance criteria are not restated in PWA terms, because a PWA
does not meet them. The criteria keep their meaning and gain an explicit scope,
and the web client gets an equivalent guarantee delivered by a different
mechanism. SRS §41.10 now carries this amendment:

> **Amended v1.1.** The offline acceptance criteria above apply to the Flutter
> tourist client. For the web client, the equivalent guarantee is delivered
> out-of-band: on trip confirmation the tourist receives an emailed PDF
> itinerary containing the full day-by-day plan, every booking voucher, driver
> identity and vehicle where assigned, pickup point, pickup PIN and the support
> number, plus downloadable calendar entries. The web application makes no
> offline guarantee and must not imply one in the interface.

## Consequences

**The API is unaffected.** It stays strictly client-agnostic (brief constraint,
SRS §6.4), so adding the Flutter tourist client at v1.1 requires no backend
change. That property is what made the ordering reversible and is what this
decision now relies on — it must not be eroded.

**Phase 2 onward builds one tourist client, not two.** The Phase 2 client slice
is web only. The Flutter tourist screens of §23–25 are out of scope until v1.1;
do not scaffold them.

**The driver app remains a Phase-N deliverable inside MVP.** Nothing in this
decision defers it, and the transfer lifecycle cannot be accepted without it.

**A new MVP obligation falls out of the §41.10 amendment:** the emailed PDF
itinerary and the calendar export. These are not optional niceties — they are
now the web client's entire answer to the offline requirement. They belong to
the trip-confirmation path and are covered by `EmailPort` and `StoragePort`,
both of which already exist as ports with fakes. Schedule them with booking
confirmation, not as a late polish item.

**`TC-300` and `TC-301` move to v1.1** along with the client they test. The web
client needs its own acceptance test for the emailed itinerary; it is not a
substitute for the offline pack and must not reuse those identifiers.

**The interface must not imply an offline guarantee it does not have.** No
"available offline" copy, no misleading install prompt, no service worker
caching that suggests the itinerary survives a lost connection. This is a
review checkpoint on the tourist web UI, not a developer preference.

## Related

- SRS v1.1 revision-history row; `scripts/amend_srs_v1_1.py` records the exact
  edits made to the baselined `.docx`.
- `docs/IMPLEMENTATION-PLAN.md` §1 conflict C1 — now resolved by this ADR.
- ADR 0001 (monorepo layout), which places `apps/web-tourist` alongside
  `apps/web-console`.
