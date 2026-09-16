"""Refunds, from obligation to money — §21.6, BR-043, BR-044, ADR 0027.

The design being tested is the one ADR 0027 decision 3 argues for: a
cancellation writes a **row**, and a retried task pays it. So the tests are in
two halves — what the handler records, and what the settler does about it — and
the seam between them is where a refund survives a gateway outage.

`BookingCancelled` carries the amount §20.9 decided. Nothing here recomputes
it: BR-043 says the refund issued equals the preview shown, and a second
implementation of the policy would be free to disagree with the first.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.apps import apps as django_apps
from django.utils import timezone

from apps.booking import services as booking_services
from apps.booking.tests import scenario
from apps.common.money import Money
from apps.common.ports_registry import get_payment_port
from apps.payment import handlers, services, tasks
from apps.payment.models import Payment, PaymentMethod, PaymentStatus, Refund, RefundStatus
from ports.payment import PaymentMethod as PortMethod

pytestmark = pytest.mark.django_db


def a_captured_payment(*, trip_id: int = 1, amount: str = "110.00") -> Payment:
    """A payment the fake gateway has taken money on."""
    intent = get_payment_port().create_intent(
        amount=Money(Decimal(amount), "USD"),
        method=PortMethod.CARD,
        idempotency_key=uuid.uuid4().hex,
    )
    get_payment_port().capture(intent.psp_reference, idempotency_key=uuid.uuid4().hex)
    return Payment.objects.create(
        trip_id=trip_id,
        tourist_id=1,
        method=PaymentMethod.CARD,
        presentment_currency="USD",
        presentment_amount=Decimal(amount),
        settlement_currency="USD",
        settlement_amount=Decimal(amount),
        psp_name="stripe",
        psp_reference=intent.psp_reference,
        idempotency_key=uuid.uuid4().hex,
        status=PaymentStatus.CAPTURED,
        captured_at=timezone.now(),
    )


def a_cancellation(
    *, trip_id: int, amount: str, booking_public_id: str = "", by: str = "TOURIST"
) -> booking_services.BookingCancelled:
    return booking_services.BookingCancelled(
        booking_public_id=booking_public_id or str(uuid.uuid4()),
        reference="BKG-2027-0000001",
        trip_id=trip_id,
        provider_id=1,
        cancelled_by=by,
        reason="Tourist cancelled.",
        refund_amount=amount,
        currency="USD",
    )


class TestWhatTheHandlerRecords:
    def test_a_cancellation_becomes_an_obligation_and_nothing_else(self) -> None:
        """ADR 0027 decision 3: the handler writes a row and does not call the
        gateway. A refund issued inside a handler would vanish on the first
        timeout, because `publish` swallows what a handler raises."""
        payment = a_captured_payment(trip_id=7)

        handlers.on_booking_cancelled(a_cancellation(trip_id=7, amount="55.00"))

        refund = Refund.objects.get()
        assert refund.payment_id == payment.pk
        assert refund.amount == Decimal("55.00")
        assert refund.status == RefundStatus.REQUESTED
        assert refund.psp_refund_reference == ""

    def test_the_amount_is_the_one_the_preview_showed(self) -> None:
        """BR-043. The handler does no arithmetic at all — the event carries
        §20.9's answer as a string for exactly this reason."""
        a_captured_payment(trip_id=8)

        handlers.on_booking_cancelled(a_cancellation(trip_id=8, amount="49.50"))

        assert Refund.objects.get().amount == Decimal("49.50")

    def test_a_zero_refund_writes_nothing(self) -> None:
        """A late cancellation under a strict policy owes nothing, and an
        obligation for nothing is a queue entry nobody can settle."""
        a_captured_payment(trip_id=9)

        handlers.on_booking_cancelled(a_cancellation(trip_id=9, amount="0.00"))

        assert Refund.objects.count() == 0

    def test_a_cancellation_with_no_captured_payment_writes_nothing(self) -> None:
        """A basket cancelled before payment: nothing was taken, so nothing is
        owed. The ordinary case, not an error."""
        handlers.on_booking_cancelled(a_cancellation(trip_id=404, amount="55.00"))

        assert Refund.objects.count() == 0

    def test_the_same_cancellation_twice_owes_once(self) -> None:
        """A replayed event or a retried task must not owe the tourist twice."""
        a_captured_payment(trip_id=10)
        booking = str(uuid.uuid4())

        handlers.on_booking_cancelled(
            a_cancellation(trip_id=10, amount="55.00", booking_public_id=booking)
        )
        handlers.on_booking_cancelled(
            a_cancellation(trip_id=10, amount="55.00", booking_public_id=booking)
        )

        assert Refund.objects.count() == 1

    def test_a_component_lost_at_capture_owes_gross_fee_and_tax(self) -> None:
        """§20.8 step 9. The tourist gets none of that component and the
        failure was not theirs, so BR-045's "everything back" applies rather
        than the policy's tiers."""
        a_captured_payment(trip_id=11, amount="200.00")

        handlers.on_component_failed_after_capture(
            booking_services.ComponentFailedAfterCapture(
                booking_public_id=str(uuid.uuid4()),
                reference="BKG-2027-0000002",
                trip_id=11,
                reason="SOLD_OUT",
                gross_amount="100.00",
                fee_amount="5.00",
                tax_amount="2.50",
                currency="USD",
            )
        )

        assert Refund.objects.get().amount == Decimal("107.50")


