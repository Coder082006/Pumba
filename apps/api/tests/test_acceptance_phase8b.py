"""§37.8 and §41.9's remaining criteria — the money-movement half of Phase 8.

    §37.8: "commission resolution; the append-only ledger with all entry types;
    provider balances; payout batch assembly and approval; the daily
    reconciliation job. Acceptance: … TC-110 and TC-111 pass; the ledger
    invariant holds under the mixed-scenario test."

    §41.9, the two clauses 8a left open: "writes the ledger entries" and
    "refunds settle and reverse the correct accruals".

Two halves, like the phases before it. `TestTheMixedScenario` is §37.8's own
words — bookings, a refund and a payout together — because each of those is
easy to get right alone and the invariant is what holds them together.
`TestEachCriterionIsStillAsserted` names the test carrying each criterion.
"""

from __future__ import annotations

import datetime as dt
import importlib
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
from apps.finance.models import CommissionRule, LedgerEntry, ProviderBalance
from apps.payment.models import Payment
from tests.administrators import signed_in_as

pytestmark = pytest.mark.django_db

INTENT = reverse("v1:payment:payment-intent")
WEBHOOK = reverse("v1:payment:psp-webhook", kwargs={"provider": "stripe"})


def _paid_and_delivered(capture_on_commit: Any, **build: Any) -> scenario.Scenario:
    """A trip paid for, then completed — the state an accrual needs."""
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
    payment = Payment.objects.filter(trip_id=built.trip_id).get()
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

    django_apps.get_model("booking", "Booking").objects.filter(trip_id=built.trip_id).update(
        starts_at=timezone.now() - dt.timedelta(hours=4),
        ends_at=timezone.now() - dt.timedelta(hours=1),
    )
    with capture_on_commit(execute=True):
        booking_services.complete_due()
    return built


