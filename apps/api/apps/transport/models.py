"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        vehicle_class, transfer_corridor, transfer_tariff,
        driver_assignment, driver_offer

Phase 6 built the first three; Phase 9a adds the two assignment tables.
Both are invented too, and for the same reason: ADR 0029 records what §7.5
never specified about them.

**These tables are invented, and ADR 0023 records the invention.** §12.4 gives
`transfer_corridor` and `transfer_tariff` a field name and a one-line meaning
each and nothing else: no §7.5 specification, no §7.3 ERD box, no §7.4
relationship row, no §7.6 index. Everything below that is not in that field
list — types, nullability, the CHECKs, the exclusion constraints, `scope`,
`vehicle_class` as a table at all — is a decision, taken against the §7.2
conventions and written down in the ADR rather than left to be reverse-
engineered from a migration.

**Nothing here references `catalogue` in SQL.** `origin_destination_id`,
`region_id` and `country_id` are plain indexed `BigIntegerField`. §6.4 gives
`transport -> location, provider`, and a foreign key to `destination` would be
that forbidden edge expressed in DDL, where import-linter cannot see it
(ADR 0012). The cost is that the database will not stop somebody deleting a
destination out from under a corridor; the seed loader and the admin service
are what check, and they can, because they are allowed to look.

**Effective dating is enforced, not merely stored.** §12.4's ladder says "first
match wins". Two corridors matching the same (origin, target, class) on the
same day would make the winner an accident of primary key order — a coin flip
on a price. The exclusion constraints below make that state unrepresentable,
which is why this module creates `btree_gist`: PostgreSQL needs it to put an
equality operator and a range overlap in the same index.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.gis.db import models as gis_models
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import (
    DateRangeField,
    DateTimeRangeField,
    RangeBoundary,
    RangeOperators,
)
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Func, Q

from apps.common.models import SoftDeleteModel
from apps.transport.validators import validate_iso_currency_code

__all__ = [
    "DateRange",
    "TsRange",
    "TariffScope",
    "VehicleClass",
    "TransferCorridor",
    "TransferTariff",
    "AssignmentStatus",
    "OfferStatus",
    "LIVE_ASSIGNMENT_STATUSES",
    "DriverAssignment",
    "DriverOffer",
]


class DateRange(Func):
    """`DATERANGE(valid_from, valid_to, '[)')` as an index expression.

    Half-open on purpose. A tariff valid to the 31st and its successor valid
    from the 31st are adjacent, not overlapping, and a closed upper bound would
    reject the ordinary way one rate replaces another.
    """

    function = "DATERANGE"
    output_field = DateRangeField()


class TariffScope(models.TextChoices):
    """Which rung of §12.4's ladder a metered row answers on.

    Steps 3 and 4 are the same table with a different applicability, and the
    SRS field list gives only `region_id`. Making the level explicit rather
    than inferring it from which column is null means the ladder reads the
    column it means to read, and a row with both set is rejected by a CHECK
    instead of silently answering on whichever step is tried first.
    """

    REGION = "REGION", "Region"
    COUNTRY = "COUNTRY", "Country"


class VehicleClass(SoftDeleteModel):
    """§12.4's class table. ADR 0023 decision 6.

    §6.4 does not name this among `transport`'s tables and Appendix C counts it
    as a seeded entity anyway ("Vehicle class | 4"). Hard rule 5 settles it:
    seat and luggage capacities are the numbers §12.4 filters on, and a
    threshold in code is the thing NFR-M07 forbids.

    `seats` excludes the driver, as §7.5.5 says of `vehicle.seat_capacity` —
    a VAN with `seats = 7` carries seven passengers.
    """

    code = models.CharField(max_length=20)
    name = models.CharField(max_length=60)
    description = models.TextField(blank=True, default="")

    seats = models.PositiveSmallIntegerField(validators=[MinValueValidator(1)])
    luggage_capacity = models.PositiveSmallIntegerField(default=0)

    #: §12.4 distinguishes COMFORT from STANDARD by this and nothing else —
    #: both carry 4 passengers and 3 bags.
    has_air_conditioning = models.BooleanField(default=False)

    #: Presentation order for the §24.16 class cards. Not a price ordering:
    #: which class is cheapest depends on the corridor, so the cards are sorted
    #: by what they are, and the price is read off each one.
    display_order = models.PositiveSmallIntegerField(default=0)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "vehicle_class"
        ordering = ["display_order", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["code"],
                condition=Q(deleted_at__isnull=True),
                name="vehicle_class_code_unique_alive",
            ),
            models.CheckConstraint(
                condition=Q(seats__gte=1),
                name="vehicle_class_seats_positive",
            ),
        ]

    def __str__(self) -> str:
        return self.code


