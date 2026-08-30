"""The travel-time resolver — SRS §10.5, §12.6, ADR 0019.

Three things are worth holding here, and two of them are about what the
resolver refuses to do.

* **The precedence is the specification's**, and each tier is checked to
  actually take priority rather than merely being present in the code.
* **An unknown place raises.** Returning a plausible duration for a location
  nobody registered would bury a planner bug under a number a tourist plans
  around.
* **The absent tiers are asserted as absent**, with their reason, in the same
  shape as `test_ports_registry.py`. `route_cache` and the destination-pair
  matrix are caches of a routing provider's answers, and D-2 has not chosen
  one; creating the tables now would be schema written to by nothing.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.apps import apps as django_apps

from apps.common.geo import Coordinates
from apps.common.geo import EstimateQuality as GeoQuality
from apps.trip.domain.sequencing import TravelEstimate
from apps.trip.models import EstimateQuality as ModelQuality
from apps.trip.travel import UnknownPlaceError, build_travel_time, place_key

#: Two points in a market deliberately unlike Zanzibar, matching the rest of
#: the trip fixtures — roughly 9 km apart.
HERE = Coordinates(lat=Decimal("-35.2800000"), lon=Decimal("174.0500000"))
THERE = Coordinates(lat=Decimal("-35.2200000"), lon=Decimal("173.9800000"))

PLACES = {"a": HERE, "b": THERE}


def resolver(**overrides: object):
    kwargs: dict[str, object] = {
        "road_factor": Decimal("1.35"),
        "speed_kmh": Decimal("45"),
    }
    kwargs.update(overrides)
    return build_travel_time(PLACES, **kwargs)  # type: ignore[arg-type]


class TestTheApproximateTier:
    def test_it_answers_when_nothing_else_can(self) -> None:
        estimate = resolver()("a", "b")
        assert estimate.seconds > 0
        assert estimate.metres > 0

    def test_it_is_marked_approximate(self) -> None:
        """§12.6 requires the mark, and §24 renders it as an explicit label.
        A leg that reached the screen without it would be a guess presented as
        a measurement."""
        assert resolver()("a", "b").quality == "APPROXIMATE"

    def test_the_road_factor_moves_the_distance(self) -> None:
        """It is a parameter and a `system_setting`, never a constant — the
        whole argument of NFR-M07 and of ADR 0019's closing paragraph."""
        low = resolver(road_factor=Decimal("1.0"))("a", "b")
        high = resolver(road_factor=Decimal("2.0"))("a", "b")
        assert high.metres > low.metres

    def test_the_speed_model_moves_the_duration(self) -> None:
        slow = resolver(speed_kmh=Decimal("20"))("a", "b")
        fast = resolver(speed_kmh=Decimal("90"))("a", "b")
        assert slow.seconds > fast.seconds

    def test_the_defaults_come_from_system_setting(self) -> None:
        """Built with no overrides at all, so the register is the source."""
        assert build_travel_time(PLACES)("a", "b").quality == "APPROXIMATE"


class TestPrecedence:
    """§12.6: route_cache, else the matrix, else haversine."""

    def _fixed(self, quality: str) -> object:
        def lookup(origin: str, target: str) -> TravelEstimate:
            return TravelEstimate(seconds=111, metres=222, quality=quality)

        return lookup

    def test_the_cache_wins(self) -> None:
        estimate = resolver(from_cache=self._fixed("ROUTED"), from_matrix=self._fixed("MATRIX"))(
            "a", "b"
        )
        assert estimate.quality == "ROUTED"
        assert estimate.seconds == 111

    def test_the_matrix_is_used_when_the_cache_misses(self) -> None:
        """A tier that returns `None` defers rather than answering. Without
        that distinction a cache miss would be indistinguishable from a cache
        entry of zero."""
        estimate = resolver(
            from_cache=lambda origin, target: None, from_matrix=self._fixed("MATRIX")
        )("a", "b")
        assert estimate.quality == "MATRIX"

    def test_haversine_is_reached_when_both_miss(self) -> None:
        estimate = resolver(
            from_cache=lambda origin, target: None,
            from_matrix=lambda origin, target: None,
        )("a", "b")
        assert estimate.quality == "APPROXIMATE"

    def test_a_routed_answer_never_consults_the_coordinates(self) -> None:
        """The tier that answers is authoritative. A provider's duration must
        not be quietly replaced by a modelled one — they would disagree on
        screen, and the worse number would win."""
        empty = build_travel_time({}, from_cache=self._fixed("ROUTED"))
        assert empty("nowhere", "elsewhere").seconds == 111