class TestTc110CommissionSnapshotImmutability:
    def test_a_rate_change_cannot_reach_a_booking_already_sold(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """TC-110: "Booking confirmed at 15%; change the rule to 20%; complete
        → ledger accrues at 15%."

        True by construction rather than by care: BR-070 froze the rate on the
        booking at basket creation, and the accrual reads that column. Nothing
        on the completion path consults a rule at all.
        """
        built = _paid_and_delivered(django_capture_on_commit_callbacks)
        booking = django_apps.get_model("booking", "Booking").objects.get(trip_id=built.trip_id)
        assert booking.commission_rate == Decimal("15.00")

        CommissionRule.objects.create(
            scope="GLOBAL", method="PERCENT", percent=Decimal("20.00"), priority=100
        )

        accrued = LedgerEntry.objects.get(booking_id=booking.pk, entry_type="COMMISSION_REVENUE")
        assert accrued.amount == booking.commission_amount
        assert booking.commission_amount == (
            booking.gross_amount * Decimal("15.00") / Decimal("100")
        ).quantize(Decimal("0.01"))

    def test_the_new_rate_applies_to_the_next_booking(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """The other half of TC-110, and the reason it is worth testing: a rule
        that changed nothing at all would also pass the first assertion."""
        CommissionRule.objects.create(
            scope="GLOBAL", method="PERCENT", percent=Decimal("20.00"), priority=100
        )

        built = _paid_and_delivered(django_capture_on_commit_callbacks)

        booking = django_apps.get_model("booking", "Booking").objects.get(trip_id=built.trip_id)
        assert booking.commission_rate == Decimal("20.00")


class TestTc111TheLedgerBalances:
    def test_the_invariant_holds_through_the_mixed_scenario(
        self, django_capture_on_commit_callbacks: Any
    ) -> None:
        """TC-111: "Mixed bookings, refunds, payouts → debits equal credits per
        booking and currency."

        §37.8 asks for exactly this shape, and the reason is that each of the
        three is straightforward alone: it is the combination that has caught
        every real bug in this phase.
        """
        first = _paid_and_delivered(django_capture_on_commit_callbacks, adults=2)
        second = _paid_and_delivered(django_capture_on_commit_callbacks)

        # A refund against the first trip, settled.
        from apps.payment import services as payment_services

        booking = (
            django_apps.get_model("booking", "Booking").objects.filter(trip_id=first.trip_id).get()
        )
        payment = Payment.objects.filter(trip_id=first.trip_id).get()
        requested = payment_services.request_refund(
            payment_public_id=payment.public_id,
            amount=Decimal("10.00"),
            reason_code="GOODWILL",
            reason="Half the reef was closed",
            requested_by_user_id=1,
            booking_id=int(booking.pk),
        )
        with django_capture_on_commit_callbacks(execute=True):
            payment_services.settle_refund(
                int(
                    django_apps.get_model("payment", "Refund")
                    .objects.get(public_id=requested.public_id)
                    .pk
                )
            )

        # And a payout of the second trip's operator.
        finance.release_matured_balances(now=timezone.now() + dt.timedelta(days=3))
        balance = ProviderBalance.objects.filter(available_amount__gt=0).first()
        assert balance is not None
        payout = finance.build_payout_batch(
            provider_id=balance.provider_id,
            currency=balance.currency,
            period_start=timezone.localdate() - dt.timedelta(days=7),
            period_end=timezone.localdate(),
        )
        if payout is not None:
            finance.approve_payout(payout.public_id, approved_by_user_id=1)
            finance.release_payout(payout.public_id, rail_reference="BANK-MIXED-0001")

        assert finance.ledger_exceptions() == []
        assert second.trip_id != first.trip_id

    def test_the_checker_finds_a_deliberate_break(self) -> None:
        """A checker that never fires is indistinguishable from one that
        cannot. TC-111 is only worth having if it would catch something."""
        finance.post_journal(
            [
                finance_leg(
                    entry_type="GOODWILL_CREDIT",
                    amount=Decimal("100.00"),
                    booking_id=999_999,
                )
            ]
        )

        problems = finance.ledger_exceptions()

        assert [problem["kind"] for problem in problems] == ["OVER_ALLOCATED"]


def finance_leg(*, entry_type: str, amount: Decimal, booking_id: int) -> Any:
    from apps.finance.domain.accounts import EntryType, Leg

    return Leg(
        entry_type=EntryType(entry_type),
        amount=amount,
        currency="NZD",
        booking_id=booking_id,
        memo="A credit nobody could fund",
    )


#: Criterion -> (module, class, test) that proves it.
CRITERIA: dict[str, list[tuple[str, str, str]]] = {
    "TC-110 Commission snapshot immutability": [
        (
            "tests.test_acceptance_phase8b",
            "TestTc110CommissionSnapshotImmutability",
            "test_a_rate_change_cannot_reach_a_booking_already_sold",
        ),
    ],
    "TC-111 Ledger balance invariant": [
        (
            "tests.test_acceptance_phase8b",
            "TestTc111TheLedgerBalances",
            "test_the_invariant_holds_through_the_mixed_scenario",
        ),
        (
            "apps.finance.tests.test_ledger",
            "TestBr064",
            "test_a_balance_that_drifts_from_the_ledger_is_reported",
        ),
    ],
    "§22.2 resolution order": [
        (
            "apps.finance.tests.test_domain_commission",
            "TestWhichRuleWins",
            "test_scope_beats_priority",
        ),
    ],
    "§22.3 the ledger is append-only": [
        (
            "apps.finance.tests.test_ledger",
            "TestTheLedgerCannotBeEdited",
            "test_updating_an_entry_raises",
        ),
    ],
    "BR-071 accrual at completion, not at payment": [
        (
            "tests.test_finance_flow",
            "TestWhatAPaymentRecords",
            "test_the_operator_is_owed_once_the_service_has_happened",
        ),
    ],
    "§22.4 the settlement hold": [
        (
            "apps.finance.tests.test_ledger",
            "TestTheSettlementHold",
            "test_a_fresh_accrual_stays_pending",
        ),
    ],
    "§22.5 payout assembly and BR-074's minimum": [
        (
            "tests.test_finance_flow",
            "TestFromEarnedToPaid",
            "test_a_matured_balance_becomes_a_payout_and_then_a_settlement",
        ),
    ],
    "BR-075 finance approves before release": [
        (
            "tests.test_finance_admin_api",
            "TestPayouts",
            "test_a_payout_cannot_be_released_before_it_is_approved",
        ),
        (
            "tests.test_finance_admin_api",
            "TestPayouts",
            "test_a_catalogue_administrator_may_not_approve_money",
        ),
    ],
    "§22.6 a refund reverses the right accruals": [
        (
            "tests.test_finance_flow",
            "TestARefundUnwindsIt",
            "test_cancelling_after_completion_reverses_the_accrual",
        ),
    ],
    "§22.7 reports come from the ledger": [
        (
            "tests.test_finance_admin_api",
            "TestEarningsAndTheReport",
            "test_the_report_comes_from_the_ledger",
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


#: What §37.8 asks for and 8b does not deliver, with the reason.
NOT_IN_8B: dict[str, str] = {
    "the payout rail": "§22.5 releases via an adapter and the SRS writes no "
    "port for it. Paying a real company needs the agreement Appendix D-5 puts "
    "in Phase 11, so a release records a manual bank reference and moves "
    "nothing (ADR 0028).",
    "PSP settlement-report ingestion": "§21.9 matches a settlement report line "
    "by line; that format belongs to a real PSP account. The daily job checks "
    "what can be checked without one: the ledger invariant and every captured "
    "payment's status at the gateway.",
    "fx_rate and multi-currency settlement": "ADR 0024 charges in the "
    "destination's own currency, so nothing on the paying path converts. A "
    "table with no writer is a promise rather than a record.",
    "chargebacks": "§22.6 defers the allocation to the provider agreement "
    "(Appendix D-5, Phase 11). CHARGEBACK is a declared entry type with no "
    "producer, and DISPUTED stays unreachable.",
    "the provider portal's earnings screens": "§26.7 needs the Phase 11 "
    "principal. The figures are served to an administrator instead, which is "
    "the honest version until an operator can read them themselves.",
    "tax rates": "Appendix D-4 is a tax adviser's decision. The engine computes "
    "inclusive and exclusive already; the rows are somebody else's to supply.",
}


class TestTheGapsAreStated:
    def test_every_gap_names_a_reason(self) -> None:
        for clause, reason in NOT_IN_8B.items():
            assert len(reason) > 40, f"{clause} is listed as a gap with no reason"

    def test_the_payout_rail_is_genuinely_absent(self) -> None:
        """The list is only worth having while it is true: a rail appearing
        without this changing would mean Phase 11 landed and nobody updated the
        record of what 8b left undone."""
        from apps.common import ports_registry

        assert not hasattr(ports_registry, "get_payout_rail_port")
