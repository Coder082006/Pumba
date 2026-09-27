"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        provider, provider_document, provider_staff, driver, vehicle

Phase 7 built `provider`, because a booking cannot exist without one:
§7.5.12 makes `booking.provider_id` NOT NULL and BR-037 requires the provider
to be VERIFIED at confirmation. Phase 9a adds `driver` and `vehicle`, which
§7.5.4 and §7.5.5 specify column for column. `provider_document` and
`provider_staff` belong to onboarding and arrive with Phase 11's portal.

**Nothing about this table is invented.** §7.5.3 specifies every column below,
its type, nullability and default. What ADR 0025 decides is only who may write
it in this phase — an administrator, through `administration`, because there is
no provider login yet.

**`region_id` and `commission_rule_id` are plain ids.** §6.4 gives
`provider -> identity` and nothing else; `region` is `catalogue`'s and
`commission_rule` is `finance`'s, and a foreign key to either would be a
forbidden edge written in DDL where import-linter cannot see it (ADR 0012).
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.gis.db import models as gis_models
from django.contrib.postgres.fields import ArrayField
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.common.fields import CITextField
from apps.common.models import SoftDeleteModel
from apps.provider.validators import validate_iso_currency_code

__all__ = [
    "ProviderTypeChoice",
    "VerifyStatus",
    "Provider",
    "VehicleClassCode",
    "Driver",
    "Vehicle",
]


def _english() -> list[str]:
    """§7.5.4's `'{en}'` default, as a callable so the list is never shared."""
    return ["en"]


class ProviderTypeChoice(models.TextChoices):
    """§7.5.3. Mirrors `domain.verification.ProviderType`."""

    TRANSPORT = "TRANSPORT", "Transport"
    ACCOMMODATION = "ACCOMMODATION", "Accommodation"
    ACTIVITY = "ACTIVITY", "Activity"


class VerifyStatus(models.TextChoices):
    """§7.5.3 and §28.4. Mirrors `domain.verification.VerifyState`."""

    DRAFT = "DRAFT", "Draft"
    SUBMITTED = "SUBMITTED", "Submitted"
    UNDER_REVIEW = "UNDER_REVIEW", "Under review"
    VERIFIED = "VERIFIED", "Verified"
    REJECTED = "REJECTED", "Rejected"
    SUSPENDED = "SUSPENDED", "Suspended"


class Provider(SoftDeleteModel):
    """§7.5.3, column for column.

    `SoftDeleteModel` supplies `id`, `public_id`, `created_at`, `updated_at`
    and `deleted_at`, which are §7.5.3's first two and last rows. A provider is
    never hard-deleted: R23 makes `booking.provider_id` RESTRICT, and a provider
    with a settled booking behind it is a provider the ledger still names.
    """

    legal_name = models.CharField(max_length=200)
    trading_name = models.CharField(max_length=200)
    provider_type = models.CharField(max_length=20, choices=ProviderTypeChoice.choices)

    contact_email = CITextField()
    contact_phone = models.CharField(max_length=20)

    #: → `region.id` (catalogue), the operating region. No FK; see module doc.
    region_id = models.BigIntegerField(db_index=True)

    verify_status = models.CharField(
        max_length=20, choices=VerifyStatus.choices, default=VerifyStatus.DRAFT
    )
    verified_at = models.DateTimeField(null=True, blank=True, default=None)

    #: → `commission_rule.id`; null uses §22.2's resolution order.
    commission_rule_id = models.BigIntegerField(null=True, blank=True, default=None)

    #: "Token from PSP; never raw bank details" — §7.5.3, and §35's reason.
    payout_account_ref = models.CharField(max_length=120, null=True, blank=True, default=None)
    payout_currency = models.CharField(
        max_length=3, default="TZS", validators=[validate_iso_currency_code]
    )

    rating_avg = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("5"))],
    )
    rating_count = models.IntegerField(default=0)

    class Meta:
        db_table = "provider"
        ordering = ["trading_name", "id"]
        indexes = [
            models.Index(
                fields=["provider_type", "verify_status"], name="provider_type_status_idx"
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(provider_type__in=ProviderTypeChoice.values),
                name="provider_type_known",
            ),
            models.CheckConstraint(
                condition=Q(verify_status__in=VerifyStatus.values),
                name="provider_verify_status_known",
            ),
            # A provider that has been verified has a moment it was verified.
            # The converse does not hold — a suspended provider keeps the
            # timestamp of the verification it lost — so this is one-way.
            models.CheckConstraint(
                condition=~Q(verify_status=VerifyStatus.VERIFIED) | Q(verified_at__isnull=False),
                name="provider_verified_has_a_time",
            ),
            models.CheckConstraint(
                condition=Q(rating_count__gte=0),
                name="provider_rating_count_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return self.trading_name


class VehicleClassCode(models.TextChoices):
    """§7.5.5's four. The codes `transport.VehicleClass` is keyed by.

    A string and not a foreign key: §6.4 gives `provider -> identity` and
    nothing else, so the class table is out of reach and the code travels
    instead (ADR 0012). The check constraint below is what keeps the two
    vocabularies from drifting.
    """

    STANDARD = "STANDARD", "Standard"
    COMFORT = "COMFORT", "Comfort"
    VAN = "VAN", "Van"
    MINIBUS = "MINIBUS", "Minibus"


class Driver(SoftDeleteModel):
    """§7.5.4, column for column. ADR 0029.

    Never hard-deleted for the reason `Provider` is not: a completed transfer
    names its driver, and §22 settles money against that name.
    """

    #: → `user.id` (identity). No FK; see the module docstring and ADR 0012.
    user_id = models.BigIntegerField()

    #: §7.5.4: a TRANSPORT provider. An independent operator gets a
    #: single-driver provider record of their own (§25.3), so this is never
    #: null — every driver is dispatched *as* somebody.
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="drivers")

    #: §30.4 names this column: envelope-encrypted, a `ports.crypto.Ciphertext`
    #: blob, decrypted only where §30.4 permits and audited when it is.
    licence_number = models.BinaryField(editable=False)
    licence_expires_on = models.DateField()

    #: §30.4 names this one too.
    national_id_ref = models.BinaryField(null=True, blank=True, default=None, editable=False)

    verify_status = models.CharField(
        max_length=20, choices=VerifyStatus.choices, default=VerifyStatus.DRAFT
    )

    #: §11.6 rule 7's availability toggle. Offline drivers are still offered
    #: work more than twelve hours out, so this narrows the candidate list
    #: rather than emptying it.
    is_online = models.BooleanField(default=False)

    #: §7.5.4: "Optional operating boundary". §11.6 rule 6 excludes a driver
    #: whose service area is set and does not contain the pickup point.
    service_area = gis_models.PolygonField(
        geography=True, srid=4326, null=True, blank=True, default=None
    )

    #: → `destination.id` (catalogue). NOT NULL, because §11.6's anchor falls
    #: back to this destination's centroid whenever the driver is offline or
    #: their last position is stale — a driver without one cannot be scored.
    home_destination_id = models.BigIntegerField(db_index=True)

    #: §7.5.4: "Displayed to tourist" (§11.4).
    languages = ArrayField(models.CharField(max_length=10), default=_english)

    rating_avg = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("5"))],
    )

    #: §7.5.4: "Rolling 30-day; input to dispatch score". Starts at 100 so a
    #: new driver is not punished for having declined nothing yet.
    acceptance_rate = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100.00"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
    )
    completed_trips = models.IntegerField(default=0)

    class Meta:
        db_table = "driver"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["verify_status", "is_online"], name="driver_dispatchable_idx"),
            models.Index(fields=["provider"], name="driver_provider_idx"),
        ]
        constraints = [
            # Partial, so a driver who closed their account does not block the
            # same person re-registering — the pattern `user_phone_unique_alive`
            # already uses.
            models.UniqueConstraint(
                fields=["user_id"],
                condition=Q(deleted_at__isnull=True),
                name="driver_user_unique_alive",
            ),
            models.CheckConstraint(
                condition=Q(verify_status__in=VerifyStatus.values),
                name="driver_verify_status_known",
            ),
            models.CheckConstraint(
                condition=Q(completed_trips__gte=0),
                name="driver_completed_trips_non_negative",
            ),
        ]

    def __str__(self) -> str:
        return f"Driver({self.public_id})"


