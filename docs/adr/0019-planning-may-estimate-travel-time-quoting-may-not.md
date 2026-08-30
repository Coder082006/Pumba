# ADR 0019 — Planning may estimate a travel time; quoting may not

**Status:** Accepted · **Date:** 2026-08-30 · **Phase:** 4

## Context

Phase 4 is the trip planner. Its core is the day-sequencing algorithm of SRS
§10.4, and line 12 of that algorithm is `t := travel_time(origin, target)`.
Every transfer the planner inserts is timed by that call.

The obvious reading is that Phase 4 cannot start until Appendix D-2 (the
routing provider) is decided. That is what I told the project owner, and it is
wrong. It rests on §10.5 read without its own exception clause:

> Travel times come from the routing port (Section 12.3), **never from
> straight-line estimates, except in the explicit degraded mode of Section
> 12.6.**

§12.6 then specifies that degraded mode as a table with one row per context,
and the two rows that matter say opposite things:

| Context | Behaviour |
|---|---|
| **Planning (generate)** | route_cache, else the nightly matrix, else **compute a haversine distance × road-factor (configurable, default 1.35) and a speed model (configurable, default 45 km/h), and mark the item `estimate_quality = APPROXIMATE`**, which the UI renders with an explicit "approximate" label |
| **Quoting (quote)** | Cache and matrix permitted. **Haversine fallback is not permitted** for a priced corridor without a fixed corridor price; the endpoint returns `502 ROUTING_UNAVAILABLE` rather than commit the platform to a guessed price |

So the specification already draws the line I thought D-2 drew, and draws it in
a different place: between *arranging a day* and *charging for it*.

**This is not the geocoding question, and the difference is the whole point.**
The fabrication this project declined twice — once on the distance chip, once
when `routing` was kept out of the port registry — was `FakeRouting.geocode`,
which derives a coordinate from a **sha256 of the query string**. That is a
number with no relationship to the world, presented as a location.

A haversine distance between two known points is the opposite kind of number.
Every endpoint the planner sequences already carries a real surveyed
coordinate: `destination.centroid`, `attraction.coordinates`,
`accommodation.coordinates` and `activity.coordinates` are all
`geography(Point, 4326)` in `apps/api/apps/catalogue/models.py`. The great-circle
distance between two of them is a fact. Multiplying it by a road factor to
approximate a driving distance is a stated, bounded, configurable
approximation — and §12.6 requires it to be *labelled as one on screen*.

**The planner also needs no geocoding whatsoever.** §13.2 confines forward
geocoding to administrative tooling and the custom-pickup picker. Sequencing
reads coordinates that are already in the catalogue. `FakeRouting.geocode` is
therefore not merely forbidden here; it is not on the path.

## Decision

**Phase 4 is built in full, including day sequencing and transfer insertion,
against a travel-time source that reports its own quality.**

1. **The domain sequencer does not fetch anything.** `travel_time` is an input
   to the pure function, not a call it makes. The algorithm of §10.4 is
   therefore complete, deterministic and fully testable with no port, no
   network and no fake — which is what §8.2 layer 3 requires of it anyway.

2. **The application layer resolves travel times through one function** with
   the §12.6 precedence: `route_cache` → nightly destination-pair matrix →
   haversine fallback. It returns a value **and its quality**, never a bare
   number.

3. **`estimate_quality` is persisted on `itinerary_item`.** §12.6 names the
   field; §7.5.11's column list does not contain it. It is added here under
   ADR 0007's rule for tables the SRS names but does not fully specify. Values:
   `ROUTED` (a provider answered, directly or from cache), `MATRIX` (the
   nightly precomputation), `APPROXIMATE` (haversine fallback). A `TRANSFER`
   item must carry one; a non-transfer item must not.

4. **The road factor and speed model are `system_setting` rows**, not
   constants: `routing.road_factor` (default `1.35`) and
   `routing.average_speed_kmh` (default `45`). NFR-M07 forbids a business
   threshold in code, and these are the two numbers a reader will most want to
   challenge.

5. **Quoting is not built in Phase 4 and must not silently inherit this.** The
   quote path is Phase 6/7. When it lands it reads the same resolver and
   **refuses** an `APPROXIMATE` result with `502 ROUTING_UNAVAILABLE`. Until
   then no code may call the resolver from a pricing path, and a test asserts
   that the pricing path does not exist rather than trusting that nobody added
   one — the same shape as `test_ports_registry.py`.

6. **The routing port stays unregistered.** `_FAKES` gains nothing;
   `get_routing_port` still does not exist. The `RoutingPort` *interface* is
   declared in Phase 4 as §37.4 requires, and the first real adapter arrives
   with D-2. `test_ports_registry.py`'s entry for `routing` keeps its reason;
   only its phase note changes.

## Consequences

**Phase 4 is no longer blocked by D-2.** Sequencing, transfer insertion,
VR-01…VR-17, cost computation, versioning and locking all ship. What D-2 buys
is accuracy and the removal of the "approximate" label — a quality upgrade to a
working planner, not the difference between a planner and none.

**Every estimated leg is visibly estimated.** That is a product cost, and it is
the honest one: a tourist reading "approximately 45 minutes" can plan around
it, where a tourist reading "45 minutes" from the same arithmetic has been
misled. The badge is required by §24 (the Transportation screen already
specifies it) and is asserted by a test, because a label that exists only in a
component nobody renders is this project's most repeated defect.

**The failure mode to watch is quality laundering.** An `APPROXIMATE` time that
is copied into a `booking_transfer` at confirmation, or averaged into a total
that is then presented as a price, has escaped its label. The guard is that
quality travels with the value through the resolver's return type rather than
being an attribute a caller may forget to read, and that Phase 6/7's quote path
is required to branch on it.

**The 1.35 road factor is a guess, and is stated as one.** It is a
configuration row precisely so that the first real routed leg can be compared
against it and the number corrected without a deployment. Zanzibar's road
network is not uniform, and a single factor will be wrong in both directions in
different places — acceptable for an advisory plan, never for a price.
