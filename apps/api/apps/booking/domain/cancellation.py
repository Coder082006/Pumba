"""What a cancellation refunds — SRS §20.9, BR-040 to BR-046, ADR 0025.

Pure. No Django, no ORM, no I/O. Layer 3 (SRS §8.2), covered to 95%.

§20.9 calls the evaluation "a pure function" and gives its inputs: the policy
snapshot, the start, the moment of cancellation, the amounts, and who cancelled.
This is that function, with ADR 0025 decision 3 applied: **the service fee sits
on top of the component's price**, as §22.1's commercial model has it, not
inside it as §20.9's worked example does.

    refund_of_price       = round(gross * percent / 100, 2)
    fee_refunded          = fee, when supply failed (BR-045) or the cancellation
                            is more than `fee_retention_hours` ahead; else 0
    tax_refunded          = round(tax * percent / 100, 2)
    refund_amount         = refund_of_price + fee_refunded + tax_refunded
    provider_compensation = gross - refund_of_price
    platform_fee_retained = fee - fee_refunded

**The percent comes from the booking's own snapshot** (BR-040), through
`common.cancellation.refund_percent_at` — the same tier walk, and so the same
strict boundary, that validated the policy when an administrator wrote it.

**Supply failure refunds everything** (BR-045): a provider, driver or the
platform cancelling returns the full price and the fee, whatever the tier.

**Nothing here can refund more than was paid** (BR-044). Every output is a
share of an input, and `assert_refundable` is the explicit check for a refund
requested by hand.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from apps.common.cancellation import parse_tiers, refund_percent_at

__all__ = [
    "Party",
    "Refund",
    "RefundExceedsCapturedError",
    "evaluate",
    "assert_refundable",
]

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)
_SECONDS_PER_HOUR = Decimal(3600)


class Party(StrEnum):
    """Who cancelled. §20.9 names the four; three of them are supply."""

    TOURIST = "TOURIST"
    PROVIDER = "PROVIDER"
    DRIVER = "DRIVER"
    PLATFORM = "PLATFORM"


#: BR-045: "Where the provider, driver or Platform causes the cancellation".
_SUPPLY = frozenset({Party.PROVIDER, Party.DRIVER, Party.PLATFORM})


class RefundExceedsCapturedError(ValueError):
    """BR-044. §32.3: `REFUND_EXCEEDS_CAPTURED`, 422."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Refund:
    """One component's cancellation, fully itemised.

    Every figure is two places and they reconcile by construction:
    `refund_of_price + provider_compensation == gross` and
    `fee_refunded + platform_fee_retained == fee`.
    """

    refund_percent: Decimal
    refund_of_price: Decimal
    fee_refunded: Decimal
    tax_refunded: Decimal
    refund_amount: Decimal
    provider_compensation: Decimal
    platform_fee_retained: Decimal


def _share(amount: Decimal, percent: Decimal) -> Decimal:
    return (amount * percent / _HUNDRED).quantize(_CENT, rounding=ROUND_HALF_UP)


def evaluate(
    *,
    snapshot: Mapping[str, object],
    starts_at: datetime,
    cancelled_at: datetime,
    gross: Decimal,
    fee: Decimal,
    tax: Decimal,
    party: Party,
    fee_retention_hours: int,
) -> Refund:
    """§20.9. `snapshot` is `booking.cancellation_policy_snapshot`, verbatim.

    `hours_before` is a `Decimal` of the exact interval over 3600, never rounded
    or truncated: a cancellation half a second past a threshold is past it, and
    dropping the microseconds would move it into the lower tier — the tourist's
    loss, on the one case most likely to be disputed.
    """
    interval = starts_at - cancelled_at
    seconds = Decimal(interval.days * 86400 + interval.seconds) + Decimal(
        interval.microseconds
    ) / Decimal(1_000_000)
    hours_before = seconds / _SECONDS_PER_HOUR

    if party in _SUPPLY:
        percent = _HUNDRED
        keeps_fee = False
    else:
        percent = refund_percent_at(
            parse_tiers(snapshot.get("tiers", [])), hours_before=hours_before
        )
        # The same strictness as a tier: more than the threshold, not at it.
        keeps_fee = not hours_before > Decimal(fee_retention_hours)

    refund_of_price = _share(gross, percent)
    fee_refunded = Decimal("0.00") if keeps_fee else fee
    tax_refunded = _share(tax, percent)
    return Refund(
        refund_percent=percent,
        refund_of_price=refund_of_price,
        fee_refunded=fee_refunded,
        tax_refunded=tax_refunded,
        refund_amount=refund_of_price + fee_refunded + tax_refunded,
        provider_compensation=gross - refund_of_price,
        platform_fee_retained=fee - fee_refunded,
    )


def assert_refundable(requested: Decimal, *, paid: Decimal, already_refunded: Decimal) -> None:
    """BR-044: "A refund may never exceed the amount captured and not already
    refunded". TC-103: "Refund 200 of a 110 booking → 422 REFUND_EXCEEDS_CAPTURED".
    """
    if requested < 0:
        raise RefundExceedsCapturedError("a refund cannot be negative")
    remaining = paid - already_refunded
    if requested > remaining:
        raise RefundExceedsCapturedError(
            f"a refund of {requested} exceeds the {remaining} captured and not yet refunded"
        )
