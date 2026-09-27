"""§11.6's scoring function, and the three promises it makes.

§11.6 asks for a dispatcher that is deterministic, transparent and tunable.
Those are the three things asserted here, because each is a promise made to
somebody outside the codebase: a driver who wants to know why they were not
offered the airport run, a provider disputing an allocation, and an
administrator who has to be able to change the platform's behaviour without a
deployment.

The figures come from §11.6's own defaults — 0.40 / 0.25 / 0.20 / 0.10 / 0.05,
a 60 km radius, a 200-trip experience ceiling — so the arithmetic in these
tests can be checked against the specification rather than against the code.

No database. This module is pure and this file keeps it that way; a test that
needed a row would be a test of the wrong thing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.transport.domain.dispatch import (
    Anchor,
    Candidate,
    Weights,
    WeightsError,
    proximity_score,
    rank,
)

#: §11.6's defaults.
DEFAULTS = Weights(
    proximity=Decimal("0.40"),
    rating=Decimal("0.25"),
    acceptance=Decimal("0.20"),
    experience=Decimal("0.10"),
    utilisation=Decimal("0.05"),
)
RADIUS = 60_000

#: Stone Town. Every pickup in this file is here.
PICKUP = (-6.163, 39.191)

#: About 1.1 km north of the pickup — a driver waiting nearby.
NEARBY = Anchor(lat=-6.153, lng=39.191, is_live=True)
#: Nungwi, the far end of the island.
FAR = Anchor(lat=-5.726, lng=39.295, is_live=True)


def a_candidate(driver_id: int = 1, **overrides: object) -> Candidate:
    values: dict[str, object] = {
        "driver_id": driver_id,
        "vehicle_id": driver_id * 10,
        "anchor": NEARBY,
        "rating_avg": Decimal("5.00"),
        "acceptance_rate": Decimal("100.00"),
        "completed_trips": 200,
        "utilisation_today": Decimal("0"),
    }
    values.update(overrides)
    return Candidate(**values)  # type: ignore[arg-type]


def ranked(*candidates: Candidate) -> tuple[int, ...]:
    return tuple(
        row.candidate.driver_id
        for row in rank(
            candidates,
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )
    )


class TestTheWeights:
    def test_a_weight_set_that_does_not_sum_to_one_is_refused(self) -> None:
        """An administrator editing one row and not the others would otherwise
        rescale every score on the platform, silently and everywhere."""
        with pytest.raises(WeightsError, match="sum to 1.0"):
            Weights(
                proximity=Decimal("0.50"),
                rating=Decimal("0.25"),
                acceptance=Decimal("0.20"),
                experience=Decimal("0.10"),
                utilisation=Decimal("0.05"),
            )

    def test_a_negative_weight_is_refused(self) -> None:
        """A negative weight inverts a component: it would make a worse driver
        rank higher, and the sum check alone would not catch it."""
        with pytest.raises(WeightsError, match="negative"):
            Weights(
                proximity=Decimal("1.10"),
                rating=Decimal("-0.10"),
                acceptance=Decimal("0"),
                experience=Decimal("0"),
                utilisation=Decimal("0"),
            )

    def test_the_specification_defaults_are_a_valid_set(self) -> None:
        assert DEFAULTS.proximity == Decimal("0.40")


class TestProximity:
    def test_a_driver_at_the_pickup_scores_one(self) -> None:
        assert proximity_score(0.0, max_radius_m=RADIUS) == Decimal("1")

    def test_a_driver_at_the_radius_scores_zero(self) -> None:
        assert proximity_score(float(RADIUS), max_radius_m=RADIUS) == Decimal("0")

    def test_a_driver_beyond_the_radius_scores_zero_and_not_less(self) -> None:
        """The radius is where proximity stops discriminating, not where a
        driver stops being a candidate — exclusion is rule 6's job. A negative
        score here would let distance veto a rating."""
        assert proximity_score(float(RADIUS) * 4, max_radius_m=RADIUS) == Decimal("0")

    def test_halfway_scores_a_half(self) -> None:
        assert proximity_score(30_000.0, max_radius_m=RADIUS) == Decimal("0.5")

    def test_a_radius_of_zero_is_refused(self) -> None:
        with pytest.raises(WeightsError, match="positive"):
            proximity_score(100.0, max_radius_m=0)


class TestTheScore:
    def test_a_perfect_driver_at_the_pickup_scores_one(self) -> None:
        """Every component normalised to [0, 1] and the weights summing to 1
        means the score is in [0, 1] too — which is what makes two scores from
        different runs comparable at all."""
        perfect = Candidate(
            driver_id=1,
            vehicle_id=10,
            anchor=Anchor(lat=PICKUP[0], lng=PICKUP[1], is_live=True),
            rating_avg=Decimal("5.00"),
            acceptance_rate=Decimal("100.00"),
            completed_trips=200,
            utilisation_today=Decimal("0"),
        )

        [scored] = rank(
            (perfect,),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert scored.score == Decimal("1.00")

    def test_a_driver_with_nothing_going_for_them_scores_zero(self) -> None:
        nobody = Candidate(
            driver_id=1,
            vehicle_id=10,
            anchor=Anchor(lat=-5.0, lng=40.5, is_live=False),
            rating_avg=Decimal("0"),
            acceptance_rate=Decimal("0"),
            completed_trips=0,
            utilisation_today=Decimal("1"),
        )

        [scored] = rank(
            (nobody,),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert scored.score == Decimal("0.00")

    def test_experience_stops_counting_at_two_hundred_trips(self) -> None:
        """§11.6's `min(completed_trips / 200, 1)`. A veteran of two thousand
        trips does not outrank one of two hundred on experience, which is what
        stops the busiest driver taking everything."""
        veteran = rank(
            (a_candidate(completed_trips=2000),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )[0]
        experienced = rank(
            (a_candidate(completed_trips=200),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )[0]

        assert veteran.experience == experienced.experience == Decimal("1")

    def test_a_busy_driver_is_ranked_below_an_idle_one(self) -> None:
        """§11.6's `1 - utilisation_today`: the component spreads work, so it
        counts *down* as the day fills up."""
        assert ranked(
            a_candidate(1, utilisation_today=Decimal("0.9")),
            a_candidate(2, utilisation_today=Decimal("0.1")),
        ) == (2, 1)

    def test_a_stored_figure_out_of_range_cannot_push_a_score_out_of_range(self) -> None:
        """A rating above five or a utilisation above one is bad data, not a
        reason for a score to leave [0, 1] and outrank everybody."""
        [scored] = rank(
            (a_candidate(rating_avg=Decimal("7.00"), utilisation_today=Decimal("3")),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert scored.rating == Decimal("1")
        assert scored.utilisation == Decimal("0")
        assert scored.score <= Decimal("1")

    def test_a_negative_stored_figure_cannot_push_a_score_below_zero_either(self) -> None:
        """The other direction. An acceptance rate that has gone negative is a
        bug somewhere upstream, and the dispatcher's job is to rank drivers in
        spite of it rather than to produce a score nothing can compare."""
        [scored] = rank(
            (a_candidate(acceptance_rate=Decimal("-40.00")),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert scored.acceptance == Decimal("0")
        assert scored.score >= Decimal("0")


class TestTheOrder:
    def test_the_nearer_driver_wins_all_else_equal(self) -> None:
        assert ranked(a_candidate(1, anchor=FAR), a_candidate(2, anchor=NEARBY)) == (2, 1)

    def test_proximity_outweighs_rating_which_is_the_point_of_the_weights(self) -> None:
        """0.40 against 0.25: a perfect driver an island away loses to a good
        one round the corner, because a tourist waiting at arrivals cares more
        about the second."""
        assert ranked(
            a_candidate(1, anchor=FAR, rating_avg=Decimal("5.00")),
            a_candidate(2, anchor=NEARBY, rating_avg=Decimal("4.00")),
        ) == (2, 1)

    def test_a_tie_is_broken_towards_the_less_experienced_driver(self) -> None:
        """§11.6's tie-break, and its stated reason: it favours supply
        development.

        Both drivers are past the 200-trip ceiling, so their *scores* are
        identical however far past it they are — and that is exactly when the
        tie-break has anything to do. The one with fewer trips behind them is
        offered the work that lets them stop being the one with fewer trips.
        """
        assert ranked(
            a_candidate(1, completed_trips=2000),
            a_candidate(2, completed_trips=200),
        ) == (2, 1)

    def test_a_complete_tie_is_broken_by_id_so_the_order_is_total(self) -> None:
        """Without this the order of two identical drivers would depend on the
        order rows came back in, and a dispatch decision could not be
        re-derived from the audit entry."""
        assert ranked(a_candidate(7), a_candidate(3), a_candidate(5)) == (3, 5, 7)

    def test_the_same_candidates_rank_the_same_way_every_time(self) -> None:
        """Deterministic, §11.6's first word."""
        candidates = (
            a_candidate(4, anchor=FAR, rating_avg=Decimal("4.20")),
            a_candidate(2, completed_trips=17),
            a_candidate(9, acceptance_rate=Decimal("62.50")),
        )

        assert ranked(*candidates) == ranked(*candidates) == ranked(*candidates)

    def test_an_empty_candidate_list_ranks_to_nothing(self) -> None:
        """TC-090's precondition. An empty answer is an ordinary one."""
        assert ranked() == ()


