"""Travel times for the planner — SRS §10.5, §12.6, ADR 0019.

Application layer (SRS §8.2 layer 2). This is the seam between the pure
sequencer, which takes travel times as an input and fetches nothing, and the
world, which has a routing provider in it — or, today, does not.

**§12.6's precedence, in order.** The specification gives three tiers for
planning:

    route_cache -> the nightly destination-pair matrix -> haversine estimate

and requires the third to be marked `estimate_quality = APPROXIMATE`, "which
the UI renders with an explicit 'approximate' label". This module implements
all three as a chain of optional lookups, and today only the last one answers.

**The first two tiers have no table, deliberately.** `route_cache` and the
destination-pair matrix are both *caches of a routing provider's answers*, and
Appendix D-2 has not chosen one. Creating those tables now would produce
exactly the defect this project keeps finding: schema that is documented,
migrated, and written to by nothing. They arrive with the adapter that fills
them, and `tests/test_travel.py` asserts their absence with the reason
attached, in the same shape as `test_ports_registry.py`.

**An unknown place raises rather than estimating.** The sequencer works in
opaque `LocationKey` strings and this module resolves them to coordinates. A
key with no coordinate is a planner bug — an item whose location the caller
failed to register — and returning a plausible duration for it would bury that
bug under a number a tourist would plan around. §13.2's rule about never
persisting an unconfirmed geocode is the same instinct one layer down.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from decimal import Decimal

from apps.common.config import get_setting
from apps.common.geo import (
    Coordinates,
    approximate_duration_seconds,
    approximate_road_distance,
)
from apps.trip.domain.sequencing import LocationKey, TravelEstimate, TravelTime

__all__ = [
    "UnknownPlaceError",
    "RoutedLookup",
    "build_travel_time",
    "place_key",
]

#: A lookup that may answer for a pair, or return `None` to defer to the next
#: tier. Both the cache and the matrix have this shape; neither exists yet.
RoutedLookup = Callable[[LocationKey, LocationKey], TravelEstimate | None]


class UnknownPlaceError(LookupError):
    """A location key the caller never registered a coordinate for."""


def place_key(kind: str, identifier: object) -> LocationKey:
    """A stable, readable handle for somewhere the planner routes to or from.

    Readable on purpose: these appear in transfer titles, in test failures and
    in the sequencer's own output, and `accommodation:41` is diagnosable where
    an opaque hash is not. It is an identity, never a coordinate — the
    coordinate lives in the map this module closes over, so a key can be
    compared and logged without carrying a position around with it.
    """
    return f"{kind}:{identifier}"


def build_travel_time(
    places: Mapping[LocationKey, Coordinates],
    *,
    road_factor: Decimal | None = None,
    speed_kmh: Decimal | None = None,
    from_cache: RoutedLookup | None = None,
    from_matrix: RoutedLookup | None = None,
) -> TravelTime:
    """A `TravelTime` for the sequencer, applying §12.6's precedence.

    `road_factor` and `speed_kmh` default to their `system_setting` values
    rather than to literals (NFR-M07). They are parameters at all so that a
    test can pin an arithmetic expectation without reaching into the settings
    register, which is a different thing to be testing.
    """
    factor = (
        road_factor if road_factor is not None else Decimal(str(get_setting("routing.road_factor")))
    )
    speed = (
        speed_kmh
        if speed_kmh is not None
        else Decimal(str(get_setting("routing.average_speed_kmh")))
    )

    def travel_time(origin: LocationKey, target: LocationKey) -> TravelEstimate:
        for tier in (from_cache, from_matrix):
            if tier is None:
                continue
            answer = tier(origin, target)
            if answer is not None:
                return answer

        try:
            start, end = places[origin], places[target]
        except KeyError as exc:
            raise UnknownPlaceError(
                f"no coordinate registered for {exc.args[0]!r}. The planner cannot "
                "estimate a leg to somewhere it does not know, and will not invent one."
            ) from exc

        distance = approximate_road_distance(start, end, road_factor=factor)
        return TravelEstimate(
            seconds=approximate_duration_seconds(distance, speed_kmh=speed),
            metres=distance.metres,
            # `Distance.quality` is `common.geo.EstimateQuality.APPROXIMATE`;
            # the model's `EstimateQuality` is a Django TextChoices with the
            # same members. Passed as its value so the domain stays free of
            # both, exactly as `TravelEstimate.quality` is documented to be.
            quality=distance.quality.value,
        )

    return travel_time
