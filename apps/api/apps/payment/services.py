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
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

from django.db import transaction
from django.utils import timezone

from apps.booking import services as booking_services
from apps.booking.services import RefundExceedsCapturedError, assert_refundable
from apps.common.audit import AuditAction, record_audit
from apps.common.config import get_setting
from apps.common.errors import ConflictError, NotFoundError, ValidationError
from apps.common.events import DomainEvent, publish
from apps.common.money import Money
from apps.common.ports_registry import get_payment_port
from apps.common.state_machine import GuardFailedError, IllegalTransitionError
from apps.inventory import services as inventory
from apps.payment import repositories as repo
from apps.payment.domain.lifecycle import PaymentState, advances, is_terminal
from apps.payment.domain.lifecycle import apply as apply_transition
from apps.payment.dto import PaymentActionDTO, PaymentDTO, RefundDTO
from apps.payment.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    PaymentWebhookEvent,
    Refund,
    RefundStatus,
    WebhookOutcome,
)
from apps.trip import services as trip_services
from ports.payment import PaymentIntent, PaymentIntentStatus, WebhookEvent
from ports.payment import PaymentMethod as PortMethod

logger = logging.getLogger(__name__)

__all__ = [
    "initiate",
    "PaymentCaptured",
    "RefundSettled",
    "RefundApprovalRequiredError",
    "ingest_webhook",
    "apply_psp_state",
    "poll_payment",
    "verify",
    "refunds_awaiting_settlement",
    "settle_refund",
    "request_refund",
    "list_refunds",
    "list_payments",
    "payment_for_trip",
    "payment_detail",
    "TripNotPayableError",
    "QuoteExpiredError",
]


@dataclass(frozen=True, slots=True, kw_only=True)
class PaymentCaptured(DomainEvent):
    """§8.9's `PaymentCaptured`. The money is taken; the trip is another fact.

    Published beside the confirmation rather than instead of it, because §19.2
    makes PAYMENT_* undisableable: a tourist whose payment succeeded and whose
    every component then failed is still owed the statement that they paid.
    """

    name = "payment.captured"
    payment_public_id: str = ""
    trip_id: int = 0
    tourist_id: int = 0
    amount: str = "0"
    currency: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class RefundSettled(DomainEvent):
    """§8.9's `RefundSettled`. 8b's ledger reverses the accrual from it.

    Primitives only, like every other event here: a handler runs after commit,
    often in another module, and a Decimal that crossed as a float would lose a
    cent on the way.
    """

    name = "payment.refund_settled"
    refund_public_id: str = ""
    payment_public_id: str = ""
    trip_id: int = 0
    amount: str = "0"
    currency: str = ""


