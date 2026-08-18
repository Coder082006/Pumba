"""RoutingPort — SRS §12.3, §13.2, §13.3.

Route, matrix, geocode and reverse geocode. No provider selected
(SRS Appendix D-2). Candidate implementations named in SRS §34.6 are Mapbox
and a self-hosted OSRM; the interface is deliberately narrow enough that both
satisfy it.

Distances are metres and durations are seconds — integers, no floats, so that
a cached value is byte-identical across reads and a fare computed from it is
reproducible (SRS principle A7).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["Coordinate", "RouteResult", "MatrixResult", "Place", "RoutingPort"]


@dataclass(frozen=True, slots=True)
class Coordinate:
    """WGS-84. SRS §7.2 stores these as geography(Point, 4326)."""

    lat: float
    lng: float

    def __post_init__(self) -> None:
        if not -90 <= self.lat <= 90:
            raise ValueError(f"lat out of range: {self.lat}")
        if not -180 <= self.lng <= 180:
            raise ValueError(f"lng out of range: {self.lng}")


@dataclass(frozen=True, slots=True)
class RouteResult:
    distance_metres: int
    duration_seconds: int
    #: Encoded polyline, retained for the completed booking (SRS §13.7).
    geometry: str | None = None


@dataclass(frozen=True, slots=True)
class MatrixResult:
    """Row-major: `cells[i][j]` is origins[i] to destinations[j]."""

    cells: tuple[tuple[RouteResult | None, ...], ...]


@dataclass(frozen=True, slots=True)
class Place:
    formatted_address: str
    coordinate: Coordinate
    provider_place_id: str | None = None


@runtime_checkable
class RoutingPort(Protocol):
    """Routing, distance matrices and geocoding.

    Implementations must raise `apps.common.errors.ExternalServiceError` on
    upstream failure so callers can apply the degraded-mode rules of
    SRS §12.6 rather than interpreting vendor-specific exceptions.
    """

    def route(self, origin: Coordinate, destination: Coordinate) -> RouteResult: ...

    def distance_matrix(
        self, origins: list[Coordinate], destinations: list[Coordinate]
    ) -> MatrixResult: ...

    def geocode(self, query: str) -> list[Place]: ...

    def reverse_geocode(self, coordinate: Coordinate) -> Place | None: ...
