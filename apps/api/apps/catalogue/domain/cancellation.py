"""Cancellation policies — SRS §14.6, §27.12.

§14.6 names four seed policies and then says the thing that matters:

    A policy is expressed generically as an ordered list of
    {hours_before, refund_percent} tiers, so administrators can create new
    policies without code changes.

So `FLEX_48H`, `MODERATE_7D`, `STRICT_14D` and `NON_REFUNDABLE` are **rows**,
not classes, not an enum, and not a `match` statement. This module knows only
the tier structure and how to read it. A market with different consumer law
gets new rows through the §27.12 console, and nothing here changes.

Three things are pinned down because the SRS states the policies in prose and
prose is ambiguous at the boundaries:

* **Tiers are ordered by `hours_before` descending** — most generous first —
  and the applicable tier is the first whose threshold the cancellation is
  still outside. `MODERATE_7D` is `[(168, 100), (48, 50)]`: more than 7 days
  gives 100%, between 7 days and 48 hours gives 50%, and inside 48 hours falls
  off the end of the list and gives nothing.

* **The boundary is strict.** §14.6 says *"more than 48 h before check-in"*,
  so a cancellation at exactly 48 hours is *not* more than 48 hours and gets
  the next tier down. Reading it inclusively would hand back money the policy
  does not offer, on the one case most likely to be disputed.

* **Falling off the end is 0%, not an error.** Every policy implicitly ends
  with "and nothing thereafter", and `NON_REFUNDABLE` is simply the policy with
  no tiers at all — which is the cleanest evidence that the generic form is the
  right one.

§20.9 owns the *evaluation* at refund time, including what the booking
snapshotted (BR-106). This module owns the structure and its validation,
because §27.12 lets an administrator create a policy in Phase 3 and an invalid
one must be rejected at the form, not discovered during a refund.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

__all__ = ["Tier", "CancellationPolicyError", "validate_tiers", "refund_percent_at"]


class CancellationPolicyError(ValueError):
    """A tier list that cannot express a coherent policy."""


@dataclass(frozen=True, slots=True, order=True)
class Tier:
    """*"More than `hours_before` hours ahead, refund `refund_percent`."*"""

    hours_before: int
    refund_percent: Decimal

    def __post_init__(self) -> None:
        if self.hours_before < 0:
            raise CancellationPolicyError("hours_before cannot be negative")
        if not Decimal(0) <= self.refund_percent <= Decimal(100):
            raise CancellationPolicyError(
                f"refund_percent must be between 0 and 100, got {self.refund_percent}"
            )


def validate_tiers(tiers: Iterable[Tier]) -> tuple[Tier, ...]:
    """Normalise and check a policy. §27.12 calls this on every admin write.

    Rejects, rather than repairs:

    * **duplicate thresholds** — two rules for the same moment, and no way to
      tell which the administrator meant;
    * **out-of-order tiers** — accepting them and sorting silently would hide a
      typo that reads as a much more generous policy than intended;
    * **a refund that increases as the date approaches** — every policy in
      §14.6 decreases, and an increasing one is a data-entry error rather than
      a commercial choice. If a market ever wants one, it should arrive as a
      deliberate change here with its own test, not by accident.
    """
    ordered = tuple(tiers)

    thresholds = [t.hours_before for t in ordered]
    if len(set(thresholds)) != len(thresholds):
        raise CancellationPolicyError("duplicate hours_before thresholds")
    if thresholds != sorted(thresholds, reverse=True):
        raise CancellationPolicyError("tiers must be ordered by hours_before, descending")

    percents = [t.refund_percent for t in ordered]
    if percents != sorted(percents, reverse=True):
        raise CancellationPolicyError("refund_percent must not increase as the date approaches")

    return ordered


def refund_percent_at(tiers: Sequence[Tier], *, hours_before: Decimal) -> Decimal:
    """The refund due for a cancellation `hours_before` hours ahead.

    `hours_before` is a `Decimal` because it is a measured interval, not a
    count: a cancellation 47.9 hours out must not round up into the 48-hour
    tier. Rounding it to an int here is the same class of mistake as using a
    float for money.
    """
    if hours_before < 0:
        # Cancelling after the service has started. The policy's last tier
        # never applies; §20.9 handles no-shows separately.
        return Decimal(0)

    for tier in tiers:
        if hours_before > tier.hours_before:
            return tier.refund_percent
    return Decimal(0)
