"""trip module — SRS §6.4.

    Owns:       trip, itinerary, itinerary_item, trip_flight
    Interface:  create_trip(), regenerate_itinerary(), validate_itinerary()
    Depends on: catalogue, transport
    Layer:      L3

Data-access layer (SRS §8.2 layer 4). Schema from §7.5.10, §7.5.11 and the
§7.3 ERD; behaviour from §10.

Three things are worth stating before the columns, because each is a decision
that a reader would otherwise have to reverse-engineer.

**Every reference to a row another module owns is a plain integer.** ADR 0012:
a `ForeignKey` installs a traversable attribute, and `item.activity.provider`
reachable from trip code is the §6.4 boundary gone — not by an import anybody
wrote, but by one the ORM created. So `tourist_id`, `destination_id`,
`accommodation_id`, `activity_id`, `attraction_id`, the two destination
endpoints and `booking_id` are `BigIntegerField`. The referential integrity is
not abandoned: this module's own migration adds each `FOREIGN KEY` in raw SQL,
because a migration dependency is a string and not an import. Inside `trip` a
`ForeignKey` is still the right tool, so `itinerary.trip`, `itinerary_item.itinerary`
and `trip_flight.trip` are real ones.

**A STAY item is a stay anchor, not a booking.** ADR 0013 removed room types,
rates and accommodation inventory from v1. What is left fixes *where the
tourist sleeps and on which nights*, so the planner can put transfers around
it. It therefore carries no price, no currency and no booking, and the
constraint below enforces that rather than trusting it — the columns remain on
the table because ACTIVITY, TRANSFER and ATTRACTION items use them.

**A TRANSFER records how confident its numbers are.** ADR 0019: §12.6 permits
a haversine estimate at planning time, provided the item is marked
`APPROXIMATE` and the UI says so. `estimate_quality` is that mark. It is NOT
NULL for a transfer and NULL for everything else, so a leg cannot reach the
screen without declaring where its duration came from, and the Phase 6/7 quote
path has something to refuse.
"""

from __future__ import annotations

from django.contrib.gis.db import models as gis_models
from django.db import models

from apps.common.models import BaseModel, TimestampedModel, VersionedModel
from apps.trip.validators import validate_iso_currency_code

__all__ = [
    "TripStatus",
    "ItemType",
    "FlightDirection",
    "EstimateQuality",
    "ValidationState",
    "Trip",
    "Itinerary",
    "ItineraryItem",
    "TripFlight",
]


class TripStatus(models.TextChoices):
    """§20.5. The transition table lives in `domain/lifecycle.py`.

    Seven states, and the same seven appear in §7.5.10, §20.5 and Appendix A.
    No eighth state exists anywhere in the specification, so a new one here is
    a specification change rather than an implementation detail.
    """

    DRAFT = "DRAFT", "Draft"
    PRICED = "PRICED", "Priced"
    PENDING_PAYMENT = "PENDING_PAYMENT", "Pending payment"
    CONFIRMED = "CONFIRMED", "Confirmed"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


class ItemType(models.TextChoices):
    """§7.5.11. The tie-break rank for identical start times is §10.4's, and
    lives in `domain/sequencing.py` — it is an ordering rule, not a property
    of the enum."""

    STAY = "STAY", "Stay"
    ACTIVITY = "ACTIVITY", "Activity"
    TRANSFER = "TRANSFER", "Transfer"
    ATTRACTION = "ATTRACTION", "Attraction"
    FREE_TIME = "FREE_TIME", "Free time"


class FlightDirection(models.TextChoices):
    """§7.3 ERD. R19 is 1 : 0..2 — at most one of each."""

    INBOUND = "INBOUND", "Inbound"
    OUTBOUND = "OUTBOUND", "Outbound"


class EstimateQuality(models.TextChoices):
    """Where a transfer's distance and duration came from — ADR 0019, §12.6.

    Ordered best to worst, and the order is the §12.6 precedence: a resolver
    tries them in this sequence and stops at the first that answers.
    """

    #: The routing provider answered, directly or from `route_cache`.
    ROUTED = "ROUTED", "Routed"
    #: The nightly destination-pair matrix.
    MATRIX = "MATRIX", "Matrix"
    #: Haversine distance times a road factor, at the configured speed. §12.6 requires this
    #: to be labelled on screen, and forbids it in a priced quote.
    APPROXIMATE = "APPROXIMATE", "Approximate"