class TestSettling:
    def test_it_pays_the_gateway_and_marks_the_row_settled(self) -> None:
        a_captured_payment(trip_id=12)
        handlers.on_booking_cancelled(a_cancellation(trip_id=12, amount="55.00"))
        refund = Refund.objects.get()

        assert services.settle_refund(int(refund.pk)) is True

        refund.refresh_from_db()
        assert refund.status == RefundStatus.SETTLED
        assert refund.psp_refund_reference
        assert refund.settled_at is not None

    def test_a_partial_refund_leaves_the_payment_partially_refunded(self) -> None:
        payment = a_captured_payment(trip_id=13)
        handlers.on_booking_cancelled(a_cancellation(trip_id=13, amount="55.00"))

        services.settle_refund(int(Refund.objects.get().pk))

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.PARTIALLY_REFUNDED

    def test_refunding_all_of_it_leaves_the_payment_refunded(self) -> None:
        """TC-102's shape: a supply failure gives everything back."""
        payment = a_captured_payment(trip_id=14)
        handlers.on_booking_cancelled(a_cancellation(trip_id=14, amount="110.00", by="PROVIDER"))

        services.settle_refund(int(Refund.objects.get().pk))

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.REFUNDED

    def test_two_partial_refunds_add_up_to_a_refunded_payment(self) -> None:
        """Two components of one trip, refunded separately. Requested through
        the administrative path, which is the one that takes an amount: the
        handler takes its amount from a cancellation, and one trip cannot be
        cancelled twice."""
        payment = a_captured_payment(trip_id=15)
        for amount in ("60.00", "50.00"):
            services.request_refund(
                payment_public_id=payment.public_id,
                amount=Decimal(amount),
                reason_code="GOODWILL",
                reason="Split refund",
                requested_by_user_id=1,
            )

        for refund_id in services.refunds_awaiting_settlement():
            services.settle_refund(refund_id)

        payment.refresh_from_db()
        assert payment.status == PaymentStatus.REFUNDED
        assert Refund.objects.filter(status=RefundStatus.SETTLED).count() == 2

    def test_tc_103_refunding_more_than_was_captured_is_refused(self) -> None:
        """TC-103: "Refund 200 of a 110 booking → 422 REFUND_EXCEEDS_CAPTURED".
        Refused before the gateway, because a PSP that accepted it would leave
        the platform out of pocket with nothing to reverse."""
        a_captured_payment(trip_id=16, amount="110.00")
        handlers.on_booking_cancelled(a_cancellation(trip_id=16, amount="200.00"))
        refund = Refund.objects.get()

        assert services.settle_refund(int(refund.pk)) is False

        refund.refresh_from_db()
        assert refund.status == RefundStatus.FAILED
        assert refund.failure_code == "REFUND_EXCEEDS_CAPTURED"

    def test_settling_twice_pays_once(self) -> None:
        """§8.8: every job idempotent. The second call finds a settled row."""
        a_captured_payment(trip_id=17)
        handlers.on_booking_cancelled(a_cancellation(trip_id=17, amount="55.00"))
        refund_id = int(Refund.objects.get().pk)

        assert services.settle_refund(refund_id) is True
        assert services.settle_refund(refund_id) is False

        assert Refund.objects.filter(status=RefundStatus.SETTLED).count() == 1

    def test_the_queue_is_oldest_first(self) -> None:
        """A tourist who has waited longest is paid next; any other order
        starves somebody quietly."""
        payment = a_captured_payment(trip_id=18)
        for amount in ("10.00", "20.00"):
            services.request_refund(
                payment_public_id=payment.public_id,
                amount=Decimal(amount),
                reason_code="GOODWILL",
                reason="Queued",
                requested_by_user_id=1,
            )

        queued = services.refunds_awaiting_settlement()

        amounts = [Refund.objects.get(pk=pk).amount for pk in queued]
        assert amounts == [Decimal("10.00"), Decimal("20.00")]

    def test_the_task_reports_what_it_did(self) -> None:
        a_captured_payment(trip_id=19)
        handlers.on_booking_cancelled(a_cancellation(trip_id=19, amount="55.00"))

        assert tasks.settle_refunds() == {"settled": 1, "failed": 0}


