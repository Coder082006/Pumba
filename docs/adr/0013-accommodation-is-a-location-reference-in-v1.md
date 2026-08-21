# ADR 0013 — Accommodation is a location reference in v1, not a bookable product

**Status:** Accepted · **Date:** 2026-08-21 · **Phase:** 3

## Context

The SRS as baselined sells accommodation. §14 gives it a two-level model, §7.5.7
and §7.5.8 give it `room_type` and `room_availability`, §17 gives it the hold
lifecycle, §37.5 gives it a whole development phase, and §38.5 makes "≥ 60
accommodation providers" an MVP success criterion.

That is the wrong product for v1, for a reason about *when* the tourist decides
rather than about what the Platform can build.

**Tourists book hotels first.** Months ahead, on Booking.com or Airbnb, before
they have thought about transfers or excursions. By the time somebody reaches
this Platform the hotel is already chosen. Competing for that decision means
competing at the wrong moment, against incumbents with a decade of supply and
review depth, using the heaviest subsystem in the specification: availability
calendars, rate resolution, minimum-stay rules, and inventory holds under row
lock. It also means roughly sixty property onboardings before launch — sixty
commercial relationships that have to exist before a single tourist can complete
a trip.

**The itinerary engine never needed to sell the room.** It needs three facts:
where the property is, when they check in, when they check out. Everything
beyond those three facts is cost without differentiation in v1. The transfer
that matters — ZNZ to the hotel, the hotel to Stone Town — needs a coordinate
and a time, and both are available the moment the tourist says where they are
staying.

**No integration route exists to recover the revenue in v1.** Airbnb has no
public API and no affiliate programme, so there is nothing to integrate.
Booking.com's affiliate programme is joinable and yields deep links at roughly
4% on completed stays; their Demand API is approval-gated and is not available
to a pre-launch product with no volume to show. The affiliate link is therefore
an optional SHOULD-HAVE (§38.2), not a v1 dependency: nothing in the booking
path, the pricing path or the itinerary path may require it.

## Decision

**Accommodation ceases to be a bookable product in v1 and becomes a location
reference.**

`accommodation` survives as a curated reference table holding the facts the
itinerary needs and nothing more: `name`, `slug`, `property_type`,
`destination_id`, `coordinates`, `address_line`, `check_in_time`,
`check_out_time`, `is_active`, `deleted_at`. It is administrator-curated
catalogue data of the same kind as `attraction` — not provider-listed supply —
so `provider_id`, `star_rating`, `amenities`, `child_policy` and
`cancellation_policy_id` leave with the subsystem.

`room_type` and `room_availability` are **deferred to v2 and dropped from the v1
schema**.

A STAY itinerary item becomes a **stay anchor**: it fixes location and dates and
carries no provider, no price, no booking and no inventory. It is captured one of
two ways.

* **Curated property.** The tourist selects a seeded `accommodation` record.
  Coordinates are known and exact, so transfer routing and pricing are accurate.
* **Free entry.** The tourist types any hotel name or address. It is resolved to
  a coordinate through the §13.2 geocoding path and shown as a map pin the
  tourist confirms. Per §13.2 an unconfirmed geocode is never silently
  persisted; if the tourist does not confirm, no anchor is created.

Either way the item supplies the origin or target for adjacent transfers and
bounds the day sequencing. Multiple non-overlapping stays across a trip remain
supported. VR-16's warning for uncovered nights stays, because a night with no
anchor is a night whose transfers cannot be planned.

## What is deferred

| Deferred to v2 | Where |
|---|---|
| `room_type`, `room_availability` | §7.5.7, §7.5.8 — dropped from the v1 schema |
| `booking_accommodation`, `booking_type = ACCOMMODATION` | §7.3 View 3, §20.2, §20.3 — enum value reserved, not renumbered |
| Rate resolution, stay totals, nightly averages | §14.2 |
| Room availability, calendar horizon extension, stop-sell | §14.3, §17 |
| Occupancy validation against room capacity | §10.6 VR-05, VR-11 |
| Accommodation Provider as an actor | §5.1, §5.3.3 |
| Accommodation listing and calendar management in the portal | §26.4, §26.5 |
| `GET /accommodations/{id}/room-types` | §9.4.3 |
| Accommodation Details and Room Selection screens | §24.12, §24.13 |

