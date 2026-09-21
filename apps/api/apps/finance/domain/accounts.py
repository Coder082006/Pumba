"""§22.3's entry types, and which account each one moves — ADR 0028.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

**The table below is §22.3's, transcribed as data.** Thirteen entry types, each
with the account it posts to and the direction it posts in, so a reviewer can
check it against the SRS line by line rather than reading it out of thirteen
`if` branches scattered through a service.

**Why the account is stored and not inferred.** §7.5.14 gives `ledger_entry` no
account column, which would make this mapping the only place the chart of
accounts exists — a dictionary in a Python file, unqueryable, and impossible to
report from. ADR 0028 adds the column; this module is what fills it, and the
single writer (`services.post_journal`) is what keeps the two in step.

**What "balanced" means here, and what it does not.** §22.3 calls the ledger
double-entry, and its own invariant is not the classical one: "for every
booking and currency, Σ CREDIT - Σ DEBIT equals the **expected residual**".
Not zero. The table above records one-sided effects on accounts — money
arriving in clearing, an obligation accruing, revenue earned — so a journal is
a *complete set of the effects one event has*, not a pair that cancels.

The invariant that is actually checkable, and the one BR-064 is about, is
therefore `allocated <= received` per booking: the platform can never owe out
more than it took in. `residual` computes the difference, and a negative one is
the error a nightly job exists to find.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

__all__ = [
    "Account",
    "Direction",
    "EntryType",
    "ENTRY_ACCOUNTS",
    "Leg",
    "LedgerRuleError",
    "RECEIPTS",
    "ALLOCATIONS",
    "account_for",
    "direction_for",
    "net_by_account",
    "residual",
    "signed",
]


class Account(StrEnum):
    """§22.3's five accounts.

    `PLATFORM_CLEARING` is where a tourist's money sits between capture and the
    provider earning it (§22.4): the platform holds it, and owes it to somebody
    it has not yet decided is owed.
    """

    PLATFORM_CLEARING = "PLATFORM_CLEARING"
    PROVIDER_PAYABLE = "PROVIDER_PAYABLE"
    PLATFORM_REVENUE = "PLATFORM_REVENUE"
    PLATFORM_EXPENSE = "PLATFORM_EXPENSE"
    PLATFORM_FX = "PLATFORM_FX"


class Direction(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class EntryType(StrEnum):
    """§22.3's thirteen.

    `CHARGEBACK` has no producer: ADR 0027 leaves DISPUTED and CHARGEBACK_LOST
    unreachable until the provider agreement of Appendix D-5 says who bears the
    loss. Declared rather than omitted, so the gap is visible in the table it
    belongs to.
    """

    CUSTOMER_PAYMENT = "CUSTOMER_PAYMENT"
    PROVIDER_ACCRUAL = "PROVIDER_ACCRUAL"
    COMMISSION_REVENUE = "COMMISSION_REVENUE"
    SERVICE_FEE_REVENUE = "SERVICE_FEE_REVENUE"
    PSP_FEE = "PSP_FEE"
    REFUND_TO_CUSTOMER = "REFUND_TO_CUSTOMER"
    PROVIDER_ACCRUAL_REVERSAL = "PROVIDER_ACCRUAL_REVERSAL"
    COMMISSION_REVERSAL = "COMMISSION_REVERSAL"
    CANCELLATION_FEE_ACCRUAL = "CANCELLATION_FEE_ACCRUAL"
    PAYOUT_SETTLEMENT = "PAYOUT_SETTLEMENT"
    FX_DIFFERENCE = "FX_DIFFERENCE"
    GOODWILL_CREDIT = "GOODWILL_CREDIT"
    CHARGEBACK = "CHARGEBACK"


#: §22.3's table: entry type -> (direction, account).
#:
#: `FX_DIFFERENCE` is the one §22.3 marks "Either", because a reconciliation
#: variance can go both ways. It is recorded here with its account and the
#: direction its caller must state — which is why `direction_for` refuses it.
ENTRY_ACCOUNTS: dict[EntryType, tuple[Direction | None, Account]] = {
    EntryType.CUSTOMER_PAYMENT: (Direction.CREDIT, Account.PLATFORM_CLEARING),
    EntryType.PROVIDER_ACCRUAL: (Direction.CREDIT, Account.PROVIDER_PAYABLE),
    EntryType.COMMISSION_REVENUE: (Direction.CREDIT, Account.PLATFORM_REVENUE),
    EntryType.SERVICE_FEE_REVENUE: (Direction.CREDIT, Account.PLATFORM_REVENUE),
    EntryType.PSP_FEE: (Direction.DEBIT, Account.PLATFORM_EXPENSE),
    EntryType.REFUND_TO_CUSTOMER: (Direction.DEBIT, Account.PLATFORM_CLEARING),
    EntryType.PROVIDER_ACCRUAL_REVERSAL: (Direction.DEBIT, Account.PROVIDER_PAYABLE),
    EntryType.COMMISSION_REVERSAL: (Direction.DEBIT, Account.PLATFORM_REVENUE),
    EntryType.CANCELLATION_FEE_ACCRUAL: (Direction.CREDIT, Account.PROVIDER_PAYABLE),
    EntryType.PAYOUT_SETTLEMENT: (Direction.DEBIT, Account.PROVIDER_PAYABLE),
    EntryType.FX_DIFFERENCE: (None, Account.PLATFORM_FX),
    EntryType.GOODWILL_CREDIT: (Direction.DEBIT, Account.PLATFORM_EXPENSE),
    EntryType.CHARGEBACK: (Direction.DEBIT, Account.PLATFORM_CLEARING),
}


class LedgerRuleError(ValueError):
    """An entry that would make the ledger say something untrue.

    Raised before anything is written, because a ledger corrected afterwards is
    a ledger nobody can rely on: §7.5.14 forbids the UPDATE that would fix it,
    and BR-077 makes every correction a new entry that is itself part of the
    record.
    """


@dataclass(frozen=True, slots=True, kw_only=True)
class Leg:
    """One line of a journal, before it is a row.

    `amount` is always positive — §7.5.14 says so — and the direction carries
    the sign. A negative amount would make the same fact expressible two ways,
    and a sum that depended on which way somebody chose.
    """

    entry_type: EntryType
    amount: Decimal
    currency: str
    direction: Direction | None = None
    booking_id: int | None = None
    payment_id: int | None = None
    provider_id: int | None = None
    payout_id: int | None = None
    reversal_of_id: int | None = None
    memo: str = ""

    def __post_init__(self) -> None:
        if self.amount <= Decimal("0"):
            raise LedgerRuleError(
                f"{self.entry_type} for {self.amount}: a ledger entry moves a positive amount "
                "and carries its sign in the direction (§7.5.14)"
            )


def account_for(entry_type: EntryType) -> Account:
    return ENTRY_ACCOUNTS[entry_type][1]


def direction_for(entry_type: EntryType, given: Direction | None = None) -> Direction:
    """The direction §22.3 fixes, or the one the caller states for FX.

    An entry type with a fixed direction ignores what it is told, because the
    direction is a property of the event and not of the caller's intent: a
    commission is revenue earned whoever posts it.
    """
    fixed, _ = ENTRY_ACCOUNTS[entry_type]
    if fixed is not None:
        return fixed
    if given is None:
        raise LedgerRuleError(
            f"{entry_type} may go either way (§22.3), so its direction must be stated"
        )
    return given


def signed(leg: Leg) -> Decimal:
    """The leg's contribution to a balance: credits add, debits subtract."""
    return (
        leg.amount
        if direction_for(leg.entry_type, leg.direction) is Direction.CREDIT
        else -leg.amount
    )


