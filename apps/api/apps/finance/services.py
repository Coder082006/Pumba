"""Application layer (SRS §8.2 layer 2).

    Public interface: accrue(), settle(), build_payout_batch()

The only door into this module. §6.5 rule 1: other modules call these and
nothing else.

**One function writes to the ledger.** `post_journal` assigns the journal id
and stamps each leg's account from §22.3's table. Nothing else inserts a row,
and nothing at all changes one (§7.5.14, BR-077).

**What a journal is.** §22.3's table records one-sided effects on accounts, and
its own invariant is "Σ CREDIT - Σ DEBIT equals the **expected residual**" —
not zero. So a journal is the complete set of effects one event has, and the
rule that matters is the one BR-064 is really about: a booking can never have
more allocated out of it than was taken in.

**Nothing here decides a commission.** BR-070 freezes the rule and the rate on
the booking at confirmation, and Phase 7 already writes them; `accrue` reads
those columns. That is most of TC-110 by construction — a rule changed to 20%
cannot alter a booking confirmed at 15%, because the accrual never consults a
rule at all.

**Money owed is not money payable.** §22.4 holds an accrual in `pending` for
`settlement_hold_days` so a dispute can surface before the platform has paid it
away, and only then is it available to a batch.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.common.commission import CommissionFacts, Rate, default_rate
from apps.common.config import get_setting
from apps.common.errors import ConflictError, NotFoundError
from apps.finance.domain import commission as domain_commission
from apps.finance.domain.accounts import (
    Account,
    Direction,
    EntryType,
    LedgerRuleError,
    Leg,
    account_for,
    direction_for,
    residual,
)
from apps.finance.models import (
    CommissionRule,
    LedgerEntry,
    Payout,
    PayoutItem,
    PayoutStatus,
    ProviderBalance,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CapturedLine",
    "post_journal",
    "accrue_capture",
    "accrue_completion",
    "reverse_for_refund",
    "release_matured_balances",
    "build_payout_batch",
    "approve_payout",
    "release_payout",
    "provider_statement",
    "ledger_exceptions",
    "account_totals",
    "active_rules",
    "create_rule",
    "update_rule",
    "list_rules",
    "list_payouts",
    "resolve_commission",
    "monthly_volume",
    "PayoutNotReleasableError",
]

#: Entry types that move a provider's balance, and which way.
_BALANCE_EFFECT = {
    EntryType.PROVIDER_ACCRUAL: Decimal("1"),
    EntryType.CANCELLATION_FEE_ACCRUAL: Decimal("1"),
    EntryType.PROVIDER_ACCRUAL_REVERSAL: Decimal("-1"),
    EntryType.PAYOUT_SETTLEMENT: Decimal("-1"),
}


class PayoutNotReleasableError(ConflictError):
    """BR-075: approval comes before release, and only finance approves."""

    code = "PAYOUT_NOT_RELEASABLE"


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------


@transaction.atomic
def post_journal(legs: Sequence[Leg], *, occurred_at: datetime | None = None) -> list[LedgerEntry]:
    """Write one event's entries — §22.3, ADR 0028.

    Idempotent where it matters: `(booking_id, entry_type)` is unique in the
    database, so a retried job that accrues the same booking twice writes
    nothing the second time rather than doubling what a provider is owed. §8.8
    asks that of `accrue_commission`; here it is a constraint rather than a
    hope.

    Returns the rows written — empty when the journal had already been posted,
    which is how a caller tells a first run from a repeat without asking.
    """
    if not legs:
        raise LedgerRuleError("a journal with no entries records nothing")
    currencies = {leg.currency for leg in legs}
    if len(currencies) > 1:
        # §7.2 pairs every amount with its currency so two figures in different
        # ones are never added. A journal spanning two would be exactly that.
        raise LedgerRuleError(f"one journal, one currency: {sorted(currencies)}")

    occurred_at = occurred_at or timezone.now()
    journal = uuid.uuid4()
    rows = [
        LedgerEntry(
            account=account_for(leg.entry_type).value,
            journal_id=journal,
            entry_type=leg.entry_type.value,
            direction=direction_for(leg.entry_type, leg.direction).value,
            amount=leg.amount,
            currency=leg.currency,
            booking_id=leg.booking_id,
            payment_id=leg.payment_id,
            provider_id=leg.provider_id,
            payout_id=leg.payout_id,
            reversal_of_id=leg.reversal_of_id,
            memo=leg.memo[:200],
            occurred_at=occurred_at,
        )
        for leg in legs
    ]
    LedgerEntry.objects.bulk_create(rows, ignore_conflicts=True)
    # Re-read rather than trusting the objects back from `bulk_create`:
    # `ignore_conflicts` leaves their primary keys unset on PostgreSQL, so a
    # caller reading them could not tell a row that was written from one the
    # unique index quietly dropped — and the balance update below depends on
    # exactly that distinction.
    posted = list(LedgerEntry.objects.filter(journal_id=journal).order_by("id"))
    if posted:
        logger.info("journal_posted", extra={"journal": str(journal), "legs": len(posted)})
        _apply_to_balances(posted)
    return posted


def _apply_to_balances(rows: Sequence[LedgerEntry]) -> None:
    """Keep `provider_balance` in step with what was just posted.

    The balance is a cache of the ledger (ADR 0028) and BR-064 re-derives it
    nightly. Updating it here rather than in each caller is what stops the two
    drifting: every path that credits a provider goes through `post_journal`.

    An accrual lands in `pending`; §22.4's hold is what later moves it.
    """
    for row in rows:
        effect = _BALANCE_EFFECT.get(EntryType(row.entry_type))
        if effect is None or row.provider_id is None:
            continue
        balance, _ = ProviderBalance.objects.select_for_update().get_or_create(
            provider_id=row.provider_id, currency=row.currency
        )
        if EntryType(row.entry_type) is EntryType.PAYOUT_SETTLEMENT:
            # The batch already took this out of `available` when it claimed
            # the money (§22.5). Taking it again here would pay the provider
            # twice on paper and drive the balance negative.
            continue
        balance.pending_amount += effect * row.amount
        balance.save(update_fields=["pending_amount", "updated_at"])


# ---------------------------------------------------------------------------
# §22.3's events
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class CapturedLine:
    """One booking's share of a captured payment.

    **Per booking, not per payment.** A payment covers a whole trip, and
    BR-064 reconciles per booking — so a capture recorded against the trip
    alone would leave every component looking as though money had been
    allocated out of it that never came in.
    """

    booking_id: int
    #: What the tourist paid for this component: its price, the platform's fee
    #: share and its tax (§18.3, allocated across the basket).
    paid: Decimal
    service_fee: Decimal
    currency: str


def accrue_capture(
    *,
    payment_id: int,
    lines: Sequence[CapturedLine],
    occurred_at: datetime | None = None,
) -> list[LedgerEntry]:
    """§22.3: money captured lands in clearing; the service fee is earned now.

    The fee is the platform's own charge to the tourist (§18.3) and is revenue
    the moment it is taken — unlike commission, which is a share of a service
    nobody has delivered yet and which §22.4 refuses to recognise until they
    have.

    What is left in clearing per booking is its tax, which belongs to neither
    party and is nobody's revenue until §18.3's tax rules land (Appendix D-4).
    """
    legs: list[Leg] = []
    for line in lines:
        legs.append(
            Leg(
                entry_type=EntryType.CUSTOMER_PAYMENT,
                amount=line.paid,
                currency=line.currency,
                payment_id=payment_id,
                booking_id=line.booking_id,
                memo="Payment captured",
            )
        )
        if line.service_fee > Decimal("0"):
            legs.append(
                Leg(
                    entry_type=EntryType.SERVICE_FEE_REVENUE,
                    amount=line.service_fee,
                    currency=line.currency,
                    payment_id=payment_id,
                    booking_id=line.booking_id,
                    memo="Platform service fee",
                )
            )
    return post_journal(legs, occurred_at=occurred_at)


def accrue_completion(
    *,
    booking_id: int,
    provider_id: int,
    gross: Decimal,
    commission: Decimal,
    net: Decimal,
    currency: str,
    occurred_at: datetime | None = None,
) -> list[LedgerEntry]:
    """BR-071: the provider earns at completion, not at payment.

    "So that the Platform does not owe a provider for a service not yet
    delivered" (§22.4). Between capture and here the money sits in clearing;
    this is the moment part of it becomes somebody else's.

    The figures come from the booking's own snapshot (BR-070) and are never
    recomputed, which is TC-110. The identity between them is checked, though:
    a net and a commission that do not add up to the gross would be two figures
    from different calculations, and the ledger would carry the difference
    forever.
    """
    if net + commission != gross:
        raise LedgerRuleError(
            f"booking {booking_id}: net {net} plus commission {commission} is not gross {gross}"
        )

    return post_journal(
        [
            Leg(
                entry_type=EntryType.PROVIDER_ACCRUAL,
                amount=net,
                currency=currency,
                booking_id=booking_id,
                provider_id=provider_id,
                memo="Service delivered",
            ),
            Leg(
                entry_type=EntryType.COMMISSION_REVENUE,
                amount=commission,
                currency=currency,
                booking_id=booking_id,
                provider_id=provider_id,
                memo="Commission at the rate frozen on the booking",
            ),
        ],
        occurred_at=occurred_at,
    )


def reverse_for_refund(
    *,
    booking_id: int,
    provider_id: int,
    refund_amount: Decimal,
    provider_compensation: Decimal,
    currency: str,
    occurred_at: datetime | None = None,
) -> list[LedgerEntry]:
    """§22.6: what a refund does depends on whether the money was earned yet.

    - **Before completion** there is no accrual, so the only entry is the
      tourist's money leaving clearing.
    - **After it**, the accrual and the commission are reversed, and whatever
      the provider keeps under the booking's own policy (§20.9) is accrued back
      as a cancellation fee.
    - **After a payout** the reversal drives the balance negative, which §22.6
      recovers from the next batch — so nothing here forbids it.

    Which case applies is read from the ledger, not from a status: the ledger
    is the record of what actually happened.
    """
    accrued = _entry(booking_id, EntryType.PROVIDER_ACCRUAL)
    commission = _entry(booking_id, EntryType.COMMISSION_REVENUE)

    legs: list[Leg] = [
        Leg(
            entry_type=EntryType.REFUND_TO_CUSTOMER,
            amount=refund_amount,
            currency=currency,
            booking_id=booking_id,
            memo="Refund settled",
        )
    ]

    if accrued is not None and commission is not None:
        legs.extend(
            [
                Leg(
                    entry_type=EntryType.PROVIDER_ACCRUAL_REVERSAL,
                    amount=accrued.amount,
                    currency=accrued.currency,
                    booking_id=booking_id,
                    provider_id=provider_id,
                    reversal_of_id=int(accrued.pk),
                    memo="Cancelled after completion",
                ),
                Leg(
                    entry_type=EntryType.COMMISSION_REVERSAL,
                    amount=commission.amount,
                    currency=commission.currency,
                    booking_id=booking_id,
                    provider_id=provider_id,
                    reversal_of_id=int(commission.pk),
                    memo="Commission reversed with its accrual",
                ),
            ]
        )
        if provider_compensation > Decimal("0"):
            legs.append(
                Leg(
                    entry_type=EntryType.CANCELLATION_FEE_ACCRUAL,
                    amount=provider_compensation,
                    currency=currency,
                    booking_id=booking_id,
                    provider_id=provider_id,
                    memo="Provider compensation under the booking's policy",
                )
            )

    return post_journal(legs, occurred_at=occurred_at)


def _entry(booking_id: int, entry_type: EntryType) -> LedgerEntry | None:
    return LedgerEntry.objects.filter(booking_id=booking_id, entry_type=entry_type.value).first()


# ---------------------------------------------------------------------------
# §22.4 — the settlement hold
# ---------------------------------------------------------------------------


def release_matured_balances(*, now: datetime | None = None) -> int:
    """§22.4: pending becomes available once the dispute window has passed.

    `settlement_hold_days` after **completion**, not after payment — the clock
    starts when the service was delivered, because that is when a tourist could
    first have a complaint about it.

    Computed from the accruals rather than from a timer on the balance row: a
    balance is a cache of the ledger, and only the ledger knows when each
    accrual happened.
    """
    now = now or timezone.now()
    hold = timedelta(days=int(get_setting("settlement_hold_days")))
    matured_before = now - hold

    moved = 0
    for balance in ProviderBalance.objects.select_for_update().filter(pending_amount__gt=0):
        ready = (
            _provider_net(balance.provider_id, balance.currency, before=matured_before)
            - balance.available_amount
        )
        if ready <= Decimal("0"):
            continue
        amount = min(ready, balance.pending_amount)
        balance.pending_amount -= amount
        balance.available_amount += amount
        balance.save(update_fields=["pending_amount", "available_amount", "updated_at"])
        moved += 1

    if moved:
        logger.info("balances_matured", extra={"providers": moved})
    return moved


def _provider_net(provider_id: int, currency: str, *, before: datetime | None = None) -> Decimal:
    """What the ledger says a provider has been credited, net of reversals.

    Payouts are excluded deliberately: this answers "how much has this provider
    earned", and `release_matured_balances` compares it against what has
    already been made available.
    """
    rows = LedgerEntry.objects.filter(provider_id=provider_id, currency=currency).exclude(
        entry_type=EntryType.PAYOUT_SETTLEMENT.value
    )
    if before is not None:
        rows = rows.filter(occurred_at__lt=before)

    total = Decimal("0")
    for row in rows.only("entry_type", "amount"):
        total += _BALANCE_EFFECT.get(EntryType(row.entry_type), Decimal("0")) * row.amount
    return total


# ---------------------------------------------------------------------------
# §22.5 — payouts
# ---------------------------------------------------------------------------


@transaction.atomic
def build_payout_batch(
    *,
    provider_id: int,
    currency: str,
    period_start: date,
    period_end: date,
) -> Payout | None:
    """§22.5: everything available for one provider, in one currency.

    `None` when there is nothing to pay — including when the balance is under
    `payout.minimum`, which BR-074 rolls forward rather than paying: a transfer
    fee on four dollars costs more than the four dollars.
    """
    balance = (
        ProviderBalance.objects.select_for_update()
        .filter(provider_id=provider_id, currency=currency)
        .first()
    )
    if balance is None or balance.available_amount <= Decimal("0"):
        return None

    minimum = Decimal(str(get_setting("payout.minimum")))
    if balance.available_amount < minimum:
        logger.info(
            "payout_rolled_forward",
            extra={"provider": provider_id, "amount": str(balance.available_amount)},
        )
        return None

    payout = Payout.objects.create(
        provider_id=provider_id,
        currency=currency,
        amount=balance.available_amount,
        period_start=period_start,
        period_end=period_end,
    )
    for booking_id, amount in _unclaimed_items(provider_id, currency).items():
        PayoutItem.objects.create(
            payout=payout, booking_id=booking_id, amount=amount, memo="Net of commission"
        )

    balance.available_amount = Decimal("0")
    balance.save(update_fields=["available_amount", "updated_at"])
    return payout


def _unclaimed_items(provider_id: int, currency: str) -> dict[int, Decimal]:
    """The bookings behind a balance — §22.5's line items.

    Everything credited to this provider that no payout has claimed yet. A
    statement an operator can check against their own diary is the difference
    between a payment they trust and one they ring up about.
    """
    claimed = set(
        PayoutItem.objects.filter(payout__provider_id=provider_id).values_list(
            "booking_id", flat=True
        )
    )
    rows = (
        LedgerEntry.objects.filter(
            provider_id=provider_id,
            currency=currency,
            booking_id__isnull=False,
            entry_type__in=[
                EntryType.PROVIDER_ACCRUAL.value,
                EntryType.CANCELLATION_FEE_ACCRUAL.value,
                EntryType.PROVIDER_ACCRUAL_REVERSAL.value,
            ],
        )
        .exclude(booking_id__in=claimed)
        .only("booking_id", "entry_type", "amount")
    )

    items: dict[int, Decimal] = {}
    for row in rows:
        booking = int(row.booking_id or 0)
        items[booking] = items.get(booking, Decimal("0")) + (
            _BALANCE_EFFECT.get(EntryType(row.entry_type), Decimal("0")) * row.amount
        )
    return {booking: amount for booking, amount in items.items() if amount > Decimal("0")}


@transaction.atomic
def approve_payout(public_id: uuid.UUID, *, approved_by_user_id: int) -> Payout:
    """BR-075: "Payouts require finance approval before release."

    The approver is recorded rather than the fact of approval, because "who"
    is the question asked afterwards.
    """
    payout = Payout.objects.select_for_update().filter(public_id=public_id).first()
    if payout is None:
        raise NotFoundError(f"no payout {public_id}")
    if payout.status != PayoutStatus.DRAFT:
        raise PayoutNotReleasableError(f"a payout in {payout.status} cannot be approved")

    payout.status = PayoutStatus.APPROVED.value
    payout.approved_by_user_id = approved_by_user_id
    payout.approved_at = timezone.now()
    payout.save(update_fields=["status", "approved_by_user_id", "approved_at", "updated_at"])
    return payout


@transaction.atomic
def release_payout(public_id: uuid.UUID, *, rail_reference: str) -> Payout:
    """§22.5's release — ADR 0028 decision 6.

    **No money moves here.** The rail that would move it is Phase 11's, behind
    a port with no adapter, because paying a real company needs an agreement
    that does not exist yet (Appendix D-5). `rail_reference` is the reference
    of a transfer somebody made at their bank and typed in afterwards — which
    is what a business does in its first year, and is honest in a way that a
    "paid" flag set by software that moved nothing would not be.

    The ledger entry is real either way: the obligation has left the platform,
    and `provider_balance` falls by the amount as the entry is posted.
    """
    payout = Payout.objects.select_for_update().filter(public_id=public_id).first()
    if payout is None:
        raise NotFoundError(f"no payout {public_id}")
    if payout.status != PayoutStatus.APPROVED:
        raise PayoutNotReleasableError(
            f"a payout in {payout.status} cannot be released; finance approves first (BR-075)"
        )
    if not rail_reference.strip():
        raise PayoutNotReleasableError(
            "a released payout records how it was paid, so the transfer can be found again"
        )

    now = timezone.now()
    post_journal(
        [
            Leg(
                entry_type=EntryType.PAYOUT_SETTLEMENT,
                amount=payout.amount,
                currency=payout.currency,
                provider_id=payout.provider_id,
                payout_id=int(payout.pk),
                memo=f"Payout {payout.public_id}",
            )
        ],
        occurred_at=now,
    )

    payout.status = PayoutStatus.PAID.value
    payout.rail_reference = rail_reference.strip()[:120]
    payout.paid_at = now
    payout.save(update_fields=["status", "rail_reference", "paid_at", "updated_at"])
    return payout


# ---------------------------------------------------------------------------
# §22.7, §21.9 — reading it back
# ---------------------------------------------------------------------------


def provider_statement(provider_id: int, *, currency: str | None = None) -> dict[str, object]:
    """§26.7: what an operator has earned, and what they have been paid.

    From the ledger, never from the booking table (§22.7), so the figures
    reconcile to money movement by construction.
    """
    rows = LedgerEntry.objects.filter(provider_id=provider_id)
    if currency:
        rows = rows.filter(currency=currency)

    totals: dict[str, Decimal] = {}
    for row in rows.only("entry_type", "amount"):
        totals[row.entry_type] = totals.get(row.entry_type, Decimal("0")) + row.amount

    balances = ProviderBalance.objects.filter(provider_id=provider_id).order_by("currency")
    return {
        "balances": [
            {
                "currency": row.currency,
                "pending": row.pending_amount,
                "available": row.available_amount,
            }
            for row in balances
        ],
        "accrued": totals.get(EntryType.PROVIDER_ACCRUAL.value, Decimal("0")),
        "reversed": totals.get(EntryType.PROVIDER_ACCRUAL_REVERSAL.value, Decimal("0")),
        "compensation": totals.get(EntryType.CANCELLATION_FEE_ACCRUAL.value, Decimal("0")),
        "commission": totals.get(EntryType.COMMISSION_REVENUE.value, Decimal("0")),
        "paid_out": totals.get(EntryType.PAYOUT_SETTLEMENT.value, Decimal("0")),
    }


def ledger_exceptions() -> list[dict[str, object]]:
    """BR-064's two checks, as an exception list — TC-111.

    1. **Per booking and currency**: nothing may be allocated out of a booking
       that was not taken in. A negative residual means the platform has
       promised more than it holds, which is the one arithmetic error in this
       system that costs real money.
    2. **Per provider and currency**: the balance rows must equal what the
       ledger says — "Σ PROVIDER_ACCRUAL - Σ PAYOUT_SETTLEMENT - Σ reversals
       equals `available_amount + pending_amount`", verbatim.

    Returns what is wrong rather than raising, because the caller is a nightly
    job whose output an operator reads — and a checker that stopped at the
    first problem would hide the second.
    """
    problems: list[dict[str, object]] = []

    per_booking: dict[tuple[int, str], list[tuple[EntryType, Decimal]]] = {}
    for row in LedgerEntry.objects.filter(booking_id__isnull=False).only(
        "booking_id", "currency", "entry_type", "amount"
    ):
        key = (int(row.booking_id or 0), row.currency)
        per_booking.setdefault(key, []).append((EntryType(row.entry_type), row.amount))

    for (booking_id, currency), entries in sorted(per_booking.items()):
        left = residual(entries)
        if left < Decimal("0"):
            problems.append(
                {
                    "kind": "OVER_ALLOCATED",
                    "booking_id": booking_id,
                    "currency": currency,
                    "difference": left,
                }
            )

    for balance in ProviderBalance.objects.all().order_by("provider_id", "currency"):
        # What the ledger says is owed, less what a live payout has already
        # claimed. A FAILED payout claims nothing — §22.5 returns its balance
        # to available — which is why this reads the status rather than the
        # payout's existence.
        expected = _provider_net(balance.provider_id, balance.currency) - _claimed_by_payouts(
            balance.provider_id, balance.currency
        )
        held = balance.pending_amount + balance.available_amount
        if expected != held:
            problems.append(
                {
                    "kind": "BALANCE_DRIFT",
                    "provider_id": balance.provider_id,
                    "currency": balance.currency,
                    "difference": held - expected,
                }
            )

    return problems


def _claimed_by_payouts(provider_id: int, currency: str) -> Decimal:
    """What live payouts have taken out of a provider's balance.

    Claimed at assembly rather than at release, because that is when §22.5
    commits the money to a batch — and a payout awaiting approval is money the
    provider must not also be paid another way.
    """
    total = Decimal("0")
    for row in (
        Payout.objects.filter(provider_id=provider_id, currency=currency)
        .exclude(status=PayoutStatus.FAILED)
        .only("amount")
    ):
        total += row.amount
    return total


def account_totals(*, currency: str | None = None) -> dict[str, Decimal]:
    """§22.7's daily figures, per account. From the ledger, by construction."""
    rows = LedgerEntry.objects.all()
    if currency:
        rows = rows.filter(currency=currency)
    totals: dict[str, Decimal] = {account.value: Decimal("0") for account in Account}
    for row in rows.only("account", "direction", "amount"):
        delta = row.amount if row.direction == Direction.CREDIT.value else -row.amount
        totals[row.account] = totals.get(row.account, Decimal("0")) + delta
    return totals