class RefundApprovalRequiredError(ConflictError):
    """BR-047: above the auto-approval limit, a finance officer decides."""

    code = "REFUND_APPROVAL_REQUIRED"


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

    # §9.4.7's last step: "schedule verify_payment_status as a webhook-failure
    # fallback". After commit, because a task that ran against a rolled-back
    # payment would poll for a row that does not exist. Imported here rather
    # than at module scope: `tasks` imports this module.
    from apps.payment.tasks import PROBE_SCHEDULE, verify_payment_status

    transaction.on_commit(
        lambda: verify_payment_status.apply_async(
            args=[int(row.pk), 0], countdown=PROBE_SCHEDULE[0], queue="payments"
        )
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


# ---------------------------------------------------------------------------
# §9.4.8 — the webhook, and §21.5's poller behind it
# ---------------------------------------------------------------------------


#: The port's statuses, which mirror §21.4's, onto this module's states. A
#: lookup rather than a cast, so a port that grows a status fails loudly in a
#: test rather than passing an unknown string into the machine.
_PORT_STATE = {
    PaymentIntentStatus.INITIATED: PaymentState.INITIATED,
    PaymentIntentStatus.PENDING: PaymentState.PENDING,
    PaymentIntentStatus.AUTHORISED: PaymentState.AUTHORISED,
    PaymentIntentStatus.CAPTURED: PaymentState.CAPTURED,
    PaymentIntentStatus.FAILED: PaymentState.FAILED,
    PaymentIntentStatus.EXPIRED: PaymentState.EXPIRED,
}


def ingest_webhook(*, provider: str, payload: bytes, headers: dict[str, str]) -> WebhookOutcome:
    """`POST /webhooks/psp/{provider}` — §9.4.8, §21.5.

    In the SRS's order, and every step of it matters:

    1. **Verify.** An unverified payload never reaches the state machine. A bad
       signature raises here and no row is written — an unsigned body is not
       evidence of anything.
    2. **Store.** The raw event is committed before it is interpreted, so a bug
       in step 4 costs a transition and never the event itself.
    3. **Deduplicate.** An event id already stored is acknowledged and dropped
       (TC-071). The unique index decides that, not a check followed by an act.
    4. **Apply**, under an advisory lock, in a second transaction.

    Between 3 and 4 the gateway is asked what the payment actually is, outside
    any transaction. PM4 makes the PSP the authority, and an event saying
    "captured" is worth less than the intent saying what was captured and for
    how much — which is also what stops a replayed body from asserting an
    amount.
    """
    event = get_payment_port().verify_webhook(payload=payload, headers=headers)

    with transaction.atomic():
        if PaymentWebhookEvent.objects.filter(psp_event_id=event.event_id).exists():
            return WebhookOutcome.DUPLICATE
        stored = repo.store_webhook_event(
            psp_name=provider,
            psp_event_id=event.event_id,
            event_type=event.event_type,
            psp_reference=event.psp_reference,
            payload=event.raw,
            signature_verified=True,
            received_at=timezone.now(),
        )

    return apply_psp_state(event=event, stored_event_id=int(stored.pk))


def apply_psp_state(*, event: WebhookEvent, stored_event_id: int | None = None) -> WebhookOutcome:
    """Move a payment to what the PSP says it is, and do what that means.

    Shared by the webhook and by §21.5's polling fallback, which is the point
    of it being a function rather than a view: a lost webhook must cost latency
    and not correctness, and two code paths that read a capture differently
    would be two systems disagreeing about whether a trip is paid for.
    """
    payment = Payment.objects.filter(psp_reference=event.psp_reference).first()
    if payment is None:
        _record_outcome(stored_event_id, WebhookOutcome.UNMATCHED, note=event.psp_reference)
        logger.warning(
            "webhook_for_unknown_payment",
            extra={"psp_reference": event.psp_reference, "event_type": event.event_type},
        )
        return WebhookOutcome.UNMATCHED

    intent = get_payment_port().fetch_status(event.psp_reference)
    target = _PORT_STATE[intent.status]

    # PM4 makes the gateway's current state the answer — with one exception the
    # gateway's own model forces. A declined card leaves a Stripe intent
    # reusable, so `fetch_status` still reports it as waiting for a payment
    # method, and taking that literally would leave a tourist staring at a
    # spinner after their card was refused. When the event reports a failure
    # the intent's state does not carry, the event is the more recent fact.
    if event.status in (PaymentIntentStatus.FAILED, PaymentIntentStatus.EXPIRED):
        target = _PORT_STATE[event.status]

    with transaction.atomic():
        repo.advisory_lock(int(payment.pk))
        locked = repo.lock_payment(int(payment.pk))
        assert locked is not None
        source = PaymentState(locked.status)

        if not advances(source, target):
            # §21.5: "applies a state transition only if it is legal and
            # advances the state, otherwise it records and ignores".
            _record_outcome(
                stored_event_id,
                WebhookOutcome.IGNORED_NOT_ADVANCING,
                note=f"{source} would not advance to {target}",
                payment_id=int(locked.pk),
            )
            return WebhookOutcome.IGNORED_NOT_ADVANCING

        failure_code = _failure_code(event) if target is PaymentState.FAILED else ""
        context: dict[str, object] = {
            "psp_reference": event.psp_reference,
            "customer_authorised": target is PaymentState.AUTHORISED,
            "psp_captured": target is PaymentState.CAPTURED,
            "captured_amount": intent.amount.amount,
            "expected_amount": locked.presentment_amount,
            "failure_code": failure_code,
            "window_elapsed": target is PaymentState.EXPIRED,
        }

        try:
            apply_transition(source, target, context)
        except GuardFailedError:
            # The guard that refuses a real arrival: a capture for an amount
            # this payment never asked for (§21.8's AMOUNT_MISMATCH, TC-073).
            # Money has moved at the PSP, so this is an exception for a human
            # rather than a state to invent — and §21.9's reconciliation will
            # find it again tomorrow whatever happens here.
            _record_outcome(
                stored_event_id,
                WebhookOutcome.IGNORED_NOT_ADVANCING,
                note="captured amount does not match the trip total",
                payment_id=int(locked.pk),
            )
            record_audit(
                AuditAction.PAYMENT_AMOUNT_MISMATCH,
                entity_type="payment",
                entity_id=str(locked.public_id),
                before={"captured": str(intent.amount.amount)},
                after={"expected": str(locked.presentment_amount)},
                reason="The gateway captured an amount this payment did not ask for.",
            )
            logger.error(
                "capture_amount_mismatch",
                extra={
                    "payment": str(locked.public_id),
                    "captured": str(intent.amount.amount),
                    "expected": str(locked.presentment_amount),
                },
            )
            return WebhookOutcome.IGNORED_NOT_ADVANCING
        except IllegalTransitionError:
            _record_outcome(
                stored_event_id,
                WebhookOutcome.IGNORED_NOT_ADVANCING,
                note=f"{source} -> {target} is not a declared edge",
                payment_id=int(locked.pk),
            )
            return WebhookOutcome.IGNORED_NOT_ADVANCING

        now = timezone.now()
        extra: dict[str, object] = {}
        if target is PaymentState.CAPTURED:
            extra = {
                "captured_at": now,
                # 8b's reconciliation replaces both from the settlement report,
                # which is the only place the real figures exist (§21.9). Until
                # then presentment and settlement are one currency, so the
                # honest rate is one — BR-065 asks for a rate, not a guess.
                "settlement_amount": intent.amount.amount,
                "fx_rate": Decimal("1"),
            }

        repo.record_transition(
            locked,
            to_status=PaymentStatus(target.value),
            occurred_at=now,
            failure_code=failure_code,
            amount=intent.amount.amount,
            currency=intent.amount.currency,
            psp_reference=event.psp_reference,
            raw_event_id=stored_event_id,
            reason=event.event_type,
            extra_fields=extra,
        )
        _record_outcome(
            stored_event_id,
            WebhookOutcome.APPLIED,
            note=f"{source} -> {target}",
            payment_id=int(locked.pk),
        )

        # §9.4.8: "on CAPTURED, run the confirmation routine of Section 20.8".
        # Inside this transaction on purpose: a captured payment beside an
        # unconfirmed trip is what §20.4 calls the most important invariant in
        # the system to hold, and two transactions could leave exactly that.
        if target is PaymentState.CAPTURED:
            booking_services.confirm_trip(locked.trip_id, payment_captured=True, now=now)
            publish(
                PaymentCaptured(
                    payment_public_id=str(locked.public_id),
                    trip_id=locked.trip_id,
                    tourist_id=locked.tourist_id,
                    amount=str(intent.amount.amount),
                    currency=intent.amount.currency,
                )
            )
        elif target in (PaymentState.FAILED, PaymentState.EXPIRED):
            booking_services.fail_basket(
                locked.trip_id, cause=booking_services.BasketFailure.PAYMENT_FAILED, now=now
            )

    return WebhookOutcome.APPLIED


def _failure_code(event: WebhookEvent) -> str:
    """§21.8's taxonomy, if the adapter put one on the event.

    A decline we cannot classify is a card decline: the tourist's next step is
    the same, and a code per issuer message would be a taxonomy nobody could
    act on.
    """
    raw = event.raw if isinstance(event.raw, dict) else {}
    code = raw.get("failure_code")
    return str(code) if code else "CARD_DECLINED"


def _record_outcome(
    stored_event_id: int | None,
    outcome: WebhookOutcome,
    *,
    note: str = "",
    payment_id: int | None = None,
) -> None:
    """Write what was done about an event, on the event's own row.

    The row's payload is immutable (migration 0002) and these four columns are
    not: the PSP's words are the evidence, and this is the platform's note
    about them. A `.update()` rather than a save, because the row was written
    in a different transaction and re-reading it to set four fields would be a
    lock taken for no reason.
    """
    if stored_event_id is None:
        return
    fields: dict[str, object] = {
        "outcome": outcome.value,
        "note": note[:500],
        "processed_at": timezone.now(),
    }
    if payment_id is not None:
        fields["payment_id"] = payment_id
    PaymentWebhookEvent.objects.filter(pk=stored_event_id).update(**fields)


def poll_payment(payment_id: int) -> str:
    """§21.5's fallback: ask the PSP, because nothing told us.

    Returns `TERMINAL` for a payment that has finished, `APPLIED` when the
    answer moved it, and `PENDING` when the tourist simply has not paid yet —
    which is the ordinary case for the first two rungs of the ladder and is not
    a problem to report.

    Deliberately not authenticated and deliberately not a service a tourist can
    call for somebody else's payment: the caller is a Celery task holding a
    storage id, exactly as §17.5's sweeper is.
    """
    payment = Payment.objects.filter(pk=payment_id).first()
    if payment is None:
        return "TERMINAL"
    if is_terminal(PaymentState(payment.status)):
        return "TERMINAL"
    if not payment.psp_reference:
        # The intent never reached the gateway — `initiate` failed the row if
        # it could, and if it could not, there is nothing to ask about.
        return "PENDING"

    intent = get_payment_port().fetch_status(payment.psp_reference)
    outcome = apply_psp_state(
        event=WebhookEvent(
            event_id=f"poll-{uuid.uuid4().hex}",
            event_type="payment.polled",
            psp_reference=payment.psp_reference,
            status=intent.status,
            raw={"source": "poll"},
        )
    )
    return "APPLIED" if outcome is WebhookOutcome.APPLIED else "PENDING"


def verify(public_id: UUID, *, tourist_id: int | None) -> PaymentDTO:
    """`POST /payments/{id}/verify` — §9.3.7's client-initiated refresh.

    The same question the poller asks, asked by the tourist's own screen while
    it waits for a 3-D Secure challenge or a mobile-money prompt to come back.
    It writes only what the PSP says, so a tourist pressing it repeatedly
    cannot move their own payment anywhere the gateway has not.
    """
    row = Payment.objects.filter(public_id=public_id).first()
    if row is None or (tourist_id is not None and row.tourist_id != tourist_id):
        raise NotFoundError(f"no payment {public_id}")
    poll_payment(int(row.pk))
    return payment_detail(public_id, tourist_id=tourist_id)


# ---------------------------------------------------------------------------
# §21.6 — refunds
# ---------------------------------------------------------------------------


def refunds_awaiting_settlement(*, limit: int = 50) -> list[int]:
    """The obligations `handlers` recorded and nobody has paid yet.

    Oldest first: a tourist who has been waiting longest is the one to pay
    next, and a queue ordered any other way starves somebody quietly.
    """
    return list(
        Refund.objects.filter(status=RefundStatus.REQUESTED)
        .order_by("requested_at", "id")
        .values_list("id", flat=True)[:limit]
    )


def settle_refund(refund_id: int) -> bool:
    """Give one refund back at the PSP, and record what that means.

    In order:

    1. **BR-044 before the gateway.** `assert_refundable` compares what is
       asked against what was captured and not already refunded. TC-103 —
       refunding 200 of a 110 booking — is refused here, before any money can
       move, because a PSP that accepted it would leave the platform out of
       pocket with nothing to reverse.
    2. **The PSP**, outside every transaction (§20.7).
    3. **The payment's own state**: PARTIALLY_REFUNDED while some of it stands,
       REFUNDED when all of it is back — the difference the §21.4 guards work
       out from the totals rather than from a flag somebody sets.
    4. **The booking**: `booking.settle_refund` moves CANCELLED → REFUNDED,
       which is §20.2's last edge and the one nothing could reach until now.

    Returns whether the money moved. A refund that fails at the gateway is
    marked FAILED with its reason rather than retried forever: three declines
    of the same refund is an operations problem, not a scheduling one.
    """
    refund = Refund.objects.filter(pk=refund_id).select_related("payment").first()
    if refund is None or refund.status != RefundStatus.REQUESTED:
        return False

    payment = refund.payment
    already = sum(
        (row.amount for row in payment.refunds.filter(status=RefundStatus.SETTLED)),
        Decimal("0"),
    )
    captured = payment.settlement_amount or payment.presentment_amount
    try:
        assert_refundable(refund.amount, paid=captured, already_refunded=already)
    except RefundExceedsCapturedError as exc:
        _fail_refund(refund, code="REFUND_EXCEEDS_CAPTURED", reason=str(exc))
        logger.error(
            "refund_exceeds_captured",
            extra={"refund": str(refund.public_id), "amount": str(refund.amount)},
        )
        return False

    try:
        result = get_payment_port().refund(
            payment.psp_reference or "",
            amount=Money(refund.amount, refund.currency),
            idempotency_key=refund.idempotency_key,
            reason=refund.reason_code,
        )
    except ValidationError as exc:
        _fail_refund(refund, code=getattr(exc, "code", "") or "REFUND_FAILED", reason=str(exc))
        return False
    except Exception as exc:
        logger.warning(
            "refund_not_settled",
            extra={"refund": str(refund.public_id), "error": str(exc)},
        )
        return False

    if not result.settled:
        # Accepted and not yet final at the PSP. The row stays REQUESTED so the
        # next run asks again, rather than telling a tourist their money is
        # back while it is still in flight.
        return False

    now = timezone.now()
    with transaction.atomic():
        repo.advisory_lock(int(payment.pk))
        locked_payment = repo.lock_payment(int(payment.pk))
        assert locked_payment is not None
        row = Refund.objects.select_for_update().get(pk=refund.pk)
        if row.status != RefundStatus.REQUESTED:
            return False
        row.status = RefundStatus.SETTLED.value
        row.psp_refund_reference = result.psp_refund_reference
        row.settled_at = now
        row.save(update_fields=["status", "psp_refund_reference", "settled_at", "updated_at"])

        refunded_total = already + row.amount
        target = (
            PaymentState.REFUNDED
            if refunded_total
            >= (locked_payment.settlement_amount or locked_payment.presentment_amount)
            else PaymentState.PARTIALLY_REFUNDED
        )
        source = PaymentState(locked_payment.status)
        if source is not target:
            apply_transition(
                source,
                target,
                {
                    "refunded_total": refunded_total,
                    "captured_amount": locked_payment.settlement_amount
                    or locked_payment.presentment_amount,
                },
            )
            repo.record_transition(
                locked_payment,
                to_status=PaymentStatus(target.value),
                occurred_at=now,
                amount=row.amount,
                currency=row.currency,
                reason=f"Refund {row.public_id} settled.",
            )

    if row.booking_id is not None:
        # Outside the payment transaction: the booking is another module's row,
        # and §20.7's rule about long transactions applies to a call that
        # confirms an entire trip in the other direction too.
        booking_services.settle_refund(
            _booking_public_id(row.booking_id),
            now=now,
            reason=f"Refund {row.public_id} settled at the provider.",
        )

    publish(
        RefundSettled(
            refund_public_id=str(row.public_id),
            payment_public_id=str(payment.public_id),
            trip_id=payment.trip_id,
            amount=str(row.amount),
            currency=row.currency,
        )
    )
    return True


def request_refund(
    *,
    payment_public_id: UUID,
    amount: Decimal,
    reason_code: str,
    reason: str,
    requested_by_user_id: int | None,
    approved_by_user_id: int | None = None,
    booking_id: int | None = None,
) -> RefundDTO:
    """`POST /refunds` — §21.6's discretionary refund, an administrator's.

    BR-047: above `refund.auto_approve_limit` a FINANCE_OFFICER must have
    approved it, and the caller proves that by naming who did. The check is
    here rather than in the view because it is a rule about money, and §8.2
    puts rules about money in the service layer.
    """
    payment = Payment.objects.filter(public_id=payment_public_id).first()
    if payment is None:
        raise NotFoundError(f"no payment {payment_public_id}")
    if payment.status not in (PaymentStatus.CAPTURED, PaymentStatus.PARTIALLY_REFUNDED):
        raise ConflictError(
            f"a payment in {payment.status} has nothing to refund",
        )

    limit = Decimal(str(get_setting("refund.auto_approve_limit")))
    if amount > limit and approved_by_user_id is None:
        raise RefundApprovalRequiredError(
            f"a refund above {limit} requires a finance officer's approval",
        )

    row = Refund.objects.create(
        payment=payment,
        booking_id=booking_id,
        amount=amount,
        currency=payment.presentment_currency,
        reason_code=reason_code,
        reason=reason[:500],
        requested_by_user_id=requested_by_user_id,
        approved_by_user_id=approved_by_user_id,
        idempotency_key=uuid.uuid4().hex,
        requested_at=timezone.now(),
    )
    return _refund_dto(row)


def _booking_public_id(booking_id: int) -> UUID:
    """Through `booking.services`, never its models: `private-booking` closes
    those to this module and the boundary is the point of the contract."""
    return booking_services.public_id_of_booking(booking_id)


def _fail_refund(refund: Refund, *, code: str, reason: str) -> None:
    refund.status = RefundStatus.FAILED.value
    refund.failure_code = code
    refund.reason = (refund.reason or reason)[:500]
    refund.save(update_fields=["status", "failure_code", "reason", "updated_at"])


def _refund_dto(row: Refund) -> RefundDTO:
    booking_public_id = None
    if row.booking_id is not None:
        booking_public_id = _booking_public_id(row.booking_id)
    return RefundDTO(
        public_id=row.public_id,
        payment_public_id=row.payment.public_id,
        booking_public_id=booking_public_id,
        status=row.status,
        currency=row.currency,
        amount=row.amount,
        reason_code=row.reason_code,
        requested_at=row.requested_at,
        settled_at=row.settled_at,
        failure_code=row.failure_code,
    )


def list_refunds(*, status: str | None = None, limit: int = 100) -> list[RefundDTO]:
    """§27.10's refund queue, newest first.

    Administrative: no `tourist_id` filter, because the caller is the console
    and the point of it is to see everyone's. The permission check is the
    view's — §30.3's "absent, not forbidden" is about a tourist reaching
    somebody else's row, not about an operator doing their job.
    """
    rows = Refund.objects.select_related("payment").order_by("-requested_at", "-id")
    if status:
        rows = rows.filter(status=status)
    return [_refund_dto(row) for row in rows[:limit]]


def list_payments(*, status: str | None = None, limit: int = 100) -> list[PaymentDTO]:
    """§27.10's payment search."""
    rows = Payment.objects.order_by("-created_at", "-id")
    if status:
        rows = rows.filter(status=status)
    return [
        _dto(row, trip_public_id=trip_services.public_id_of_trip(row.trip_id))
        for row in rows[:limit]
    ]
