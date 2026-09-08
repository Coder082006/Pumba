"""Application layer (SRS §8.2 layer 2).

    The ONLY module boundary. Other modules call this and nothing else
    (SRS §6.5 rule 1). Orchestrates a use case in one transaction and
    emits domain events.

    Returns DTOs and primitives — never ORM instances (SRS §6.5 rule 5).

    Public interface: quote_transfer(), assign_driver(), dispatch_offer()

Phase 6 delivers `quote_transfer()`. The two dispatch functions belong to
Phases 9-10 and are not stubbed here — §4.2's working agreement forbids
scaffolding future-phase code, and an empty `assign_driver` would read as a
capability rather than an absence.

**This module resolves nothing.** §6.4 gives `transport -> location, provider`,
and §12.4's ladder branches on the region of the origin, the country default
and whether either endpoint is a gateway — all `catalogue` facts. The caller
hands them over already resolved on a `LegEndpoint`, and ADR 0023 records why:
`trip` is the only module that may see both sides, so the tourist-facing view
lives there, and `administration` serves the admin CRUD for the same reason.

**§12.6, in one place.** A corridor prices with no distance at all, so a
routing outage does not stop an airport transfer being quoted. A metered rule
cannot, and this is where that refusal happens — *after* the ladder has run,
because whether a missing distance matters is exactly what the ladder decides.
Refusing earlier would block a corridor quote that §12.6 explicitly permits;
refusing later would mean not refusing at all.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from uuid import UUID

from apps.common.errors import ExternalServiceError, ValidationError
from apps.transport import repositories as repo
from apps.transport.domain import tariffs as domain
from apps.transport.dto import (
    CorridorDTO,
    FareBreakdown,
    FareOption,
    LegQuote,
    LegRequest,
    TariffMatchDTO,
    VehicleClassDTO,
)
from apps.transport.models import TransferCorridor, TransferTariff, VehicleClass

__all__ = [
    "NoTariffConfiguredError",
    "RoutingUnavailableError",
    "quote_transfer",
    "vehicle_classes",
    "corridors",
    "preview",
]


class NoTariffConfiguredError(ValidationError):
    """§12.4: "No match -> 422 NO_TARIFF_CONFIGURED (never guess a price)".

    A 422 rather than a 404: the route exists, the platform simply has no fare
    for it. §24.16 renders this as "transfers to this location are not yet
    available — contact support", which is true and actionable, where a guessed
    price would be neither.
    """

    code = "NO_TARIFF_CONFIGURED"
    default_message = "No transfer tariff is configured for this route."


class RoutingUnavailableError(ExternalServiceError):
    """§12.6: "the endpoint returns 502 ROUTING_UNAVAILABLE rather than commit
    the platform to a guessed price".

    Retryable, and only ever raised for a metered leg. A corridor has a fixed
    price and needs no distance, so an outage never reaches this for the legs
    §12.6 says must keep working.
    """

    code = "ROUTING_UNAVAILABLE"
    default_message = (
        "We cannot measure this route right now, and we will not guess a price. "
        "Please try again shortly."
    )


# -- mapping ORM rows to the pure domain ------------------------------------


def _class_dto(row: VehicleClass) -> VehicleClassDTO:
    return VehicleClassDTO(
        public_id=row.public_id,
        code=row.code,
        name=row.name,
        description=row.description,
        seats=row.seats,
        luggage_capacity=row.luggage_capacity,
        has_air_conditioning=row.has_air_conditioning,
    )


def _class_spec(row: VehicleClass) -> domain.VehicleClassSpec:
    return domain.VehicleClassSpec(
        code=row.code,
        seats=row.seats,
        luggage_capacity=row.luggage_capacity,
        display_order=row.display_order,
    )


def _corridor_rule(row: TransferCorridor) -> domain.CorridorRule:
    return domain.CorridorRule(
        rule_id=row.pk,
        vehicle_class=row.vehicle_class.code,
        currency=row.currency,
        is_active=row.is_active,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        origin_destination_id=row.origin_destination_id,
        target_destination_id=row.target_destination_id,
        fixed_price=row.fixed_price,
        is_bidirectional=row.is_bidirectional,
    )


def _metered_rule(row: TransferTariff) -> domain.MeteredRule:
    return domain.MeteredRule(
        rule_id=row.pk,
        vehicle_class=row.vehicle_class.code,
        currency=row.currency,
        is_active=row.is_active,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        region_id=row.region_id,
        country_id=row.country_id,
        base_fare=row.base_fare,
        per_km_rate=row.per_km_rate,
        per_minute_rate=row.per_minute_rate,
        minimum_fare=row.minimum_fare,
        night_surcharge_pct=row.night_surcharge_pct,
        night_from=row.night_from,
        night_to=row.night_to,
        airport_surcharge=row.airport_surcharge,
    )


# -- reads ------------------------------------------------------------------


def vehicle_classes() -> tuple[VehicleClassDTO, ...]:
    """§9.3.4: "Classes with capacity and indicative pricing", public.

    Capacity only. "Indicative pricing" is not served here and deliberately so:
    a class costs what the corridor costs, which depends on the route and the
    date, and a single number attached to a class would be a quote nobody asked
    for and nobody could be held to. §24.16's cards get their prices from
    `POST /transport/quotes`, which knows the leg.
    """
    return tuple(_class_dto(row) for row in repo.live_vehicle_classes())


def corridors() -> tuple[CorridorDTO, ...]:
    """§9.3.4's public corridor list — where the platform runs transfers."""
    return tuple(
        CorridorDTO(
            public_id=row.public_id,
            origin_destination_id=row.origin_destination_id,
            target_destination_id=row.target_destination_id,
            vehicle_class_code=row.vehicle_class.code,
            is_bidirectional=row.is_bidirectional,
        )
        for row in repo.published_corridors()
    )


