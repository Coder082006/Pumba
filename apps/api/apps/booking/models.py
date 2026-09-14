"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        booking, booking_accommodation, booking_activity, booking_transfer,
        booking_status_history

Phase 7 builds four of the five. `booking_accommodation` is reserved by ADR 0013
and is not created; the `ACCOMMODATION` value stays in the type enum so that
reviving it in v2 is a CHECK change rather than a renumbering.

**Only `booking` has a specification.** §7.5.12 gives its columns; the other
three exist in the SRS only as box contents in the §7.3 ERD. ADR 0025 designs
them against §7.2 and records every column this file adds that the SRS does not
name, so the invention is visible rather than buried in a migration.

**Nothing here is soft-deleted.** §7.2: "Financial and booking records are
never soft-deleted or hard-deleted." A booking is a promise made to a tourist
and an obligation to a provider; both outlive any decision to hide it.

**Cross-module references are plain ids** (ADR 0012): `trip_id`, `tourist_id`,
`provider_id`, `activity_id` and the rest carry no SQL foreign key. The two
foreign keys below — subtype to booking, history to booking — are inside this
module, and are R24's and R26's CASCADE.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.gis.db import models as gis_models
from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel, TimestampedModel, VersionedModel

__all__ = [
    "BookingType",
    "BookingStatus",
    "CancellingParty",
    "CancellationReason",
    "Booking",
    "BookingActivity",
    "BookingTransfer",
    "BookingStatusHistory",
]

_MONEY = {"max_digits": 14, "decimal_places": 2}


class BookingType(models.TextChoices):
    """§7.5.12. ACCOMMODATION is reserved, not live (ADR 0013)."""

    ACCOMMODATION = "ACCOMMODATION", "Accommodation"
    ACTIVITY = "ACTIVITY", "Activity"
    TRANSFER = "TRANSFER", "Transfer"


#: The types a booking may actually be in v1. The CHECK below admits these and
#: no others; the enum keeps the third so its value is never reused.
LIVE_TYPES = (BookingType.ACTIVITY, BookingType.TRANSFER)


class BookingStatus(models.TextChoices):
    """§20.2's ten states. Mirrors `domain.lifecycle.BookingState`."""

    DRAFT = "DRAFT", "Draft"
    PENDING = "PENDING", "Pending"
    AWAITING_PROVIDER = "AWAITING_PROVIDER", "Awaiting provider"
    CONFIRMED = "CONFIRMED", "Confirmed"
    IN_PROGRESS = "IN_PROGRESS", "In progress"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"
    REFUNDED = "REFUNDED", "Refunded"
    NO_SHOW = "NO_SHOW", "No-show"
    FAILED = "FAILED", "Failed"


class CancellingParty(models.TextChoices):
    """Who ended it. ADR 0025: BR-045 turns on this, not on the reason."""

    TOURIST = "TOURIST", "Tourist"
    PROVIDER = "PROVIDER", "Provider"
    DRIVER = "DRIVER", "Driver"
    PLATFORM = "PLATFORM", "Platform"


class CancellationReason(models.TextChoices):
    """§7.5.12's "coded reason", which the SRS never enumerates. ADR 0025."""

    TOURIST_REQUEST = "TOURIST_REQUEST", "Tourist request"
    TRIP_CANCELLED = "TRIP_CANCELLED", "Whole trip cancelled"
    PROVIDER_DECLINED = "PROVIDER_DECLINED", "Provider declined"
    PROVIDER_RESPONSE_TIMEOUT = "PROVIDER_RESPONSE_TIMEOUT", "Provider did not respond"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE", "Provider cannot fulfil"
    ADMIN_ACTION = "ADMIN_ACTION", "Administrative action"