class ValidationState(models.TextChoices):
    """The worst finding severity from the last §10.6 run.

    §7.3 names the column and not its values (ADR 0007). Three states rather
    than a boolean, because "generated but never validated" and "validated
    clean" are different facts and the client renders them differently: an
    itinerary in `NOT_VALIDATED` has no banner, one in `VALID` has a positive
    one, and only `ERRORS` blocks quoting.
    """

    NOT_VALIDATED = "NOT_VALIDATED", "Not validated"
    VALID = "VALID", "Valid"
    WARNINGS = "WARNINGS", "Warnings only"
    ERRORS = "ERRORS", "Errors"


class Trip(BaseModel, VersionedModel):
    """§7.5.10.

    Not a `SoftDeleteModel`. §7.2 reserves soft deletion for catalogue and
    user-facing entities and excludes financial and booking records; a trip
    that a tourist abandons is `CANCELLED`, which is a state in §20.5 with its
    own timestamp, not a row hidden from queries. `cancelled_at` is that
    record.
    """

    #: §7.5.10: UNIQUE, human-facing `TRP-YYYY-NNNNNNN`. Generated in
    #: `services.py` — the format is a business rule, and a database default
    #: could not read the year from the platform's own clock discipline.
    reference = models.CharField(max_length=20, unique=True, editable=False)

    #: → `identity.tourist_profile.id`. ADR 0012: plain integer, FK in SQL.
    tourist_id = models.BigIntegerField(db_index=True)

    #: → `catalogue.destination.id`. The gateway destination is bound from the
    #: inbound flight (§10.2); this is the destination the trip is *to*.
    destination_id = models.BigIntegerField(db_index=True)

    title = models.CharField(max_length=140, null=True, blank=True, default=None)

    start_date = models.DateField()
    end_date = models.DateField()

    adults = models.PositiveSmallIntegerField(default=1)
    children = models.PositiveSmallIntegerField(default=0)
    infants = models.PositiveSmallIntegerField(default=0)

    status = models.CharField(max_length=20, choices=TripStatus.choices, default=TripStatus.DRAFT)

    #: §7.5.10: "Presentment currency, locked at PRICED". Not null from
    #: creation — every money column in this schema is paired with one (§7.2),
    #: and a trip whose currency arrives later has line totals that briefly
    #: mean nothing. Defaulted from the destination's country at creation.
    currency = models.CharField(max_length=3, validators=[validate_iso_currency_code])

    #: §10.7. All four are NUMERIC(14,2); §18.5 forbids float anywhere on this
    #: path, and `Money` is the only way they are computed.
    subtotal_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fee_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    priced_at = models.DateTimeField(null=True, blank=True, default=None)
    quote_expires_at = models.DateTimeField(null=True, blank=True, default=None)
    confirmed_at = models.DateTimeField(null=True, blank=True, default=None)
    cancelled_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "trip"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="trip_end_date_not_before_start",
            ),
            models.CheckConstraint(
                condition=models.Q(adults__gte=1),
                name="trip_has_at_least_one_adult",
            ),
            # §7.5.10's third constraint, verbatim. It is the one that makes a
            # displayed total trustworthy: any path that writes a subtotal and
            # forgets the fee fails here rather than undercharging.
            models.CheckConstraint(
                condition=models.Q(
                    total_amount=models.F("subtotal_amount")
                    + models.F("fee_amount")
                    + models.F("tax_amount")
                ),
                name="trip_total_is_the_sum_of_its_parts",
            ),
        ]
        indexes = [
            models.Index(fields=["tourist_id", "status"], name="trip_tourist_status_idx"),
            models.Index(fields=["status", "quote_expires_at"], name="trip_quote_expiry_idx"),
        ]

    def __str__(self) -> str:
        return self.reference


