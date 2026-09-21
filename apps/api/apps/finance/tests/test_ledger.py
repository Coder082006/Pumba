"""The ledger, and the rules that make it worth having — §22.3, BR-064, BR-077.

Three things are asserted here, and they are the three that would cost real
money if they were wrong: an entry cannot be edited, a booking cannot have more
allocated out of it than came in, and a provider's balance says what the ledger
says.

The arithmetic uses §22.1's worked example — a 110.00 activity at 15% — because
a test whose figures come from the specification is a test somebody can check
against the specification.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from django.db.utils import ProgrammingError
from django.utils import timezone

from apps.finance import services
from apps.finance.domain.accounts import Account, EntryType, LedgerRuleError, Leg
from apps.finance.models import LedgerEntry, ProviderBalance

pytestmark = pytest.mark.django_db

#: §22.1's worked example. The tourist pays the component's price *and* the
#: platform's service fee (§18.3), so 115.50 leaves their card for a 110.00
#: dive — and only the 110.00 is ever split with the operator.
GROSS = Decimal("110.00")
FEE = Decimal("5.50")
PAID = GROSS + FEE
COMMISSION = Decimal("16.50")
NET = Decimal("93.50")


def capture(booking_id: int = 1, *, fee: Decimal = FEE) -> list[LedgerEntry]:
    return services.accrue_capture(
        payment_id=1,
        booking_id=booking_id,
        gross=GROSS + fee,
        service_fee=fee,
        currency="USD",
    )


def complete(booking_id: int = 1, provider_id: int = 7, **overrides: Decimal) -> list[LedgerEntry]:
    figures = {"gross": GROSS, "commission": COMMISSION, "net": NET}
    figures.update(overrides)
    return services.accrue_completion(
        booking_id=booking_id,
        provider_id=provider_id,
        currency="USD",
        **figures,  # type: ignore[arg-type]
    )


class TestPostingAJournal:
    def test_every_leg_is_stamped_with_its_account(self) -> None:
        """ADR 0028: the account is stored, not inferred, so a finance report
        is a query rather than a reconstruction."""
        capture()

        entries = {row.entry_type: row for row in LedgerEntry.objects.all()}
        assert entries["CUSTOMER_PAYMENT"].account == Account.PLATFORM_CLEARING
        assert entries["SERVICE_FEE_REVENUE"].account == Account.PLATFORM_REVENUE

    def test_the_legs_of_one_event_share_a_journal(self) -> None:
        capture()

        journals = {row.journal_id for row in LedgerEntry.objects.all()}
        assert len(journals) == 1

    def test_two_events_do_not(self) -> None:
        capture()
        complete()

        assert len({row.journal_id for row in LedgerEntry.objects.all()}) == 2

    def test_a_journal_spanning_two_currencies_is_refused(self) -> None:
        """§7.2 pairs every amount with its currency precisely so that two
        figures in different ones are never added together."""
        with pytest.raises(LedgerRuleError, match="one currency"):
            services.post_journal(
                [
                    Leg(entry_type=EntryType.CUSTOMER_PAYMENT, amount=GROSS, currency="USD"),
                    Leg(entry_type=EntryType.PSP_FEE, amount=Decimal("1.00"), currency="TZS"),
                ]
            )

    def test_an_entry_for_nothing_is_refused(self) -> None:
        with pytest.raises(LedgerRuleError, match="positive"):
            Leg(entry_type=EntryType.CUSTOMER_PAYMENT, amount=Decimal("0"), currency="USD")

    def test_an_empty_journal_is_refused(self) -> None:
        with pytest.raises(LedgerRuleError, match="records nothing"):
            services.post_journal([])


class TestTheLedgerCannotBeEdited:
    def test_updating_an_entry_raises(self) -> None:
        """§7.5.14: UPDATE and DELETE are revoked. BR-077: a correction is a
        new entry, never a changed one — which is the difference between a
        ledger and a spreadsheet."""
        capture()
        row = LedgerEntry.objects.first()
        assert row is not None

        with pytest.raises(ProgrammingError):
            LedgerEntry.objects.filter(pk=row.pk).update(amount=Decimal("1.00"))

    def test_deleting_an_entry_raises(self) -> None:
        capture()
        row = LedgerEntry.objects.first()
        assert row is not None

        with pytest.raises(ProgrammingError):
            LedgerEntry.objects.filter(pk=row.pk).delete()


class TestAccrual:
    def test_completion_splits_the_gross_between_provider_and_platform(self) -> None:
        """§22.1's worked example: 110.00 at 15% is 93.50 to the operator and
        16.50 to Pumba."""
        complete()

        entries = {row.entry_type: row.amount for row in LedgerEntry.objects.all()}
        assert entries["PROVIDER_ACCRUAL"] == NET
        assert entries["COMMISSION_REVENUE"] == COMMISSION
        assert entries["PROVIDER_ACCRUAL"] + entries["COMMISSION_REVENUE"] == GROSS

    def test_figures_that_do_not_add_up_are_refused(self) -> None:
        """Two figures from different calculations would leave the ledger
        carrying the difference forever."""
        with pytest.raises(LedgerRuleError, match="is not gross"):
            complete(commission=Decimal("20.00"))

    def test_accruing_twice_credits_once(self) -> None:
        """§8.8: idempotent by `(booking_id, entry_type)` — and here that is a
        unique index, so a retried job cannot double what a provider is owed."""
        complete()
        second = complete()

        assert second == []
        assert LedgerEntry.objects.filter(entry_type="PROVIDER_ACCRUAL").count() == 1
        assert ProviderBalance.objects.get().pending_amount == NET

    def test_the_provider_is_owed_but_not_yet_payable(self) -> None:
        """§22.4: the money waits out the settlement hold first."""
        complete()

        balance = ProviderBalance.objects.get()
        assert balance.pending_amount == NET
        assert balance.available_amount == Decimal("0")

    def test_capture_alone_owes_nobody(self) -> None:
        """BR-071. Until the dive happens, the money is the platform's to hold
        and nobody's to be paid."""
        capture()

        assert not ProviderBalance.objects.exists()