class Booking(BaseModel, VersionedModel):
    """§7.5.12, plus the five columns ADR 0025 adds.

    `BaseModel` supplies `id`, `public_id`, `created_at` and `updated_at`;
    `VersionedModel` supplies `version` (§7.7's optimistic locking).
    """

    #: `BKG-YYYY-NNNNNNN`, from `common.reference`: random, placed by
    #: insert-and-retry against the UNIQUE constraint.
    reference = models.CharField(max_length=20, unique=True, editable=False)

    trip_id = models.BigIntegerField(db_index=True)
    #: "denormalised for query" — §7.5.12.
    tourist_id = models.BigIntegerField()
    provider_id = models.BigIntegerField()

    booking_type = models.CharField(max_length=20, choices=BookingType.choices)
    status = models.CharField(
        max_length=20, choices=BookingStatus.choices, default=BookingStatus.PENDING
    )

    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    pax_count = models.SmallIntegerField()

    #: The component's price — its itinerary line total. The service fee is
    #: *not* inside it (ADR 0025 decision 3, after §22.1).
    gross_amount = models.DecimalField(**_MONEY)
    #: This component's share of the trip's service fee and tax. ADR 0025.
    fee_amount = models.DecimalField(**_MONEY, default=Decimal("0.00"))
    tax_amount = models.DecimalField(**_MONEY, default=Decimal("0.00"))
    currency = models.CharField(max_length=3)

    #: Snapshotted at basket creation (TC-060).
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    #: Computed from the frozen rate at capture (§20.8 step 14).
    commission_amount = models.DecimalField(**_MONEY, null=True, blank=True)
    net_amount = models.DecimalField(**_MONEY, null=True, blank=True)

    #: Which policy this was sold under (ADR 0025 decision 4). Null for a
    #: transfer, which has no listing.
    cancellation_policy_id = models.BigIntegerField(null=True, blank=True, default=None)
    #: BR-041. The tiers BR-040 evaluates — never the live policy row.
    cancellation_policy_snapshot = models.JSONField()

    confirmed_at = models.DateTimeField(null=True, blank=True, default=None)
    cancelled_at = models.DateTimeField(null=True, blank=True, default=None)
    cancelled_by = models.CharField(
        max_length=20, choices=CancellingParty.choices, null=True, blank=True, default=None
    )
    cancellation_reason = models.CharField(
        max_length=40, choices=CancellationReason.choices, null=True, blank=True, default=None
    )
    completed_at = models.DateTimeField(null=True, blank=True, default=None)

    #: The on-request deadline. The rule, not the sweep (ADR 0025 decision 7).
    response_due_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "booking"
        ordering = ["starts_at", "id"]
        indexes = [
            models.Index(
                fields=["provider_id", "status", "starts_at"], name="booking_provider_idx"
            ),
            models.Index(fields=["tourist_id", "status"], name="booking_tourist_idx"),
            models.Index(fields=["status", "starts_at"], name="booking_status_starts_idx"),
            models.Index(
                fields=["response_due_at"],
                condition=Q(status="AWAITING_PROVIDER"),
                name="booking_response_due_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(booking_type__in=[t.value for t in LIVE_TYPES]),
                name="booking_type_is_a_v1_type",
            ),
            models.CheckConstraint(
                condition=Q(status__in=BookingStatus.values), name="booking_status_known"
            ),
            models.CheckConstraint(
                condition=Q(ends_at__gte=models.F("starts_at")), name="booking_ends_after_it_starts"
            ),
            models.CheckConstraint(condition=Q(pax_count__gte=1), name="booking_pax_positive"),
            models.CheckConstraint(
                condition=Q(gross_amount__gte=0) & Q(fee_amount__gte=0) & Q(tax_amount__gte=0),
                name="booking_amounts_non_negative",
            ),
            models.CheckConstraint(
                condition=Q(commission_rate__isnull=True)
                | (Q(commission_rate__gte=0) & Q(commission_rate__lte=100)),
                name="booking_commission_rate_is_a_percentage",
            ),
            # The amount and the net are computed together, from the rate, at
            # capture. One without the other is half a settlement.
            models.CheckConstraint(
                condition=(Q(commission_amount__isnull=True) & Q(net_amount__isnull=True))
                | (
                    Q(commission_amount__isnull=False)
                    & Q(net_amount__isnull=False)
                    & Q(commission_rate__isnull=False)
                ),
                name="booking_commission_settles_together",
            ),
            models.CheckConstraint(
                condition=(
                    Q(cancelled_at__isnull=True)
                    & Q(cancelled_by__isnull=True)
                    & Q(cancellation_reason__isnull=True)
                )
                | (
                    Q(cancelled_at__isnull=False)
                    & Q(cancelled_by__isnull=False)
                    & Q(cancellation_reason__isnull=False)
                ),
                name="booking_cancellation_is_whole",
            ),
            models.CheckConstraint(
                condition=~Q(status="AWAITING_PROVIDER") | Q(response_due_at__isnull=False),
                name="booking_awaiting_provider_has_a_deadline",
            ),
            models.CheckConstraint(
                condition=~Q(booking_type="ACTIVITY") | Q(cancellation_policy_id__isnull=False),
                name="booking_activity_names_its_policy",
            ),
        ]

    def __str__(self) -> str:
        return self.reference