class Itinerary(TimestampedModel):
    """§7.3 ERD. One per trip — `trip_id` is marked (U).

    No `public_id`: it is never addressed on its own. Every route is
    `/trips/{id}/itinerary…`, so the trip's identifier is the one that crosses
    the wire and §7.2's rule about sequential integers is satisfied without a
    second UUID nobody would use.

    §10.3: a trip owns exactly one *current* itinerary. History is versions of
    this row plus the archived items of §10.8, not additional itineraries.
    """

    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name="itinerary")

    #: §10.8: incremented by every generate. Starts at 1 — v1 is the empty
    #: itinerary created with the trip (§10.2), which is a real version a
    #: tourist can look at, not a placeholder.
    version = models.IntegerField(default=1)

    generated_at = models.DateTimeField(null=True, blank=True, default=None)

    validation_state = models.CharField(
        max_length=20,
        choices=ValidationState.choices,
        default=ValidationState.NOT_VALIDATED,
    )

    #: Totals across TRANSFER items, for the §24.16 summary. Null until a
    #: generate has run; zero is a different fact (a trip with no transfers).
    total_distance_m = models.IntegerField(null=True, blank=True, default=None)
    total_travel_seconds = models.IntegerField(null=True, blank=True, default=None)

    class Meta:
        db_table = "itinerary"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(version__gte=1),
                name="itinerary_version_starts_at_one",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.trip.reference} v{self.version}"