class TestUnknownPlaces:
    def test_an_unregistered_origin_raises(self) -> None:
        with pytest.raises(UnknownPlaceError, match="will not invent one"):
            resolver()("missing", "b")

    def test_an_unregistered_target_raises(self) -> None:
        with pytest.raises(UnknownPlaceError):
            resolver()("a", "missing")

    def test_the_message_names_the_key(self) -> None:
        """A planner bug, and the fix is to register the coordinate — so the
        error has to say which one is absent."""
        with pytest.raises(UnknownPlaceError, match="missing"):
            resolver()("missing", "b")


class TestPlaceKey:
    def test_it_is_readable(self) -> None:
        """These appear in transfer titles and in test failures.
        `accommodation:41` is diagnosable where a hash is not."""
        assert place_key("accommodation", 41) == "accommodation:41"

    def test_different_kinds_do_not_collide(self) -> None:
        assert place_key("accommodation", 1) != place_key("attraction", 1)


class TestTheTwoQualityVocabularies:
    """`common.geo` and `trip.models` describe quality differently on purpose.

    `common.geo.EstimateQuality` is binary — MEASURED or APPROXIMATE — because
    that is the distinction §12.6 draws for *pricing*: a measured figure may be
    quoted and an approximate one may not.

    `trip.models.EstimateQuality` splits MEASURED into ROUTED and MATRIX,
    because §12.6's planning row lists three sources and knowing whether a leg
    came from the live provider or from last night's precomputation is worth
    having on the row.

    They meet at exactly one value, and this pins that they still agree on it.
    """

    def test_approximate_means_the_same_thing_in_both(self) -> None:
        assert GeoQuality.APPROXIMATE.value == ModelQuality.APPROXIMATE.value

    def test_every_quality_this_module_produces_is_storable(self) -> None:
        """The one that would break silently. The resolver returns a plain
        string, and the model has choices — so a value produced here that the
        model does not recognise would insert and then fail a constraint far
        from the code that chose it.
        """
        produced = {resolver()("a", "b").quality}
        assert produced <= {q.value for q in ModelQuality}

    def test_measured_is_deliberately_not_a_model_value(self) -> None:
        """It is not an oversight. A row records *which* source answered, and
        MEASURED would erase that distinction — so an adapter must map its
        answer onto ROUTED or MATRIX rather than passing MEASURED through.
        """
        assert GeoQuality.MEASURED.value not in {q.value for q in ModelQuality}


class TestTheTiersThatDoNotExistYet:
    """ADR 0019 and Appendix D-2, in the shape of `test_ports_registry.py`."""

    #: Tables §10.5 names, with the reason each is absent. Adding one here is
    #: not enough on its own — the test below re-proves it is really absent.
    DEFERRED_TABLES = {
        "route_cache": (
            "§10.5 caches routing-provider answers keyed by geohash. Appendix "
            "D-2 has not chosen a provider, so there are no answers to cache "
            "and the table would be written to by nothing. It arrives with the "
            "adapter that fills it."
        ),
        "destination_pair_matrix": (
            "§10.5's nightly precomputation, built by calling the routing "
            "port's matrix product for every active destination pair. Same "
            "reason: no provider, nothing to precompute."
        ),
    }

    def test_neither_table_exists(self) -> None:
        existing = {model._meta.db_table for model in django_apps.get_models()}
        for table, reason in self.DEFERRED_TABLES.items():
            assert table not in existing, (
                f"{table} now exists. {reason} If D-2 has been decided, this "
                "test is the place to record that first."
            )

    def test_each_absence_states_its_reason(self) -> None:
        for table, reason in self.DEFERRED_TABLES.items():
            assert "D-2" in reason or "provider" in reason, table
            assert len(reason) > 60

    def test_the_resolver_works_without_them(self) -> None:
        """The point of the arrangement: planning is not blocked on D-2.
        Sequencing gets a real number today, labelled for what it is."""
        assert build_travel_time(PLACES)("a", "b").seconds > 0