def active_rules() -> list[CommissionRule]:
    """Every live commission rule, for the console and the resolver."""
    today = timezone.localdate()
    return list(
        CommissionRule.objects.filter(is_active=True)
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
        .filter(Q(valid_to__isnull=True) | Q(valid_to__gte=today))
        .order_by("-priority", "id")
    )


# ---------------------------------------------------------------------------
# §22.2 — which rule a booking is sold under
# ---------------------------------------------------------------------------


def resolve_commission(facts: CommissionFacts) -> Rate:
    """The resolver `common.commission` calls, registered at start-up.

    `booking` freezes the rate at basket creation (BR-070) and may not import
    this module (§6.4), so the lookup lives in `common` and this fills it in.
    The whole of §22.2 is behind this one function: scope order, priority,
    method, clamps.

    Falls back to the platform default when nothing matches, which is what a
    platform with no rules configured has always done — and `rule_id` stays
    `None`, so a booking's snapshot says plainly that no rule was matched.
    """
    rules = [_as_domain_rule(row) for row in active_rules()]
    chosen = domain_commission.select(
        rules,
        provider_id=facts.provider_id,
        booking_type=facts.booking_type,
        listing_id=facts.listing_id,
        on=timezone.localdate(),
    )
    if chosen is None:
        return default_rate(facts)

    amount, percent = domain_commission.compute(
        chosen,
        gross=facts.gross_amount,
        monthly_volume=monthly_volume(facts.provider_id, facts.currency),
    )
    return Rate(percent=percent, amount=amount, rule_id=chosen.id)


