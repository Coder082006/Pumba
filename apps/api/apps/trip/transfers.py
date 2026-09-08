"""Pricing a transfer leg — SRS §9.4.4, §12.2, §12.4, §12.6.

Application layer (SRS §8.2 layer 2). The seam between a leg as a tourist
describes it — "from the airport to my hotel, two of us, two bags" — and a leg
as `transport` can price it.

**Why this lives in `trip` and not in `transport`.** §12.4's ladder branches on
the region of the origin, the country above it and whether either endpoint is a
gateway. All three are `catalogue` facts, and §6.4 gives
`transport -> location, provider`. `.importlinter` gives `trip -> catalogue,
transport`, which makes `trip` the only module that may see both sides, so the
resolving happens here and `transport` is handed facts. ADR 0023; the same
argument that put `POST /trips/{id}/quote` in `booking` under ADR 0022.

**§12.2's four bindings, as two fields.** A leg endpoint is "a destination
centroid, an accommodation, an activity meeting point, or an explicit
coordinate supplied by the tourist". The first three are a reference; the
fourth is not a *replacement* for one but an addition to it, because §7.5.11
already models a transfer as `origin_destination_id` **and** `origin_point` —
the destination says which tariff applies, the point says where the driver
stops. A bare coordinate with no destination has no region and no country, so
§12.4 has no rung it can answer on, and pretending otherwise would mean
inventing a spatial lookup the specification does not describe.

**ADR 0019's guard, inverted here.** Phase 4 asserted that no pricing path
called the travel resolver. This is that path, and the rule it must obey is
§12.6's: a measured route may price a leg, an `APPROXIMATE` one may not. The
translation is deliberately blunt — an approximate estimate becomes `None` on
the way into `transport` — so there is no number for a fare to be computed from
by accident. The estimate is still *reported*, carrying its quality, because
§24.17 requires the "approximate" badge and a leg with no numbers at all cannot
be badged.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone

from apps.catalogue import services as catalogue
from apps.common.errors import NotFoundError, ValidationError
from apps.common.geo import Coordinates
from apps.common.money import Money
from apps.transport import services as transport
from apps.transport.dto import LegEndpoint, LegQuote, LegRequest
from apps.trip.domain.sequencing import TravelEstimate
from apps.trip.travel import build_travel_time, place_key

__all__ = [
    "BINDINGS",
    "EndpointRef",
    "LegSpec",
    "ResolvedLeg",
    "quote_legs",
    "LegFare",
    "fares_for",
    "corridors",
]

#: §12.2's bindings that name a catalogue row, in the order a client is most
#: likely to send them. `destination` resolves through its own function because
#: `resolve_listing_ref` deliberately refuses it — a destination carries the
#: currency and timezone a trip cannot be opened without, and offering a second
#: door to that table would let a caller take the one that answers less.
BINDINGS: tuple[str, ...] = ("destination", "accommodation", "activity", "attraction")


@dataclass(frozen=True, slots=True, kw_only=True)
class EndpointRef:
    """One end of a leg, as it arrives on the wire.

    `point` is a precise pickup or drop-off *within* the referenced place, not
    an alternative to it — see the module docstring. A pin dropped in a hotel
    car park is still a leg from that hotel's destination as far as §12.4 is
    concerned, and the tariff must not change because the tourist moved the
    marker fifty metres.
    """

    kind: str
    reference: str | UUID
    point: Coordinates | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class LegSpec:
    """§9.4.4's `legs[]`, before anything has been resolved."""

    reference: str
    origin: EndpointRef
    target: EndpointRef
    depart_at: dt.datetime
    pax: int
    luggage: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedLeg:
    """What the quote says about a leg beyond its price.

    `estimate_quality` travels with the distance for the reason ADR 0019 gives:
    a number a tourist could mistake for a measurement must carry the evidence
    that it is not one, and the carrying has to be structural rather than a
    field a caller may forget to read.
    """

    quote: LegQuote
    distance_m: int
    travel_seconds: int
    estimate_quality: str
    origin_name: str
    target_name: str


