"""Money through the ledger, from a card to an operator's balance — §22.

Under `tests/` because it spans `identity`, `booking`, `payment` and `finance`,
and §6.4 lets none of their own suites import the others.

This is the test that proves the accounting is *connected*: `apps/finance`'s
own tests show the ledger keeps its rules, and this shows the rules are reached
by a real payment. The two failures it exists to catch are a handler nobody
registered and an event nobody publishes — both of which leave a system that
passes every unit test and records no money at all.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from django.apps import apps as django_apps
from django.urls import reverse
from django.utils import timezone

from apps.booking import services as booking_services
from apps.booking.tests import scenario
from apps.common.ports_registry import get_payment_port
from apps.finance import services as finance
from apps.finance.models import LedgerEntry, ProviderBalance
from apps.payment.models import Payment
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

INTENT = reverse("v1:payment:payment-intent")
WEBHOOK = reverse("v1:payment:psp-webhook", kwargs={"provider": "stripe"})


def _paid_trip(capture_on_commit: Any, **build: Any) -> tuple[Any, scenario.Scenario]:
    """A trip reserved, paid for and confirmed — the state 8a leaves behind."""
    client = signed_in_as()
    profile = django_apps.get_model("identity", "TouristProfile").objects.order_by("-id").first()
    assert profile is not None
    built = scenario.build(tourist_id=int(profile.id), **build)

    quote = booking_services.quote_trip(built.trip_public_id, tourist_id=int(profile.id))
    booking_services.create_basket(
        built.trip_public_id, tourist_id=int(profile.id), quote_token=quote.quote_token
    )
    client.post(
        INTENT,
        {"trip_id": str(built.trip_public_id)},
        format="json",
        HTTP_IDEMPOTENCY_KEY=uuid.uuid4().hex,
    )
    payment = Payment.objects.get()
    get_payment_port().capture(payment.psp_reference or "", idempotency_key=uuid.uuid4().hex)
    with capture_on_commit(execute=True):
        client.post(
            WEBHOOK,
            json.dumps(
                {
                    "event_id": f"evt_{uuid.uuid4().hex[:10]}",
                    "event_type": "payment_intent.succeeded",
                    "psp_reference": payment.psp_reference,
                    "status": "CAPTURED",
                }
            ),
            content_type="application/json",
            HTTP_X_FAKE_SIGNATURE="valid",
        )
    return client, built


def _finish(built: scenario.Scenario, capture_on_commit: Any) -> None:
    """Move the booking's times into the past and run the sweeper."""
    booking_model = django_apps.get_model("booking", "Booking")
    booking_model.objects.filter(trip_id=built.trip_id).update(
        starts_at=timezone.now() - dt.timedelta(hours=4),
        ends_at=timezone.now() - dt.timedelta(hours=1),
    )
    with capture_on_commit(execute=True):
        booking_services.complete_due()


