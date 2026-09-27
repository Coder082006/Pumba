"""§11.6's dispatch anchor — where a driver was, and whether we still believe it.

One behaviour carries almost all the risk here, and it is the freshness rule.
Proximity is the heaviest term in §11.6's score at 0.40, so a stale position
returned as though it were current does not merely misinform the dispatcher —
it actively sends the airport run to the driver who lost signal near the airport
an hour ago, over the one who is actually outside. Returning nothing is the
safer wrong answer, because §11.6 has a defined fallback for it.

The other assertions are about `recorded_at` meaning the device's clock rather
than ours, which is what makes a buffered upload out of a tunnel (§33) place a
driver where they were rather than where they are.

Nothing here creates a `driver`. §6.4 gives this module no dependencies at all,
so the column is a bare integer by design (ADR 0012).
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.db import IntegrityError, transaction

from apps.location import services
from apps.location.models import DriverLocation

pytestmark = pytest.mark.django_db

NOW = dt.datetime(2027, 6, 1, 12, 0, tzinfo=dt.UTC)
FRESH = dt.timedelta(minutes=15)

#: Stone Town and Nungwi.
TOWN = (-6.163, 39.191)
NUNGWI = (-5.726, 39.295)


def a_reading(driver_id: int = 1, *, minutes_ago: int = 0, at: tuple[float, float] = TOWN, **kw):
    return DriverLocation.objects.create(
        driver_id=driver_id,
        point=Point(at[1], at[0], srid=4326),
        recorded_at=NOW - dt.timedelta(minutes=minutes_ago),
        **kw,
    )


class TestRecordingAPosition:
    def test_a_reading_is_stored_as_given(self) -> None:
        dto = services.record_position(
            driver_id=1,
            lat=TOWN[0],
            lng=TOWN[1],
            recorded_at=NOW,
            accuracy_m=12,
            speed_kph=Decimal("41.5"),
            heading=180,
        )

        assert dto.driver_id == 1
        assert round(dto.lat, 3) == TOWN[0]
        assert round(dto.lng, 3) == TOWN[1]
        assert dto.accuracy_m == 12

    def test_nothing_is_filtered_out_at_write_time(self) -> None:
        """§33's plausibility rules are Phase 10's, and applying them here
        would discard the evidence they are checked against. `driver_location`
        rows are also what §20.10 reads to settle a no-show dispute, so a
        discarded reading is a dispute nobody can answer."""
        dto = services.record_position(
            driver_id=1, lat=TOWN[0], lng=TOWN[1], recorded_at=NOW, accuracy_m=3000
        )

        assert dto.accuracy_m == 3000

    def test_a_reading_carries_no_public_id(self) -> None:
        """A position is never addressed individually over the wire and there
        will be millions a week. Two unread columns are two columns of index
        and storage per row."""
        assert not hasattr(a_reading(), "public_id")

    def test_an_impossible_bearing_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            a_reading(heading=400)

    def test_a_negative_accuracy_is_refused(self) -> None:
        """Accuracy is a radius. A negative one is a device lying about its own
        uncertainty, and §33's checks would read it as perfect confidence."""
        with pytest.raises(IntegrityError), transaction.atomic():
            a_reading(accuracy_m=-1)

    def test_a_negative_speed_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            a_reading(speed_kph=Decimal("-5.0"))


class TestTheAnchor:
    def test_a_fresh_reading_comes_back(self) -> None:
        a_reading(1, minutes_ago=3)

        found = services.last_known([1], now=NOW, within=FRESH)

        assert found[1].driver_id == 1
        assert round(found[1].lat, 3) == TOWN[0]

    def test_a_stale_reading_does_not(self) -> None:
        """§11.6: "if online and fresh (< 15 min)". This is the assertion that
        matters most in the file — a stale position scores a driver as nearby
        because that is where they were before their phone lost signal, and
        proximity is 40% of the score."""
        a_reading(1, minutes_ago=90)

        assert services.last_known([1], now=NOW, within=FRESH) == {}

    def test_absence_is_the_answer_rather_than_an_error(self) -> None:
        """§11.6 has a defined fallback — the home destination's centroid — so
        "we do not know" has to be an ordinary answer the dispatcher can act
        on, not something it has to catch."""
        assert services.last_known([1, 2, 3], now=NOW, within=FRESH) == {}

    def test_only_the_latest_reading_is_returned(self) -> None:
        """A driver reports every few seconds while online. The anchor is where
        they are, not a history."""
        a_reading(1, minutes_ago=10, at=NUNGWI)
        a_reading(1, minutes_ago=1, at=TOWN)

        found = services.last_known([1], now=NOW, within=FRESH)

        assert len(found) == 1
        assert round(found[1].lat, 3) == TOWN[0]

    def test_several_drivers_are_answered_in_one_query(self) -> None:
        """A dispatch run scores every eligible driver. A per-driver lookup
        would make the query count a function of how healthy the supply is —
        so more drivers available would mean a slower dispatch."""
        a_reading(1, minutes_ago=2)
        a_reading(2, minutes_ago=4, at=NUNGWI)
        a_reading(3, minutes_ago=200)

        with_positions = services.last_known([1, 2, 3], now=NOW, within=FRESH)

        assert set(with_positions) == {1, 2}

    def test_asking_about_nobody_asks_the_database_nothing(self) -> None:
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as queries:
            assert services.last_known([], now=NOW, within=FRESH) == {}

        assert len(queries) == 0

    def test_the_window_is_the_callers_to_choose(self) -> None:
        """A dispatcher scoring a job three days out and a tracking screen
        following a car ask different questions of this table. A constant here
        would answer both with whichever was written first."""
        a_reading(1, minutes_ago=45)

        assert services.last_known([1], now=NOW, within=FRESH) == {}
        assert 1 in services.last_known([1], now=NOW, within=dt.timedelta(hours=2))

    def test_freshness_is_measured_on_the_devices_clock(self) -> None:
        """`recorded_at`, not `received_at`. A driver leaving a tunnel uploads
        a buffered burst (§33); treating arrival as the reading time would
        place them where they were minutes ago and score them on it."""
        stale_but_just_arrived = a_reading(1, minutes_ago=120)

        assert stale_but_just_arrived.received_at is not None
        assert services.last_known([1], now=NOW, within=FRESH) == {}
