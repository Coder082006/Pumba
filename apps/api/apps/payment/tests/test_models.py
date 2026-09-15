"""The four tables, and the rules the database itself enforces — ADR 0027.

Not a tour of the schema. Each test below is a rule that would otherwise be an
application promise: BR-062's one live payment per trip, PM6's immutable
transaction log, §21.5's unique event id, and the refund invariants. A promise
kept only by the code that happens to write the row is one a shell session, a
data migration or next year's endpoint can break without noticing.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.db.utils import ProgrammingError
from django.utils import timezone

from apps.payment.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    PaymentTransaction,
    PaymentWebhookEvent,
    Refund,
    RefundStatus,
)

pytestmark = pytest.mark.django_db


def a_payment(**overrides: object) -> Payment:
    fields: dict[str, object] = {
        "trip_id": 1,
        "tourist_id": 1,
        "method": PaymentMethod.CARD,
        "presentment_currency": "USD",
        "presentment_amount": Decimal("834.75"),
        "settlement_currency": "USD",
        "psp_name": "stripe",
        "idempotency_key": uuid.uuid4().hex,
    }
    fields.update(overrides)
    return Payment.objects.create(**fields)


class TestTheDefaults:
    def test_a_new_payment_is_initiated(self) -> None:
        assert a_payment().status == PaymentStatus.INITIATED

    def test_settlement_is_unknown_until_capture(self) -> None:
        """§7.5.13 marks the amount nullable and the currency not: the PSP
        account fixes the currency, and only the capture fixes the amount."""
        payment = a_payment()
        assert payment.settlement_amount is None
        assert payment.fx_rate is None
        assert payment.settlement_currency == "USD"

    def test_it_gets_a_uuid_for_the_outside_world(self) -> None:
        assert a_payment().public_id is not None


class TestBr062OneLivePaymentPerTrip:
    def test_a_second_live_payment_on_one_trip_is_refused(self) -> None:
        a_payment(trip_id=42)

        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(trip_id=42)

    def test_a_retry_after_a_failure_is_allowed(self) -> None:
        """R27: "retries create new payment rows". A declined card leaves a
        terminal row behind, and a tourist may try another card — which a plain
        unique constraint on trip_id would have forbidden."""
        a_payment(trip_id=43, status=PaymentStatus.FAILED)

        second = a_payment(trip_id=43)

        assert Payment.objects.filter(trip_id=43).count() == 2
        assert second.status == PaymentStatus.INITIATED

    def test_a_disputed_payment_still_counts_as_live(self) -> None:
        """A chargeback does not free the trip to be paid for again: two live
        claims on one trip is the state BR-062 exists to prevent."""
        a_payment(trip_id=44, status=PaymentStatus.DISPUTED)

        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(trip_id=44)


class TestTheConstraints:
    def test_an_unknown_status_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(status="NEARLY_PAID")

    def test_an_unknown_method_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(method="CHEQUE")

    def test_a_zero_charge_is_refused(self) -> None:
        """A payment for nothing is a state §9.4.7 cannot produce and §20.4
        could not reconcile: it would confirm a trip nobody paid for."""
        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(presentment_amount=Decimal("0.00"))

    def test_one_psp_reference_belongs_to_one_payment(self) -> None:
        """§7.6, and the other half of webhook matching: two payments claiming
        one reference would make a capture ambiguous."""
        a_payment(trip_id=45, psp_reference="pi_1")

        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(trip_id=46, psp_reference="pi_1")

    def test_two_payments_may_both_be_waiting_for_a_reference(self) -> None:
        """The uniqueness is conditional: `psp_reference` is null until the PSP
        answers, and two INITIATED payments on different trips are ordinary."""
        a_payment(trip_id=47)
        a_payment(trip_id=48)

        assert Payment.objects.filter(psp_reference__isnull=True).count() == 2

    def test_one_idempotency_key_belongs_to_one_payment(self) -> None:
        key = uuid.uuid4().hex
        a_payment(trip_id=49, idempotency_key=key)

        with pytest.raises(IntegrityError), transaction.atomic():
            a_payment(trip_id=50, idempotency_key=key)


class TestPaymentTransactionIsAppendOnly:
    def transaction_row(self) -> PaymentTransaction:
        return PaymentTransaction.objects.create(
            payment=a_payment(),
            from_status=PaymentStatus.INITIATED,
            to_status=PaymentStatus.PENDING,
            actor_role="SYSTEM",
            occurred_at=timezone.now(),
        )

    def test_updating_one_raises(self) -> None:
        """PM6. The trigger, not the code, is what makes this true for a shell
        session and a data migration as well as for the ORM."""
        row = self.transaction_row()

        with pytest.raises(ProgrammingError), transaction.atomic():
            PaymentTransaction.objects.filter(pk=row.pk).update(to_status="CAPTURED")

    def test_deleting_one_raises(self) -> None:
        row = self.transaction_row()

        with pytest.raises(ProgrammingError), transaction.atomic():
            PaymentTransaction.objects.filter(pk=row.pk).delete()

    def test_a_payment_with_history_cannot_be_deleted_either(self) -> None:
        """§7.2: financial records are never deleted. Django cascades in Python
        row by row, so the refusal reaches the parent."""
        row = self.transaction_row()

        with pytest.raises(ProgrammingError), transaction.atomic():
            Payment.objects.filter(pk=row.payment_id).delete()

    def test_an_actorless_row_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            PaymentTransaction.objects.create(
                payment=a_payment(),
                to_status=PaymentStatus.PENDING,
                actor_role="",
                occurred_at=timezone.now(),
            )


class TestTheWebhookLog:
    def event(self, **overrides: object) -> PaymentWebhookEvent:
        fields: dict[str, object] = {
            "psp_name": "stripe",
            "psp_event_id": uuid.uuid4().hex,
            "event_type": "payment_intent.succeeded",
            "payload": {"id": "evt_1"},
            "signature_verified": True,
            "received_at": timezone.now(),
        }
        fields.update(overrides)
        return PaymentWebhookEvent.objects.create(**fields)

    def test_one_event_id_is_stored_once(self) -> None:
        """§21.5's deduplication key, and the whole of TC-071: the second
        arrival of an event cannot become a second row."""
        self.event(psp_event_id="evt_dup")

        with pytest.raises(IntegrityError), transaction.atomic():
            self.event(psp_event_id="evt_dup")

    def test_it_is_append_only(self) -> None:
        """The PSP's own words. Editing them would be editing the evidence."""
        row = self.event()

        with pytest.raises(ProgrammingError), transaction.atomic():
            PaymentWebhookEvent.objects.filter(pk=row.pk).update(event_type="something.else")

    def test_an_event_may_arrive_before_we_know_the_payment(self) -> None:
        """§21.9's "unmatched at PSP" is a reconciliation exception, not an
        error, so the row stores itself with no payment rather than refusing."""
        assert self.event(payment_id=None).payment_id is None