class TestWhatTheAuditEntryWillSay:
    def test_every_component_survives_the_decision(self) -> None:
        """§11.6: "a provider dispute can be answered with the exact
        computation". A total alone answers nothing."""
        [scored] = rank(
            (a_candidate(rating_avg=Decimal("4.00")),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        components = scored.components()

        assert set(components) == {
            "proximity",
            "rating",
            "acceptance",
            "experience",
            "utilisation",
            "score",
            "distance_m",
            "anchor_is_live",
        }
        assert components["rating"] == "0.80"

    def test_the_components_are_strings_so_no_float_reaches_the_audit_log(self) -> None:
        [scored] = rank(
            (a_candidate(),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert all(isinstance(value, str) for value in scored.components().values())

    def test_a_guessed_anchor_says_so(self) -> None:
        """ "We did not know where they were" is a different answer from "they
        were far away", and a driver disputing a low proximity score is
        entitled to know which one it was."""
        [scored] = rank(
            (a_candidate(anchor=Anchor(lat=-6.2, lng=39.2, is_live=False)),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert scored.components()["anchor_is_live"] == "false"

    def test_the_distance_is_reported_in_whole_metres(self) -> None:
        """Nungwi to Stone Town is about 50 km by air. The figure is there so a
        dispute can be checked against a map, not so it can be recomputed."""
        [scored] = rank(
            (a_candidate(anchor=FAR),),
            pickup_lat=PICKUP[0],
            pickup_lng=PICKUP[1],
            weights=DEFAULTS,
            max_radius_m=RADIUS,
        )

        assert 45_000 < scored.distance_m < 55_000
        assert "." not in scored.components()["distance_m"]