class TestWhatAPaymentRecords:
    def test_capture_puts_the_money_in_clearing_and_earns_the_fee(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """§22.3, §22.4: the platform holds it; only the service fee is its own."""
        _paid_trip(django_capture_on_commit_callbacks)

        types = {row.entry_type for row in LedgerEntry.objects.all()}
        assert "CUSTOMER_PAYMENT" in types
        assert "PROVIDER_ACCRUAL" not in types, "BR-071: nothing is earned until it happens"
        assert not ProviderBalance.objects.exists()

    def test_the_operator_is_owed_once_the_service_has_happened(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """BR-071, and the whole point of 8b: completion is what earns money."""
        _, built = _paid_trip(django_capture_on_commit_callbacks)

        _finish(built, django_capture_on_commit_callbacks)

        balance = ProviderBalance.objects.get()
        booking = django_apps.get_model("booking", "Booking").objects.get()
        assert balance.provider_id == booking.provider_id
        assert balance.pending_amount == booking.net_amount
        assert balance.available_amount == Decimal("0")

    def test_the_split_is_the_one_frozen_on_the_booking(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """TC-110's shape: what the ledger accrues is what was agreed."""
        _, built = _paid_trip(django_capture_on_commit_callbacks)
        _finish(built, django_capture_on_commit_callbacks)

        booking = django_apps.get_model("booking", "Booking").objects.get()
        accrual = LedgerEntry.objects.get(entry_type="PROVIDER_ACCRUAL")
        commission = LedgerEntry.objects.get(entry_type="COMMISSION_REVENUE")
        assert accrual.amount == booking.net_amount
        assert commission.amount == booking.commission_amount
        assert accrual.amount + commission.amount == booking.gross_amount

    def test_the_ledger_balances_after_all_of_it(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """TC-111, on a real trip rather than a constructed one."""
        _, built = _paid_trip(django_capture_on_commit_callbacks)
        _finish(built, django_capture_on_commit_callbacks)

        assert finance.ledger_exceptions() == []


class TestFromEarnedToPaid:
    def test_a_matured_balance_becomes_a_payout_and_then_a_settlement(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """§22.4 and §22.5 end to end: held, released, batched, approved, paid."""
        _, built = _paid_trip(django_capture_on_commit_callbacks, adults=2)
        _finish(built, django_capture_on_commit_callbacks)
        balance = ProviderBalance.objects.get()

        finance.release_matured_balances(now=timezone.now() + dt.timedelta(days=3))
        balance.refresh_from_db()
        assert balance.available_amount > Decimal("0")

        payout = finance.build_payout_batch(
            provider_id=balance.provider_id,
            currency=balance.currency,
            period_start=timezone.localdate() - dt.timedelta(days=7),
            period_end=timezone.localdate(),
        )
        assert payout is not None
        assert payout.amount == balance.available_amount
        assert payout.items.count() == 1

        finance.approve_payout(payout.public_id, approved_by_user_id=1)
        finance.release_payout(payout.public_id, rail_reference="BANK-2027-0001")

        payout.refresh_from_db()
        balance.refresh_from_db()
        assert payout.status == "PAID"
        assert balance.available_amount == Decimal("0")
        assert LedgerEntry.objects.filter(entry_type="PAYOUT_SETTLEMENT").count() == 1
        assert finance.ledger_exceptions() == []

    def test_a_payout_cannot_be_released_before_finance_approves(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """BR-075, which is the only thing standing between a batch and money
        leaving the platform."""
        _, built = _paid_trip(django_capture_on_commit_callbacks, adults=2)
        _finish(built, django_capture_on_commit_callbacks)
        finance.release_matured_balances(now=timezone.now() + dt.timedelta(days=3))
        balance = ProviderBalance.objects.get()
        payout = finance.build_payout_batch(
            provider_id=balance.provider_id,
            currency=balance.currency,
            period_start=timezone.localdate() - dt.timedelta(days=7),
            period_end=timezone.localdate(),
        )
        assert payout is not None

        with pytest.raises(finance.PayoutNotReleasableError):
            finance.release_payout(payout.public_id, rail_reference="BANK-2027-0002")


class TestARefundUnwindsIt:
    def test_cancelling_after_completion_reverses_the_accrual(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """§22.6's second case, through the real cancellation and settlement."""
        client, built = _paid_trip(django_capture_on_commit_callbacks)
        _finish(built, django_capture_on_commit_callbacks)
        booking = django_apps.get_model("booking", "Booking").objects.get()

        # A completed booking is past cancelling by a tourist (BR-042), so the
        # refund here is the administrative one §27.9 gives support.
        from apps.payment import services as payment_services

        payment = Payment.objects.get()
        refund = payment_services.request_refund(
            payment_public_id=payment.public_id,
            amount=Decimal("10.00"),
            reason_code="GOODWILL",
            reason="Rain stopped play",
            requested_by_user_id=1,
            booking_id=int(booking.pk),
        )
        with django_capture_on_commit_callbacks(execute=True):
            payment_services.settle_refund(
                int(
                    django_apps.get_model("payment", "Refund")
                    .objects.get(public_id=refund.public_id)
                    .pk
                )
            )

        types = {row.entry_type for row in LedgerEntry.objects.all()}
        assert "REFUND_TO_CUSTOMER" in types
        assert "PROVIDER_ACCRUAL_REVERSAL" in types
        assert "COMMISSION_REVERSAL" in types
        assert finance.ledger_exceptions() == []
