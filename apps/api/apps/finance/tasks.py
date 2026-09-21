"""finance module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Celery tasks — §8.8's finance queue.

Three jobs, and none of them decides anything: §22.4's rules are in
`services.release_matured_balances`, §22.5's in `build_payout_batch`, BR-064's
in `ledger_exceptions`. What is here is when they run and what an operator
reads afterwards.

**They alert rather than repair.** §21.9's reconciliation produces "an
exception worklist", and BR-064 "raises a P2 alert" — not a correction. A job
that quietly fixed a ledger discrepancy would be a job that hid the bug that
caused it, and the ledger it fixed would no longer be a record of what happened.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.finance import services
from apps.finance.models import ProviderBalance

logger = logging.getLogger(__name__)

__all__ = ["mature_provider_balances", "build_payout_batches", "check_the_ledger"]


@shared_task(name="finance.mature_provider_balances", queue="finance")
def mature_provider_balances() -> dict[str, int]:
    """§22.4's settlement hold, checked daily.

    "A configurable hold period after completion allows disputes to surface
    before funds become available for payout." Daily is the right cadence for a
    window measured in days: an hourly job would move money a few hours earlier
    and run twenty-three more times for nothing.
    """
    moved = services.release_matured_balances()
    return {"providers": moved}


@shared_task(name="finance.build_payout_batches", queue="finance")
def build_payout_batches() -> dict[str, int]:
    """§8.8: "Beat, weekly. Assemble provider payouts. Advisory-locked."

    One batch per provider and currency, for the week that has just ended.
    A provider under `payout.minimum` is skipped rather than paid, and
    `build_payout_batch` says so in the log — BR-074 rolls that balance
    forward, it does not lose it.
    """
    today = timezone.localdate()
    period_end = today - timedelta(days=1)
    period_start = period_end - timedelta(days=6)

    built = skipped = 0
    for balance in ProviderBalance.objects.filter(available_amount__gt=0).order_by(
        "provider_id", "currency"
    ):
        payout = services.build_payout_batch(
            provider_id=balance.provider_id,
            currency=balance.currency,
            period_start=period_start,
            period_end=period_end,
        )
        if payout is None:
            skipped += 1
        else:
            built += 1

    if built or skipped:
        logger.info("payout_batches_built", extra={"built": built, "skipped": skipped})
    return {"built": built, "rolled_forward": skipped}


@shared_task(name="finance.check_the_ledger", queue="finance")
def check_the_ledger() -> dict[str, int]:
    """BR-064 and TC-111, nightly.

    Two questions: has any booking had more allocated out of it than came in,
    and does every provider balance still equal what the ledger says. Both are
    logged at ERROR when they fail, because §22.3 makes this a P2 alert and a
    silent imbalance is money nobody is looking for.
    """
    problems = services.ledger_exceptions()
    if problems:
        logger.error(
            "ledger_invariant_broken",
            extra={"count": len(problems), "first": problems[0]},
        )
    return {"exceptions": len(problems)}
