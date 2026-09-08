"""§12.4's ladder and its metered computation — pure, no database.

**The centrepiece is `TestTheWorkedExample`.** §37.6's acceptance clause and
§41.6 both require that "the worked example in Section 12.4 reproduces
exactly", and this is where that is asserted — on the arithmetic itself, with
no fixtures, no routing, no network and nothing that could make it flaky. If it
comes out 37.99 the phase has failed, and this test is what says so.

Everything else here exists because §12.4 is a sequence of five lines whose
*order* is the specification. Applying the minimum fare after the surcharges,
or rounding the distance component before adding the time component, produces
numbers that look like rounding disagreements and are not.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.common.money import Money
from apps.transport.domain.tariffs import (
    CorridorRule,
    Endpoint,
    Fare,
    MatchKind,
    MeteredRule,
    TariffError,
    TariffMatch,
    VehicleClassSpec,
    cheapest_fitting,
    eligible,
    is_night,
    metered,
    price,
    resolve,
)

TODAY = dt.date(2027, 8, 10)

#: **Naive on purpose, and DTZ001 is silenced rather than satisfied.** §12.4
#: evaluates the night window against "pickup_at local time", so this is local
#: wall-clock time in the destination's zone. Attaching a tzinfo here would
#: make it a different instant and would let a UTC-tagged noon be compared
#: against a Zanzibar surcharge window — the exact error `pickup_local` exists
#: to prevent. The aware instant lives on `LegRequest.depart_at`.
NOON = dt.datetime(2027, 8, 10, 12, 0)  # noqa: DTZ001
MIDNIGHT = dt.datetime(2027, 8, 10, 0, 30)  # noqa: DTZ001

#: The four §12.4 classes. STANDARD and COMFORT differ by air conditioning
#: alone — both carry four passengers and three bags.
STANDARD = VehicleClassSpec(code="STANDARD", seats=4, luggage_capacity=3, display_order=1)
COMFORT = VehicleClassSpec(code="COMFORT", seats=4, luggage_capacity=3, display_order=2)
VAN = VehicleClassSpec(code="VAN", seats=7, luggage_capacity=6, display_order=3)
MINIBUS = VehicleClassSpec(code="MINIBUS", seats=14, luggage_capacity=12, display_order=4)

ZNZ = Endpoint(destination_id=1, region_id=10, country_id=100, is_gateway=True)
NUNGWI = Endpoint(destination_id=2, region_id=20, country_id=100)
PAJE = Endpoint(destination_id=3, region_id=30, country_id=100)
CUSTOM_PIN = Endpoint(destination_id=None, region_id=None, country_id=None)


def _corridor(**overrides: object) -> CorridorRule:
    values: dict[str, object] = {
        "rule_id": 1,
        "vehicle_class": "STANDARD",
        "currency": "USD",
        "origin_destination_id": 1,
        "target_destination_id": 2,
        "fixed_price": Decimal("38.00"),
        "valid_from": dt.date(2027, 1, 1),
    }
    values.update(overrides)
    return CorridorRule(**values)  # type: ignore[arg-type]


def _metered(**overrides: object) -> MeteredRule:
    """§12.4's worked-example tariff: base 12.00, 0.40/km, nothing per minute."""
    values: dict[str, object] = {
        "rule_id": 100,
        "vehicle_class": "STANDARD",
        "currency": "USD",
        "region_id": 20,
        "base_fare": Decimal("12.00"),
        "per_km_rate": Decimal("0.4000"),
        "per_minute_rate": Decimal("0.0000"),
        "airport_surcharge": Decimal("3.04"),
        "valid_from": dt.date(2027, 1, 1),
    }
    values.update(overrides)
    return MeteredRule(**values)  # type: ignore[arg-type]


MATCH = TariffMatch(kind=MatchKind.TARIFF, rule_id=100, step=3)


