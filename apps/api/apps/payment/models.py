"""Data-access layer (SRS §8.2 layer 4).

    Owns:
        payment, payment_transaction, refund, payment_webhook_event

**One of the four is specified.** §7.5.13 gives `payment` its columns and stops
there; `payment_transaction`, `refund` and `payment_webhook_event` are named in
§6.4, related in §7.4 and defined nowhere. ADR 0027 designs them against §7.2
and records every column invented here, so the invention is visible rather than
buried in a migration — the same treatment ADR 0007 and ADR 0025 gave earlier
phases.

Even `payment` is short of what §21 asks of it: no timestamps, no `expires_at`
for a state called EXPIRED, no CHECK over the ten §21.4 statuses, and a
`payment_instrument_summary` column its own prose names and its table omits.
All four are added here, under ADR 0027.

**Nothing is soft-deleted and nothing is edited.** §7.2: "Financial and booking
records are never soft-deleted or hard-deleted." `payment_transaction` and
`payment_webhook_event` go further and are append-only by trigger (§7.7 lists
immutability as a database mechanism, not an application promise) — a payment's
history is the evidence in a dispute, and evidence somebody can correct after
the fact is not evidence.

**No card data, ever** (PM1, SAQ A). `payment_instrument_summary` holds the
PSP's token, the brand and the last four digits. A column that could hold a PAN
is a column somebody eventually puts one in, so there is none.

**Cross-module references are plain ids** (ADR 0012): `trip_id`, `tourist_id`
and `booking_id` carry no SQL foreign key. The three keys below are inside this
module.
"""

from __future__ import annotations

from django.db import models
from django.db.models import Q

from apps.common.models import BaseModel, TimestampedModel, VersionedModel

__all__ = [
    "PaymentMethod",
    "PaymentStatus",
    "LIVE_STATUSES",
    "RefundStatus",
    "WebhookOutcome",
    "Payment",
    "PaymentTransaction",
    "Refund",
    "PaymentWebhookEvent",
]


class PaymentMethod(models.TextChoices):
    """§7.5.13. MOBILE_MONEY has no adapter in 8a (§21.2's second rail)."""

    CARD = "CARD", "Card"
    MOBILE_MONEY = "MOBILE_MONEY", "Mobile money"


class PaymentStatus(models.TextChoices):
    """§21.4's ten states.

    DISPUTED and CHARGEBACK_LOST are declared and unreachable in 8a: no
    chargeback ingestion is built, because §22.6 defers the allocation to the
    provider agreement, which is Appendix D-5 and Phase 11. Declared rather
    than omitted so the table matches the machine and the gap is visible.
    """

    INITIATED = "INITIATED", "Initiated"
    PENDING = "PENDING", "Pending"
    AUTHORISED = "AUTHORISED", "Authorised"
    CAPTURED = "CAPTURED", "Captured"
    PARTIALLY_REFUNDED = "PARTIALLY_REFUNDED", "Partially refunded"
    REFUNDED = "REFUNDED", "Refunded"
    FAILED = "FAILED", "Failed"
    EXPIRED = "EXPIRED", "Expired"
    DISPUTED = "DISPUTED", "Disputed"
    CHARGEBACK_LOST = "CHARGEBACK_LOST", "Chargeback lost"


#: BR-062: "a trip may hold at most one non-terminal payment at a time". These
#: are the statuses that count as holding one, and the partial unique index
#: below is the enforcement.
#:
#: DISPUTED is live on purpose: a disputed payment is still the trip's payment,
#: and a second one taken beside it would be two claims on one trip.
LIVE_STATUSES = (
    PaymentStatus.INITIATED,
    PaymentStatus.PENDING,
    PaymentStatus.AUTHORISED,
    PaymentStatus.CAPTURED,
    PaymentStatus.PARTIALLY_REFUNDED,
    PaymentStatus.DISPUTED,
)


class RefundStatus(models.TextChoices):
    """ADR 0027 decision 5. The SRS gives `refund` no lifecycle at all.

    A refund is asynchronous at the PSP, so "requested" and "settled" are
    different facts and a tourist must never be told the second while only the
    first is true.
    """

    REQUESTED = "REQUESTED", "Requested"
    SETTLED = "SETTLED", "Settled"
    FAILED = "FAILED", "Failed"