#: Entry types that bring money in, and those that give it out or promise it
#: to somebody. BR-064's invariant is that the second never exceeds the first.
RECEIPTS = (EntryType.CUSTOMER_PAYMENT,)

ALLOCATIONS = (
    EntryType.PROVIDER_ACCRUAL,
    EntryType.COMMISSION_REVENUE,
    EntryType.SERVICE_FEE_REVENUE,
    EntryType.CANCELLATION_FEE_ACCRUAL,
    EntryType.REFUND_TO_CUSTOMER,
    EntryType.GOODWILL_CREDIT,
    EntryType.CHARGEBACK,
)

#: Reversals, which take an allocation back. Held separately rather than
#: signed into the list above, because "this was undone" and "this never
#: happened" are different statements and the ledger records the first.
REVERSALS = (
    EntryType.PROVIDER_ACCRUAL_REVERSAL,
    EntryType.COMMISSION_REVERSAL,
)


def residual(entries: Sequence[tuple[EntryType, Decimal]]) -> Decimal:
    """What a booking's money has not yet been assigned to — BR-064.

    Received, less everything allocated out of it, plus anything since
    reversed. Zero once a completed booking's money has all been accounted
    for; positive while the platform still holds it in clearing; **negative
    never** — that would be the platform owing out more than it took in, which
    is the state the nightly job exists to find.
    """
    total = Decimal("0")
    for entry_type, amount in entries:
        if entry_type in RECEIPTS:
            total += amount
        elif entry_type in ALLOCATIONS:
            total -= amount
        elif entry_type in REVERSALS:
            total += amount
    return total


def net_by_account(legs: Sequence[Leg]) -> dict[tuple[Account, str], Decimal]:
    """What a journal does to each account, per currency.

    The shape a balance update and a finance report both want, computed once
    here rather than twice in two services that could disagree.
    """
    totals: dict[tuple[Account, str], Decimal] = {}
    for leg in legs:
        key = (account_for(leg.entry_type), leg.currency)
        totals[key] = totals.get(key, Decimal("0")) + signed(leg)
    return totals