class TestTheWorkedExample:
    """§12.4, verbatim:

        Worked example (ZNZ -> Nungwi, STANDARD, daytime, no corridor row):
        base 12.00 + 0.40 x 57.4 (= 22.96) + 0.00 x 85 = 34.96; night
        surcharge none; airport surcharge 3.04 -> USD 38.00.

    §9.4.4's response body gives the same leg as `distance_m: 57400`,
    `travel_seconds: 5100` (85 minutes) with the breakdown
    `{"base":"12.00","distance":"22.96","time":"0.00","surcharges":"3.04"}`.
    The two agree, and both are asserted.
    """

    def _fare(self) -> object:
        return metered(
            _metered(),
            distance_m=57_400,
            travel_seconds=5_100,
            pickup_local=NOON,
            is_airport=True,
            match=MATCH,
        )

    def test_the_total_is_thirty_eight_dollars_exactly(self) -> None:
        fare = self._fare()
        assert fare.total.amount == Decimal("38.00")  # type: ignore[attr-defined]
        assert fare.total.currency == "USD"  # type: ignore[attr-defined]

    def test_the_breakdown_is_the_one_in_section_9_4_4(self) -> None:
        fare = self._fare()
        assert fare.base.amount == Decimal("12.00")  # type: ignore[attr-defined]
        assert fare.distance.amount == Decimal("22.96")  # type: ignore[attr-defined]
        assert fare.time.amount == Decimal("0.00")  # type: ignore[attr-defined]
        assert fare.surcharges.amount == Decimal("3.04")  # type: ignore[attr-defined]

    def test_the_breakdown_sums_to_the_total(self) -> None:
        """A breakdown that did not add up would be the first thing a tourist
        queried, and `Fare.__post_init__` refuses to construct one — so this
        asserts the guard as well as the arithmetic."""
        fare = self._fare()
        parts = fare.base + fare.distance + fare.time + fare.surcharges  # type: ignore[attr-defined]
        assert parts == fare.total  # type: ignore[attr-defined]

    def test_without_the_airport_surcharge_it_is_thirty_four_ninety_six(self) -> None:
        """The intermediate §12.4 states as `= 34.96`. Asserting it separately
        localises a failure: a wrong total with a right subtotal is a surcharge
        bug, and the two are fixed in different places."""
        fare = metered(
            _metered(),
            distance_m=57_400,
            travel_seconds=5_100,
            pickup_local=NOON,
            is_airport=False,
            match=MATCH,
        )
        assert fare.total.amount == Decimal("34.96")


class TestTheOrderOfTheFiveLines:
    def test_the_minimum_fare_floors_the_metered_sum(self) -> None:
        rule = _metered(minimum_fare=Decimal("25.00"), airport_surcharge=Decimal("0"))
        fare = metered(
            rule,
            distance_m=1_000,
            travel_seconds=120,
            pickup_local=NOON,
            is_airport=False,
            match=MATCH,
        )
        # 12.00 + 0.40 = 12.40, floored to 25.00.
        assert fare.total.amount == Decimal("25.00")

    def test_the_floor_is_applied_before_the_surcharges_not_after(self) -> None:
        """The assertion that pins §12.4's order.

        Floor first: max(12.40, 25.00) = 25.00, then +3.04 = 28.04.
        Surcharge first: 12.40 + 3.04 = 15.44, then floored = 25.00.

        Both are defensible readings of a carelessly written spec and they
        differ by three dollars on exactly the short airport hop a tourist is
        most likely to book.
        """
        fare = metered(
            _metered(minimum_fare=Decimal("25.00")),
            distance_m=1_000,
            travel_seconds=120,
            pickup_local=NOON,
            is_airport=True,
            match=MATCH,
        )
        assert fare.total.amount == Decimal("28.04")

    def test_the_night_surcharge_multiplies_before_the_airport_one_adds(self) -> None:
        """25% of 34.96 is 43.70, then +3.04 = 46.74. Adding the airport
        surcharge first would multiply it too, and charge 25% more for the
        airport itself."""
        rule = _metered(
            night_surcharge_pct=Decimal("25.00"),
            night_from=dt.time(22, 0),
            night_to=dt.time(6, 0),
        )
        fare = metered(
            rule,
            distance_m=57_400,
            travel_seconds=5_100,
            pickup_local=MIDNIGHT,
            is_airport=True,
            match=MATCH,
        )
        assert fare.total.amount == Decimal("46.74")

    def test_rounding_happens_once_at_the_end(self) -> None:
        """Two components that each round up would add a cent that the
        unrounded sum does not have. 0.005 + 0.005 rounds to 0.01, not 0.02."""
        rule = _metered(
            base_fare=Decimal("0.00"),
            per_km_rate=Decimal("0.0050"),
            per_minute_rate=Decimal("0.0050"),
            airport_surcharge=Decimal("0"),
        )
        fare = metered(
            rule,
            distance_m=1_000,
            travel_seconds=60,
            pickup_local=NOON,
            is_airport=False,
            match=MATCH,
        )
        assert fare.total.amount == Decimal("0.01")

    def test_the_per_minute_component_is_charged(self) -> None:
        """The control for `time`: every assertion above uses §12.4's example,
        whose per-minute rate is zero, so without this the whole term could be
        missing and nothing would notice."""
        fare = metered(
            _metered(per_minute_rate=Decimal("0.5000"), airport_surcharge=Decimal("0")),
            distance_m=0,
            travel_seconds=600,
            pickup_local=NOON,
            is_airport=False,
            match=MATCH,
        )
        assert fare.time.amount == Decimal("5.00")
        assert fare.total.amount == Decimal("17.00")