class TestTheBookingReachesRefunded:
    def test_a_settled_refund_moves_the_booking_to_refunded(self) -> None:
        """§20.2's last edge, which nothing could reach before this phase."""
        built = scenario.build()
        quote = booking_services.quote_trip(built.trip_public_id, tourist_id=built.tourist_id)
        booking_services.create_basket(
            built.trip_public_id, tourist_id=built.tourist_id, quote_token=quote.quote_token
        )
        booking_services.confirm_trip(built.trip_id, payment_captured=True)
        a_captured_payment(trip_id=built.trip_id, amount="500.00")
        booking = django_apps.get_model("booking", "Booking").objects.get()
        booking_services.cancel_booking(
            booking.public_id,
            actor=booking_services.Actor.TOURIST,
            actor_user_id=None,
        )

        handlers.on_booking_cancelled(
            a_cancellation(
                trip_id=built.trip_id,
                amount="55.00",
                booking_public_id=str(booking.public_id),
            )
        )
        services.settle_refund(int(Refund.objects.get().pk))

        booking.refresh_from_db()
        assert booking.status == "REFUNDED"


class TestBr047Approval:
    def test_a_large_discretionary_refund_needs_a_finance_officer(self) -> None:
        """BR-047, and the limit is a `system_setting` row rather than a
        constant (hard rule 5): `refund.auto_approve_limit`, 200 by default."""
        payment = a_captured_payment(trip_id=20, amount="500.00")

        with pytest.raises(services.RefundApprovalRequiredError):
            services.request_refund(
                payment_public_id=payment.public_id,
                amount=Decimal("300.00"),
                reason_code="GOODWILL",
                reason="A gesture",
                requested_by_user_id=1,
            )

    def test_the_same_refund_is_allowed_once_somebody_approved_it(self) -> None:
        payment = a_captured_payment(trip_id=21, amount="500.00")

        refund = services.request_refund(
            payment_public_id=payment.public_id,
            amount=Decimal("300.00"),
            reason_code="GOODWILL",
            reason="A gesture",
            requested_by_user_id=1,
            approved_by_user_id=2,
        )

        assert refund.status == RefundStatus.REQUESTED
        assert Refund.objects.get().approved_by_user_id == 2

    def test_a_small_one_needs_nobody(self) -> None:
        """§21.6: refunds arising from a cancellation policy are issued without
        human approval, and a small goodwill refund is not worth a queue."""
        payment = a_captured_payment(trip_id=22, amount="500.00")

        refund = services.request_refund(
            payment_public_id=payment.public_id,
            amount=Decimal("20.00"),
            reason_code="GOODWILL",
            reason="A gesture",
            requested_by_user_id=1,
        )

        assert refund.status == RefundStatus.REQUESTED

    def test_a_payment_nobody_captured_has_nothing_to_refund(self) -> None:
        payment = Payment.objects.create(
            trip_id=23,
            tourist_id=1,
            method=PaymentMethod.CARD,
            presentment_currency="USD",
            presentment_amount=Decimal("100.00"),
            settlement_currency="USD",
            psp_name="stripe",
            idempotency_key=uuid.uuid4().hex,
            status=PaymentStatus.PENDING,
        )

        with pytest.raises(Exception, match="nothing to refund"):
            services.request_refund(
                payment_public_id=payment.public_id,
                amount=Decimal("10.00"),
                reason_code="GOODWILL",
                reason="A gesture",
                requested_by_user_id=1,
            )
