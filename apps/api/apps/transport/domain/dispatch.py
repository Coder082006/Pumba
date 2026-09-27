"""Driver candidate scoring — SRS §11.6.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§11.6 opens by saying what this must be: *"Deterministic, transparent,
administrator-tunable. No machine learning."* Each of those three is a design
constraint with teeth, and this module is where they are kept.

**Deterministic** means the same inputs produce the same order, every time,
including when two drivers score identically. §11.6's tie-break is therefore
part of the function and not an afterthought: lower `completed_trips` first,
which favours supply development, then `driver_id` ascending, which is a total
order because no two drivers share one.

**Transparent** means the arithmetic survives the decision. `Scored` carries
every component, not just the sum, because §11.6 ends by requiring that "a
provider dispute can be answered with the exact computation". A score of 0.72
answers nothing; 0.40 x 0.31 proximity + 0.25 x 0.96 rating does.

**Administrator-tunable** means the weights, the radius and the freshness
window arrive as arguments. Nothing here reads a setting, because reading one
would make this file impure and, worse, would hide the tuning from the caller
that has to audit it.

The score, verbatim from §11.6::

    score = w_prox * proximity_score
          + w_rate * (rating_avg / 5)
          + w_acc  * (acceptance_rate / 100)
          + w_exp  * min(completed_trips / 200, 1)
          + w_util * (1 - utilisation_today)

    proximity_score = 1 - min(distance(driver_anchor, pickup) / max_radius_m, 1)
    driver_anchor   = last_known_location if online and fresh (< 15 min),
                      else driver.home_destination.centroid

**Every component is normalised to [0, 1] and the weights sum to 1**, so a
score is itself in [0, 1] and two scores from different runs are comparable.
That is asserted rather than assumed: `Weights` refuses a set that does not
sum to one, because a typo in a settings row would otherwise silently rescale
every dispatch decision the platform makes.

**Distance is planar, not geodesic.** The anchor and the pickup are metres
apart on an island, `max_radius_m` is a normalisation ceiling rather than a
boundary, and the difference between the two calculations over 60 km is far
smaller than the difference between a driver's last ping and where they
actually are. A precise distance here would be false precision; what matters is
the ordering, and the ordering does not change.

**Rule 5 is not in this module.** "No overlapping assignment" is a question
about rows, and this file has none — the caller filters on it before scoring,
and ADR 0029's EXCLUDE constraint is what makes the answer binding.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal

__all__ = [
    "Weights",
    "Anchor",
    "Candidate",
    "Scored",
    "WeightsError",
    "rank",
    "proximity_score",
]

#: §11.6's experience ceiling: two hundred completed trips is a fully
#: experienced driver, and the two hundred and first earns nothing more.
EXPERIENCE_CEILING = Decimal("200")

#: Degrees of latitude per metre, near enough for the ordering this produces.
#: One degree of latitude is ~111.32 km everywhere; longitude shrinks with
#: the cosine of the latitude, which `_metres_between` applies.
_METRES_PER_DEGREE = 111_320.0

_ONE = Decimal("1")
_ZERO = Decimal("0")


class WeightsError(ValueError):
    """A weight set that cannot produce a comparable score."""


@dataclass(frozen=True, slots=True)
class Weights:
    """§11.6's five, which an administrator may retune (`dispatch.weights`).

    Refusing a set that does not sum to one is the whole reason this is a
    class rather than a dict. The defaults are 0.40 / 0.25 / 0.20 / 0.10 /
    0.05; an administrator who edits one row and not the others would
    otherwise rescale every score on the platform without any error to notice.
    """

    proximity: Decimal
    rating: Decimal
    acceptance: Decimal
    experience: Decimal
    utilisation: Decimal

    def __post_init__(self) -> None:
        total = self.proximity + self.rating + self.acceptance + self.experience + self.utilisation
        if total != _ONE:
            raise WeightsError(f"dispatch weights must sum to 1.0, these sum to {total}")
        if any(
            weight < _ZERO
            for weight in (
                self.proximity,
                self.rating,
                self.acceptance,
                self.experience,
                self.utilisation,
            )
        ):
            raise WeightsError("a dispatch weight may not be negative")


@dataclass(frozen=True, slots=True)
class Anchor:
    """Where §11.6 believes a driver is.

    `is_live` says which of the two the coordinates are: the driver's last
    reported position, or the centroid of their home destination. It is
    recorded rather than inferred because the audit entry has to be able to
    say *why* a driver scored badly on proximity, and "we did not know where
    they were" is a different answer from "they were far away".
    """

    lat: float
    lng: float
    is_live: bool


@dataclass(frozen=True, slots=True)
class Candidate:
    """One driver-and-vehicle pair that has already passed §11.6's filter.

    `utilisation_today` is the fraction of the day already committed to other
    assignments, in [0, 1]. It is supplied rather than computed because the
    assignments that determine it are rows.
    """

    driver_id: int
    vehicle_id: int
    anchor: Anchor
    rating_avg: Decimal
    acceptance_rate: Decimal
    completed_trips: int
    utilisation_today: Decimal


@dataclass(frozen=True, slots=True)
class Scored:
    """A candidate, its place in the order, and the arithmetic that put it there."""

    candidate: Candidate
    score: Decimal
    proximity: Decimal
    rating: Decimal
    acceptance: Decimal
    experience: Decimal
    utilisation: Decimal
    distance_m: float

    def components(self) -> dict[str, str]:
        """The audit entry's payload — §11.6's "each candidate's component
        scores", as strings so no float reaches a JSON column."""
        return {
            "proximity": str(self.proximity),
            "rating": str(self.rating),
            "acceptance": str(self.acceptance),
            "experience": str(self.experience),
            "utilisation": str(self.utilisation),
            "score": str(self.score),
            "distance_m": str(round(self.distance_m)),
            "anchor_is_live": str(self.candidate.anchor.is_live).lower(),
        }