def monthly_volume(provider_id: int, currency: str, *, today: date | None = None) -> Decimal:
    """What a provider completed in the **preceding calendar month** — §22.2.

    Last month rather than this one, so a TIERED rate is "deterministic within
    a month": two identical bookings must not cost a provider different
    amounts because one was sold on the 2nd and one on the 30th.

    Read from the ledger, because an accrual is the record that a service was
    delivered and paid for — a booking table would count things that were
    later refunded.
    """
    today = today or timezone.localdate()
    first_of_this_month = today.replace(day=1)
    start = (first_of_this_month - timedelta(days=1)).replace(day=1)

    total = Decimal("0")
    for row in LedgerEntry.objects.filter(
        provider_id=provider_id,
        currency=currency,
        entry_type=EntryType.PROVIDER_ACCRUAL.value,
        occurred_at__gte=start,
        occurred_at__lt=first_of_this_month,
    ).only("amount"):
        total += row.amount
    return total


def _as_domain_rule(row: CommissionRule) -> domain_commission.Rule:
    """A frozen copy, so nothing re-reads a rule mid-calculation."""
    return domain_commission.Rule(
        id=int(row.pk),
        scope=domain_commission.Scope(row.scope),
        method=domain_commission.Method(row.method),
        priority=row.priority,
        listing_id=row.listing_id,
        provider_id=row.provider_id,
        booking_type=row.booking_type,
        percent=row.percent,
        flat_amount=row.flat_amount,
        tiers=tuple(
            (Decimal(str(band.get("from_volume", "0"))), Decimal(str(band.get("percent", "0"))))
            for band in (row.tiers or [])
        ),
        min_fee=row.min_fee,
        max_fee=row.max_fee,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        is_active=row.is_active,
    )


