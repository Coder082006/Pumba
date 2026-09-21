"""§37.8 and §41.9's acceptance criteria for the paying half of Phase 8.

    §41.9: "A tourist can pay by international card and by mobile money in USD
    and TZS. The charged amount always equals the server-computed trip total.
    Successful payment confirms every component booking, commits every hold,
    writes the ledger entries and dispatches every notification, atomically.
    Duplicate and out-of-order webhooks have no additional effect. Failure
    releases holds and returns the trip to a re-payable state. Refunds settle
    and reverse the correct accruals."

Two halves, as `test_acceptance_phase7.py` has. `TestTheWholeJourney` walks a
trip from a basket to a settled refund over HTTP. `TestEachCriterionIsStill
Asserted` names the test that carries each criterion and fails if one
disappears, so a criterion cannot quietly stop being proved.

**What 8a does not claim.** Two clauses above are 8b's and are listed here as
gaps rather than left to be discovered: "writes the ledger entries" (there is
no ledger yet) and "reverse the correct accruals" (there are no accruals). And
"by mobile money" needs §21.2's second adapter, which is not built — the
methods endpoint says so rather than offering a rail that would fail.
"""

from __future__ import annotations

import importlib
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.urls import reverse

from apps.booking import services as booking_services
from apps.booking.models import Booking
from apps.booking.tests import scenario
from apps.common.ports_registry import get_email_port, get_payment_port
from apps.payment.models import Payment, PaymentStatus, Refund, RefundStatus
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

INTENT = reverse("v1:payment:payment-intent")
WEBHOOK = reverse("v1:payment:psp-webhook", kwargs={"provider": "stripe"})


