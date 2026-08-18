# ADR 0002 — Tourist website is built before the Flutter app

**Status:** Proposed — awaiting product decision · **Date:** 2026-08-18 · **Phase:** 1

## Context

The brief orders delivery web-first: API, tourist website, provider/admin
console, then Flutter. The SRS says something different.

- **§38.2** places "a public web booking front end" in **SHOULD HAVE —
  Immediately After MVP**, not in §38.1 MUST HAVE.
- **§23–25** specify the tourist client as a Flutter application with 32
  screens. No web tourist client appears anywhere in the document.
- **§34.5** rejects Next.js explicitly: *"Alternative: Next.js if server
  rendering or SEO becomes a requirement — it is not, since both applications
  are behind authentication."* That reasoning is sound for the portal and
  console and false for a public tourist site.
- **§38.1** requires offline access to the confirmed itinerary, driver details,
  PIN and support number, with acceptance criterion **§41.10**. A PWA can
  approximate this; it cannot match a Flutter app using platform secure
  storage.

## Decision

Build `apps/web-tourist` as the v1 tourist client, using Next.js 15 with the
App Router so catalogue pages render on the server for SEO.

**This ADR is Proposed, not Accepted.** It records a divergence that needs a
product-owner decision on one of:

- **(a)** Re-baseline the MVP: web is the v1 tourist client, the Flutter app
  moves to SHOULD HAVE, §41.10 is restated in PWA terms.
- **(b)** Web is additional: the Flutter app stays MUST HAVE and v1 does not
  launch without it.
- **(c)** Keep §38.1 as written and build Flutter first, contradicting the brief.

## Consequences

Phase 1 is unaffected — the foundation is identical under all three options,
which is why work proceeded without the decision. The decision must land before
Phase 2 exits, because it determines whether the Phase 2 client slice is web
only or web plus Flutter.

The API is unaffected either way: it stays strictly client-agnostic (brief
constraint, SRS §6.4), so adding Flutter later requires no backend change. That
property is what makes the ordering reversible.

Under (a), §41.10 must be rewritten. Do not let it stand unamended while a web
client cannot satisfy it — an acceptance criterion nothing can pass is worse
than a weaker one honestly stated.