class TestTheNightWindow:
    def test_a_wrapping_window_covers_the_small_hours(self) -> None:
        """22:00-06:00 evaluated as `from <= t <= to` would apply to nothing at
        all, every night, silently. That is the bug this function exists to
        prevent, and 00:30 is where it would show."""
        assert is_night(dt.time(0, 30), night_from=dt.time(22, 0), night_to=dt.time(6, 0))

    def test_a_wrapping_window_covers_the_late_evening(self) -> None:
        assert is_night(dt.time(23, 0), night_from=dt.time(22, 0), night_to=dt.time(6, 0))

    def test_a_wrapping_window_excludes_the_afternoon(self) -> None:
        assert not is_night(dt.time(14, 0), night_from=dt.time(22, 0), night_to=dt.time(6, 0))

    def test_a_same_day_window_works_too(self) -> None:
        assert is_night(dt.time(13, 0), night_from=dt.time(12, 0), night_to=dt.time(14, 0))
        assert not is_night(dt.time(15, 0), night_from=dt.time(12, 0), night_to=dt.time(14, 0))

    def test_the_bounds_are_inclusive(self) -> None:
        """§12.4 writes it `[night_from, night_to]`, and square brackets are
        inclusive."""
        assert is_night(dt.time(22, 0), night_from=dt.time(22, 0), night_to=dt.time(6, 0))
        assert is_night(dt.time(6, 0), night_from=dt.time(22, 0), night_to=dt.time(6, 0))

    def test_no_window_is_never_night(self) -> None:
        assert not is_night(dt.time(3, 0), night_from=None, night_to=None)

    def test_a_daytime_pickup_pays_no_night_surcharge(self) -> None:
        rule = _metered(
            night_surcharge_pct=Decimal("25.00"),
            night_from=dt.time(22, 0),
            night_to=dt.time(6, 0),
        )
        fare = metered(
            rule,
            distance_m=57_400,
            travel_seconds=5_100,
            pickup_local=NOON,
            is_airport=True,
            match=MATCH,
        )
        assert fare.total.amount == Decimal("38.00")