class TransferCorridor(SoftDeleteModel):
    """§12.4's fixed-price route. Steps 1 and 2 of the ladder.

    A corridor is the answer to "what does the airport run to Nungwi cost", and
    it needs no distance, no duration and no routing provider to give it. That
    is what makes Phase 6 deliverable while Appendix D-2 is still open: the
    legs a seeded catalogue can produce all resolve here, and the metered
    fallback below is exercised by tests rather than by tourists.

    `is_bidirectional` is step 2. One row prices ZNZ to Nungwi and Nungwi to
    ZNZ, because the fare is the fare; a corridor that is genuinely asymmetric
    gets two rows with the flag off.
    """

    origin_destination_id = models.BigIntegerField(db_index=True)
    target_destination_id = models.BigIntegerField(db_index=True)

    vehicle_class = models.ForeignKey(
        VehicleClass, on_delete=models.PROTECT, related_name="corridors"
    )

    fixed_price = models.DecimalField(
        max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])

    is_bidirectional = models.BooleanField(default=True)

    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True, default=None)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "transfer_corridor"
        ordering = ["origin_destination_id", "target_destination_id", "vehicle_class"]
        indexes = [
            models.Index(
                fields=["origin_destination_id", "target_destination_id", "vehicle_class"],
                name="transfer_corridor_lookup",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(fixed_price__gt=Decimal("0")),
                name="transfer_corridor_price_positive",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F("valid_from")),
                name="transfer_corridor_window_ordered",
            ),
            # §12.2: "A leg whose origin and target resolve to the same
            # coordinate is rejected at validation." A corridor from a place to
            # itself is that leg, priced in advance.
            models.CheckConstraint(
                condition=~Q(origin_destination_id=models.F("target_destination_id")),
                name="transfer_corridor_endpoints_differ",
            ),
            ExclusionConstraint(
                name="transfer_corridor_no_overlapping_window",
                expressions=[
                    ("origin_destination_id", RangeOperators.EQUAL),
                    ("target_destination_id", RangeOperators.EQUAL),
                    ("vehicle_class", RangeOperators.EQUAL),
                    (
                        DateRange("valid_from", "valid_to", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(deleted_at__isnull=True, is_active=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.origin_destination_id}->{self.target_destination_id} {self.vehicle_class_id}"


class TransferTariff(SoftDeleteModel):
    """§12.4's metered fallback. Steps 3 and 4 of the ladder.

    Every rate is a column rather than a constant because §12.4 is the pricing
    model for a platform that is meant to open a second destination without a
    deployment (§4.2). `NUNGWI_TRANSFER_PRICE = 38` is the prohibited example
    in the specification itself, and this table is the required instead.

    **`waiting_rate_per_minute` is stored and never read in v1.** §12.4 says it
    is "applied after free waiting allowance" and the SRS defines that
    allowance nowhere — no Appendix B key, no other mention. (`wait.airport_
    minutes` and `wait.standard_minutes` are no-show thresholds for dispatch,
    not billing allowances; using them here would invent a business rule.) The
    column exists so the schema does not preclude §38's SHOULD-HAVE vehicle
    hire, and `apps/transport/tests/test_tariff_model.py` asserts nothing reads
    it, so its arrival is a decision rather than a discovery.
    """

    scope = models.CharField(max_length=10, choices=TariffScope.choices)

    #: Exactly one of these is set, per `scope`. Both are `catalogue` rows and
    #: neither is a foreign key — see the module docstring.
    region_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    country_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)

    vehicle_class = models.ForeignKey(
        VehicleClass, on_delete=models.PROTECT, related_name="tariffs"
    )

    base_fare = models.DecimalField(max_digits=14, decimal_places=2)

    #: Four decimal places, not two. These are rates rather than amounts, and
    #: §12.4's own example uses 0.40 per kilometre — a per-metre equivalent
    #: quantized to a cent would be zero. The *result* is rounded once, at the
    #: end, by `domain.tariffs.metered`.
    per_km_rate = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))
    per_minute_rate = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal("0"))

    minimum_fare = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    night_surcharge_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"))
    #: Local time in the origin's zone, per §12.4: "IF pickup_at local time
    #: within [night_from, night_to]". A window that wraps midnight is normal
    #: and is handled in the domain, not here.
    night_from = models.TimeField(null=True, blank=True, default=None)
    night_to = models.TimeField(null=True, blank=True, default=None)

    airport_surcharge = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))

    waiting_rate_per_minute = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0")
    )

    currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])

    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True, default=None)

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "transfer_tariff"
        ordering = ["scope", "vehicle_class"]
        constraints = [
            # Scope coherence, in both directions — the same shape as
            # `destination_gateway_columns_coherent`. A COUNTRY row carrying a
            # region id would answer on step 4 while claiming to be step 3.
            models.CheckConstraint(
                condition=(
                    Q(scope=TariffScope.REGION, region_id__isnull=False, country_id__isnull=True)
                    | Q(
                        scope=TariffScope.COUNTRY,
                        country_id__isnull=False,
                        region_id__isnull=True,
                    )
                ),
                name="transfer_tariff_scope_columns_coherent",
            ),
            models.CheckConstraint(
                condition=(
                    Q(night_from__isnull=True, night_to__isnull=True)
                    | Q(night_from__isnull=False, night_to__isnull=False)
                ),
                name="transfer_tariff_night_window_coherent",
            ),
            # A surcharge with no window would apply never or always depending
            # on who read it. Requiring the window makes the intent explicit.
            models.CheckConstraint(
                condition=(Q(night_surcharge_pct=Decimal("0")) | Q(night_from__isnull=False)),
                name="transfer_tariff_night_surcharge_needs_a_window",
            ),
            models.CheckConstraint(
                condition=(
                    Q(base_fare__gte=Decimal("0"))
                    & Q(per_km_rate__gte=Decimal("0"))
                    & Q(per_minute_rate__gte=Decimal("0"))
                    & Q(minimum_fare__gte=Decimal("0"))
                    & Q(night_surcharge_pct__gte=Decimal("0"))
                    & Q(airport_surcharge__gte=Decimal("0"))
                    & Q(waiting_rate_per_minute__gte=Decimal("0"))
                ),
                name="transfer_tariff_rates_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(valid_to__isnull=True) | Q(valid_to__gte=models.F("valid_from")),
                name="transfer_tariff_window_ordered",
            ),
            # Two exclusion constraints rather than one, because NULL is not
            # equal to NULL: a single constraint over both id columns would
            # never fire for COUNTRY rows, whose `region_id` is null.
            ExclusionConstraint(
                name="transfer_tariff_no_overlapping_region_window",
                expressions=[
                    ("region_id", RangeOperators.EQUAL),
                    ("vehicle_class", RangeOperators.EQUAL),
                    (
                        DateRange("valid_from", "valid_to", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(deleted_at__isnull=True, is_active=True, scope=TariffScope.REGION),
            ),
            ExclusionConstraint(
                name="transfer_tariff_no_overlapping_country_window",
                expressions=[
                    ("country_id", RangeOperators.EQUAL),
                    ("vehicle_class", RangeOperators.EQUAL),
                    (
                        DateRange("valid_from", "valid_to", RangeBoundary()),
                        RangeOperators.OVERLAPS,
                    ),
                ],
                condition=Q(deleted_at__isnull=True, is_active=True, scope=TariffScope.COUNTRY),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.scope}:{self.region_id or self.country_id} {self.vehicle_class_id}"


class TsRange(Func):
    """`TSTZRANGE(starts_at, ends_at, '[)')` as an index expression.

    Half-open, for the reason `DateRange` is: a driver whose job ends at
    14:00 is free to start another at 14:00. A closed upper bound would refuse
    back-to-back work, which is how a transfer driver makes a living.
    """

    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class AssignmentStatus(models.TextChoices):
    """§11.7's machine, plus the one §11.8 adds.

    INCIDENT is not drawn in §11.7's diagram and is required by §11.8's
    "Driver sets INCIDENT with a reason" — a breakdown mid-trip is neither a
    completion nor a cancellation, and settling it pro rata needs a state that
    says so.
    """

    PENDING = "PENDING", "Pending"
    OFFERED = "OFFERED", "Offered"
    ASSIGNED = "ASSIGNED", "Assigned"
    EN_ROUTE = "EN_ROUTE", "En route"
    ARRIVED = "ARRIVED", "Arrived"
    STARTED = "STARTED", "Started"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"
    UNFULFILLED = "UNFULFILLED", "Unfulfilled"
    INCIDENT = "INCIDENT", "Incident"


#: The statuses in which a driver's diary is genuinely blocked. A completed or
#: abandoned job must stop excluding them from work, or a busy week would
#: permanently retire them — which is what makes the EXCLUDE constraint below
#: partial rather than absolute.
LIVE_ASSIGNMENT_STATUSES = (
    AssignmentStatus.ASSIGNED,
    AssignmentStatus.EN_ROUTE,
    AssignmentStatus.ARRIVED,
    AssignmentStatus.STARTED,
)


class OfferStatus(models.TextChoices):
    """One candidate's answer to one offer.

    SUPERSEDED is for the offer that was still open when the assignment was
    resolved another way — an administrator assigning manually under §11.8,
    say. It is distinct from EXPIRED because a driver's acceptance rate must
    not be penalised for an offer that was withdrawn out from under them.
    """

    SENT = "SENT", "Sent"
    ACCEPTED = "ACCEPTED", "Accepted"
    DECLINED = "DECLINED", "Declined"
    EXPIRED = "EXPIRED", "Expired"
    SUPERSEDED = "SUPERSEDED", "Superseded"


class DriverAssignment(SoftDeleteModel):
    """§7.3's `driver_assignment` box, R2, §11.7. Designed in ADR 0029.

    **`starts_at` and `ends_at` are the driver's unavailability, not the
    service.** §7.6 writes the exclusion constraint over
    `tstzrange(starts_at, ends_at)` and §11.6 rule 5 defines the window as
    `[pickup_at - pre_buffer, expected_end + post_buffer]`. Storing the
    buffered window is the only way both are true at once. The real pickup time
    stays on `booking_transfer.pickup_at`, which remains its single source; no
    service here computes one from the other.
    """

    #: → `booking.id`. R2 makes it `1 : 0..1`, so one live assignment per
    #: transfer. No FK: §6.4 forbids `transport -> booking` and a foreign key
    #: would be that edge written in DDL (ADR 0012).
    booking_id = models.BigIntegerField()

    #: → `driver.id` and `vehicle.id` (provider). Null until somebody accepts.
    driver_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    vehicle_id = models.BigIntegerField(null=True, blank=True, default=None)

    status = models.CharField(
        max_length=20, choices=AssignmentStatus.choices, default=AssignmentStatus.PENDING
    )

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    #: §11.7's guards are "inside geofence (300 m)" and "inside drop-off
    #: geofence", and both centres live on `booking_transfer` — a table this
    #: module may not read (§6.4). Snapshotted here for the same reason
    #: `starts_at` is: the alternative is a driver's ARRIVED having to be
    #: judged by a module that can see the booking, which would put §11.7's
    #: machine and the thing that enforces it in two different places.
    #:
    #: A snapshot is also correct on its own terms. §11.8 re-times a delayed
    #: flight but nothing moves an airport's meeting point, and an assignment
    #: already accepted should be judged against the place the driver agreed to.
    pickup_point = gis_models.PointField(geography=True, srid=4326, null=True, default=None)
    dropoff_point = gis_models.PointField(geography=True, srid=4326, null=True, default=None)

    #: §11.9: "generated at assignment and stored hashed". A column that could
    #: be read back would defeat the protocol it exists to support.
    pickup_pin_hash = models.CharField(max_length=255, null=True, blank=True, default=None)

    assigned_at = models.DateTimeField(null=True, blank=True, default=None)
    en_route_at = models.DateTimeField(null=True, blank=True, default=None)
    arrived_at = models.DateTimeField(null=True, blank=True, default=None)
    started_at = models.DateTimeField(null=True, blank=True, default=None)
    completed_at = models.DateTimeField(null=True, blank=True, default=None)

    #: §13: a transition attempted outside the geofence is *permitted* with a
    #: reason and flagged in the audit log. Stored as well as audited so the
    #: assignment itself carries the fact — a dispute reads one row.
    override_reason = models.CharField(max_length=200, null=True, blank=True, default=None)
    cancellation_reason = models.CharField(max_length=200, null=True, blank=True, default=None)
    incident_reason = models.CharField(max_length=200, null=True, blank=True, default=None)

    #: §7.6 names this table for optimistic locking.
    version = models.IntegerField(default=0)

    class Meta:
        db_table = "driver_assignment"
        ordering = ["starts_at", "id"]
        indexes = [
            models.Index(fields=["status", "starts_at"], name="assignment_due_idx"),
            models.Index(fields=["booking_id"], name="assignment_booking_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["booking_id"],
                condition=Q(deleted_at__isnull=True),
                name="assignment_one_per_booking_alive",
            ),
            models.CheckConstraint(
                condition=Q(status__in=AssignmentStatus.values),
                name="assignment_status_known",
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gt=models.F("starts_at")),
                name="assignment_window_is_forwards",
            ),
            # An assignment past PENDING/OFFERED names a driver, and one that
            # names a driver names the vehicle they are bringing. Either half
            # alone is a row nobody can act on: a tourist cannot be told what
            # to look for at arrivals (§11.4).
            models.CheckConstraint(
                condition=Q(driver_id__isnull=True, vehicle_id__isnull=True)
                | Q(driver_id__isnull=False, vehicle_id__isnull=False),
                name="assignment_driver_and_vehicle_together",
            ),
            # §7.6, verbatim: a driver cannot hold two overlapping assignments.
            # Partial, so only live work blocks the diary — see
            # LIVE_ASSIGNMENT_STATUSES. This is §11.6 rule 5's guarantee, and
            # TC-084 asserts *this*, not the eligibility filter that also
            # checks it.
            ExclusionConstraint(
                name="assignment_no_overlapping_work",
                expressions=[
                    ("driver_id", RangeOperators.EQUAL),
                    (TsRange("starts_at", "ends_at", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=Q(deleted_at__isnull=True, status__in=LIVE_ASSIGNMENT_STATUSES),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.public_id} {self.status}"


class DriverOffer(models.Model):
    """One offer to one candidate — §11.5. Designed in ADR 0029.

    Not a `SoftDeleteModel`: an offer becomes a historical record the moment it
    is answered, and its whole lifetime is `created_at` to `responded_at`.

    **`score_components` is on the row, not only in the audit entry.** §11.6
    requires that a provider dispute "can be answered with the exact
    computation", and a dispute is about one driver's offer. The audit entry
    holds the whole candidate list; this holds its own arithmetic, and it
    survives whatever retention the log has.
    """

    assignment = models.ForeignKey(
        DriverAssignment, on_delete=models.CASCADE, related_name="offers"
    )

    #: → `driver.id` (provider). No FK; see `DriverAssignment`.
    driver_id = models.BigIntegerField(db_index=True)
    vehicle_id = models.BigIntegerField()

    #: Where this driver came in §11.6's ranking. 1 is the top candidate.
    rank = models.SmallIntegerField()
    score = models.DecimalField(max_digits=6, decimal_places=5)
    score_components = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=OfferStatus.choices, default=OfferStatus.SENT)
    expires_at = models.DateTimeField()
    responded_at = models.DateTimeField(null=True, blank=True, default=None)
    decline_reason = models.CharField(max_length=200, null=True, blank=True, default=None)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "driver_offer"
        ordering = ["assignment_id", "rank", "id"]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="offer_expiry_sweep_idx"),
            models.Index(fields=["driver_id", "status"], name="offer_driver_idx"),
        ]
        constraints = [
            # §11.5 offers to `candidate[i]`, singular, and advances. Two live
            # offers would make TC-083's race a data error rather than a
            # contended one — and would let two drivers both be told the job
            # is theirs.
            models.UniqueConstraint(
                fields=["assignment"],
                condition=Q(status=OfferStatus.SENT),
                name="offer_one_live_per_assignment",
            ),
            # A driver is asked about a given job once. Re-offering after a
            # decline is how a dispatcher loop with an off-by-one quietly
            # harasses somebody.
            models.UniqueConstraint(
                fields=["assignment", "driver_id"],
                name="offer_one_per_driver_per_assignment",
            ),
            models.CheckConstraint(
                condition=Q(status__in=OfferStatus.values), name="offer_status_known"
            ),
            models.CheckConstraint(condition=Q(rank__gte=1), name="offer_rank_is_a_place"),
            models.CheckConstraint(
                condition=Q(score__gte=0) & Q(score__lte=1),
                name="offer_score_is_a_fraction",
            ),
        ]

    def __str__(self) -> str:
        return f"offer {self.pk} to driver {self.driver_id} ({self.status})"
