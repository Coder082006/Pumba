"""Application layer (SRS §8.2 layer 2).

    Public interface: initiate(), verify(), refund()

The only door into this module. §6.5 rule 1: other modules call these and
nothing else.

**The charge is computed here, from the trip.** PM2 and BR-060: a client-supplied
amount is never trusted, and `initiate` does not accept one. TC-073 submits
8.34 against a basket of 834.75; there is no parameter for it to arrive in, and
the attempt is audited rather than silently ignored.

**The PSP call happens outside the transaction.** §20.7: "The booking module
never holds a transaction open across an external call." The same rule binds
this module harder, because its external call takes money: the row is written
and committed, then Stripe is asked, then the answer is recorded. A gateway
that hangs for thirty seconds must not hold a row lock for thirty seconds.

**Which leaves a window, deliberately.** Between the commit and the answer
there is a payment row in INITIATED with no `psp_reference`. That is the state
§21.5's poller exists to resolve, and it is strictly better than the
alternative: a PSP that charged a card while our transaction rolled back would
be money taken for a payment we have no record of.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.booking import services as booking_services
from apps.common.audit import AuditAction, record_audit
from apps.common.config import get_setting
from apps.common.errors import ConflictError, NotFoundError, ValidationError
from apps.common.money import Money
from apps.common.ports_registry import get_payment_port
from apps.inventory import services as inventory
from apps.payment import repositories as repo
from apps.payment.domain.lifecycle import PaymentState
from apps.payment.domain.lifecycle import apply as apply_transition
from apps.payment.dto import PaymentActionDTO, PaymentDTO
from apps.payment.models import Payment, PaymentMethod, PaymentStatus
from apps.trip import services as trip_services
from ports.payment import PaymentIntent
from ports.payment import PaymentMethod as PortMethod

logger = logging.getLogger(__name__)

__all__ = [
    "initiate",
    "payment_for_trip",
    "payment_detail",
    "TripNotPayableError",
    "QuoteExpiredError",
]


class TripNotPayableError(ConflictError):
    """§9.4.7: 409 TRIP_NOT_PAYABLE."""

    code = "TRIP_NOT_PAYABLE"


class QuoteExpiredError(ConflictError):
    """§9.4.7: 409 QUOTE_EXPIRED. The offer lapsed; price it again."""

    code = "QUOTE_EXPIRED"


#: The statuses a payment may be in and still be worth handing back rather than
#: starting again. A CAPTURED payment is not one of them — that trip is paid
#: for, and asking to pay again is TRIP_NOT_PAYABLE, not a second intent.
_RESUMABLE = (PaymentStatus.INITIATED, PaymentStatus.PENDING, PaymentStatus.AUTHORISED)


def _dto(
    row: Payment, *, trip_public_id: UUID, action: PaymentActionDTO | None = None
) -> PaymentDTO:
    return PaymentDTO(
        public_id=row.public_id,
        trip_public_id=trip_public_id,
        status=row.status,
        method=row.method,
        currency=row.presentment_currency,
        amount=row.presentment_amount,
        action=action,
        failure_code=row.failure_code,
        expires_at=row.expires_at,
        captured_at=row.captured_at,
        created_at=row.created_at,
    )


def _action(intent: PaymentIntent) -> PaymentActionDTO | None:
    if intent.action is None:
        return None
    return PaymentActionDTO(type=intent.action.type, payload=dict(intent.action.payload))


@transaction.atomic
def _open_row(
    *,
    trip_public_id: UUID,
    tourist_id: int,
    method: PaymentMethod,
    amount: Decimal,
    currency: str,
) -> tuple[Payment, int]:
    """Everything that must be true before a card is charged, under one lock.

    Returns the payment row and the trip's storage id. Committed before the PSP
    is called, for the reason in the module docstring.
    """
    trip = trip_services.get_trip(trip_public_id, tourist_id=tourist_id)
    if trip is None:
        raise NotFoundError(f"no trip {trip_public_id}")
    if trip.status != "PENDING_PAYMENT":
        raise TripNotPayableError(
            f"a trip in {trip.status} cannot be paid for; reserve it first",
        )
    now = timezone.now()
    if trip.quote_expires_at is None or trip.quote_expires_at <= now:
        raise QuoteExpiredError("the price this trip was reserved at has lapsed; get a new one")

    trip_id = booking_services.owned_trip_id(trip_public_id, tourist_id=tourist_id)
    if trip_id is None:
        raise NotFoundError(f"no trip {trip_public_id}")

    window = timedelta(minutes=int(get_setting("payment.window_minutes")))
    return (
        repo.create_payment(
            trip_id=trip_id,
            tourist_id=tourist_id,
            method=method.value,
            presentment_currency=currency,
            presentment_amount=amount,
            # 8b replaces this with what the settlement report says the PSP
            # actually paid out, together with the rate BR-065 requires. Until
            # then the honest statement is that we charge and settle in one
            # currency, which is what ADR 0024 keeps true by charging in the
            # destination's own currency.
            settlement_currency=currency,
            psp_name="stripe",
            idempotency_key=uuid.uuid4().hex,
            expires_at=now + window,
        ),
        trip_id,
    )


def initiate(
    trip_public_id: UUID,
    *,
    tourist_id: int,
    method: PaymentMethod = PaymentMethod.CARD,
    return_url: str | None = None,
    client_amount: Decimal | None = None,
) -> PaymentDTO:
    """`POST /payments/intents` — §9.4.7.

    `client_amount` is accepted and **never charged**. §9.4.7 gives the request
    body no amount, but a client can send one anyway, and TC-073 requires that
    submitting 8.34 against a basket of 834.75 charges 834.75 and logs the
    attempt. Reading it here is what makes the audit possible; the charge comes
    from the trip either way.
    """
    trip = trip_services.get_trip(trip_public_id, tourist_id=tourist_id)
    if trip is None:
        raise NotFoundError(f"no trip {trip_public_id}")

    if client_amount is not None and client_amount != trip.total_amount:
        # §21.8: "Hard failure, audited as a potential tampering attempt." The
        # payment is not refused — the tourist may simply have a stale page —
        # but the figure they sent is recorded beside the one they are charged.
        record_audit(
            AuditAction.PAYMENT_AMOUNT_MISMATCH,
            entity_type="trip",
            entity_id=str(trip_public_id),
            before={"submitted": str(client_amount)},
            after={"charged": str(trip.total_amount), "currency": trip.currency},
            reason="The amount submitted is not the amount this trip costs.",
        )

    trip_id = booking_services.owned_trip_id(trip_public_id, tourist_id=tourist_id)
    if trip_id is not None:
        existing = repo.live_payment_for_trip(trip_id)
        if existing is not None and existing.status not in _RESUMABLE:
            raise TripNotPayableError(
                f"this trip already has a {existing.status.lower()} payment",
            )
        if existing is not None:
            # BR-062 allows one live payment per trip, so a tourist who
            # reloaded the page gets the intent they already have rather than a
            # refusal. The PSP is asked for its current action, because a
            # client secret is the thing the browser needs and we do not store
            # one (it is a credential, not a record).
            intent = get_payment_port().fetch_status(existing.psp_reference or "")
            return _dto(existing, trip_public_id=trip_public_id, action=_action(intent))

    payment, trip_id = _open_row(
        trip_public_id=trip_public_id,
        tourist_id=tourist_id,
        method=method,
        amount=trip.total_amount,
        currency=trip.currency,
    )

    # Outside the transaction, on purpose (module docstring).
    try:
        intent = get_payment_port().create_intent(
            amount=Money(trip.total_amount, trip.currency),
            method=PortMethod(method.value),
            idempotency_key=payment.idempotency_key,
            return_url=return_url,
            metadata={"trip": str(trip_public_id), "payment": str(payment.public_id)},
        )
    except ValidationError as exc:
        _fail(payment, code=getattr(exc, "code", "") or "CARD_DECLINED", reason=str(exc))
        raise
    except Exception:
        # An outage leaves no usable payment. Failing the row rather than
        # leaving it INITIATED is what lets the tourist try again: BR-062
        # permits one *live* payment per trip, and a row nobody can finish
        # would lock the trip out of payment until it expired.
        _fail(payment, code="GATEWAY_UNAVAILABLE", reason="The gateway could not be reached.")
        raise

    with transaction.atomic():
        row = repo.lock_payment(payment.pk)
        assert row is not None
        row.psp_reference = intent.psp_reference
        row.save(update_fields=["psp_reference", "updated_at"])
        repo.record_transition(
            row,
            to_status=PaymentStatus(
                apply_transition(
                    PaymentState(row.status),
                    PaymentState.PENDING,
                    {"psp_reference": intent.psp_reference},
                ).value
            ),
            occurred_at=timezone.now(),
            psp_reference=intent.psp_reference,
            amount=row.presentment_amount,
            currency=row.presentment_currency,
            reason="Intent accepted by the gateway.",
        )
        # §17.2: the hold runs to the payment window from the moment the intent
        # exists, so a 3-D Secure challenge does not cost the tourist their
        # seats. The basket already extended them once; this re-extends from
        # now, which is what "from the moment a payment intent is created"
        # means when a tourist reserves and pays twenty minutes later.
        inventory.extend_holds(
            trip_id=trip_id,
            until=row.expires_at or timezone.now(),
            now=timezone.now(),
        )

    return _dto(row, trip_public_id=trip_public_id, action=_action(intent))


def _fail(payment: Payment, *, code: str, reason: str) -> None:
    """Record a refusal that happened at the gateway, in its own transaction.

    Its own, because the caller is about to raise: a failure recorded inside
    the transaction that then rolls back is a failure nobody can see, and the
    whole point of the row is that the next attempt knows this one happened.
    """
    with transaction.atomic():
        row = repo.lock_payment(payment.pk)
        if row is None or row.status != PaymentStatus.INITIATED:
            return
        repo.record_transition(
            row,
            to_status=PaymentStatus.FAILED,
            occurred_at=timezone.now(),
            failure_code=code,
            reason=reason[:500],
        )


def payment_for_trip(trip_public_id: UUID, *, tourist_id: int) -> PaymentDTO | None:
    """The live payment for one of this tourist's trips, if there is one."""
    trip_id = booking_services.owned_trip_id(trip_public_id, tourist_id=tourist_id)
    if trip_id is None:
        return None
    row = repo.live_payment_for_trip(trip_id)
    return None if row is None else _dto(row, trip_public_id=trip_public_id)


def payment_detail(public_id: UUID, *, tourist_id: int | None) -> PaymentDTO:
    """`GET /payments/{id}` — §30.3: a foreign payment is absent, not forbidden.

    The trip's public id comes from `trip.services`, which is the only way this
    module may learn it: `private-trip` closes that module's models and
    selectors to everyone (ADR 0012), and a payment stores the storage id.
    """
    row = Payment.objects.filter(public_id=public_id).first()
    if row is None or (tourist_id is not None and row.tourist_id != tourist_id):
        raise NotFoundError(f"no payment {public_id}")
    return _dto(row, trip_public_id=trip_services.public_id_of_trip(row.trip_id))