class TestTheLadder:
    def test_step_one_is_a_corridor_in_this_direction(self) -> None:
        rule, match = resolve(  # type: ignore[misc]
            corridors=[_corridor()],
            tariffs=[_metered()],
            origin=ZNZ,
            target=NUNGWI,
            vehicle_class="STANDARD",
            on=TODAY,
        )
        assert match == TariffMatch(kind=MatchKind.CORRIDOR, rule_id=1, step=1)
        assert isinstance(rule, CorridorRule)

    def test_step_two_is_the_same_corridor_reversed(self) -> None:
        rule, match = resolve(  # type: ignore[misc]
            corridors=[_corridor(is_bidirectional=True)],
            tariffs=[],
            origin=NUNGWI,
            target=ZNZ,
            vehicle_class="STANDARD",
            on=TODAY,
        )
        assert match.step == 2
        assert match.kind == MatchKind.CORRIDOR

    def test_a_one_way_corridor_does_not_answer_backwards(self) -> None:
        """`is_bidirectional = false` is how an asymmetric route is expressed.
        Answering anyway would price the return leg at the outbound fare."""
        assert (
            resolve(
                corridors=[_corridor(is_bidirectional=False)],
                tariffs=[],
                origin=NUNGWI,
                target=ZNZ,
                vehicle_class="STANDARD",
                on=TODAY,
            )
            is None
        )

    def test_step_three_is_the_region_of_the_origin(self) -> None:
        """§12.4 says "region of origin" and means it: a leg out of Nungwi is
        priced on Nungwi's rates wherever it is going."""
        rule, match = resolve(  # type: ignore[misc]
            corridors=[],
            tariffs=[_metered(region_id=20), _metered(rule_id=101, region_id=30)],
            origin=NUNGWI,
            target=PAJE,
            vehicle_class="STANDARD",
            on=TODAY,
        )
        assert match == TariffMatch(kind=MatchKind.TARIFF, rule_id=100, step=3)
        assert isinstance(rule, MeteredRule)

    def test_step_four_is_the_country_default(self) -> None:
        national = _metered(rule_id=200, region_id=None, country_id=100)
        rule, match = resolve(  # type: ignore[misc]
            corridors=[],
            tariffs=[national],
            origin=NUNGWI,
            target=PAJE,
            vehicle_class="STANDARD",
            on=TODAY,
        )
        assert match == TariffMatch(kind=MatchKind.TARIFF, rule_id=200, step=4)

    def test_a_region_tariff_beats_the_country_default(self) -> None:
        """The control for step ordering. Without it steps 3 and 4 could be
        swapped and every other test here would still pass."""
        _, match = resolve(  # type: ignore[misc]
            corridors=[],
            tariffs=[
                _metered(rule_id=200, region_id=None, country_id=100),
                _metered(rule_id=100, region_id=20),
            ],
            origin=NUNGWI,
            target=PAJE,
            vehicle_class="STANDARD",
            on=TODAY,
        )
        assert match.step == 3

    def test_nothing_configured_returns_none(self) -> None:
        """The whole point of the phase. `None` means 422 NO_TARIFF_CONFIGURED;
        it does not mean free, and there is no default to fall back on."""
        assert (
            resolve(
                corridors=[],
                tariffs=[],
                origin=NUNGWI,
                target=PAJE,
                vehicle_class="STANDARD",
                on=TODAY,
            )
            is None
        )

    def test_another_class_does_not_borrow_this_ones_price(self) -> None:
        assert (
            resolve(
                corridors=[_corridor()],
                tariffs=[_metered()],
                origin=ZNZ,
                target=NUNGWI,
                vehicle_class="MINIBUS",
                on=TODAY,
            )
            is None
        )

    def test_a_custom_pin_falls_through_to_the_metered_steps(self) -> None:
        """§12.1's last leg pattern. A corridor is defined between two
        destinations, so a leg from a dropped map pin cannot match one — that
        is correct behaviour rather than a gap."""
        assert (
            resolve(
                corridors=[_corridor()],
                tariffs=[],
                origin=CUSTOM_PIN,
                target=NUNGWI,
                vehicle_class="STANDARD",
                on=TODAY,
            )
            is None
        )

    def test_a_leg_to_where_it_started_is_refused(self) -> None:
        with pytest.raises(TariffError, match="§12.2"):
            resolve(
                corridors=[_corridor()],
                tariffs=[],
                origin=NUNGWI,
                target=NUNGWI,
                vehicle_class="STANDARD",
                on=TODAY,
            )