def create_rule(**fields: object) -> CommissionRule:
    """§27.11's console, writing one §22.2 rule."""
    return CommissionRule.objects.create(**fields)


def update_rule(public_id: uuid.UUID, **fields: object) -> tuple[dict[str, Any], CommissionRule]:
    """Change a rule, and report what it was.

    The before-image is what an audit entry records: a rate that moved from 15
    to 20 is a different story from one that was always 20, and only the first
    explains a provider's invoice.
    """
    rule = CommissionRule.objects.filter(public_id=public_id).first()
    if rule is None:
        raise NotFoundError(f"no commission rule {public_id}")

    before: dict[str, Any] = {
        "percent": rule.percent,
        "flat_amount": rule.flat_amount,
        "priority": rule.priority,
        "is_active": rule.is_active,
    }
    for name, value in fields.items():
        setattr(rule, name, value)
    rule.save()
    return before, rule


def list_rules() -> list[CommissionRule]:
    """Every rule, live or not — a console that hid the expired ones would
    make "why was this booking charged 18%" unanswerable."""
    return list(CommissionRule.objects.order_by("scope", "-priority", "id"))


def list_payouts(*, status: str | None = None) -> list[Payout]:
    rows = Payout.objects.prefetch_related("items").order_by("-period_end", "provider_id")
    if status:
        rows = rows.filter(status=status)
    return list(rows)