def _metres_between(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    """Planar distance, with longitude corrected for latitude.

    See the module docstring: the ordering this feeds is insensitive to the
    difference between this and a geodesic calculation at island scale.
    """
    mean_lat = math.radians((lat_a + lat_b) / 2)
    north = (lat_a - lat_b) * _METRES_PER_DEGREE
    east = (lng_a - lng_b) * _METRES_PER_DEGREE * math.cos(mean_lat)
    return math.hypot(north, east)


def proximity_score(distance_m: float, *, max_radius_m: int) -> Decimal:
    """§11.6: `1 - min(distance / max_radius_m, 1)`.

    A driver beyond the radius scores zero rather than negative — the radius
    is where proximity stops discriminating, not where a driver stops being a
    candidate. Exclusion by distance is the service area's job (rule 6), and
    conflating the two would quietly turn a tuning constant into a boundary.
    """
    if max_radius_m <= 0:
        raise WeightsError("dispatch.max_radius_m must be positive")
    ratio = Decimal(str(min(distance_m / max_radius_m, 1.0)))
    return _clamp(_ONE - ratio)


def _clamp(value: Decimal) -> Decimal:
    """Into [0, 1]. Every component is a fraction, and a stored figure that
    has drifted out of range — a rating above five, a negative acceptance
    rate — must not be able to move a score outside it."""
    if value < _ZERO:
        return _ZERO
    return _ONE if value > _ONE else value


def _score_one(
    candidate: Candidate,
    *,
    pickup_lat: float,
    pickup_lng: float,
    weights: Weights,
    max_radius_m: int,
) -> Scored:
    distance = _metres_between(candidate.anchor.lat, candidate.anchor.lng, pickup_lat, pickup_lng)
    proximity = proximity_score(distance, max_radius_m=max_radius_m)
    rating = _clamp(candidate.rating_avg / Decimal("5"))
    acceptance = _clamp(candidate.acceptance_rate / Decimal("100"))
    experience = _clamp(Decimal(candidate.completed_trips) / EXPERIENCE_CEILING)
    utilisation = _clamp(_ONE - _clamp(candidate.utilisation_today))

    score = (
        weights.proximity * proximity
        + weights.rating * rating
        + weights.acceptance * acceptance
        + weights.experience * experience
        + weights.utilisation * utilisation
    )
    return Scored(
        candidate=candidate,
        score=score,
        proximity=proximity,
        rating=rating,
        acceptance=acceptance,
        experience=experience,
        utilisation=utilisation,
        distance_m=distance,
    )


def rank(
    candidates: tuple[Candidate, ...],
    *,
    pickup_lat: float,
    pickup_lng: float,
    weights: Weights,
    max_radius_m: int,
) -> tuple[Scored, ...]:
    """§11.6's candidate list, best first.

    The sort key carries the tie-break §11.6 specifies, so the order is total
    and reproducible: highest score, then fewest completed trips, then lowest
    driver id. Two drivers who are identical on paper still come back in the
    same order on every run — which is what makes a dispatch decision one
    somebody can re-derive months later from the audit entry alone.
    """
    scored = [
        _score_one(
            candidate,
            pickup_lat=pickup_lat,
            pickup_lng=pickup_lng,
            weights=weights,
            max_radius_m=max_radius_m,
        )
        for candidate in candidates
    ]
    scored.sort(
        key=lambda row: (
            -row.score,
            row.candidate.completed_trips,
            row.candidate.driver_id,
            row.candidate.vehicle_id,
        )
    )
    return tuple(scored)