class WebhookOutcome(models.TextChoices):
    """What was done about an event, including doing nothing on purpose.

    §21.5 makes a non-action legitimate — "the handler applies a state
    transition only if it is legal and advances the state, otherwise it records
    and ignores" — and a row that recorded nothing about why would make an
    out-of-order event indistinguishable from a bug.
    """

    RECEIVED = "RECEIVED", "Received, not yet processed"
    APPLIED = "APPLIED", "Applied"
    DUPLICATE = "DUPLICATE", "Duplicate event id"
    IGNORED_NOT_ADVANCING = "IGNORED_NOT_ADVANCING", "Ignored: would not advance the state"
    UNMATCHED = "UNMATCHED", "No payment for this reference"


class Payment(BaseModel, VersionedModel):
    """§7.5.13, with ADR 0027's five additions."""

    #: → `trip.id` and `identity.tourist_profile.id`. ADR 0012: plain ids.
    trip_id = models.BigIntegerField(db_index=True)
    tourist_id = models.BigIntegerField(db_index=True)

    method = models.CharField(max_length=20, choices=PaymentMethod.choices)

    #: §18.3's four currencies, two of which live here. Presentment is what the
    #: tourist is charged; settlement is what the PSP pays the platform, and is
    #: unknown until capture (§7.5.13 marks the amount nullable for that
    #: reason — the currency is not, because the PSP account fixes it).
    presentment_currency = models.CharField(max_length=3)
    presentment_amount = models.DecimalField(max_digits=14, decimal_places=2)
    settlement_currency = models.CharField(max_length=3)
    settlement_amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    #: BR-065: every conversion stores its rate. Null until capture tells us one.
    fx_rate = models.DecimalField(
        max_digits=18, decimal_places=8, null=True, blank=True, default=None
    )

    status = models.CharField(
        max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.INITIATED
    )
    psp_name = models.CharField(max_length=40)
    #: Unique when present: §7.6, and the other half of webhook matching.
    psp_reference = models.CharField(
        max_length=120, null=True, blank=True, default=None, db_index=True
    )
    #: §9.1's key, held per payment because §7.6 makes it unique here. The
    #: `@idempotent` store is a different mechanism for a different scope
    #: (issue S2); this one is what the PSP is asked with.
    idempotency_key = models.CharField(max_length=64, unique=True)

    #: §21.8's normalised taxonomy — never a PSP's own decline code.
    failure_code = models.CharField(max_length=40, blank=True, default="")

    #: ADR 0027 decision 1. Token, brand, last four. Never a PAN.
    payment_instrument_summary = models.JSONField(null=True, blank=True, default=None)

    #: §17.2: the hold window a payment extends, so EXPIRED has something to
    #: read rather than being inferred from a clock nobody wrote down.
    expires_at = models.DateTimeField(null=True, blank=True, default=None)
    authorised_at = models.DateTimeField(null=True, blank=True, default=None)
    captured_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "payment"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["trip_id", "status"], name="payment_trip_status_idx"),
            models.Index(fields=["tourist_id", "-created_at"], name="payment_tourist_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["psp_reference"],
                name="payment_psp_reference_unique",
                condition=Q(psp_reference__isnull=False),
            ),
            # BR-062, as ADR 0027 decision 2 reads it: one *live* payment per
            # trip. A declined card leaves a terminal row behind and the
            # tourist may try again, which R27 requires and a plain unique
            # constraint would forbid.
            models.UniqueConstraint(
                fields=["trip_id"],
                name="payment_one_live_per_trip",
                condition=Q(status__in=[status.value for status in LIVE_STATUSES]),
            ),
            models.CheckConstraint(
                condition=Q(status__in=PaymentStatus.values), name="payment_status_is_known"
            ),
            models.CheckConstraint(
                condition=Q(method__in=PaymentMethod.values), name="payment_method_is_known"
            ),
            models.CheckConstraint(
                condition=Q(presentment_amount__gt=0), name="payment_amount_is_positive"
            ),
        ]

    def __str__(self) -> str:
        return f"Payment({self.public_id}, {self.status})"