class ItineraryItem(BaseModel):
    """§7.5.11.

    `public_id` is added here although §7.5.11's column list omits it (ADR
    0007). §9.4.2 exposes `PATCH /trips/{id}/items/{item_id}` and
    `DELETE /trips/{id}/items/{item_id}`, and §10.6's findings carry
    `item_ids[]` for the client to anchor an inline fix against. Those are
    client-visible identifiers, and §7.2 with principle A6 forbid a sequential
    integer being one.

    **The nullable columns are not optional decoration.** §7.5.11 requires "a
    CHECK per item_type asserting that exactly the correct subset of nullable
    FKs is populated", and the constraints below are that. Without them a
    TRANSFER with no endpoints, or a STAY carrying a price, is a row the
    planner will happily write and something far away will later fail on.
    """

    itinerary = models.ForeignKey(Itinerary, on_delete=models.CASCADE, related_name="items")

    #: 1-based, relative to `trip.start_date`.
    day_number = models.PositiveSmallIntegerField()
    sequence_no = models.PositiveSmallIntegerField()

    item_type = models.CharField(max_length=20, choices=ItemType.choices)
    title = models.CharField(max_length=160)

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()

    # -- references to rows other modules own (ADR 0012) ---------------------
    accommodation_id = models.BigIntegerField(null=True, blank=True, default=None)
    activity_id = models.BigIntegerField(null=True, blank=True, default=None)
    activity_departure_id = models.BigIntegerField(null=True, blank=True, default=None)
    attraction_id = models.BigIntegerField(null=True, blank=True, default=None)
    origin_destination_id = models.BigIntegerField(null=True, blank=True, default=None)
    target_destination_id = models.BigIntegerField(null=True, blank=True, default=None)

    #: A free-entry stay anchor's coordinate (ADR 0013). Set only when a STAY
    #: names no curated `accommodation_id`; every other item type reads its
    #: coordinate from the catalogue row it references, so a second copy here
    #: would be a stale duplicate of a surveyed value.
    #:
    #: §13.2: this is written only after the tourist has confirmed the pin.
    #: The Platform never silently persists an unconfirmed geocode, so the
    #: service layer refuses to create the anchor at all rather than storing a
    #: provisional point here.
    location_point = gis_models.PointField(
        geography=True, srid=4326, null=True, blank=True, default=None
    )

    #: §7.5.11: "Precise pickup" / "Precise drop-off". Required on a TRANSFER,
    #: because the destination ids are nullable — a leg from a hotel to an
    #: attraction has coordinates at both ends and a destination at neither.
    origin_point = gis_models.PointField(
        geography=True, srid=4326, null=True, blank=True, default=None
    )
    target_point = gis_models.PointField(
        geography=True, srid=4326, null=True, blank=True, default=None
    )

    distance_m = models.IntegerField(null=True, blank=True, default=None)
    travel_seconds = models.IntegerField(null=True, blank=True, default=None)

    #: ADR 0019. NOT NULL exactly when this is a TRANSFER.
    estimate_quality = models.CharField(
        max_length=20,
        choices=EstimateQuality.choices,
        null=True,
        blank=True,
        default=None,
    )

    #: §7.5.11: "Rooms, seats or vehicles".
    quantity = models.PositiveSmallIntegerField(default=1)
    pax_count = models.PositiveSmallIntegerField(null=True, blank=True, default=None)

    unit_price = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    line_total = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    currency = models.CharField(
        max_length=3,
        null=True,
        blank=True,
        default=None,
        validators=[validate_iso_currency_code],
    )

    #: → `booking.booking.id`, populated on basket creation (Phase 7).
    booking_id = models.BigIntegerField(null=True, blank=True, default=None)

    #: §10.3: true once the covering booking is confirmed. A regeneration that
    #: would alter a locked item is rejected with 409 (§10.8).
    is_locked = models.BooleanField(default=False)

    class Meta:
        db_table = "itinerary_item"
        ordering = ["day_number", "sequence_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["itinerary", "day_number", "sequence_no"],
                name="itinerary_item_position_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(ends_at__gte=models.F("starts_at")),
                name="itinerary_item_ends_after_it_starts",
            ),
            models.CheckConstraint(
                condition=models.Q(day_number__gte=1) & models.Q(sequence_no__gte=1),
                name="itinerary_item_position_is_one_based",
            ),
            # §7.5.11's "a CHECK per item_type". Written as one constraint per
            # type rather than a single disjunction, so a violation names the
            # type that was malformed instead of reporting that the row is not
            # any of five shapes.
            #
            # Each clause reads: for this type, these columns are set and
            # these are absent. `~Q(item_type=X) | (the shape)` is the SQL
            # implication — rows of other types pass trivially.
            models.CheckConstraint(
                # ADR 0013: a curated property or a confirmed free-entry pin,
                # never both and never neither; and no commercial columns at
                # all, because there is no room, no price and no booking
                # behind a stay anchor.
                condition=~models.Q(item_type=ItemType.STAY)
                | (
                    (
                        models.Q(accommodation_id__isnull=False, location_point__isnull=True)
                        | models.Q(accommodation_id__isnull=True, location_point__isnull=False)
                    )
                    & models.Q(
                        activity_id__isnull=True,
                        activity_departure_id__isnull=True,
                        attraction_id__isnull=True,
                        origin_destination_id__isnull=True,
                        target_destination_id__isnull=True,
                        unit_price__isnull=True,
                        line_total__isnull=True,
                        currency__isnull=True,
                        booking_id__isnull=True,
                    )
                ),
                name="itinerary_item_stay_is_an_anchor",
            ),
            models.CheckConstraint(
                # `activity_departure_id` is deliberately *not* required. §10.2
                # binds the departure when the itinerary is generated, and an
                # activity chosen before its date is settled is a legitimate
                # draft state. VR-06 is what refuses to quote one.
                condition=~models.Q(item_type=ItemType.ACTIVITY)
                | models.Q(
                    activity_id__isnull=False,
                    accommodation_id__isnull=True,
                    attraction_id__isnull=True,
                    origin_destination_id__isnull=True,
                    target_destination_id__isnull=True,
                    location_point__isnull=True,
                ),
                name="itinerary_item_activity_names_an_activity",
            ),
            models.CheckConstraint(
                condition=~models.Q(item_type=ItemType.ATTRACTION)
                | models.Q(
                    attraction_id__isnull=False,
                    accommodation_id__isnull=True,
                    activity_id__isnull=True,
                    activity_departure_id__isnull=True,
                    origin_destination_id__isnull=True,
                    target_destination_id__isnull=True,
                    location_point__isnull=True,
                ),
                name="itinerary_item_attraction_names_an_attraction",
            ),
            models.CheckConstraint(
                # Endpoints are coordinates, not ids: the destination columns
                # are set only when an endpoint happens to be a destination.
                # The distance, the duration and their provenance are all
                # required together — a leg with a duration and no
                # `estimate_quality` is exactly the laundering ADR 0019 exists
                # to prevent.
                condition=~models.Q(item_type=ItemType.TRANSFER)
                | models.Q(
                    origin_point__isnull=False,
                    target_point__isnull=False,
                    distance_m__isnull=False,
                    travel_seconds__isnull=False,
                    estimate_quality__isnull=False,
                    accommodation_id__isnull=True,
                    activity_id__isnull=True,
                    activity_departure_id__isnull=True,
                    attraction_id__isnull=True,
                    location_point__isnull=True,
                ),
                name="itinerary_item_transfer_has_endpoints_and_provenance",
            ),
            models.CheckConstraint(
                condition=~models.Q(item_type=ItemType.FREE_TIME)
                | models.Q(
                    accommodation_id__isnull=True,
                    activity_id__isnull=True,
                    activity_departure_id__isnull=True,
                    attraction_id__isnull=True,
                    origin_destination_id__isnull=True,
                    target_destination_id__isnull=True,
                    location_point__isnull=True,
                    unit_price__isnull=True,
                    line_total__isnull=True,
                    currency__isnull=True,
                ),
                name="itinerary_item_free_time_references_nothing",
            ),
            # The other direction of ADR 0019's rule: only a transfer may
            # carry provenance. Without this, an ACTIVITY could be stamped
            # ROUTED and inherit a credibility it never earned.
            models.CheckConstraint(
                condition=models.Q(item_type=ItemType.TRANSFER)
                | models.Q(estimate_quality__isnull=True),
                name="itinerary_item_only_transfers_carry_provenance",
            ),
            # §7.2: never money without its currency, in both directions.
            models.CheckConstraint(
                condition=(
                    models.Q(unit_price__isnull=True, line_total__isnull=True)
                    | models.Q(currency__isnull=False)
                ),
                name="itinerary_item_money_carries_its_currency",
            ),
        ]
        indexes = [
            models.Index(fields=["itinerary", "day_number"], name="itinerary_item_day_idx"),
            models.Index(fields=["booking_id"], name="itinerary_item_booking_idx"),
        ]

    def __str__(self) -> str:
        return f"day {self.day_number} #{self.sequence_no} {self.item_type}"


