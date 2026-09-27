"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        route_cache, driver_location, geofence

Phase 9a builds **one** of the three, and only the part of it dispatch needs.
ADR 0029 records why, and the reasons are different for each:

- **`driver_location`** arrives now because §11.6's scoring anchor is
  `last_known_location if online and fresh (< 15 min)`, and without it every
  driver is scored from their home destination — which makes proximity, the
  heaviest weight in the function at 0.40, almost meaningless. What arrives is
  a position and a time and nothing else. Daily partitioning, the 30-day
  retention of §31.5, `purge_driver_locations` (§8.8), ingest plausibility
  checks (§33) and the WebSocket fan-out are Phase 10's, which is where they
  have a consumer and an access pattern to be designed against.

- **`geofence`** does not arrive. §11.7's guards are a distance from
  `booking_transfer.pickup_point` and `dropoff_point`, both of which are
  already stored, so there is nothing to put in a row here that is not already
  in one there. What needs the table is Phase 10's `DRIVER_NEARBY` at 1,500 m
  and the geofence-*triggered* events, which fire on a named region belonging
  to no single booking.

- **`route_cache`** does not arrive either, and has not been needed: §12.6's
  estimates go through `RoutingPort` and a corridor priced over an approximate
  distance records that fact on the booking (ADR 0023). A cache is a
  performance decision, and there is no traffic yet to make it from.

**No foreign key to `driver`.** §6.4 gives `location -> —` : this module
depends on nothing, which is stronger than every other module's contract. A
foreign key to `provider.driver` would be that contract broken in DDL where
import-linter cannot see it (ADR 0012).
"""

from __future__ import annotations

from django.contrib.gis.db import models as gis_models
from django.db import models

__all__ = ["DriverLocation"]


class DriverLocation(models.Model):
    """Where a driver was, and when — §13, ADR 0029.

    Append-only in practice and not by trigger: nothing in the system updates a
    position, because a correction to a GPS reading is a new reading. The
    trigger the three genuinely append-only tables carry is deliberately not
    here — it would have to survive Phase 10 turning this into a partitioned
    table, and a constraint that has to be dropped and recreated during a
    migration is a constraint that will be absent at exactly the wrong moment.

    No `public_id` and no `updated_at`: a position is never addressed
    individually over the wire, never amended, and there will eventually be
    millions of them per week. Two columns nothing reads are two columns per
    row of index and storage.
    """

    #: → `driver.id` (provider). No FK; see the module docstring.
    driver_id = models.BigIntegerField()

    point = gis_models.PointField(geography=True, srid=4326)

    #: Metres of horizontal error the device claimed. §11.6 does not use it and
    #: §33's plausibility checks will: a 3 km accuracy radius is a position that
    #: should not move a dispatch decision.
    accuracy_m = models.IntegerField(null=True, blank=True, default=None)

    speed_kph = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True, default=None
    )
    #: Degrees clockwise from north, 0-359.
    heading = models.SmallIntegerField(null=True, blank=True, default=None)

    #: When the *device* recorded it, which is not when we received it. A
    #: driver coming out of a tunnel uploads a buffered burst (§33), and
    #: treating the arrival time as the reading time would place them where
    #: they were minutes ago and score them on it.
    recorded_at = models.DateTimeField(db_index=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "driver_location"
        # §7.6: "Time-series scan; proximity". The composite descending index is
        # what makes `last_known()` a single-row lookup per driver rather than a
        # sort of everything they have ever reported.
        indexes = [
            models.Index(fields=["driver_id", "-recorded_at"], name="driver_location_latest_idx"),
            gis_models.Index(fields=["point"], name="driver_location_point_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(accuracy_m__isnull=True) | models.Q(accuracy_m__gte=0),
                name="driver_location_accuracy_non_negative",
            ),
            models.CheckConstraint(
                condition=models.Q(heading__isnull=True)
                | (models.Q(heading__gte=0) & models.Q(heading__lte=359)),
                name="driver_location_heading_is_a_bearing",
            ),
            models.CheckConstraint(
                condition=models.Q(speed_kph__isnull=True) | models.Q(speed_kph__gte=0),
                name="driver_location_speed_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"driver {self.driver_id} at {self.recorded_at:%Y-%m-%d %H:%M}"
