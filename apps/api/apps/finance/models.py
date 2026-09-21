"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        commission_rule, ledger_entry, provider_balance, payout, payout_item

**One of the five is specified, and not adequately.** §7.5.14 gives
`ledger_entry` its columns and leaves it unable to express a double entry: no
account, no journal, every reference nullable while BR-064 reconciles per
booking. ADR 0028 adds the two columns and designs the other four tables, which
§6.4 names and the SRS never specifies.

**`fx_rate` is deliberately absent.** §6.4 assigns it here and ADR 0024 settled
that a trip is priced, locked and charged in the destination's own currency —
so nothing on the paying path converts, and a table with no writer is a promise
rather than a record. It arrives with presentment currency.

**The ledger is append-only in the database.** §7.5.14: "UPDATE and DELETE are
revoked at the database role level." A correction is a new entry naming
`reversal_of_id` (BR-077), never an edit — which is the difference between a
ledger and a spreadsheet.

**Cross-module references are plain ids** (ADR 0012). Only `payout_item` holds
a real foreign key, to its payout, because that one is inside this module.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel, TimestampedModel

__all__ = [
    "CommissionScope",
    "CommissionMethod",
    "CommissionRule",
    "LedgerEntry",
    "ProviderBalance",
    "PayoutStatus",
    "Payout",
    "PayoutItem",
]


class CommissionScope(models.TextChoices):
    """§22.2's four, in resolution order — most specific first."""

    LISTING = "LISTING", "One listing"
    PROVIDER = "PROVIDER", "One provider"
    TYPE = "TYPE", "A booking type"
    GLOBAL = "GLOBAL", "Everything"


class CommissionMethod(models.TextChoices):
    """§22.2's three calculation methods."""

    PERCENT = "PERCENT", "A percentage of gross"
    FLAT = "FLAT", "A fixed amount"
    TIERED = "TIERED", "Bands over the provider's monthly volume"


class CommissionRule(BaseModel):
    """§22.2. Referenced by `provider.commission_rule_id`, which is nullable —
    "null uses resolution order (22.2)" (§7.5.3)."""

    scope = models.CharField(max_length=20, choices=CommissionScope.choices)
    #: What the scope matches on: a listing id, a provider id, or a booking
    #: type. Null for GLOBAL, which matches everything by construction.
    listing_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    provider_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    booking_type = models.CharField(max_length=20, blank=True, default="")

    #: §22.2: "the highest-priority active rule matching the booking". Scope
    #: order decides first; priority breaks ties inside one scope, which is how
    #: a negotiated rate for one hotel beats a general one for that provider.
    priority = models.IntegerField(default=0)

    method = models.CharField(
        max_length=20, choices=CommissionMethod.choices, default=CommissionMethod.PERCENT
    )
    percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, default=None
    )
    flat_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    #: TIERED's bands, `[{"from_volume": "0", "percent": "18"}, ...]`, evaluated
    #: against the provider's completed volume in the **preceding calendar
    #: month** so the rate is deterministic within a month (§22.2).
    tiers = models.JSONField(default=list, blank=True)

    min_fee = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    max_fee = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    currency = models.CharField(max_length=3, blank=True, default="")

    valid_from = models.DateField(null=True, blank=True, default=None)
    valid_to = models.DateField(null=True, blank=True, default=None)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "commission_rule"
        ordering = ["-priority", "id"]
        indexes = [
            models.Index(fields=["scope", "is_active"], name="commission_scope_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(scope__in=CommissionScope.values), name="commission_scope_is_known"
            ),
            models.CheckConstraint(
                condition=Q(method__in=CommissionMethod.values), name="commission_method_is_known"
            ),
            # A percentage rule with no percentage charges nothing and looks
            # like an oversight either way.
            models.CheckConstraint(
                condition=~Q(method=CommissionMethod.PERCENT) | Q(percent__isnull=False),
                name="commission_percent_has_a_percent",
            ),
            models.CheckConstraint(
                condition=~Q(method=CommissionMethod.FLAT) | Q(flat_amount__isnull=False),
                name="commission_flat_has_an_amount",
            ),
            models.CheckConstraint(
                condition=Q(percent__isnull=True) | (Q(percent__gte=0) & Q(percent__lte=100)),
                name="commission_percent_is_a_percentage",
            ),
        ]

    def __str__(self) -> str:
        return f"CommissionRule({self.scope}, {self.method})"