def _resolve_place(ref: EndpointRef) -> catalogue.TransferPlace:
    """One endpoint, to the destination facts §12.4 prices it on.

    A missing row is a 404 rather than a 422. §30.3: a listing that was
    withdrawn must be indistinguishable from one that never existed, and a
    field-level "no such accommodation" would distinguish them.
    """
    if ref.kind not in BINDINGS:
        raise ValidationError(
            f"{ref.kind!r} is not a transfer endpoint; expected one of {list(BINDINGS)}."
        )

    if ref.kind == "destination":
        planning = catalogue.resolve_planning_ref(ref.reference, today=timezone.localdate())
        storage_id = None if planning is None else planning.storage_id
    else:
        listing = catalogue.resolve_listing_ref(ref.kind, ref.reference, today=timezone.localdate())
        storage_id = None if listing is None else listing.storage_id

    if storage_id is None:
        raise NotFoundError(f"No {ref.kind} matches {ref.reference!r}.")

    places = catalogue.transfer_places(ref.kind, [storage_id])
    place = places.get(storage_id)
    if place is None:
        # The row resolved a moment ago and does not now. Treating it as absent
        # is the only answer that cannot be wrong.
        raise NotFoundError(f"No {ref.kind} matches {ref.reference!r}.")
    return place


def _endpoint(place: catalogue.TransferPlace) -> LegEndpoint:
    return LegEndpoint(
        label=place.destination_name,
        destination_id=place.destination_id,
        region_id=place.region_id,
        country_id=place.country_id,
        is_gateway=place.is_gateway,
    )


def _coordinate(ref: EndpointRef, place: catalogue.TransferPlace) -> Coordinates:
    """Where the driver actually stops.

    The tourist's pin wins over the destination centroid when they dropped one,
    because a centroid is the middle of a town and a transfer is to a door. It
    changes the distance and therefore a metered fare; it never changes which
    tariff applies, which is what keeps a moved marker from re-pricing a leg
    onto a different rung.
    """
    return ref.point or place.coordinates


def _local(when: dt.datetime, zone: str) -> dt.datetime:
    """§12.4's "pickup_at local time", as a naive wall clock in the
    destination's zone.

    Naive on purpose: the night window is a pair of `TIME` columns with no
    offset, and comparing an aware instant against them would either fail or,
    worse, compare against UTC and shift a 22:00-06:00 surcharge to 01:00-09:00
    in Zanzibar — wrong in both directions and differently wrong in a market on
    the other side of the meridian.
    """
    return when.astimezone(ZoneInfo(zone)).replace(tzinfo=None)


def quote_legs(legs: Sequence[LegSpec]) -> tuple[ResolvedLeg, ...]:
    """§9.4.4, end to end: resolve, measure, price.

    Each *distinct* endpoint is resolved once, so a return trip between the
    same two places resolves two endpoints rather than four — which is the
    common shape, because §12.7 makes a return journey two independent legs.
    `transport` then prices every leg together, so the tariff side is a fixed
    three queries however long the request is.
    """
    resolved: dict[tuple[str, str], catalogue.TransferPlace] = {}
    for leg in legs:
        for ref in (leg.origin, leg.target):
            key = (ref.kind, str(ref.reference))
            if key not in resolved:
                resolved[key] = _resolve_place(ref)

    places: dict[str, Coordinates] = {}
    for leg in legs:
        for role, ref in (("origin", leg.origin), ("target", leg.target)):
            place = resolved[(ref.kind, str(ref.reference))]
            places[_key(leg, role)] = _coordinate(ref, place)

    travel = build_travel_time(places)

    requests: list[LegRequest] = []
    estimates: dict[str, TravelEstimate] = {}
    for leg in legs:
        origin_place = resolved[(leg.origin.kind, str(leg.origin.reference))]
        target_place = resolved[(leg.target.kind, str(leg.target.reference))]
        estimate = travel(_key(leg, "origin"), _key(leg, "target"))
        estimates[leg.reference] = estimate

        measured = estimate.quality != "APPROXIMATE"
        requests.append(
            LegRequest(
                reference=leg.reference,
                origin=_endpoint(origin_place),
                target=_endpoint(target_place),
                depart_at=leg.depart_at,
                pickup_local=_local(leg.depart_at, origin_place.timezone),
                pax=leg.pax,
                luggage=leg.luggage,
                # §12.6. An approximate estimate is not a distance a price may
                # be computed from, and the way to guarantee that is for there
                # to be no number rather than a number with a caveat.
                distance_m=estimate.metres if measured else None,
                travel_seconds=estimate.seconds if measured else None,
            )
        )

    quotes = transport.quote_transfer(requests)
    by_reference = {quote.reference: quote for quote in quotes}

    return tuple(
        ResolvedLeg(
            quote=by_reference[leg.reference],
            distance_m=estimates[leg.reference].metres,
            travel_seconds=estimates[leg.reference].seconds,
            estimate_quality=estimates[leg.reference].quality,
            origin_name=resolved[(leg.origin.kind, str(leg.origin.reference))].destination_name,
            target_name=resolved[(leg.target.kind, str(leg.target.reference))].destination_name,
        )
        for leg in legs
    )