class PaymentTransaction(TimestampedModel):
    """PM6: "Every payment state change writes an immutable row."

    Append-only by trigger. `raw_event` names the webhook that caused the
    change where one did, so the PSP's own words and our reading of them can be
    put side by side during a dispute.
    """

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="transactions")
    #: Null on the row that records the payment's creation.
    from_status = models.CharField(max_length=20, null=True, blank=True, default=None)
    to_status = models.CharField(max_length=20)
    amount = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True, default=None
    )
    currency = models.CharField(max_length=3, blank=True, default="")
    failure_code = models.CharField(max_length=40, blank=True, default="")
    psp_reference = models.CharField(max_length=120, blank=True, default="")
    raw_event_id = models.BigIntegerField(null=True, blank=True, default=None)
    #: SYSTEM for a webhook or a poll; a user id for an administrative action.
    actor_role = models.CharField(max_length=40)
    actor_user_id = models.BigIntegerField(null=True, blank=True, default=None)
    reason = models.CharField(max_length=500, blank=True, default="")
    occurred_at = models.DateTimeField()

    class Meta:
        db_table = "payment_transaction"
        ordering = ["occurred_at", "id"]
        indexes = [
            models.Index(fields=["payment", "occurred_at"], name="payment_txn_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=~Q(actor_role=""), name="payment_txn_names_an_actor"),
        ]


class Refund(BaseModel):
    """§21.6, with ADR 0027's lifecycle.

    **The row exists before the PSP is called.** §8.9's bus swallows a handler's
    failure so one consumer cannot roll back its publisher, and there is no
    sweeper behind refunds the way §17.5's stands behind holds — so a refund
    issued inside a handler would vanish on a bad afternoon. The handler writes
    this row; a retried task settles it.
    """

    payment = models.ForeignKey(Payment, on_delete=models.CASCADE, related_name="refunds")
    #: → `booking.id`. §21.6: a partial refund carries the booking so 8b's
    #: ledger can reverse the right accrual. Null only for a trip-level
    #: discretionary refund, which has no single component to name.
    booking_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)

    amount = models.DecimalField(max_digits=14, decimal_places=2)
    currency = models.CharField(max_length=3)

    #: §20.9's `provider_compensation`, carried from the cancellation that
    #: decided it. 8b's ledger accrues it back to the operator (§22.6), and
    #: recomputing it there would be a second reading of a policy the tourist
    #: has already been shown (BR-043).
    provider_compensation = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    status = models.CharField(
        max_length=20, choices=RefundStatus.choices, default=RefundStatus.REQUESTED
    )
    #: Why it is owed: a cancellation reason, a supply failure, or goodwill.
    reason_code = models.CharField(max_length=40)
    reason = models.CharField(max_length=500, blank=True, default="")

    #: Null means the platform, automatically, under a cancellation policy —
    #: §21.6's "issued without human approval". A discretionary refund names
    #: who asked, and BR-047 names who approved it above the auto limit.
    requested_by_user_id = models.BigIntegerField(null=True, blank=True, default=None)
    approved_by_user_id = models.BigIntegerField(null=True, blank=True, default=None)

    psp_refund_reference = models.CharField(max_length=120, blank=True, default="")
    failure_code = models.CharField(max_length=40, blank=True, default="")
    idempotency_key = models.CharField(max_length=64, unique=True)

    requested_at = models.DateTimeField()
    settled_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "refund"
        ordering = ["-requested_at", "id"]
        indexes = [
            models.Index(fields=["payment", "status"], name="refund_payment_status_idx"),
            models.Index(fields=["status", "requested_at"], name="refund_worklist_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=0), name="refund_amount_is_positive"),
            models.CheckConstraint(
                condition=Q(status__in=RefundStatus.values), name="refund_status_is_known"
            ),
            models.CheckConstraint(
                condition=Q(status=RefundStatus.SETTLED, settled_at__isnull=False)
                | ~Q(status=RefundStatus.SETTLED),
                name="refund_settled_has_a_time",
            ),
        ]


class PaymentWebhookEvent(TimestampedModel):
    """§9.4.8, §21.5. The payload is stored before anything reads it.

    Append-only by trigger, and unique on `psp_event_id` — which is what makes
    a replayed webhook a 200 with no second effect (TC-071) rather than a
    second capture.
    """

    psp_name = models.CharField(max_length=40)
    psp_event_id = models.CharField(max_length=120, unique=True)
    event_type = models.CharField(max_length=80)
    psp_reference = models.CharField(max_length=120, blank=True, default="", db_index=True)
    payload = models.JSONField()
    signature_verified = models.BooleanField(default=False)
    #: Null while the event names a reference we do not know — which is a
    #: reconciliation exception in 8b (§21.9's "unmatched at PSP"), not an error.
    payment_id = models.BigIntegerField(null=True, blank=True, default=None, db_index=True)
    outcome = models.CharField(
        max_length=30, choices=WebhookOutcome.choices, default=WebhookOutcome.RECEIVED
    )
    note = models.CharField(max_length=500, blank=True, default="")
    received_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        db_table = "payment_webhook_event"
        ordering = ["-received_at", "id"]
        indexes = [
            models.Index(fields=["psp_name", "received_at"], name="webhook_event_arrival_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(outcome__in=WebhookOutcome.values), name="webhook_outcome_is_known"
            ),
        ]
