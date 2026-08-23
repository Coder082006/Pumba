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
    BoundingBox,
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


#: Tanzania, as the seed set states it.
TANZANIA = BoundingBox(
    min_lat=Decimal("-11.7500000"),
    min_lon=Decimal("29.3200000"),
    max_lat=Decimal("-0.9500000"),
    max_lon=Decimal("40.4500000"),
)


class TestBoundingBox:
    """The guard against a transposed pair — see the class docstring in `geo`."""

    @pytest.mark.parametrize("point", [ZNZ, STONE_TOWN, NUNGWI])
    def test_a_real_anchor_is_inside_its_country(self, point: Coordinates) -> None:
        assert TANZANIA.contains(point)

    @pytest.mark.parametrize("point", [ZNZ, STONE_TOWN, NUNGWI])
    def test_the_same_anchor_transposed_is_not(self, point: Coordinates) -> None:
        """The whole reason this class exists.

        Both halves are individually legal — `Coordinates` accepts the swapped
        pair without complaint — and the result is a point in the Gulf of
        Guinea. Only the country box can tell the difference.
        """
        swapped = Coordinates(lat=point.lon, lon=point.lat)
        assert not TANZANIA.contains(swapped)

    def test_the_edges_are_inclusive(self) -> None:
        """A box is a closed interval. A destination sitting exactly on a
        stated bound is inside it — the alternative is a guard that rejects
        the very coordinate somebody chose the bound to admit."""
        corner = Coordinates(lat=TANZANIA.min_lat, lon=TANZANIA.max_lon)
        assert TANZANIA.contains(corner)

    @pytest.mark.parametrize(
        "lat,lon",
        [
            ("-12.0000000", "35.0000000"),  # south of it
            ("-0.5000000", "35.0000000"),  # north of it
            ("-6.0000000", "29.0000000"),  # west of it
            ("-6.0000000", "41.0000000"),  # east of it
        ],
    )
    def test_a_point_outside_any_edge_is_refused(self, lat: str, lon: str) -> None:
        assert not TANZANIA.contains(Coordinates(Decimal(lat), Decimal(lon)))

    def test_a_box_with_its_latitudes_the_wrong_way_round_is_refused(self) -> None:
        """A box built upside down contains nothing, so every write beneath it
        would fail with a message about the destination rather than about the
        country that is actually wrong."""
        with pytest.raises(ValueError, match="south of"):
            BoundingBox(
                min_lat=Decimal("0"),
                min_lon=Decimal("29"),
                max_lat=Decimal("-11"),
                max_lon=Decimal("40"),
            )

    def test_a_zero_width_box_is_refused(self) -> None:
        with pytest.raises(ValueError, match="must differ"):
            BoundingBox(
                min_lat=Decimal("-11"),
                min_lon=Decimal("39"),
                max_lat=Decimal("-1"),
                max_lon=Decimal("39"),
            )

    @pytest.mark.parametrize("value", ["91", "-91"])
    def test_a_latitude_bound_outside_the_globe_is_refused(self, value: str) -> None:
        with pytest.raises(ValueError, match="out of range"):
            BoundingBox(
                min_lat=Decimal("-90") if value == "91" else Decimal(value),
                min_lon=Decimal("0"),
                max_lat=Decimal(value) if value == "91" else Decimal("90"),
                max_lon=Decimal("1"),
            )

    def test_a_longitude_bound_outside_the_globe_is_refused(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            BoundingBox(
                min_lat=Decimal("-1"),
                min_lon=Decimal("-181"),
                max_lat=Decimal("1"),
                max_lon=Decimal("1"),
            )


class TestABoxThatCrossesTheAntimeridian:
    """Fiji, Kiribati, and the Chatham Islands.

    No such market is planned, and the case is supported anyway: the
    alternative is a longitude check that rejects every coordinate in the
    country on the day one is opened, and a guard that fires on correct data
    gets deleted rather than fixed.
    """

    FIJI = BoundingBox(
        min_lat=Decimal("-20.7000000"),
        min_lon=Decimal("176.9000000"),
        max_lat=Decimal("-12.4000000"),
        max_lon=Decimal("-178.2000000"),
    )

    def test_it_is_recognised_as_wrapping(self) -> None:
        assert self.FIJI.crosses_antimeridian
        assert not TANZANIA.crosses_antimeridian

    @pytest.mark.parametrize("lon", ["177.4000000", "-179.9000000", "180.0000000"])
    def test_both_sides_of_the_line_are_inside(self, lon: str) -> None:
        assert self.FIJI.contains(Coordinates(Decimal("-17.7000000"), Decimal(lon)))

    @pytest.mark.parametrize("lon", ["150.0000000", "-100.0000000", "0.0000000"])
    def test_the_long_way_round_is_still_outside(self, lon: str) -> None:
        """The wrap must widen the box across 180°, not turn it into "any
        longitude" — which is what an `or` written the other way round does."""
        assert not self.FIJI.contains(Coordinates(Decimal("-17.7000000"), Decimal(lon)))


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
