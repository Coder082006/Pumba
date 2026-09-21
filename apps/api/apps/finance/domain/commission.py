"""§22.2's rule resolution and calculation, as pure functions.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§22.2, verbatim on the order:

    scope = LISTING   and listing_id  matches       (a negotiated rate for one hotel)
    scope = PROVIDER  and provider_id matches
    scope = TYPE      and booking_type matches      (e.g. all ACTIVITY = 18%)
    scope = GLOBAL                                   (default fallback)

**Scope first, then priority.** A negotiated rate for one listing beats a
general rate for its provider even if the provider's rule was given a higher
priority number — otherwise the order above would be advisory. Priority breaks
ties *within* a scope, which is what it is for: two rules for one provider, one
of them seasonal.

**TIERED reads last month's volume, not this month's.** §22.2: "evaluated
against the completed volume of the preceding calendar month so the rate is
deterministic within a month". A rate that moved mid-month would make two
identical bookings cost the provider different amounts depending on when in
the month they happened to be sold.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

__all__ = [
    "Scope",
    "Method",
    "Rule",
    "SCOPE_ORDER",
    "matches",
    "select",
    "compute",
]

_CENTS = Decimal("0.01")


class Scope(StrEnum):
    LISTING = "LISTING"
    PROVIDER = "PROVIDER"
    TYPE = "TYPE"
    GLOBAL = "GLOBAL"


class Method(StrEnum):
    PERCENT = "PERCENT"
    FLAT = "FLAT"
    TIERED = "TIERED"


#: Most specific first. The index in this tuple is the sort key.
SCOPE_ORDER: tuple[Scope, ...] = (Scope.LISTING, Scope.PROVIDER, Scope.TYPE, Scope.GLOBAL)


@dataclass(frozen=True, slots=True, kw_only=True)
class Rule:
    """One `commission_rule` row, as this module needs it.

    A frozen copy rather than the model: §8.2 keeps the domain free of Django,
    and a rule that could be re-read mid-calculation is a rule that could
    change between the resolution and the snapshot.
    """

    id: int
    scope: Scope
    method: Method
    priority: int = 0
    listing_id: int | None = None
    provider_id: int | None = None
    booking_type: str = ""
    percent: Decimal | None = None
    flat_amount: Decimal | None = None
    tiers: tuple[tuple[Decimal, Decimal], ...] = ()
    min_fee: Decimal | None = None
    max_fee: Decimal | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    is_active: bool = True


def matches(
    rule: Rule,
    *,
    provider_id: int,
    booking_type: str,
    listing_id: int | None,
    on: date | None = None,
) -> bool:
    """Whether a rule applies to this booking at all.

    A LISTING rule cannot match a transfer, which has no listing — and it must
    not fall through to matching *everything*, which is what comparing two
    `None`s would do.
    """
    if not rule.is_active:
        return False
    if on is not None:
        if rule.valid_from is not None and on < rule.valid_from:
            return False
        if rule.valid_to is not None and on > rule.valid_to:
            return False

    if rule.scope is Scope.LISTING:
        return listing_id is not None and rule.listing_id == listing_id
    if rule.scope is Scope.PROVIDER:
        return rule.provider_id == provider_id
    if rule.scope is Scope.TYPE:
        return bool(rule.booking_type) and rule.booking_type == booking_type
    return True


def select(
    rules: Sequence[Rule],
    *,
    provider_id: int,
    booking_type: str,
    listing_id: int | None,
    on: date | None = None,
) -> Rule | None:
    """§22.2's "highest-priority active rule matching the booking".

    Scope order first, priority second, lowest id last — the third is not in
    the SRS and is there because two rules that tie on both would otherwise
    resolve differently depending on the order the database returned them, and
    a commission that depends on that is a commission nobody can reproduce
    (BR-054's "reproducible forever", applied to the other side of the sale).
    """
    candidates = [
        rule
        for rule in rules
        if matches(
            rule,
            provider_id=provider_id,
            booking_type=booking_type,
            listing_id=listing_id,
            on=on,
        )
    ]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda rule: (SCOPE_ORDER.index(rule.scope), -rule.priority, rule.id),
    )


def compute(
    rule: Rule, *, gross: Decimal, monthly_volume: Decimal = Decimal("0")
) -> tuple[Decimal, Decimal]:
    """The commission a rule charges on one gross amount.

    Returns `(amount, percent)` — the money and the rate it worked out to,
    because BR-070 snapshots a percentage onto the booking and a flat fee still
    has to be recorded as one to stay reproducible.

    Rounded once, half up, at the end: §18.5 rounds per line total, and a clamp
    applied to an unrounded figure would round a different number than the one
    it clamped.
    """
    if rule.method is Method.FLAT:
        amount = rule.flat_amount or Decimal("0")
    elif rule.method is Method.TIERED:
        amount = gross * _band(rule, monthly_volume) / Decimal("100")
    else:
        amount = gross * (rule.percent or Decimal("0")) / Decimal("100")

    if rule.min_fee is not None:
        amount = max(amount, rule.min_fee)
    if rule.max_fee is not None:
        amount = min(amount, rule.max_fee)
    # Never more than the sale itself: a commission larger than the gross would
    # make the provider owe the platform for having worked.
    amount = min(max(amount, Decimal("0")), gross).quantize(_CENTS, rounding=ROUND_HALF_UP)

    percent = (
        Decimal("0")
        if gross == Decimal("0")
        else (amount * Decimal("100") / gross).quantize(_CENTS, rounding=ROUND_HALF_UP)
    )
    return amount, percent


def _band(rule: Rule, monthly_volume: Decimal) -> Decimal:
    """The percentage for a provider's volume — §22.2's TIERED bands.

    The highest band whose floor the volume reaches. An empty ladder charges
    nothing, which is visible in a statement immediately; guessing a default
    would not be.
    """
    applicable = [percent for floor, percent in rule.tiers if monthly_volume >= floor]
    return max(applicable) if applicable else Decimal("0")
