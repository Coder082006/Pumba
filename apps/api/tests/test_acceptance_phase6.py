"""§37.6 and §41.6's acceptance criteria, as tests.

    §37.6: "A quote for every seeded corridor resolves deterministically; an
    unconfigured corridor returns NO_TARIFF_CONFIGURED rather than a guessed
    price; the worked example in Section 12.4 reproduces exactly."

    §41.6: "Every seeded corridor quotes deterministically with the correct
    distance, duration and price, and the worked example in Section 12.4
    reproduces exactly. Vehicle classes are filtered by capacity. An
    unconfigured corridor returns NO_TARIFF_CONFIGURED and never a guessed
    price. Routing outages degrade planning to cached data with an explicit
    'approximate' label and block priced quoting rather than inventing a price."

Six claims, and each is asserted against **the committed seed files** rather
than against fixtures — for the reason `tests/test_seed.py` gives about the
catalogue: a criterion proved on invented rows proves the code and says nothing
about whether the platform ships able to price a transfer.

The worked example is the exception and is deliberately a unit test. §12.4
gives it in USD and the seed prices in TZS, because `country.default_currency`
is TZS and VR-10 requires a trip's items to agree with its own currency
(ADR 0023). Reproducing it therefore belongs where no trip currency applies,
which is `apps/transport/tests/test_domain_tariffs.py`; this file asserts that
it is still asserted, so deleting it there fails here.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.catalogue import services as catalogue
from apps.transport import services as transport
from apps.transport.dto import LegEndpoint, LegRequest
from apps.transport.models import TransferCorridor, TransferTariff, VehicleClass

pytestmark = pytest.mark.django_db

#: Naive local time in the destination's zone — §12.4's "pickup_at local time".
PICKUP_LOCAL = dt.datetime(2027, 8, 10, 14, 45)  # noqa: DTZ001
DEPART_AT = dt.datetime(2027, 8, 10, 11, 45, tzinfo=dt.UTC)


@pytest.fixture
def seeded() -> None:
    """The committed seed files, loaded into this test's transaction.

    Per test rather than per class, and slower for it. A class-scoped fixture
    would have to write outside the per-test transaction to be shared, and the
    rows would then survive into every test that ran afterwards — including the
    two below that delete every corridor to prove a refusal. A seed set that
    leaked would make those deletions permanent and the failure would appear in
    an unrelated file.
    """
    call_command("seed")


def _endpoint(slug: str) -> LegEndpoint:
    ref = catalogue.resolve_planning_ref(slug, today=dt.date(2026, 1, 2))
    assert ref is not None, slug
    place = catalogue.transfer_places("destination", [ref.storage_id])[ref.storage_id]
    return LegEndpoint(
        label=place.destination_name,
        destination_id=place.destination_id,
        region_id=place.region_id,
        country_id=place.country_id,
        is_gateway=place.is_gateway,
    )


def _leg(origin: LegEndpoint, target: LegEndpoint, **overrides: object) -> LegRequest:
    values: dict[str, object] = {
        "reference": "leg",
        "origin": origin,
        "target": target,
        "depart_at": DEPART_AT,
        "pickup_local": PICKUP_LOCAL,
        "pax": 2,
        "luggage": 2,
        # No measured route exists until Appendix D-2, and a corridor needs
        # none. That is the point of the arrangement, not a gap in the test.
        "distance_m": None,
        "travel_seconds": None,
    }
    values.update(overrides)
    return LegRequest(**values)  # type: ignore[arg-type]


@pytest.mark.usefixtures("seeded")
class TestEverySeededCorridorQuotes:
    """§41.6's first sentence, over the whole shipped set rather than a sample."""

    def _pairs(self) -> list[tuple[int, int, str]]:
        return [
            (row.origin_destination_id, row.target_destination_id, row.vehicle_class.code)
            for row in TransferCorridor.objects.select_related("vehicle_class")
        ]

    def test_the_seed_is_not_empty(self) -> None:
        """The control that makes every other assertion here mean something. A
        loop over an empty queryset passes silently, which is how a seed that
        stopped loading would go unnoticed for a phase."""
        assert TransferCorridor.objects.count() >= 100
        assert TransferTariff.objects.count() == 4
        assert VehicleClass.objects.count() == 4

    def test_every_corridor_prices_in_both_directions(self) -> None:
        """Both, because every seeded corridor is bidirectional and §12.4 step 2
        is what serves the return leg. A set that priced outbound and not back
        would strand every tourist on the day they fly home.

        Every leg goes in one call, which is what `quote_transfer` is built for
        and is also the strongest form of this assertion: three queries answer
        for the whole seeded network, so a regression that made the ladder
        per-leg would show up here as a timeout rather than as a slow test
        nobody looked at.
        """
        pairs = self._pairs()
        assert pairs

        wanted = sorted({p[0] for p in pairs} | {p[1] for p in pairs})
        places = catalogue.transfer_places("destination", wanted)

        legs = []
        expected: dict[str, str] = {}
        for index, (origin_id, target_id, code) in enumerate(pairs):
            for direction, (a, b) in enumerate(((origin_id, target_id), (target_id, origin_id))):
                reference = f"{index}-{direction}"
                expected[reference] = code
                legs.append(_leg(_place(places, a), _place(places, b), reference=reference))

        quotes = transport.quote_transfer(legs)
        unpriced = [
            f"{quote.reference} ({expected[quote.reference]})"
            for quote in quotes
            if not any(o.vehicle_class.code == expected[quote.reference] for o in quote.options)
        ]
        assert unpriced == [], unpriced

    def test_the_same_leg_prices_the_same_twice(self) -> None:
        """TC-902's determinism, on the tariff path. A ladder whose answer
        depended on the order a queryset happened to return would pass every
        other test here and fail this one intermittently — which is the worst
        way for it to fail."""
        znz, nungwi = _endpoint("znz-airport"), _endpoint("nungwi")
        first = transport.quote_transfer([_leg(znz, nungwi)])[0]
        second = transport.quote_transfer([_leg(znz, nungwi)])[0]
        assert [(o.vehicle_class.code, o.price.amount, o.match.step) for o in first.options] == [
            (o.vehicle_class.code, o.price.amount, o.match.step) for o in second.options
        ]

    def test_a_gateway_leg_answers_on_step_one(self) -> None:
        """The seed writes a gateway as the origin so §9.3.4's route map reads
        the way a tourist expects. The ladder must then answer on step 1 for the
        arrival transfer, which is the most-travelled leg in the catalogue."""
        quote = transport.quote_transfer([_leg(_endpoint("znz-airport"), _endpoint("nungwi"))])[0]
        assert quote.options
        assert all(option.match.step == 1 for option in quote.options)

    def test_the_price_is_in_the_destinations_currency(self) -> None:
        """VR-10: a trip's items must all resolve to one currency, and §4.2
        takes it from the destination. A corridor priced in USD would be
        unquotable inside a TZS trip and the failure would surface as a
        `PricingError` three modules away."""
        quote = transport.quote_transfer([_leg(_endpoint("znz-airport"), _endpoint("nungwi"))])[0]
        assert {option.price.currency for option in quote.options} == {"TZS"}