class TripFlight(TimestampedModel):
    """§11.2, and the §7.3 ERD box. R19 is 1 : 0..2 CASCADE.

    §11.2 is explicit that V1 integrates no flight-status feed: "the tourist
    and the driver may each update actual arrival time, and the system re-times
    the transfer accordingly". `actual_at` is that field, and its being NULL is
    the ordinary case rather than missing data — which is why the arrival
    buffer of VR-07 is applied to `scheduled_at` until somebody says otherwise.
    """

    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name="flights")

    direction = models.CharField(max_length=10, choices=FlightDirection.choices)

    flight_number = models.CharField(max_length=10)
    airline_iata = models.CharField(max_length=3)

    #: → `catalogue.destination.id`, the gateway. §10.2 binds the trip's
    #: gateway from this row, which is why §7.5.6's `is_gateway` flag exists
    #: instead of an airport table.
    gateway_destination_id = models.BigIntegerField(db_index=True)

    #: TIMESTAMPTZ, stored UTC (§7.2). The offset a tourist typed is not
    #: retained separately: the gateway destination carries the IANA zone, and
    #: a zone renders every past and future instant correctly where a stored
    #: offset is right only until the next transition.
    scheduled_at = models.DateTimeField()
    actual_at = models.DateTimeField(null=True, blank=True, default=None)

    terminal = models.CharField(max_length=20, null=True, blank=True, default=None)

    pax_count = models.PositiveSmallIntegerField()
    luggage_count = models.PositiveSmallIntegerField(default=0)

    class Meta:
        db_table = "trip_flight"
        ordering = ["direction"]
        constraints = [
            # R19's "0..2" is exactly this: at most one flight per direction.
            models.UniqueConstraint(
                fields=["trip", "direction"],
                name="trip_flight_one_per_direction",
            ),
            models.CheckConstraint(
                condition=models.Q(pax_count__gte=1),
                name="trip_flight_carries_at_least_one_passenger",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.airline_iata}{self.flight_number} {self.direction}"
