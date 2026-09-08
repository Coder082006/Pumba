"""`quote_transfer` — SRS §9.4.4, §12.4, §12.6.

The domain tests next door prove the arithmetic. This file proves the three
things only the service layer can get wrong.

**Which failure a tourist is told about.** §24.16 renders `NO_TARIFF_CONFIGURED`
as "transfers to this location are not yet available — contact support" and a
routing outage as a retry. Collapsing the two would offer a retry that can
never succeed, or send somebody to support over a transient outage.

**That §12.6's refusal happens after the ladder, not before it.** A corridor
has a fixed price and needs no distance, so an outage must not stop an airport
transfer being quoted; a metered rule must refuse. Whether a missing distance
matters is exactly what the ladder decides, so a guard at the door would be
wrong in the direction that looks safe.

**That the query count does not grow.** `trip.services.generate_itinerary`
prices every transfer in an itinerary at once and pins its query budget. A
per-leg query here would fail a test in another module for a reason nobody
would look for here.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.transport import services
from apps.transport.domain.tariffs import MatchKind
from apps.transport.dto import LegEndpoint, LegRequest
from apps.transport.models import TariffScope, TransferCorridor, TransferTariff, VehicleClass

pytestmark = pytest.mark.django_db

#: Stand-ins for `catalogue` rows this module may not see (ADR 0012).
ZNZ_ID, NUNGWI_ID, PAJE_ID = 1, 2, 3
NORTH_REGION, EAST_REGION, TANZANIA = 20, 30, 100

# Naive local time in the destination's zone — §12.4 evaluates the night
# window against "pickup_at local time". See `dto.LegRequest`.
PICKUP_LOCAL = dt.datetime(2027, 8, 10, 14, 45)  # noqa: DTZ001
DEPART_AT = dt.datetime(2027, 8, 10, 11, 45, tzinfo=dt.UTC)

ZNZ = LegEndpoint(
    label="Zanzibar Airport",
    destination_id=ZNZ_ID,
    region_id=NORTH_REGION,
    country_id=TANZANIA,
    is_gateway=True,
)
NUNGWI = LegEndpoint(
    label="Nungwi", destination_id=NUNGWI_ID, region_id=NORTH_REGION, country_id=TANZANIA
)
PAJE = LegEndpoint(label="Paje", destination_id=PAJE_ID, region_id=EAST_REGION, country_id=TANZANIA)


def _classes() -> dict[str, VehicleClass]:
    spec = [
        ("STANDARD", 4, 3, 1),
        ("COMFORT", 4, 3, 2),
        ("VAN", 7, 6, 3),
        ("MINIBUS", 14, 12, 4),
    ]
    return {
        code: VehicleClass.objects.create(
            code=code,
            name=code.title(),
            seats=seats,
            luggage_capacity=bags,
            display_order=order,
        )
        for code, seats, bags, order in spec
    }


def _leg(**overrides: object) -> LegRequest:
    values: dict[str, object] = {
        "reference": "leg-1",
        "origin": ZNZ,
        "target": NUNGWI,
        "depart_at": DEPART_AT,
        "pickup_local": PICKUP_LOCAL,
        "pax": 2,
        "luggage": 2,
        "distance_m": 57_400,
        "travel_seconds": 5_100,
    }
    values.update(overrides)
    return LegRequest(**values)  # type: ignore[arg-type]


def _corridor(klass: VehicleClass, price: str, **overrides: object) -> TransferCorridor:
    values: dict[str, object] = {
        "origin_destination_id": ZNZ_ID,
        "target_destination_id": NUNGWI_ID,
        "vehicle_class": klass,
        "fixed_price": Decimal(price),
        "currency": "TZS",
        "valid_from": dt.date(2027, 1, 1),
    }
    values.update(overrides)
    return TransferCorridor.objects.create(**values)


def _tariff(klass: VehicleClass, **overrides: object) -> TransferTariff:
    values: dict[str, object] = {
        "scope": TariffScope.REGION,
        "region_id": NORTH_REGION,
        "vehicle_class": klass,
        "base_fare": Decimal("12.00"),
        "per_km_rate": Decimal("0.4000"),
        "airport_surcharge": Decimal("3.04"),
        "currency": "USD",
        "valid_from": dt.date(2027, 1, 1),
    }
    values.update(overrides)
    return TransferTariff.objects.create(**values)


class TestACorridorPricesTheLeg:
    def test_every_fitting_class_with_a_corridor_gets_an_option(self) -> None:
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        _corridor(classes["VAN"], "130000.00")

        (quote,) = services.quote_transfer([_leg()])

        assert [o.vehicle_class.code for o in quote.options] == ["STANDARD", "VAN"]
        assert [o.price.amount for o in quote.options] == [
            Decimal("90000.00"),
            Decimal("130000.00"),
        ]

    def test_a_class_with_no_corridor_is_simply_absent(self) -> None:
        """Not an error. A platform that runs STANDARD on this route and not
        MINIBUS has a shorter list of cards, not a broken quote."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        (quote,) = services.quote_transfer([_leg()])
        assert [o.vehicle_class.code for o in quote.options] == ["STANDARD"]

    def test_the_option_names_the_rule_that_priced_it(self) -> None:
        """§12.4 stores "the matched rule id ... so the price is reproducible
        forever", and Phase 7 snapshots this onto `booking_transfer`."""
        classes = _classes()
        row = _corridor(classes["STANDARD"], "90000.00")
        (quote,) = services.quote_transfer([_leg()])
        (option,) = quote.options
        assert option.match.kind is MatchKind.CORRIDOR
        assert option.match.rule_id == row.pk
        assert option.match.rule_public_id == row.public_id
        assert option.match.step == 1

    def test_the_reverse_direction_answers_on_step_two(self) -> None:
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00", is_bidirectional=True)
        (quote,) = services.quote_transfer([_leg(origin=NUNGWI, target=ZNZ)])
        assert quote.options[0].match.step == 2

    def test_a_corridor_price_carries_no_surcharge(self) -> None:
        """A fixed corridor price is the whole price. Adding the airport
        surcharge on top would charge for the airport twice — once inside the
        negotiated fare and once beside it."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        (quote,) = services.quote_transfer([_leg()])
        (option,) = quote.options
        assert option.breakdown.surcharges == Decimal("0.00")
        assert option.breakdown.base == Decimal("90000.00")


class TestTheMeteredFallback:
    def test_it_answers_when_no_corridor_does(self) -> None:
        classes = _classes()
        _tariff(classes["STANDARD"])
        (quote,) = services.quote_transfer([_leg()])
        (option,) = quote.options
        assert option.match.kind is MatchKind.TARIFF
        assert option.match.step == 3
        # §12.4's worked example, reached through the service layer.
        assert option.price.amount == Decimal("38.00")

    def test_a_corridor_beats_it(self) -> None:
        """The control for the ladder's order at the service layer. Both are
        configured; step 1 must win."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        _tariff(classes["STANDARD"])
        (quote,) = services.quote_transfer([_leg()])
        assert quote.options[0].match.step == 1


