"""Coordinates and straight-line distance — SRS §13.1, §12.6.

§13.1 fixes the coordinate model: WGS 84 (SRID 4326), *"exchanged over the API
as decimal degrees with a maximum of seven decimal places"*, and — importantly —
*"Distance computations use ST_Distance on the geography type (metres,
geodesic), never planar approximations."*

So why is there a haversine here at all?

Because §12.6 defines exactly one situation in which a straight-line figure is
permitted: the degraded planning mode, where the routing provider is
unavailable and no cache or matrix entry exists. There, the SRS allows
*"a haversine distance x road-factor (configurable, default 1.35) and a speed
model (configurable, default 45 km/h)"* — and requires the result be marked
`estimate_quality = APPROXIMATE`, *"which the UI renders with an explicit
'approximate' label"*.

That marking is not decoration. In Phase 3 the routing adapter is a fake,
because D2 is unresolved, so every distance this module can produce is
invented. An invented number rendered as fact on the most-indexed public page
in the product is a lie to a tourist deciding where to stay. `EstimateQuality`
therefore travels *with* the value in `Distance`, rather than being a flag some
caller may forget to read — and §12.6's quoting row makes the same point in
money: a haversine fallback is *not* permitted for a priced corridor, which
returns `502 ROUTING_UNAVAILABLE` rather than commit the platform to a guess.

`road_factor` and `speed_kmh` are parameters, never constants. They are
`route.road_factor` and `route.speed_kmh` in `system_setting` (rule 5), and a
different market has different roads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

__all__ = [
    "Coordinates",
    "EstimateQuality",
    "Distance",
    "COORDINATE_PRECISION",
    "haversine_metres",
    "approximate_road_distance",
    "approximate_duration_seconds",
]

#: §13.1: "a maximum of seven decimal places". Roughly 11 mm at the equator —
#: far finer than any pickup point needs, and the point at which storing more
#: is storing noise.
COORDINATE_PRECISION = 7

#: WGS 84 mean radius, metres. The value ST_Distance's spheroid reduces to for
#: a sphere; used only for the §12.6 estimate, never for a stored distance.
_EARTH_RADIUS_M = 6371008.8


class EstimateQuality(StrEnum):
    """§12.6. Travels with the number so it cannot be dropped on the way out."""

    MEASURED = "MEASURED"
    """From the routing provider, or from a cache entry that came from it."""

    APPROXIMATE = "APPROXIMATE"
    """Haversine times the road factor. Must be rendered with an explicit label."""


@dataclass(frozen=True, slots=True)
class Coordinates:
    """WGS 84 decimal degrees. §7.2 stores these as `geography(Point, 4326)`."""

    lat: Decimal
    lon: Decimal

    def __post_init__(self) -> None:
        if not -90 <= self.lat <= 90:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180 <= self.lon <= 180:
            raise ValueError(f"longitude out of range: {self.lon}")
        if self.lat.as_tuple().exponent < -COORDINATE_PRECISION:  # type: ignore[operator]
            raise ValueError(f"latitude exceeds {COORDINATE_PRECISION} decimal places")
        if self.lon.as_tuple().exponent < -COORDINATE_PRECISION:  # type: ignore[operator]
            raise ValueError(f"longitude exceeds {COORDINATE_PRECISION} decimal places")


@dataclass(frozen=True, slots=True)
class Distance:
    """A distance and how much it can be trusted.

    Pairing them is the whole design. A bare `int` of metres loses the one fact
    the caller needs in order to decide whether it may be rendered without a
    label, put in JSON-LD, or used to price a transfer.
    """

    metres: int
    quality: EstimateQuality

    @property
    def is_measured(self) -> bool:
        return self.quality is EstimateQuality.MEASURED

    @property
    def may_be_stated_as_fact(self) -> bool:
        """May this appear unlabelled, in metadata, or in structured data?

        Only a measured distance may. §12.6 requires an approximate one carry
        an explicit label, and structured data has nowhere to put one — a
        `TouristDestination` with a fabricated `distance` is a fabrication
        published to search engines.
        """
        return self.is_measured


def haversine_metres(origin: Coordinates, destination: Coordinates) -> int:
    """Great-circle distance, rounded to whole metres.

    Not a road distance and not a substitute for `ST_Distance` on stored
    geography — see the module docstring. Exposed because `approximate_road_
    distance` is built on it and both deserve their own tests.
    """
    lat1, lon1 = math.radians(float(origin.lat)), math.radians(float(origin.lon))
    lat2, lon2 = math.radians(float(destination.lat)), math.radians(float(destination.lon))

    sin_dlat = math.sin((lat2 - lat1) / 2)
    sin_dlon = math.sin((lon2 - lon1) / 2)
    a = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    return round(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, a))))


def approximate_road_distance(
    origin: Coordinates, destination: Coordinates, *, road_factor: Decimal
) -> Distance:
    """§12.6's degraded-mode estimate. Always `APPROXIMATE`, by construction.

    There is no parameter that makes this return `MEASURED`, because nothing
    this function can compute is measured.
    """
    if road_factor <= 0:
        raise ValueError("road_factor must be positive")
    straight = Decimal(haversine_metres(origin, destination))
    metres = (straight * road_factor).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Distance(metres=int(metres), quality=EstimateQuality.APPROXIMATE)


def approximate_duration_seconds(distance: Distance, *, speed_kmh: Decimal) -> int:
    """§12.6's speed model. Refuses to guess a duration for a measured distance.

    A measured distance arrives from the routing provider with its own
    duration; deriving a second one from an average speed would silently
    replace a real figure with a worse one, and the two would disagree on
    screen.
    """
    if speed_kmh <= 0:
        raise ValueError("speed_kmh must be positive")
    if distance.is_measured:
        raise ValueError("a measured distance carries its own duration; do not model one")
    hours = Decimal(distance.metres) / Decimal(1000) / speed_kmh
    return int((hours * Decimal(3600)).quantize(Decimal(1), rounding=ROUND_HALF_UP))
