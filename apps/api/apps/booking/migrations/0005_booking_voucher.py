"""`booking_voucher` — ADR 0026 decision 3.

One row per issue. The rendered content is frozen on the row, so a re-render
reproduces the same bytes and the stored sha256 can refuse one that does not.
"""

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0004_provider_response_sweep"),
    ]

    operations = [
        migrations.CreateModel(
            name="BookingVoucher",
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
                ("issue_number", models.PositiveSmallIntegerField()),
                ("content", models.JSONField()),
                ("storage_key", models.CharField(max_length=200)),
                ("sha256", models.CharField(max_length=64)),
                ("size_bytes", models.PositiveIntegerField()),
                ("issued_at", models.DateTimeField()),
                ("issued_by_user_id", models.BigIntegerField(blank=True, default=None, null=True)),
                ("reason", models.CharField(blank=True, default="", max_length=500)),
                (
                    "booking",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vouchers",
                        to="booking.booking",
                    ),
                ),
            ],
            options={
                "db_table": "booking_voucher",
                "ordering": ["booking_id", "issue_number"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("booking", "issue_number"), name="booking_voucher_issue_unique"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("issue_number__gte", 1)),
                        name="booking_voucher_issue_positive",
                    ),
                ],
            },
        ),
    ]