class TestWhatHappensWhenNothingPrices:
    def test_nothing_configured_raises_and_names_the_route(self) -> None:
        """§12.4: "No match -> 422 NO_TARIFF_CONFIGURED (never guess a price)".

        The route is in the message because §24.16 renders it against a
        specific pickup, and "no tariff configured" without one is not
        something a tourist can act on or support can diagnose.
        """
        _classes()
        with pytest.raises(services.NoTariffConfiguredError) as caught:
            services.quote_transfer([_leg()])
        assert caught.value.status_code == 422
        assert caught.value.code == "NO_TARIFF_CONFIGURED"
        assert "Zanzibar Airport" in str(caught.value)
        assert "Nungwi" in str(caught.value)

    def test_a_party_too_large_for_anything_is_not_an_error(self) -> None:
        """A fact about the party, not about the configuration. The two look
        identical on a screen and mean opposite things."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        (quote,) = services.quote_transfer([_leg(pax=20)])
        assert quote.options == ()

    def test_luggage_alone_can_empty_the_list(self) -> None:
        """Two passengers with thirteen suitcases fit nothing the platform
        runs, and that is a fact about the luggage rather than about the
        tariff table."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        (quote,) = services.quote_transfer([_leg(pax=2, luggage=13)])
        assert quote.options == ()

    def test_a_class_that_fits_but_has_no_fare_is_a_configuration_gap(self) -> None:
        """Nine bags fit a MINIBUS, so the party is servable — but no MINIBUS
        fare is configured on this route, and nothing else fits. That is
        `NO_TARIFF_CONFIGURED`, not an empty list: the difference is whether
        support has something to fix."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        with pytest.raises(services.NoTariffConfiguredError):
            services.quote_transfer([_leg(pax=2, luggage=9)])


class TestSection126:
    def test_a_metered_leg_with_no_measured_route_is_refused(self) -> None:
        """§12.6: "Haversine fallback is not permitted for a priced corridor
        without a fixed corridor price; the endpoint returns 502
        ROUTING_UNAVAILABLE rather than commit the platform to a guessed
        price"."""
        classes = _classes()
        _tariff(classes["STANDARD"])
        with pytest.raises(services.RoutingUnavailableError) as caught:
            services.quote_transfer([_leg(distance_m=None, travel_seconds=None)])
        assert caught.value.status_code == 502
        assert caught.value.code == "ROUTING_UNAVAILABLE"
        assert caught.value.retryable is True

    def test_a_corridor_still_prices_during_a_routing_outage(self) -> None:
        """The assertion the whole placement of the check exists for.

        §12.6 permits cache and matrix for quoting and forbids only the
        haversine fallback "for a priced corridor **without a fixed corridor
        price**". A guard at the door would refuse this leg, which is the
        airport transfer §24.16 exists to sell, and would look like caution.
        """
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        (quote,) = services.quote_transfer([_leg(distance_m=None, travel_seconds=None)])
        assert quote.options[0].price.amount == Decimal("90000.00")
        assert quote.distance_m is None

    def test_a_corridor_class_is_offered_while_a_metered_one_is_dropped(self) -> None:
        """Mixed on one leg: STANDARD has a corridor, VAN only a tariff, and
        the road cannot be measured.

        The tourist is offered what we can price rather than nothing. Refusing
        the whole leg would withhold an airport transfer we have a fixed price
        for, which §12.6 explicitly permits quoting during an outage.
        """
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        _tariff(classes["VAN"])
        (quote,) = services.quote_transfer([_leg(distance_m=None, travel_seconds=None)])
        assert [o.vehicle_class.code for o in quote.options] == ["STANDARD"]

    def test_the_refusal_arrives_when_nothing_else_could_be_priced(self) -> None:
        """The other half of the rule. With only a metered VAN configured and a
        party that needs one, there is nothing to fall back to and the honest
        answer is the outage rather than an empty list — an empty list reads as
        "no vehicle exists for you", which is a different and untrue claim."""
        classes = _classes()
        _tariff(classes["VAN"])
        with pytest.raises(services.RoutingUnavailableError):
            services.quote_transfer([_leg(pax=5, distance_m=None, travel_seconds=None)])

    def test_only_the_metered_path_refuses(self) -> None:
        """The control: with a distance, the same leg prices."""
        classes = _classes()
        _tariff(classes["VAN"])
        (quote,) = services.quote_transfer([_leg(pax=5)])
        assert quote.options[0].vehicle_class.code == "VAN"