def _key(leg: LegSpec, role: str) -> str:
    """A location key unique to one end of one leg.

    Keyed by the leg rather than by the place, because two legs may share an
    endpoint with *different* precise points — a hotel pickup and a hotel
    drop-off at opposite ends of a long driveway — and a place-keyed map would
    silently give the second one the first one's coordinate.
    """
    return place_key(f"leg-{leg.reference}", role)


def corridors() -> tuple[Mapping[str, object], ...]:
    """§9.3.4's `GET /transport/corridors`, with the destinations named.

    `transport` publishes internal destination ids because ADR 0012 makes a
    cross-module reference an id; §7.2 forbids one reaching a client. This is
    where the two rules meet: the ids are resolved to slugs and names on the
    way out, in the module that is allowed to look them up.
    """
    rows = transport.corridors()
    wanted = {row.origin_destination_id for row in rows} | {
        row.target_destination_id for row in rows
    }
    named = catalogue.transfer_places("destination", sorted(wanted))

    def _side(destination_id: int) -> dict[str, str]:
        place = named.get(destination_id)
        if place is None:
            # A corridor pointing at a destination that no longer exists. No
            # SQL foreign key stops that (ADR 0012), so it is a real state and
            # the honest rendering is the id's absence rather than a crash.
            return {"slug": "", "name": ""}
        return {"slug": place.destination_slug, "name": place.destination_name}

    return tuple(
        {
            "id": row.public_id,
            "origin": _side(row.origin_destination_id),
            "target": _side(row.target_destination_id),
            "vehicle_class": row.vehicle_class_code,
            "is_bidirectional": row.is_bidirectional,
        }
        for row in rows
    )


# ---------------------------------------------------------------------------
# Pricing the legs §10.4 inserted — SRS §10.7, §12.4, §12.6
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class LegFare:
    """What a planned transfer costs, and the inputs that decided it.

    §12.2 requires the class and the luggage count to be stored on the item so
    the leg "can be re-priced identically later", and §7.5.11 has the two
    destination ids for the same reason. All four are carried here so
    `_persist` writes them in the same breath as the money.
    """

    money: Money
    vehicle_class: str
    luggage: int
    origin_destination_id: int
    target_destination_id: int


def _parse_key(key: str | None) -> tuple[str, int] | None:
    """A sequencer location key, split into the row it names.

    `place_key` writes `"accommodation:41"`, and this reads it. The one key it
    cannot resolve is `"item:<uuid>"` — a free-entry stay anchor, which by
    definition has no catalogue row (ADR 0013) — so it returns `None` and the
    caller falls back to the trip's own destination. That is not a guess:
    BR-010 gives a trip exactly one destination, §10.6 keeps the anchor inside
    it, and §12.4 prices on "region of origin", which is that destination's
    region either way.
    """
    if not key or ":" not in key:
        return None
    kind, _, identifier = key.partition(":")
    if kind not in BINDINGS or not identifier.isdigit():
        return None
    return kind, int(identifier)