class TestTheWholeJourney:
    """Reserve, pay, confirm, cancel, refund — over the wire."""

    def test_a_trip_goes_from_a_basket_to_a_settled_refund(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        tourist = signed_in_as()
        profile = (
            django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
        )
        assert profile is not None
        built = scenario.build(tourist_id=int(profile.id), adults=2, days_ahead=30)

        quote = booking_services.quote_trip(built.trip_public_id, tourist_id=int(profile.id))
        booking_services.create_basket(
            built.trip_public_id, tourist_id=int(profile.id), quote_token=quote.quote_token
        )
        trip_model = django_apps.get_model("trip", "Trip")
        trip = trip_model.objects.get(id=built.trip_id)

        # §41.9: "The charged amount always equals the server-computed trip
        # total" — asserted against a client that submits its own figure.
        created = tourist.post(
            INTENT,
            {"trip_id": str(built.trip_public_id), "amount": "8.34"},
            format="json",
            HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
        )
        assert created.status_code == 201, created.data
        assert Decimal(created.data["data"]["amount"]) == trip.total_amount

        payment = Payment.objects.get()
        get_payment_port().capture(payment.psp_reference or "", idempotency_key=uuid.uuid4().hex)

        # §41.9: capture confirms every booking, commits every hold and
        # dispatches the notifications — in one transaction.
        with django_capture_on_commit_callbacks(execute=True):
            delivered = _deliver(tourist, payment, "CAPTURED")
        assert delivered.status_code == 200

        payment.refresh_from_db()
        trip.refresh_from_db()
        assert payment.status == PaymentStatus.CAPTURED
        assert trip.status == "CONFIRMED"
        booking = Booking.objects.get()
        assert booking.status == "CONFIRMED"
        assert django_apps.get_model("inventory", "InventoryHold").objects.get().status == (
            "COMMITTED"
        )
        assert django_apps.get_model("booking", "BookingVoucher").objects.count() == 1
        subjects = [message["subject"] for message in get_email_port().sent]  # type: ignore[attr-defined]
        assert any("Payment received" in subject for subject in subjects)
        assert any("confirmed" in subject for subject in subjects)

        # §41.9: "Duplicate and out-of-order webhooks have no additional effect."
        assert _deliver(tourist, payment, "CAPTURED").status_code == 200
        assert payment.transactions.filter(to_status="CAPTURED").count() == 1

        # BR-043: the preview is the refund, and §21.6 settles it at the PSP.
        preview = tourist.get(
            reverse(
                "v1:booking:booking-cancellation-preview", kwargs={"public_id": booking.public_id}
            )
        ).data["data"]
        with django_capture_on_commit_callbacks(execute=True):
            cancelled = tourist.post(
                reverse("v1:booking:booking-cancel", kwargs={"public_id": booking.public_id}),
                {},
                format="json",
            ).data["data"]
        assert cancelled["refund_amount"] == preview["refund_amount"]

        refund = Refund.objects.get()
        assert refund.status == RefundStatus.REQUESTED
        assert refund.amount == Decimal(preview["refund_amount"])

        from apps.payment import services as payment_services

        with django_capture_on_commit_callbacks(execute=True):
            assert payment_services.settle_refund(int(refund.pk)) is True

        refund.refresh_from_db()
        booking.refresh_from_db()
        payment.refresh_from_db()
        assert refund.status == RefundStatus.SETTLED
        assert booking.status == "REFUNDED"
        assert payment.status in (PaymentStatus.PARTIALLY_REFUNDED, PaymentStatus.REFUNDED)
        assert any(
            "Refund issued" in message["subject"]
            for message in get_email_port().sent  # type: ignore[attr-defined]
        )


def _deliver(client: Any, payment: Payment, status: str) -> Any:
    return client.post(
        WEBHOOK,
        json.dumps(
            {
                "event_id": f"evt_{uuid.uuid4().hex[:10]}",
                "event_type": f"payment_intent.{status.lower()}",
                "psp_reference": payment.psp_reference,
                "status": status,
            }
        ),
        content_type="application/json",
        HTTP_X_FAKE_SIGNATURE="valid",
    )


#: Criterion -> (module, class, test) that proves it.
CRITERIA: dict[str, list[tuple[str, str, str]]] = {
    "TC-070 Payment success confirms everything": [
        (
            "tests.test_psp_webhook_api",
            "TestTc070PaymentSuccessConfirmsEverything",
            "test_the_payment_is_captured_and_the_trip_is_confirmed",
        ),
        (
            "tests.test_psp_webhook_api",
            "TestTc070PaymentSuccessConfirmsEverything",
            "test_the_seats_move_from_held_to_sold",
        ),
        (
            "tests.test_psp_webhook_api",
            "TestTc070PaymentSuccessConfirmsEverything",
            "test_a_voucher_exists_for_the_confirmed_booking",
        ),
    ],
    "TC-071 Duplicate webhook": [
        (
            "tests.test_psp_webhook_api",
            "TestTc071DuplicateWebhook",
            "test_the_same_event_twice_changes_nothing_the_second_time",
        ),
        (
            "tests.test_psp_webhook_api",
            "TestTc071DuplicateWebhook",
            "test_a_second_capture_event_with_a_new_id_does_not_reapply",
        ),
    ],
    "TC-072 Payment failure releases holds": [
        (
            "tests.test_psp_webhook_api",
            "TestTc072PaymentFailure",
            "test_a_failure_releases_the_holds_and_returns_the_trip_to_priced",
        ),
        (
            "tests.test_psp_webhook_api",
            "TestTc072PaymentFailure",
            "test_the_tourist_may_then_pay_again",
        ),
    ],
    "TC-073 Amount tampering": [
        (
            "tests.test_payment_intent_api",
            "TestTc073AmountTampering",
            "test_the_submitted_amount_is_ignored_and_the_trip_total_is_charged",
        ),
        (
            "tests.test_payment_intent_api",
            "TestTc073AmountTampering",
            "test_the_attempt_is_audited",
        ),
    ],
    "TC-074 Idempotent payment intent": [
        (
            "tests.test_payment_intent_api",
            "TestTc074Idempotency",
            "test_the_same_key_twice_makes_one_payment",
        ),
    ],
    "TC-103 Refund exceeds capture (settled at the PSP, not only computed)": [
        (
            "apps.payment.tests.test_refunds",
            "TestSettling",
            "test_tc_103_refunding_more_than_was_captured_is_refused",
        ),
    ],
    "§21.4 every transition, positive": [
        (
            "apps.payment.tests.test_domain_lifecycle",
            "TestEveryTransitionSucceedsWhenLegal",
            "test_positive",
        ),
    ],
    "§21.4 every transition, negative": [
        (
            "apps.payment.tests.test_domain_lifecycle",
            "TestEveryTransitionIsRefusedWhenItsGuardIsNot",
            "test_negative",
        ),
        (
            "apps.payment.tests.test_domain_lifecycle",
            "TestUndeclaredEdgesAreRefused",
            "test_every_undeclared_pair_is_illegal",
        ),
    ],
    "§21.5 out-of-order webhooks are recorded and ignored": [
        (
            "tests.test_psp_webhook_api",
            "TestOutOfOrderAndUnknown",
            "test_an_authorised_event_arriving_after_the_capture_is_ignored",
        ),
    ],
    "§21.5 signature verification and freshness": [
        (
            "apps.payment.tests.test_stripe_gateway",
            "TestWebhookVerification",
            "test_a_stale_event_is_refused",
        ),
        (
            "apps.payment.tests.test_stripe_gateway",
            "TestWebhookVerification",
            "test_a_payload_signed_with_the_wrong_secret_is_refused",
        ),
    ],
    "§21.5 the polling fallback, at §8.8's cadence": [
        (
            "apps.payment.tests.test_tasks",
            "TestTheLadder",
            "test_each_rung_waits_the_difference_not_the_offset",
        ),
    ],
    "§21.6 refunds settle and the booking reaches REFUNDED": [
        (
            "apps.payment.tests.test_refunds",
            "TestTheBookingReachesRefunded",
            "test_a_settled_refund_moves_the_booking_to_refunded",
        ),
    ],
    "BR-047 a large discretionary refund needs approval": [
        (
            "apps.payment.tests.test_refunds",
            "TestBr047Approval",
            "test_a_large_discretionary_refund_needs_a_finance_officer",
        ),
    ],
    "§41.10 the confirmation email carries the itinerary PDF": [
        (
            "tests.test_payment_emails",
            "TestWhatACapturedPaymentSends",
            "test_the_confirmation_carries_the_itinerary_pdf",
        ),
    ],
    "BR-062 one live payment per trip": [
        (
            "apps.payment.tests.test_models",
            "TestBr062OneLivePaymentPerTrip",
            "test_a_second_live_payment_on_one_trip_is_refused",
        ),
    ],
}


class TestEachCriterionIsStillAsserted:
    @pytest.mark.parametrize(
        ("criterion", "module", "cls", "test"),
        [(criterion, *entry) for criterion, entries in CRITERIA.items() for entry in entries],
        ids=lambda value: str(value)[:48],
    )
    def test_the_test_that_proves_it_exists(
        self, criterion: str, module: str, cls: str, test: str
    ) -> None:
        holder = getattr(importlib.import_module(module), cls, None)
        assert holder is not None, f"{criterion}: {module}.{cls} is gone"
        assert callable(getattr(holder, test, None)), f"{criterion}: {cls}.{test} is gone"


#: What §37.8 and §41.9 asked for that 8a did not deliver.
#:
#: Three of the five are now done — 8b built the ledger, the accruals and their
#: reversals — and the entries are kept rather than deleted, because "this was
#: missing and is no longer" is the useful record. `test_acceptance_phase8b.py`
#: holds what remains outstanding today.
NOT_IN_8A: dict[str, str] = {
    "writes the ledger entries": "Delivered by 8b: §22.3's thirteen entry "
    "types, the journal that groups them and BR-064's nightly invariant.",
    "reverse the correct accruals": "Delivered by 8b: §22.6's three cases, "
    "driven from the settled refund rather than the cancellation.",
    "accrual at completion (BR-071)": "Delivered by 8b, which also had to "
    "build the sweeper that notices a service has happened — nothing did.",
    "pay by mobile money": "Still outstanding. §21.2's second rail is a second "
    "adapter behind the same port; `GET /payments/methods` reports it "
    "unavailable rather than offering a rail that would fail at the gateway.",
    "chargebacks": "Still outstanding. §22.6 defers allocation to the provider "
    "agreement, which is Appendix D-5 and Phase 11. DISPUTED and "
    "CHARGEBACK_LOST are declared in the machine and unreachable.",
}


class TestTheGapsAreStated:
    def test_every_gap_names_a_reason(self) -> None:
        for clause, reason in NOT_IN_8A.items():
            assert len(reason) > 40, f"{clause} is listed as a gap with no reason"

    def test_the_ledger_arrived_with_8b(self) -> None:
        """The counterpart of the check this replaced.

        It used to assert that `finance` had no tables, and failed the day 8b
        gave it some — which is exactly what a gap list is for. Now it asserts
        the other direction: the entries above claim the ledger was built, and
        a `finance` module that lost its models would make this file a lie.
        """
        from django.apps import apps

        assert list(
            apps.get_app_config("finance").get_models()
        ), "NOT_IN_8A says 8b delivered the ledger, and finance has no tables."