class TestTheQueryBudget:
    def test_it_does_not_grow_with_the_number_of_legs(self, django_assert_num_queries) -> None:  # type: ignore[no-untyped-def]
        """Three reads: the classes, the corridors, the tariffs. One itinerary
        with eight transfers must cost the same as one with one."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        _corridor(
            classes["STANDARD"],
            "70000.00",
            origin_destination_id=NUNGWI_ID,
            target_destination_id=PAJE_ID,
        )
        legs = [_leg(reference=f"leg-{n}", origin=NUNGWI, target=PAJE) for n in range(8)] + [_leg()]

        with django_assert_num_queries(3):
            quotes = services.quote_transfer(legs)

        assert len(quotes) == 9
        assert all(q.options for q in quotes)

    def test_no_legs_is_no_queries(self, django_assert_num_queries) -> None:  # type: ignore[no-untyped-def]
        with django_assert_num_queries(0):
            assert services.quote_transfer([]) == ()


class TestThePublicReads:
    def test_vehicle_classes_publish_capacity_and_no_price(self) -> None:
        """§9.3.4 calls it "capacity and indicative pricing"; the price is not
        served, because a class costs what the corridor costs and a number
        attached to a class would be a quote nobody could be held to."""
        _classes()
        published = services.vehicle_classes()
        assert [c.code for c in published] == ["STANDARD", "COMFORT", "VAN", "MINIBUS"]
        assert not any(hasattr(c, "price") for c in published)

    def test_a_withdrawn_class_is_not_published(self) -> None:
        classes = _classes()
        classes["MINIBUS"].is_active = False
        classes["MINIBUS"].save(update_fields=["is_active"])
        assert "MINIBUS" not in [c.code for c in services.vehicle_classes()]

    def test_corridors_publish_the_route_and_not_the_fare(self) -> None:
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        (published,) = services.corridors()
        assert published.origin_destination_id == ZNZ_ID
        assert published.vehicle_class_code == "STANDARD"
        assert not hasattr(published, "fixed_price")


class TestThePreviewToolUsesTheSamePath:
    def test_it_returns_the_same_answer_as_a_tourist_quote(self) -> None:
        """§27.11's tool. A second implementation would eventually disagree
        with the first, and the disagreement would surface as an administrator
        insisting a price is right while a tourist is charged something else."""
        classes = _classes()
        _corridor(classes["STANDARD"], "90000.00")
        leg = _leg()
        assert services.preview(leg) == services.quote_transfer([leg])[0]

    def test_it_shows_which_rung_answered(self) -> None:
        classes = _classes()
        _tariff(classes["STANDARD"])
        assert services.preview(_leg()).options[0].match.step == 3
