"""Coordinates and the §12.6 degraded-mode estimate.

The distance tests are ordinary arithmetic. The tests that matter are the ones
asserting an approximate figure cannot be mistaken for a measured one, because
in Phase 3 the routing adapter is a fake and every distance here is invented.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.catalogue.domain.geo import (
    COORDINATE_PRECISION,
    Coordinates,
    Distance,
    EstimateQuality,
    approximate_duration_seconds,
    approximate_road_distance,
    haversine_metres,
)

# Seed catalogue anchors, to seven decimal places (§13.1).
ZNZ = Coordinates(Decimal("-6.2220000"), Decimal("39.2249000"))
STONE_TOWN = Coordinates(Decimal("-6.1631000"), Decimal("39.1892000"))
NUNGWI = Coordinates(Decimal("-5.7261000"), Decimal("39.2969000"))

ROAD_FACTOR = Decimal("1.35")
SPEED_KMH = Decimal("45")


class TestCoordinates:
    def test_a_valid_pair_is_accepted(self) -> None:
        assert ZNZ.lat == Decimal("-6.2220000")

    @pytest.mark.parametrize("lat", ["90", "-90", "0"])
    def test_latitude_bounds_are_inclusive(self, lat: str) -> None:
        assert Coordinates(Decimal(lat), Decimal("0")).lat == Decimal(lat)

    @pytest.mark.parametrize("lon", ["180", "-180", "0"])
    def test_longitude_bounds_are_inclusive(self, lon: str) -> None:
        assert Coordinates(Decimal("0"), Decimal(lon)).lon == Decimal(lon)

    def test_latitude_above_ninety_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="latitude out of range"):
            Coordinates(Decimal("90.0000001"), Decimal("0"))

    def test_latitude_below_minus_ninety_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="latitude out of range"):
            Coordinates(Decimal("-90.0000001"), Decimal("0"))

    def test_longitude_beyond_the_antimeridian_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="longitude out of range"):
            Coordinates(Decimal("0"), Decimal("180.0000001"))

    def test_seven_decimal_places_are_allowed(self) -> None:
        # §13.1: "a maximum of seven decimal places".
        assert (
            Coordinates(Decimal("-6.1234567"), Decimal("39.1234567")).lat.as_tuple().exponent == -7
        )

    def test_eight_decimal_places_are_rejected(self) -> None:
        with pytest.raises(ValueError, match=f"exceeds {COORDINATE_PRECISION}"):
            Coordinates(Decimal("-6.12345678"), Decimal("39.0"))

    def test_excess_longitude_precision_is_rejected(self) -> None:
        with pytest.raises(ValueError, match=f"exceeds {COORDINATE_PRECISION}"):
            Coordinates(Decimal("-6.0"), Decimal("39.12345678"))

    def test_coordinates_are_hashable_and_comparable(self) -> None:
        assert Coordinates(Decimal("1.0"), Decimal("2.0")) == Coordinates(
            Decimal("1.0"), Decimal("2.0")
        )
        assert len({ZNZ, ZNZ}) == 1


class TestHaversine:
    def test_the_distance_to_itself_is_zero(self) -> None:
        assert haversine_metres(ZNZ, ZNZ) == 0

    def test_it_is_symmetric(self) -> None:
        assert haversine_metres(ZNZ, NUNGWI) == haversine_metres(NUNGWI, ZNZ)

    def test_the_airport_to_stone_town_is_about_seven_kilometres(self) -> None:
        # §4.1 gives 7 km by road; straight line is a little under.
        metres = haversine_metres(ZNZ, STONE_TOWN)
        assert 6_000 <= metres <= 8_000

    def test_the_airport_to_nungwi_is_about_fifty_kilometres_straight_line(self) -> None:
        # §4.1 gives 57 km by road, which is why a road factor exists.
        metres = haversine_metres(ZNZ, NUNGWI)
        assert 50_000 <= metres <= 60_000

    def test_a_degree_of_latitude_is_about_111_kilometres(self) -> None:
        a = Coordinates(Decimal("0"), Decimal("0"))
        b = Coordinates(Decimal("1"), Decimal("0"))
        assert 110_000 <= haversine_metres(a, b) <= 112_000

    def test_antipodal_points_are_half_the_circumference(self) -> None:
        a = Coordinates(Decimal("0"), Decimal("0"))
        b = Coordinates(Decimal("0"), Decimal("180"))
        assert 20_000_000 <= haversine_metres(a, b) <= 20_040_000

    def test_it_crosses_the_antimeridian_without_going_the_long_way(self) -> None:
        a = Coordinates(Decimal("0"), Decimal("179.5"))
        b = Coordinates(Decimal("0"), Decimal("-179.5"))
        assert haversine_metres(a, b) < 120_000

    def test_it_is_deterministic(self) -> None:
        assert haversine_metres(ZNZ, NUNGWI) == haversine_metres(ZNZ, NUNGWI)


class TestApproximateRoadDistance:
    def test_it_applies_the_road_factor(self) -> None:
        straight = haversine_metres(ZNZ, NUNGWI)
        got = approximate_road_distance(ZNZ, NUNGWI, road_factor=ROAD_FACTOR)
        assert got.metres == round(straight * 1.35)

    def test_the_result_is_always_approximate(self) -> None:
        got = approximate_road_distance(ZNZ, NUNGWI, road_factor=ROAD_FACTOR)
        assert got.quality is EstimateQuality.APPROXIMATE

    def test_no_road_factor_produces_a_measured_distance(self) -> None:
        # There is no argument that makes this function return MEASURED,
        # because nothing it can compute is measured.
        for factor in ("1", "1.35", "2.5"):
            got = approximate_road_distance(ZNZ, NUNGWI, road_factor=Decimal(factor))
            assert got.is_measured is False

    def test_a_zero_road_factor_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="road_factor must be positive"):
            approximate_road_distance(ZNZ, NUNGWI, road_factor=Decimal("0"))

    def test_a_negative_road_factor_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="road_factor must be positive"):
            approximate_road_distance(ZNZ, NUNGWI, road_factor=Decimal("-1"))

    def test_the_factor_is_a_parameter_not_a_constant(self) -> None:
        # rule 5: thresholds are system_setting rows. A different market has
        # different roads.
        low = approximate_road_distance(ZNZ, NUNGWI, road_factor=Decimal("1.1"))
        high = approximate_road_distance(ZNZ, NUNGWI, road_factor=Decimal("1.8"))
        assert high.metres > low.metres


class TestQualityCannotBeLost:
    """The reason `Distance` exists instead of a bare int."""

    def test_an_approximate_distance_may_not_be_stated_as_fact(self) -> None:
        got = approximate_road_distance(ZNZ, NUNGWI, road_factor=ROAD_FACTOR)
        assert got.may_be_stated_as_fact is False

    def test_a_measured_distance_may_be(self) -> None:
        got = Distance(metres=57_000, quality=EstimateQuality.MEASURED)
        assert got.may_be_stated_as_fact is True
        assert got.is_measured is True

    def test_quality_travels_with_the_value(self) -> None:
        # A caller cannot obtain the metres without also having the quality in
        # hand, which is the property that stops it being forgotten in a
        # serializer.
        got = approximate_road_distance(ZNZ, STONE_TOWN, road_factor=ROAD_FACTOR)
        assert set(vars(type(got))["__slots__"]) == {"metres", "quality"}


class TestApproximateDuration:
    APPROX = Distance(metres=45_000, quality=EstimateQuality.APPROXIMATE)

    def test_forty_five_kilometres_at_forty_five_kmh_is_one_hour(self) -> None:
        assert approximate_duration_seconds(self.APPROX, speed_kmh=SPEED_KMH) == 3600

    def test_halving_the_speed_doubles_the_time(self) -> None:
        slow = approximate_duration_seconds(self.APPROX, speed_kmh=Decimal("22.5"))
        assert slow == 7200

    def test_a_measured_distance_refuses_a_modelled_duration(self) -> None:
        # It arrives from the provider with its own duration; deriving a second
        # one would put two disagreeing figures on the same screen.
        measured = Distance(metres=45_000, quality=EstimateQuality.MEASURED)
        with pytest.raises(ValueError, match="carries its own duration"):
            approximate_duration_seconds(measured, speed_kmh=SPEED_KMH)

    def test_a_zero_speed_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="speed_kmh must be positive"):
            approximate_duration_seconds(self.APPROX, speed_kmh=Decimal("0"))

    def test_a_negative_speed_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="speed_kmh must be positive"):
            approximate_duration_seconds(self.APPROX, speed_kmh=Decimal("-45"))

    def test_rounding_is_half_up_and_deterministic(self) -> None:
        distance = Distance(metres=1, quality=EstimateQuality.APPROXIMATE)
        # 1 m at 45 km/h is 0.08 s, which rounds to 0 rather than drifting.
        assert approximate_duration_seconds(distance, speed_kmh=SPEED_KMH) == 0

    def test_the_speed_model_is_a_parameter(self) -> None:
        fast = approximate_duration_seconds(self.APPROX, speed_kmh=Decimal("90"))
        slow = approximate_duration_seconds(self.APPROX, speed_kmh=Decimal("30"))
        assert fast < slow