# -- the quote --------------------------------------------------------------


def quote_transfer(legs: Sequence[LegRequest]) -> tuple[LegQuote, ...]:
    """§9.4.4, from the tariff down. One quote per leg, one option per class.

    Two queries in total regardless of how many legs and classes are involved.
    That is not premature: `trip.services.generate_itinerary` prices every
    transfer in an itinerary at once and its N+1 budget is a pinned assertion,
    so a per-leg query here would fail a test in another module for a reason
    nobody would look for here.

    Raises `NoTariffConfiguredError` naming the leg, because §24.16 renders the
    message against a specific pickup and "no tariff configured" without a
    route is not something a tourist can act on or support can diagnose.
    """
    if not legs:
        return ()

    classes = list(repo.live_vehicle_classes())
    class_ids = [row.pk for row in classes]
    by_code = {row.code: row for row in classes}

    pairs = [
        (leg.origin.destination_id, leg.target.destination_id)
        for leg in legs
        if leg.origin.destination_id is not None and leg.target.destination_id is not None
    ]
    region_ids = sorted({leg.origin.region_id for leg in legs if leg.origin.region_id is not None})
    country_ids = sorted(
        {leg.origin.country_id for leg in legs if leg.origin.country_id is not None}
    )

    corridor_rows = repo.corridors_between(pairs, class_ids=class_ids)
    tariff_rows = repo.tariffs_for(
        region_ids=region_ids, country_ids=country_ids, class_ids=class_ids
    )

    corridor_rules = [_corridor_rule(row) for row in corridor_rows]
    tariff_rules = [_metered_rule(row) for row in tariff_rows]
    corridor_public = {row.pk: row.public_id for row in corridor_rows}
    tariff_public = {row.pk: row.public_id for row in tariff_rows}

    return tuple(
        _quote_one(
            leg,
            classes=classes,
            by_code=by_code,
            corridor_rules=corridor_rules,
            tariff_rules=tariff_rules,
            corridor_public=corridor_public,
            tariff_public=tariff_public,
        )
        for leg in legs
    )