class BookingActivity(models.Model):
    """The §7.3 ERD's `booking_activity` box. ADR 0025."""

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, primary_key=True, related_name="activity"
    )
    activity_id = models.BigIntegerField(db_index=True)
    activity_departure_id = models.BigIntegerField(db_index=True)
    pax_adult = models.SmallIntegerField()
    pax_child = models.SmallIntegerField(default=0)
    meeting_at = models.DateTimeField()

    class Meta:
        db_table = "booking_activity"
        constraints = [
            models.CheckConstraint(
                condition=Q(pax_adult__gte=0) & Q(pax_child__gte=0),
                name="booking_activity_pax_non_negative",
            ),
        ]


class BookingTransfer(models.Model):
    """The §7.3 ERD's `booking_transfer` box. ADR 0025.

    Everything BR-054 freezes lives here: the rule that priced it, the distance
    and duration, and — added — the quality of that estimate, so a corridor
    priced over an approximate distance cannot become a "routed" fact later.
    """

    booking = models.OneToOneField(
        Booking, on_delete=models.CASCADE, primary_key=True, related_name="transfer"
    )
    origin_destination_id = models.BigIntegerField(null=True, blank=True, default=None)
    target_destination_id = models.BigIntegerField(null=True, blank=True, default=None)
    pickup_point = gis_models.PointField(geography=True, srid=4326)
    dropoff_point = gis_models.PointField(geography=True, srid=4326)
    pickup_at = models.DateTimeField()
    distance_m = models.IntegerField(null=True, blank=True, default=None)
    travel_seconds = models.IntegerField(null=True, blank=True, default=None)
    estimate_quality = models.CharField(max_length=20)
    vehicle_class = models.CharField(max_length=20)
    luggage_count = models.SmallIntegerField(default=0)
    is_airport_transfer = models.BooleanField(default=False)
    trip_flight_id = models.BigIntegerField(null=True, blank=True, default=None)

    #: ADR 0023: steps 1-2 match a corridor, steps 3-4 a tariff, and one column
    #: cannot address both tables. Exactly one is set.
    corridor_id = models.BigIntegerField(null=True, blank=True, default=None)
    tariff_id = models.BigIntegerField(null=True, blank=True, default=None)

    class Meta:
        db_table = "booking_transfer"
        constraints = [
            models.CheckConstraint(
                condition=(Q(corridor_id__isnull=False) & Q(tariff_id__isnull=True))
                | (Q(corridor_id__isnull=True) & Q(tariff_id__isnull=False)),
                name="booking_transfer_one_pricing_rule",
            ),
            models.CheckConstraint(
                condition=Q(estimate_quality__in=["ROUTED", "MATRIX", "APPROXIMATE"]),
                name="booking_transfer_quality_known",
            ),
            models.CheckConstraint(
                condition=Q(luggage_count__gte=0), name="booking_transfer_luggage_non_negative"
            ),
        ]


class BookingStatusHistory(TimestampedModel):
    """R26: one row per transition, append-only. ADR 0025.

    The append-only property is a database trigger, not a convention: an
    application promise not to UPDATE a history row is the kind that erodes one
    "just this once" correction at a time.
    """

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="history")
    #: Null on the row that records the booking's creation.
    from_status = models.CharField(max_length=20, null=True, blank=True, default=None)
    to_status = models.CharField(max_length=20)
    #: Null for a System transition, which says so in `actor_role`.
    actor_user_id = models.BigIntegerField(null=True, blank=True, default=None)
    actor_role = models.CharField(max_length=40)
    reason = models.CharField(max_length=500, blank=True, default="")
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "booking_status_history"
        ordering = ["occurred_at", "id"]
        indexes = [models.Index(fields=["booking", "occurred_at"], name="booking_history_idx")]
        constraints = [
            models.CheckConstraint(
                condition=~Q(actor_role=""), name="booking_history_names_an_actor"
            ),
        ]
