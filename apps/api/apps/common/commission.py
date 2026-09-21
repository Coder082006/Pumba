"""Which commission a booking is sold under — SRS §22.2, ADR 0028.

**Why this is in `common`.** §6.4 gives `commission_rule` to `finance`, and
BR-070 freezes the rate onto the booking at the moment the basket is created —
which happens in `booking`. `finance` may import `booking`; `booking` may not
import `finance`. As written, the rule lives in a module the code that needs it
cannot reach.

This is the shape of issue S1 and of `common.audit`, and it is resolved the
same way: **split the lookup from the table.** This module is the lookup and
lives in `common`, which every module may import. `finance` owns the table, the
console and the resolution order, and registers a database-backed resolver here
at start-up.

**Until it does, the answer is the global default.** A platform with no rules
configured still sells at `commission.default_percent`, which is what Phase 7
did and what every seed relies on. The degradation is deliberate and visible:
`Rate.rule_id` is `None`, so a booking snapshot says plainly that no rule was
matched rather than implying one was.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from apps.common.config import get_setting

__all__ = [
    "CommissionFacts",
    "Rate",
    "Resolver",
    "register_resolver",
    "clear_resolver",
    "resolve",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class CommissionFacts:
    """What §22.2 resolves against.

    Everything a rule may match on, resolved by the caller before it asks.
    A resolver that fetched its own facts would need to reach the modules that
    hold them, which is the dependency this module exists to avoid.
    """

    provider_id: int
    booking_type: str
    gross_amount: Decimal
    currency: str
    #: The listing being sold, where there is one. A transfer has no listing,
    #: which is why the LISTING scope simply cannot match one.
    listing_id: int | None = None


@dataclass(frozen=True, slots=True)
class Rate:
    """The answer, and which rule gave it.

    `percent` is what BR-070 snapshots onto the booking, so a FLAT or TIERED
    rule still reports the percentage it worked out to — the booking column is
    a percentage, and a flat fee expressed as one is still reproducible.
    """

    percent: Decimal
    amount: Decimal
    rule_id: int | None = None


Resolver = Callable[[CommissionFacts], Rate]

_resolver: Resolver | None = None


def register_resolver(resolver: Resolver) -> None:
    """Called by `FinanceConfig.ready()`. Never from business code."""
    global _resolver
    _resolver = resolver


def clear_resolver() -> None:
    """Test helper, so a suite can prove the fallback still works."""
    global _resolver
    _resolver = None


def resolve(facts: CommissionFacts) -> Rate:
    """§22.2's rate for one booking, or the global default.

    A resolver that raises is not allowed to fail a booking: a platform that
    could not sell anything because a commission table was unreadable would be
    worse off than one charging its default rate, and the default is a rate
    somebody chose. The failure is logged at ERROR rather than swallowed.
    """
    if _resolver is not None:
        try:
            return _resolver(facts)
        except Exception:
            logger.exception(
                "commission_resolver_failed",
                extra={"provider_id": facts.provider_id, "type": facts.booking_type},
            )
    return default_rate(facts)


def default_rate(facts: CommissionFacts) -> Rate:
    """`commission.default_percent`, applied to the gross.

    A `system_setting` row rather than a constant (hard rule 5): the platform's
    default take is a commercial decision, and §4.1 will not have it need a
    deployment.
    """
    percent = Decimal(str(get_setting("commission.default_percent"))).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    amount = (facts.gross_amount * percent / Decimal("100")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return Rate(percent=percent, amount=amount, rule_id=None)