def _place(places: dict[int, catalogue.TransferPlace], destination_id: int) -> LegEndpoint:
    place = places[destination_id]
    return LegEndpoint(
        label=place.destination_name,
        destination_id=place.destination_id,
        region_id=place.region_id,
        country_id=place.country_id,
        is_gateway=place.is_gateway,
    )


@pytest.mark.usefixtures("seeded")
class TestVehicleClassesAreFilteredByCapacity:
    """§41.6's third sentence. §12.4: "the quote returns only classes whose
    seats >= pax and luggage >= luggage_count"."""

    def test_a_couple_may_have_any_class(self) -> None:
        quote = transport.quote_transfer(
            [_leg(_endpoint("znz-airport"), _endpoint("nungwi"), pax=2, luggage=2)]
        )[0]
        assert {o.vehicle_class.code for o in quote.options} == {
            "STANDARD",
            "COMFORT",
            "VAN",
            "MINIBUS",
        }

    def test_five_travellers_rule_out_the_four_seaters(self) -> None:
        quote = transport.quote_transfer(
            [_leg(_endpoint("znz-airport"), _endpoint("nungwi"), pax=5, luggage=2)]
        )[0]
        assert {o.vehicle_class.code for o in quote.options} == {"VAN", "MINIBUS"}

    def test_luggage_counts_as_well_as_people(self) -> None:
        """Two travellers with seven cases do not fit in a car, and a filter
        that read only the seat count would sell them one."""
        quote = transport.quote_transfer(
            [_leg(_endpoint("znz-airport"), _endpoint("nungwi"), pax=2, luggage=7)]
        )[0]
        assert {o.vehicle_class.code for o in quote.options} == {"MINIBUS"}

    def test_a_party_larger_than_anything_gets_no_options(self) -> None:
        quote = transport.quote_transfer(
            [_leg(_endpoint("znz-airport"), _endpoint("nungwi"), pax=30, luggage=0)]
        )[0]
        assert quote.options == ()