class LedgerEntry(TimestampedModel):
    """§7.5.14, with ADR 0028's two additions.

    Written only by `services.post_journal`, which refuses an unbalanced set.
    Nothing else may insert a row, and nothing at all may change one.
    """

    #: ADR 0028. §22.3's five accounts, stored rather than inferred, so a
    #: finance report is a query and §21.9's variance has somewhere to go.
    account = models.CharField(max_length=30, db_index=True)
    #: ADR 0028. The legs of one economic event share it, so "debits equal
    #: credits" is checkable per event and not only per booking.
    journal_id = models.UUIDField(db_index=True)

    entry_type = models.CharField(max_length=30)
    direction = models.CharField(max_length=6)
    #: §7.5.14: "Always positive". The direction carries the sign.
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)

    booking_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    payment_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    provider_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    payout_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    #: BR-077: a correction is a new entry pointing at the one it reverses.
    reversal_of_id = models.BigIntegerField(null=True, blank=True, default=None)

    memo = models.CharField(max_length=200, blank=True, default="")
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "ledger_entry"
        ordering = ["occurred_at", "id"]
        indexes = [
            # §7.6, verbatim: statement and reconciliation.
            models.Index(
                fields=["provider_id", "currency", "occurred_at"], name="ledger_statement_idx"
            ),
            models.Index(fields=["booking_id"], name="ledger_booking_idx"),
            models.Index(fields=["account", "occurred_at"], name="ledger_account_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="ledger_amount_is_positive"),
            models.CheckConstraint(
                condition=Q(direction__in=["DEBIT", "CREDIT"]), name="ledger_direction_is_known"
            ),
            # §8.8: `accrue_commission` is "idempotent by (booking_id,
            # entry_type)". Here that is a constraint rather than a hope — a
            # retried job cannot accrue the same booking twice.
            models.UniqueConstraint(
                fields=["booking_id", "entry_type"],
                condition=Q(booking_id__isnull=False),
                name="ledger_one_entry_per_booking_and_type",
            ),
        ]

    def __str__(self) -> str:
        return f"LedgerEntry({self.entry_type}, {self.direction} {self.amount} {self.currency})"


class ProviderBalance(TimestampedModel):
    """§22.4's two pots, per provider and currency.

    BR-064 names both: "Σ PROVIDER_ACCRUAL - Σ PAYOUT_SETTLEMENT - Σ reversals
    equals `provider_balance.available_amount + pending_amount`". The row is a
    cache of the ledger, and the nightly check is what keeps it honest.
    """

    provider_id = models.BigIntegerField(db_index=True)
    currency = models.CharField(max_length=3)

    #: Accrued, and inside the settlement hold (§22.4, `settlement_hold_days`).
    pending_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    #: Past the hold and payable. May go negative after a refund that followed a
    #: payout (§22.6) — recovered from the next batch, which is why nothing here
    #: forbids it.
    available_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        db_table = "provider_balance"
        ordering = ["provider_id", "currency"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider_id", "currency"], name="one_balance_per_provider_and_currency"
            ),
        ]


class PayoutStatus(models.TextChoices):
    """§22.5's lifecycle. Approval is BR-075's, and release is a separate act.

    FAILED "returns the balance to available, with an alert", which is why it
    is a state rather than a deletion.
    """

    DRAFT = "DRAFT", "Assembled, awaiting approval"
    APPROVED = "APPROVED", "Approved by finance"
    PAID = "PAID", "Released"
    FAILED = "FAILED", "Release failed"


class Payout(BaseModel):
    """§22.5. One batch, per provider and currency, for one period."""

    provider_id = models.BigIntegerField(db_index=True)
    currency = models.CharField(max_length=3)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=PayoutStatus.choices, default=PayoutStatus.DRAFT
    )

    period_start = models.DateField()
    period_end = models.DateField()

    approved_by_user_id = models.BigIntegerField(null=True, blank=True, default=None)
    approved_at = models.DateTimeField(null=True, blank=True, default=None)

    #: ADR 0028 decision 6: no rail moves money in 8b, so this is the reference
    #: of a transfer somebody made at their bank and typed in afterwards.
    rail_reference = models.CharField(max_length=120, blank=True, default="")
    paid_at = models.DateTimeField(null=True, blank=True, default=None)
    failure_reason = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        db_table = "payout"
        ordering = ["-period_end", "provider_id"]
        indexes = [
            models.Index(fields=["provider_id", "status"], name="payout_provider_status_idx"),
            models.Index(fields=["status", "period_end"], name="payout_worklist_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="payout_amount_is_positive"),
            models.CheckConstraint(
                condition=Q(status__in=PayoutStatus.values), name="payout_status_is_known"
            ),
            # §8.3's advisory lock stops two batches being built at once; this
            # stops two surviving if one ever were.
            models.UniqueConstraint(
                fields=["provider_id", "currency", "period_end"],
                condition=~Q(status=PayoutStatus.FAILED),
                name="one_live_payout_per_provider_period",
            ),
            models.CheckConstraint(
                condition=Q(period_end__gte=models.F("period_start")),
                name="payout_period_ends_after_it_starts",
            ),
        ]

    def __str__(self) -> str:
        return f"Payout({self.public_id}, {self.status})"


class PayoutItem(TimestampedModel):
    """§22.5's "line items referencing each booking".

    What makes a provider statement auditable back to a service: an operator
    who disagrees with a figure can be shown which dives it is made of.
    """

    payout = models.ForeignKey(Payout, on_delete=models.CASCADE, related_name="items")
    booking_id = models.BigIntegerField(db_index=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    memo = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        db_table = "payout_item"
        ordering = ["payout_id", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["payout", "booking_id"], name="one_item_per_booking_and_payout"
            ),
        ]