def _places_for(keys: Iterable[str | None]) -> dict[tuple[str, int], catalogue.TransferPlace]:
    """Every key resolved, in one query per catalogue table.

    Batched because `generate_itinerary` prices every transfer in an itinerary
    at once and pins its catalogue-read budget. Resolving a key at a time cost
    two extra reads per leg and failed
    `test_catalogue_reads_do_not_scale_with_items` — which is the test doing
    exactly what it was written for, on the first change that could have
    broken it.
    """
    wanted: dict[str, set[int]] = {}
    for key in keys:
        parsed = _parse_key(key)
        if parsed is not None:
            wanted.setdefault(parsed[0], set()).add(parsed[1])

    resolved: dict[tuple[str, int], catalogue.TransferPlace] = {}
    for kind, ids in wanted.items():
        for identifier, place in catalogue.transfer_places(kind, sorted(ids)).items():
            resolved[(kind, identifier)] = place
    return resolved


def fares_for(
    legs: Sequence[tuple[int, str | None, str | None, int | None, int | None, str | None]],
    *,
    trip_destination: catalogue.TransferPlace,
    depart_at: Mapping[int, dt.datetime],
    pax: int,
    luggage: int,
    strict: bool,
) -> dict[int, LegFare]:
    """Price a planned itinerary's transfers, by planner item id.

    Each tuple is `(item_id, origin_key, target_key, distance_m,
    travel_seconds, estimate_quality)` — the sequencer's own output, so this
    works for a leg it invented as well as one already stored.

    **`strict` is ADR 0019's line, as a parameter.** Planning tolerates a leg
    it cannot price and leaves it unpriced, exactly as Phase 4 did: a tourist
    arranging days must not be blocked because a fare is unavailable, and
    §10.7 already treats a transfer's line total as optional. Quoting does not
    tolerate it, because a total that silently omitted a leg would be a
    commitment to a price the platform had not computed.
    """
    if not legs:
        return {}

    known = _places_for(
        key for _, origin_key, target_key, *_rest in legs for key in (origin_key, target_key)
    )

    def place(key: str | None) -> catalogue.TransferPlace:
        parsed = _parse_key(key)
        return trip_destination if parsed is None else known.get(parsed, trip_destination)

    requests: list[LegRequest] = []
    endpoints: dict[str, tuple[catalogue.TransferPlace, catalogue.TransferPlace]] = {}

    for item_id, origin_key, target_key, distance_m, travel_seconds, quality in legs:
        origin = place(origin_key)
        target = place(target_key)
        if origin.destination_id == target.destination_id:
            # §12.2: a leg whose ends resolve to the same place is not a leg.
            # Within one town both ends share a destination, which is ordinary
            # and unpriceable by a corridor — the metered fallback is what
            # §12.4 has for it, and without a measured road there is no fare.
            continue

        reference = str(item_id)
        endpoints[reference] = (origin, target)
        measured = quality is not None and quality != "APPROXIMATE"
        requests.append(
            LegRequest(
                reference=reference,
                origin=_endpoint(origin),
                target=_endpoint(target),
                depart_at=depart_at[item_id],
                pickup_local=_local(depart_at[item_id], origin.timezone),
                pax=pax,
                luggage=luggage,
                distance_m=distance_m if measured else None,
                travel_seconds=travel_seconds if measured else None,
            )
        )

    if not requests:
        return {}

    try:
        quotes = transport.quote_transfer(requests)
    except (transport.NoTariffConfiguredError, transport.RoutingUnavailableError):
        if strict:
            raise
        # Planning carries on with unpriced legs. §10.7 sources a transfer's
        # line total from §12.4 and has always allowed it to be absent; what
        # changes in Phase 6 is that it is usually present.
        return {}

    fares: dict[int, LegFare] = {}
    for quote in quotes:
        option = transport.default_option(quote)
        if option is None:
            continue
        origin, target = endpoints[quote.reference]
        fares[int(quote.reference)] = LegFare(
            money=option.price,
            vehicle_class=option.vehicle_class.code,
            luggage=luggage,
            origin_destination_id=origin.destination_id,
            target_destination_id=target.destination_id,
        )
    return fares