class TestOnlyLiveRulesAnswer:
    def test_a_withdrawn_corridor_is_skipped(self) -> None:
        assert (
            resolve(
                corridors=[_corridor(is_active=False)],
                tariffs=[],
                origin=ZNZ,
                target=NUNGWI,
                vehicle_class="STANDARD",
                on=TODAY,
            )
            is None
        )

    def test_a_rule_that_has_not_started_is_skipped(self) -> None:
        assert (
            resolve(
                corridors=[_corridor(valid_from=dt.date(2028, 1, 1))],
                tariffs=[],
                origin=ZNZ,
                target=NUNGWI,
                vehicle_class="STANDARD",
                on=TODAY,
            )
            is None
        )

    def test_an_expired_rule_is_skipped(self) -> None:
        assert (
            resolve(
                corridors=[_corridor(valid_to=dt.date(2027, 1, 31))],
                tariffs=[],
                origin=ZNZ,
                target=NUNGWI,
                vehicle_class="STANDARD",
                on=TODAY,
            )
            is None
        )

    def test_the_window_is_half_open_at_the_top(self) -> None:
        """A rule valid *to* today has been replaced by the one valid *from*
        today. Matching the exclusion constraint in `models.py` means the
        database and the ladder agree about which day a rate changes on."""
        rule = _corridor(valid_to=TODAY)
        assert not rule.applies_on(TODAY)
        assert rule.applies_on(TODAY - dt.timedelta(days=1))

    def test_the_ladder_filters_rather_than_trusting_the_caller(self) -> None:
        """A repository that forgot its `WHERE` clause hands this module an
        expired rate. The answer must still be right."""
        _, match = resolve(  # type: ignore[misc]
            corridors=[
                _corridor(rule_id=9, valid_to=dt.date(2027, 1, 31), fixed_price=Decimal("5.00")),
                _corridor(rule_id=10, valid_from=dt.date(2027, 2, 1)),
            ],
            tariffs=[],
            origin=ZNZ,
            target=NUNGWI,
            vehicle_class="STANDARD",
            on=TODAY,
        )
        assert match.rule_id == 10


class TestPricingAMatchedRule:
    def test_a_corridor_needs_no_distance_at_all(self) -> None:
        """The reason Phase 6 ships while Appendix D-2 is open, and the reason
        §12.6 lets a corridor be quoted when routing is down."""
        fare = price(
            _corridor(),
            TariffMatch(kind=MatchKind.CORRIDOR, rule_id=1, step=1),
            distance_m=None,
            travel_seconds=None,
            pickup_local=NOON,
            is_airport=True,
        )
        assert fare.total.amount == Decimal("38.00")
        assert fare.base.amount == Decimal("38.00")
        assert fare.surcharges.amount == Decimal("0.00")

    def test_a_corridor_ignores_the_airport_surcharge(self) -> None:
        """A fixed corridor price is the whole price. Adding a surcharge to it
        would charge the airport twice: once inside the negotiated fare and
        once on top."""
        with_distance = price(
            _corridor(),
            TariffMatch(kind=MatchKind.CORRIDOR, rule_id=1, step=1),
            distance_m=57_400,
            travel_seconds=5_100,
            pickup_local=MIDNIGHT,
            is_airport=True,
        )
        assert with_distance.total.amount == Decimal("38.00")

    def test_a_metered_rule_without_a_distance_refuses(self) -> None:
        """§12.6: haversine is not permitted for a priced corridor without a
        fixed price. Substituting a zero would bill the flag-fall for a
        hundred-kilometre drive and look like a bargain rather than a bug."""
        with pytest.raises(TariffError, match="ROUTING_UNAVAILABLE"):
            price(
                _metered(),
                MATCH,
                distance_m=None,
                travel_seconds=None,
                pickup_local=NOON,
                is_airport=True,
            )

    def test_a_metered_rule_is_priced_through_the_same_door(self) -> None:
        """`price` is the one entry point the service layer uses, so the
        metered branch has to be exercised through it and not only through
        `metered` directly — otherwise the dispatch could be wrong and every
        arithmetic test above would still pass."""
        fare = price(
            _metered(),
            MATCH,
            distance_m=57_400,
            travel_seconds=5_100,
            pickup_local=NOON,
            is_airport=True,
        )
        assert fare.total.amount == Decimal("38.00")
        assert fare.match.step == 3

    def test_a_breakdown_that_does_not_add_up_cannot_be_built(self) -> None:
        """The guard behind §9.4.4's `breakdown`. A fare whose parts disagreed
        with its total would be the first thing a tourist queried and the last
        thing anybody could explain, so it is unconstructable rather than
        merely untested."""
        usd = Money(Decimal("1.00"), "USD")
        with pytest.raises(TariffError, match="does not sum"):
            Fare(
                total=usd,
                base=usd,
                distance=usd,
                time=Money.zero("USD"),
                surcharges=Money.zero("USD"),
                match=MATCH,
            )

    def test_a_negative_distance_is_refused(self) -> None:
        with pytest.raises(TariffError, match="negative"):
            metered(
                _metered(),
                distance_m=-1,
                travel_seconds=60,
                pickup_local=NOON,
                is_airport=False,
                match=MATCH,
            )

    def test_a_float_rate_is_refused(self) -> None:
        """§18.5 prohibits float anywhere on the pricing path, and a rate
        arriving from a JSON feed is the most likely place one would appear."""
        with pytest.raises(TariffError, match="Decimal"):
            metered(
                _metered(per_km_rate=0.4),
                distance_m=1_000,
                travel_seconds=60,
                pickup_local=NOON,
                is_airport=False,
                match=MATCH,
            )