class Vehicle(SoftDeleteModel):
    """§7.5.5, column for column. ADR 0029.

    `provider_id` is stored as well as reachable through the driver because
    §7.5.5 specifies it: a transport company's fleet is the company's, and a
    driver who moves between operators does not take the minibus with them.
    """

    driver = models.ForeignKey(Driver, on_delete=models.PROTECT, related_name="vehicles")
    provider = models.ForeignKey(Provider, on_delete=models.PROTECT, related_name="vehicles")

    plate_number = models.CharField(max_length=20)
    make = models.CharField(max_length=40)
    model = models.CharField(max_length=40)
    year = models.SmallIntegerField()

    #: §7.5.5: "Aids airport identification" — §11.9's meeting protocol shows
    #: it to the tourist alongside the photograph and plate.
    colour = models.CharField(max_length=20)

    #: §7.5.5: "Excludes driver". §11.6 rule 3 compares it against pax.
    seat_capacity = models.SmallIntegerField()
    #: §7.5.5: "Large bags".
    luggage_capacity = models.SmallIntegerField()

    vehicle_class = models.CharField(
        max_length=20, choices=VehicleClassCode.choices, default=VehicleClassCode.STANDARD
    )
    has_air_conditioning = models.BooleanField(default=True)

    #: Both "block dispatch when past" — §7.5.5, enforced by §11.6 rule 2.
    insurance_expires_on = models.DateField()
    inspection_expires_on = models.DateField()

    verify_status = models.CharField(
        max_length=20, choices=VerifyStatus.choices, default=VerifyStatus.DRAFT
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "vehicle"
        ordering = ["id"]
        indexes = [
            models.Index(fields=["driver", "is_active"], name="vehicle_driver_active_idx"),
            models.Index(fields=["vehicle_class"], name="vehicle_class_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["plate_number"],
                condition=Q(deleted_at__isnull=True),
                name="vehicle_plate_unique_alive",
            ),
            models.CheckConstraint(
                condition=Q(vehicle_class__in=VehicleClassCode.values),
                name="vehicle_class_known",
            ),
            models.CheckConstraint(
                condition=Q(verify_status__in=VerifyStatus.values),
                name="vehicle_verify_status_known",
            ),
            models.CheckConstraint(
                condition=Q(seat_capacity__gte=1) & Q(luggage_capacity__gte=0),
                name="vehicle_capacity_sane",
            ),
            models.CheckConstraint(
                condition=Q(year__gte=1950) & Q(year__lte=2100),
                name="vehicle_year_plausible",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.colour} {self.make} {self.model} ({self.plate_number})"