The pure domain modules are **retained and marked v2 in place**, not deleted:
`domain/occupancy.py` whole, and `pricing.stay_total` / `pricing.nightly_average`.
They are cheap, they are tested, and they will be wanted again exactly as
written. `pricing.stay_nights` is *not* v2 — BR-101's "check-out strictly after
check-in, maximum 30 nights" applies to a stay anchor exactly as it applied to a
booking, and it is what §24.11 validates with.

`cancellation_policy` is **not** deferred. §14.6 has it referenced by properties
*and activities*, and activities remain a v1 booked product, so the table, its
tier parsing and its admin management (§27.12) are untouched v1 code.

## What is gained

Phase 5 collapses from "Accommodation and Inventory" to "Activity Inventory and
Holds" — the hold lifecycle, the concurrency-safe commit, the sweeper and the
reconciliation checker are all still built, against one counter table instead of
two. The oversell guarantee (§38.5, zero incidents) is unchanged in strength and
smaller in surface.

Launch stops depending on sixty commercial relationships. It depends on the
supply the Platform can actually differentiate on: drivers and activity
operators, where the tourist has *not* already decided, and where a beach
intermediary and a cash negotiation are the incumbents.

Transfer pricing coverage improves rather than degrades, which is the
counter-intuitive part. Appendix C can now seed roughly forty known Zanzibar
properties with coordinates, because they are reference data rather than
inventory — a thing the old model forbade, since seeding a property implied
claims about price and availability that only its owner could make. A tourist
who booked the Ocean Breeze on Booking.com gets an exact transfer quote on day
one.

## The subsystem returns in v2

Nothing in this design prevents it. `accommodation` keeps its identity, its
coordinates and its destination; `room_type` re-attaches to it by
`accommodation_id`, `room_availability` re-attaches to `room_type`, and the
migrations that removed them are reversible operations that v2 reverses. The
`booking_type` enum value is reserved, so v2 adds no renumbering. The domain
functions are still in the tree with their tests.

The condition for reviving it is commercial, not technical: real supply
relationships, and enough completed-trip volume to make a Demand API application
or a direct-contract conversation worth having. Until then the Platform sells
what it is best placed to sell, and knows where the tourist sleeps.

## Consequences

This partly reverses the Q2 decision, which brought `room_availability` forward
into Phase 3 so that TC-020 could be written against it. TC-020 is void in v1;
it is **amended in place, not deleted**, and returns with v2.

`room_type` and `room_availability` leave the schema through *additive* drop
migrations rather than by editing `catalogue/0003` and `inventory/0001`. Those
two migrations are pushed, CI applies them from zero on every run, and ADR 0011
and ADR 0012 both quote `inventory/0001` by name including its
`room_availability_room_type_fk` SQL. Rewriting them would falsify two accepted
records. The drop order is forced by that foreign key: `inventory/0002` removes
`room_availability`, then `catalogue/0006` removes `room_type` and reduces
`accommodation`, depending on `inventory/0002` — a migration dependency is a
string, not an import, so ADR 0012 still holds.

`Resource.ROOM_TYPE` and `Resource.ROOM_AVAILABILITY` are **removed** from the
authorisation enum rather than reserved, which is the opposite of what happens
to `booking_type`. The distinction is that `booking_type` is a persisted string
in a CHECK constraint, where a gap would cost a renumbering, while `Resource` is
an in-process `StrEnum` that is never written anywhere. A reserved member there
would either break the totality assertion over `Role × Resource` or carry an
ownership path — `room_type__accommodation__provider_id` — that can never
resolve. `Resource.ACCOMMODATION` stays, but moves from a provider-listed rule
to an administered one, because a location record has no provider owner.

The honest cost: a tourist who has not yet booked a hotel gets less help from
this Platform than the old design promised. §24.11 answers that as well as it
can without inventory — the curated list makes a good property easy to name, and
the optional affiliate deep link (§38.2) sends them somewhere that can actually
sell it. That is a worse experience than an integrated booking, and a better one
than a subsystem nobody has supply for.
