# ADR 0016 — Map tiles are a configured URL behind MapLibre, and the provider is D9

**Status:** Accepted · **Date:** 2026-08-23 · **Phase:** 3 · **Resolves:** Q5 · **Registers:** Appendix D — D9

## Context

Four screens in the tourist web client need a map: Explore (a list/map toggle
over a destination's listings), Destination, Attraction, and the §24.11 "where
are you staying" flow, where §13.2 requires a free-entry hotel name to be
resolved to a coordinate and **shown as a pin the tourist confirms** before
anything is persisted. That last one is not decoration — an unconfirmed geocode
is never silently stored, so the map is load-bearing in a write path.

**There is no map provider in the decisions register.** Appendix D's D2 covers
*routing*, and SRS §34.6 recommends "Mapbox or a self-hosted OSRM for routing
and matrix work, with a high-quality geocoder for address resolution, all behind
`RoutingPort`". Tiles are named nowhere. That is an easy gap to miss because
several routing vendors also sell tiles, so a single logo on an invoice can hide
two separately licensed, separately priced, separately rate-limited products.
Resolving D2 does not resolve tiles.

The gap has to be closed now rather than at launch, because the alternative is
four screens built against whichever SDK someone reached for first. A tile
vendor's SDK is not a thin wrapper — it carries its own style spec, its own
layer model and its own auth. Choosing it implicitly and discovering the
commercial terms afterwards is how a rendering library becomes an unbudgeted
line item and a rewrite.

## Decision

### 1. The renderer is MapLibre GL JS, and it is not a commercial decision

MapLibre GL JS is BSD-3-Clause, community-governed, and consumes any provider
serving standard raster or vector tiles. It is a fork of the last open-source
Mapbox GL JS release, so it is not a niche choice, and it has no account, no
token and no terms attached to the library itself.

Picking it commits us to a *rendering* library and to nothing else. That is the
property that matters: the vendor decision stays open while four screens get
built.

### 2. The tile source is a `system_setting`, not a build-time constant

`map.tile_url` holds the URL template and `map.tile_attribution` holds the
attribution string that must be rendered with it. Both are `system_setting`
rows (rule 5), so changing provider is an administrator action, not a
deployment — which is the same standard §41.12 sets for opening a market.

They default to OpenStreetMap's standard raster tiles with the required
`© OpenStreetMap contributors` attribution, so development and CI work with no
account and no key.

Attribution is a setting **beside** the URL rather than hardcoded next to the
component, because every provider requires a different string and getting it
wrong is a licence breach rather than a cosmetic bug. Pairing them means a
provider swap cannot change one without the other — the same pairing discipline
`money`/`currency` and `entrance_fee`/`fee_currency` already follow.

### 3. A tile key, when there is one, is a secret and never reaches the client bundle

Commercial providers authenticate with a key in the URL. §30.9 forbids a secret
in source control, in images and in committed env files, and a key baked into a
Next.js bundle is published to every visitor.

So the template is served to the browser at runtime from configuration, never
inlined at build time. Where a provider's key cannot be domain-restricted, the
tile request is proxied. Which of the two applies is a consequence of D9 and is
recorded when D9 resolves.

### 4. The default cannot go to production, and that is the decision D9 blocks

OpenStreetMap's Tile Usage Policy does not permit commercial production traffic.
The default here is a **development** default. Shipping it to a launched product
would be both a licence breach and an operational risk, since the community
tile servers can and do block heavy consumers without notice.

**Appendix D gains D9 — map tile provider**, owner Commercial / Architecture,
blocking a production launch and not blocking Phase 3. The realistic candidates
are MapTiler, Mapbox, Stadia Maps and self-hosted tiles; the choice is
cost-versus-operations and belongs with D2's conversation, because a single
vendor may price both together even though the products are separate.

## Consequences

**Phase 3's map screens are unblocked, and none of them names a vendor.** The
map component reads two settings and renders. No screen, no test and no fixture
mentions a provider, so D9 resolving is a settings change plus — if the provider
needs a key — the proxy decision in point 3.

**The web client must render maps without CLS.** The container reserves its box
in server-rendered HTML and MapLibre is lazy-loaded into it, because §29's
NFR-P01 gate requires CLS < 0.1 and a map that mounts into a collapsed div is
the single easiest way to fail it.

**A launch checklist item exists that is not a code change.** "D9 resolved and
`map.tile_url` pointed at a licensed provider" has to appear on the go-live
list, because nothing in CI can detect that a legal default is being used
illegally. This ADR is the record that the gap is deliberate and dated rather
than forgotten.

**This does not pre-empt D2.** `RoutingPort` still owns routing, matrices and
geocoding, and §12.6's degraded mode still returns `APPROXIMATE` distances from
a fake adapter until D2 lands. Tiles are a display concern; routing is a pricing
concern. Keeping them separate is what stops one vendor negotiation blocking
both.
