"""Data-access layer (SRS §8.2 layer 4). Writes, and the locks around them.

Every status change goes through `record_transition`, which moves the row and
writes the `payment_transaction` beside it in one call. Two callers doing that
in two steps is how a payment ends up with a status nothing accounts for — PM6
asks for a row per change, and the only way that stays true is for there to be
one way to change one.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from django.db import connection

from apps.payment.models import Payment, PaymentStatus, PaymentTransaction, PaymentWebhookEvent

__all__ = [
    "create_payment",
    "live_payment_for_trip",
    "lock_payment",
    "lock_payment_by_reference",
    "record_transition",
    "advisory_lock",
    "store_webhook_event",
]


def create_payment(**fields: object) -> Payment:
    return Payment.objects.create(**fields)


def live_payment_for_trip(trip_id: int) -> Payment | None:
    """BR-062's row, if there is one.

    The partial unique index makes "the" live payment well defined: there can
    be at most one, so this returns a row rather than a queryset and a caller
    never has to decide which of two to use.
    """
    from apps.payment.models import LIVE_STATUSES

    return Payment.objects.filter(
        trip_id=trip_id, status__in=[s.value for s in LIVE_STATUSES]
    ).first()


def lock_payment(payment_id: int) -> Payment | None:
    return Payment.objects.select_for_update().filter(pk=payment_id).first()


def lock_payment_by_reference(psp_reference: str) -> Payment | None:
    return Payment.objects.select_for_update().filter(psp_reference=psp_reference).first()


def advisory_lock(payment_id: int) -> None:
    """§9.4.8: "acquire an advisory lock on the payment".

    Transaction-scoped (`pg_advisory_xact_lock`), so it is released on commit
    or rollback and no code path can leak one. It is taken *in addition* to the
    row lock because two webhooks for the same payment may arrive at two
    workers a millisecond apart, and the second must wait for the first to
    finish confirming an entire trip — not merely to finish updating a row.

    §8.3 names this lock for exactly this reason: "exactly-once effect under
    duplicate webhooks".
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)", [_PAYMENT_LOCK_NAMESPACE, payment_id]
        )


#: An arbitrary, fixed first key so payment locks cannot collide with any other
#: advisory lock the system takes. 8b's payout batching will take its own.
_PAYMENT_LOCK_NAMESPACE = 8021


def record_transition(
    payment: Payment,
    *,
    to_status: PaymentStatus,
    occurred_at: datetime,
    actor_role: str = "SYSTEM",
    actor_user_id: int | None = None,
    reason: str = "",
    failure_code: str = "",
    amount: Decimal | None = None,
    currency: str = "",
    psp_reference: str = "",
    raw_event_id: int | None = None,
    extra_fields: dict[str, object] | None = None,
) -> Payment:
    """Move the payment and write the row that says so, together.

    `extra_fields` carries the stamps that belong to a particular arrival —
    `captured_at`, `settlement_amount`, `fx_rate` — so the caller does not save
    the row twice and leave a window where the status has moved and the
    timestamp has not.
    """
    from_status = payment.status
    payment.status = to_status.value
    changed = ["status", "version", "updated_at"]
    if failure_code:
        payment.failure_code = failure_code
        changed.append("failure_code")
    for name, value in (extra_fields or {}).items():
        setattr(payment, name, value)
        changed.append(name)
    payment.version += 1
    payment.save(update_fields=sorted(set(changed)))

    PaymentTransaction.objects.create(
        payment=payment,
        from_status=from_status,
        to_status=to_status.value,
        amount=amount,
        currency=currency,
        failure_code=failure_code,
        psp_reference=psp_reference or (payment.psp_reference or ""),
        raw_event_id=raw_event_id,
        actor_role=actor_role,
        actor_user_id=actor_user_id,
        reason=reason,
        occurred_at=occurred_at,
    )
    return payment


def store_webhook_event(**fields: object) -> PaymentWebhookEvent:
    """§21.5: the payload is durable before anything reads it."""
    return PaymentWebhookEvent.objects.create(**fields)