class TestTheSettlementHold:
    def test_an_accrual_matures_once_the_hold_has_passed(self) -> None:
        complete()

        moved = services.release_matured_balances(now=timezone.now() + dt.timedelta(days=3))

        balance = ProviderBalance.objects.get()
        assert moved == 1
        assert balance.pending_amount == Decimal("0")
        assert balance.available_amount == NET

    def test_a_fresh_accrual_stays_pending(self) -> None:
        """Two days, by default. A dispute raised on the second day should find
        the money still here."""
        complete()

        assert services.release_matured_balances() == 0
        assert ProviderBalance.objects.get().available_amount == Decimal("0")


class TestRefunds:
    def test_a_refund_before_completion_touches_no_provider(self) -> None:
        """§22.6's first case: there is no accrual to reverse."""
        capture()

        services.reverse_for_refund(
            booking_id=1,
            provider_id=7,
            refund_amount=PAID,
            provider_compensation=Decimal("0"),
            currency="USD",
        )

        assert LedgerEntry.objects.filter(entry_type="PROVIDER_ACCRUAL_REVERSAL").count() == 0
        assert not ProviderBalance.objects.exists()

    def test_a_refund_after_completion_reverses_both_sides(self) -> None:
        """§22.6's second case: the accrual and the commission both go back."""
        capture()
        complete()

        services.reverse_for_refund(
            booking_id=1,
            provider_id=7,
            refund_amount=Decimal("55.00"),
            provider_compensation=Decimal("49.50"),
            currency="USD",
        )

        entries = {row.entry_type: row.amount for row in LedgerEntry.objects.all()}
        assert entries["PROVIDER_ACCRUAL_REVERSAL"] == NET
        assert entries["COMMISSION_REVERSAL"] == COMMISSION
        assert entries["CANCELLATION_FEE_ACCRUAL"] == Decimal("49.50")

    def test_the_reversal_names_the_entry_it_reverses(self) -> None:
        """BR-077. A correction that did not point at what it corrected would
        leave two figures and no way to tell which came first."""
        capture()
        complete()
        accrual = LedgerEntry.objects.get(entry_type="PROVIDER_ACCRUAL")

        services.reverse_for_refund(
            booking_id=1,
            provider_id=7,
            refund_amount=PAID,
            provider_compensation=Decimal("0"),
            currency="USD",
        )

        reversal = LedgerEntry.objects.get(entry_type="PROVIDER_ACCRUAL_REVERSAL")
        assert reversal.reversal_of_id == accrual.pk

    def test_the_balance_falls_by_what_was_taken_back(self) -> None:
        capture()
        complete()

        services.reverse_for_refund(
            booking_id=1,
            provider_id=7,
            refund_amount=Decimal("55.00"),
            provider_compensation=Decimal("49.50"),
            currency="USD",
        )

        # Owed 93.50, reversed 93.50, compensated 49.50.
        assert ProviderBalance.objects.get().pending_amount == Decimal("49.50")


class TestBr064:
    def test_a_healthy_ledger_reports_nothing(self) -> None:
        capture()
        complete()

        assert services.ledger_exceptions() == []

    def test_allocating_more_than_came_in_is_reported(self) -> None:
        """The one arithmetic error in this system that costs real money: the
        platform promising out more than it holds."""
        capture()
        complete()
        services.post_journal(
            [
                Leg(
                    entry_type=EntryType.GOODWILL_CREDIT,
                    amount=Decimal("500.00"),
                    currency="USD",
                    booking_id=1,
                    memo="An apology nobody could afford",
                )
            ]
        )

        problems = services.ledger_exceptions()

        assert [problem["kind"] for problem in problems] == ["OVER_ALLOCATED"]
        assert problems[0]["booking_id"] == 1

    def test_a_balance_that_drifts_from_the_ledger_is_reported(self) -> None:
        """BR-064's second half, verbatim: the balance rows must equal what the
        ledger says. The row is a cache, and this is what keeps it honest."""
        capture()
        complete()
        balance = ProviderBalance.objects.get()
        ProviderBalance.objects.filter(pk=balance.pk).update(pending_amount=Decimal("999.00"))

        problems = services.ledger_exceptions()

        assert [problem["kind"] for problem in problems] == ["BALANCE_DRIFT"]
