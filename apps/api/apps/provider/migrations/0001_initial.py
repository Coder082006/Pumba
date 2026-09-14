"""`provider` — SRS §7.5.3, ADR 0025.

The table a booking is payable to. §7.5.3 specifies every column; nothing here
is invented. It is built in Phase 7 rather than Phase 11 because §7.5.12 makes
`booking.provider_id` NOT NULL, and there was nothing for it to point at.

Depends on `identity.0001_initial` for one reason: that migration installs the
`citext` extension `contact_email` is declared with, and a fresh database
migrating `provider` first would otherwise fail on an unknown type.
"""

import uuid
from decimal import Decimal

import django.core.validators
import django.utils.timezone
from django.db import migrations, models

import apps.common.fields
import apps.provider.validators
from apps.common.db import attach_updated_at_trigger


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("identity", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Provider",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        db_index=True, default=django.utils.timezone.now, editable=False
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(default=django.utils.timezone.now, editable=False),
                ),
                (
                    "public_id",
                    models.UUIDField(
                        db_index=True, default=uuid.uuid4, editable=False, unique=True
                    ),
                ),
                (
                    "deleted_at",
                    models.DateTimeField(blank=True, default=None, editable=False, null=True),
                ),
                ("legal_name", models.CharField(max_length=200)),
                ("trading_name", models.CharField(max_length=200)),
                (
                    "provider_type",
                    models.CharField(
                        choices=[
                            ("TRANSPORT", "Transport"),
                            ("ACCOMMODATION", "Accommodation"),
                            ("ACTIVITY", "Activity"),
                        ],
                        max_length=20,
                    ),
                ),
                ("contact_email", apps.common.fields.CITextField()),
                ("contact_phone", models.CharField(max_length=20)),
                ("region_id", models.BigIntegerField(db_index=True)),
                (
                    "verify_status",
                    models.CharField(
                        choices=[
                            ("DRAFT", "Draft"),
                            ("SUBMITTED", "Submitted"),
                            ("UNDER_REVIEW", "Under review"),
                            ("VERIFIED", "Verified"),
                            ("REJECTED", "Rejected"),
                            ("SUSPENDED", "Suspended"),
                        ],
                        default="DRAFT",
                        max_length=20,
                    ),
                ),
                ("verified_at", models.DateTimeField(blank=True, default=None, null=True)),
                ("commission_rule_id", models.BigIntegerField(blank=True, default=None, null=True)),
                (
                    "payout_account_ref",
                    models.CharField(blank=True, default=None, max_length=120, null=True),
                ),
                (
                    "payout_currency",
                    models.CharField(
                        default="TZS",
                        max_length=3,
                        validators=[apps.provider.validators.validate_iso_currency_code],
                    ),
                ),
                (
                    "rating_avg",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=3,
                        validators=[
                            django.core.validators.MinValueValidator(Decimal("0")),
                            django.core.validators.MaxValueValidator(Decimal("5")),
                        ],
                    ),
                ),
                ("rating_count", models.IntegerField(default=0)),
            ],
            options={
                "db_table": "provider",
                "ordering": ["trading_name", "id"],
                "indexes": [
                    models.Index(
                        fields=["provider_type", "verify_status"], name="provider_type_status_idx"
                    )
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(
                            ("provider_type__in", ["TRANSPORT", "ACCOMMODATION", "ACTIVITY"])
                        ),
                        name="provider_type_known",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            (
                                "verify_status__in",
                                [
                                    "DRAFT",
                                    "SUBMITTED",
                                    "UNDER_REVIEW",
                                    "VERIFIED",
                                    "REJECTED",
                                    "SUSPENDED",
                                ],
                            )
                        ),
                        name="provider_verify_status_known",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(("verify_status", "VERIFIED"), _negated=True),
                            ("verified_at__isnull", False),
                            _connector="OR",
                        ),
                        name="provider_verified_has_a_time",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("rating_count__gte", 0)),
                        name="provider_rating_count_non_negative",
                    ),
                ],
            },
        ),
        attach_updated_at_trigger("provider"),
    ]
