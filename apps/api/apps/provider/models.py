"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        provider, provider_document, provider_staff, driver, vehicle

Phase 7 builds `provider`, because a booking cannot exist without one:
§7.5.12 makes `booking.provider_id` NOT NULL and BR-037 requires the provider
to be VERIFIED at confirmation. The other four tables belong to provider
onboarding (Phase 11) and to drivers (Phase 9) and arrive with them.

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

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

from apps.common.fields import CITextField
from apps.common.models import SoftDeleteModel
from apps.provider.validators import validate_iso_currency_code

__all__ = ["ProviderTypeChoice", "VerifyStatus", "Provider"]


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