class TestRefunds:
    def a_refund(self, **overrides: object) -> Refund:
        fields: dict[str, object] = {
            "payment": a_payment(),
            "amount": Decimal("55.00"),
            "currency": "USD",
            "reason_code": "TOURIST_CANCELLED",
            "idempotency_key": uuid.uuid4().hex,
            "requested_at": timezone.now(),
        }
        fields.update(overrides)
        return Refund.objects.create(**fields)

    def test_a_new_refund_is_requested_and_not_settled(self) -> None:
        """ADR 0027 decision 5: the two are different facts, and a tourist must
        never be told the second while only the first is true."""
        refund = self.a_refund()

        assert refund.status == RefundStatus.REQUESTED
        assert refund.settled_at is None

    def test_a_settled_refund_must_say_when(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            self.a_refund(status=RefundStatus.SETTLED)

    def test_a_settled_refund_with_a_time_is_accepted(self) -> None:
        refund = self.a_refund(status=RefundStatus.SETTLED, settled_at=timezone.now())

        assert refund.settled_at is not None

    def test_a_refund_of_nothing_is_refused(self) -> None:
        with pytest.raises(IntegrityError), transaction.atomic():
            self.a_refund(amount=Decimal("0.00"))

    def test_one_idempotency_key_belongs_to_one_refund(self) -> None:
        """The key that stops a retried task refunding twice."""
        key = uuid.uuid4().hex
        self.a_refund(idempotency_key=key)

        with pytest.raises(IntegrityError), transaction.atomic():
            self.a_refund(idempotency_key=key)