def _quote_one(
    leg: LegRequest,
    *,
    classes: Sequence[VehicleClass],
    by_code: dict[str, VehicleClass],
    corridor_rules: Sequence[domain.CorridorRule],
    tariff_rules: Sequence[domain.MeteredRule],
    corridor_public: Mapping[int, UUID],
    tariff_public: Mapping[int, UUID],
) -> LegQuote:
    fitting = domain.eligible(
        (_class_spec(row) for row in classes), pax=leg.pax, luggage=leg.luggage
    )

    options: list[FareOption] = []
    metered_was_wanted = False

    for spec in fitting:
        found = domain.resolve(
            corridors=corridor_rules,
            tariffs=tariff_rules,
            origin=domain.Endpoint(
                destination_id=leg.origin.destination_id,
                region_id=leg.origin.region_id,
                country_id=leg.origin.country_id,
                is_gateway=leg.origin.is_gateway,
            ),
            target=domain.Endpoint(
                destination_id=leg.target.destination_id,
                region_id=leg.target.region_id,
                country_id=leg.target.country_id,
                is_gateway=leg.target.is_gateway,
            ),
            vehicle_class=spec.code,
            on=leg.on,
        )
        if found is None:
            continue

        rule, match = found

        # §12.6, and the reason it is checked here rather than at the door: a
        # corridor is quotable during a routing outage and a metered rule is
        # not, so the answer depends on which rung answered.
        if isinstance(rule, domain.MeteredRule) and (
            leg.distance_m is None or leg.travel_seconds is None
        ):
            metered_was_wanted = True
            continue

        fare = domain.price(
            rule,
            match,
            distance_m=leg.distance_m,
            travel_seconds=leg.travel_seconds,
            pickup_local=leg.pickup_local,
            is_airport=leg.touches_a_gateway,
        )
        options.append(
            FareOption(
                vehicle_class=_class_dto(by_code[spec.code]),
                price=fare.total,
                breakdown=FareBreakdown(
                    base=fare.base.amount,
                    distance=fare.distance.amount,
                    time=fare.time.amount,
                    surcharges=fare.surcharges.amount,
                ),
                match=TariffMatchDTO(
                    kind=match.kind,
                    rule_id=match.rule_id,
                    rule_public_id=(
                        corridor_public[match.rule_id]
                        if match.kind is domain.MatchKind.CORRIDOR
                        else tariff_public[match.rule_id]
                    ),
                    step=match.step,
                ),
            )
        )

    if options:
        return LegQuote(
            reference=leg.reference,
            distance_m=leg.distance_m,
            travel_seconds=leg.travel_seconds,
            options=tuple(options),
        )

    # Nothing priced. Which of the two failures it was matters to the tourist:
    # §24.16 shows a retry for one and "contact support" for the other, and
    # collapsing them would offer a retry that can never succeed.
    if metered_was_wanted:
        raise RoutingUnavailableError(
            f"{leg.origin.label} to {leg.target.label} is priced by distance, and no "
            "measured route is available. §12.6 forbids estimating one for a price."
        )
    if not fitting:
        # Not a configuration problem. The party is larger than anything the
        # platform runs, which is a fact about the party and reads as one.
        return LegQuote(
            reference=leg.reference,
            distance_m=leg.distance_m,
            travel_seconds=leg.travel_seconds,
            options=(),
        )
    raise NoTariffConfiguredError(
        f"No tariff is configured for {leg.origin.label} to {leg.target.label}."
    )


def preview(leg: LegRequest) -> LegQuote:
    """§27.11's quote-preview tool: "for any origin-destination-class
    combination".

    The same code path as a tourist quote, on purpose. A preview computed by a
    second implementation would eventually disagree with the real one, and the
    disagreement would surface as an administrator insisting a price is right
    while a tourist is being charged something else.

    What it adds is not arithmetic but provenance: every option carries the
    rule that priced it and the rung it answered on, so a fare that fell
    through to the country default when a corridor was expected is visible
    rather than merely correct-looking.
    """
    return quote_transfer([leg])[0]


def today_in(zone: dt.tzinfo) -> dt.date:
    """The date the effective-date filter is evaluated against.

    A helper rather than an inline `date.today()`, because "today" at the
    server is not "today" at the destination for three hours out of every
    twenty-four, and a tariff that starts tomorrow would begin pricing this
    evening in one of them.
    """
    return dt.datetime.now(tz=zone).date()