class TestVehicleClassesFilterRatherThanSelect:
    ALL = (STANDARD, COMFORT, VAN, MINIBUS)

    def test_a_couple_with_two_bags_may_have_any_class(self) -> None:
        assert eligible(self.ALL, pax=2, luggage=2) == (STANDARD, COMFORT, VAN, MINIBUS)

    def test_five_passengers_rule_out_the_four_seaters(self) -> None:
        """§12.4: "the quote returns only classes whose seats >= pax"."""
        assert eligible(self.ALL, pax=5, luggage=2) == (VAN, MINIBUS)

    def test_luggage_counts_as_well_as_people(self) -> None:
        """Two passengers with seven suitcases do not fit in a car, and a
        filter that only looked at seats would sell them one."""
        assert eligible(self.ALL, pax=2, luggage=7) == (MINIBUS,)

    def test_a_party_too_large_for_anything_gets_nothing(self) -> None:
        assert eligible(self.ALL, pax=20, luggage=0) == ()

    def test_the_order_is_stable(self) -> None:
        """TC-902 wants byte-identical responses. A set-derived order would
        satisfy every other assertion here and fail that one intermittently."""
        assert eligible(reversed(self.ALL), pax=1, luggage=0) == eligible(
            self.ALL, pax=1, luggage=0
        )

    def test_no_passengers_is_not_a_leg(self) -> None:
        with pytest.raises(TariffError, match="at least one passenger"):
            eligible(self.ALL, pax=0, luggage=0)


class TestTheDefaultClassAPlannerInserts:
    def _fare(self, amount: str) -> object:
        return price(
            _corridor(fixed_price=Decimal(amount)),
            TariffMatch(kind=MatchKind.CORRIDOR, rule_id=1, step=1),
            distance_m=None,
            travel_seconds=None,
            pickup_local=NOON,
            is_airport=False,
        )

    def test_the_cheapest_fitting_class_wins(self) -> None:
        assert (
            cheapest_fitting(
                [(VAN, self._fare("52.00")), (STANDARD, self._fare("38.00"))]  # type: ignore[list-item]
            )
            == STANDARD
        )

    def test_a_tie_breaks_deterministically(self) -> None:
        """STANDARD and COMFORT cost the same on many corridors and carry the
        same party. Breaking the tie on display order rather than on whichever
        the queryset returned first is what makes a regenerated itinerary
        byte-identical."""
        pairs = [(COMFORT, self._fare("38.00")), (STANDARD, self._fare("38.00"))]
        assert cheapest_fitting(pairs) == STANDARD  # type: ignore[arg-type]
        assert cheapest_fitting(list(reversed(pairs))) == STANDARD  # type: ignore[arg-type]

    def test_nothing_fitting_yields_nothing(self) -> None:
        assert cheapest_fitting([]) is None