@pytest.mark.usefixtures("seeded")
class TestAnUnconfiguredRouteNeverGuesses:
    """§41.6's fourth sentence, and §12.4's "never guess a price"."""

    def test_it_raises_rather_than_returning_a_number(self) -> None:
        TransferCorridor.all_objects.all().delete()
        TransferTariff.all_objects.all().delete()
        with pytest.raises(transport.NoTariffConfiguredError) as caught:
            transport.quote_transfer([_leg(_endpoint("znz-airport"), _endpoint("nungwi"))])
        assert caught.value.status_code == 422
        assert caught.value.code == "NO_TARIFF_CONFIGURED"

    def test_a_metered_route_with_no_measured_road_refuses_too(self) -> None:
        """§41.6's last sentence: "routing outages ... block priced quoting
        rather than inventing a price". Every distance is a haversine estimate
        until D-2, so a leg with only a metered tariff has nothing lawful to
        charge for."""
        TransferCorridor.all_objects.all().delete()
        with pytest.raises(transport.RoutingUnavailableError) as caught:
            transport.quote_transfer([_leg(_endpoint("znz-airport"), _endpoint("nungwi"))])
        assert caught.value.status_code == 502
        assert caught.value.retryable is True


class TestTheWorkedExampleIsStillAsserted:
    """§37.6 and §41.6 both name it, and it is proved next door.

    A pointer rather than a copy: two implementations of one arithmetic check
    would eventually disagree, and the one that was wrong would be whichever
    nobody was reading. What this asserts is that the assertion exists — so
    deleting it there fails here, which is the only failure mode a pointer has.
    """

    EXPECTED = "38.00"
    HOME = Path(__file__).resolve().parents[1] / "apps/transport/tests/test_domain_tariffs.py"

    def test_the_domain_test_file_exists(self) -> None:
        assert self.HOME.is_file(), f"§12.4's worked example was asserted in {self.HOME}"

    def test_it_still_asserts_thirty_eight_dollars(self) -> None:
        source = self.HOME.read_text(encoding="utf-8")
        assert "TestTheWorkedExample" in source
        assert self.EXPECTED in source
        assert "57_400" in source, "the example's distance is 57.4 km (§9.4.4: 57400 m)"

    def test_the_arithmetic_here_agrees_with_it(self) -> None:
        """The one line of duplication worth having: if §12.4's example is ever
        amended, this fails beside the other file rather than after it."""
        base = Decimal("12.00")
        distance = Decimal("0.40") * Decimal("57.4")
        surcharge = Decimal("3.04")
        assert base + distance + surcharge == Decimal(self.EXPECTED)
