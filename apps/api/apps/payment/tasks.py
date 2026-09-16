"""payment module — SRS §6.4.

Infrastructure layer (SRS §8.2 layer 5). Celery tasks.

**§21.5's fallback, and why it is a countdown rather than a beat.** A webhook
that never arrives is not a periodic condition to sweep for — it is one
payment, waiting. So `verify_payment_status` is scheduled when the intent is
created and re-schedules itself along §21.5's ladder: 30 s, 2 min, 5 min,
10 min, 20 min, 30 min, "then mark for review" (§8.8).

Nothing here decides anything. The interpretation of a PSP state is
`services.apply_psp_state`, shared with the webhook, so a lost webhook costs
latency and never correctness — two code paths reading a capture differently
would be two systems disagreeing about whether a trip is paid for.
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.payment import services

logger = logging.getLogger(__name__)

__all__ = ["verify_payment_status", "PROBE_SCHEDULE"]

#: §21.5, verbatim: "polls the PSP at 30 s, 2 min, 5 min, 10 min, 20 min and
#: 30 min after intent". Held as offsets from the intent rather than as gaps,
#: because that is how the SRS states them and how an operator reads them.
PROBE_SCHEDULE = (30, 120, 300, 600, 1200, 1800)


@shared_task(name="payment.verify_payment_status", queue="payments")
def verify_payment_status(payment_id: int, attempt: int = 0) -> str:
    """Ask the PSP what happened, because nothing told us.

    Returns what it did, as a string, because §8.8 gives this job no other
    output and "the poller ran" is not the same statement as "the poller found
    nothing".

    Re-schedules itself until the payment is terminal or the ladder runs out.
    The last rung logs at ERROR rather than raising: a payment nobody can
    resolve is an operational fact, and a task that failed would be retried by
    Celery on a schedule that is not §21.5's.
    """
    outcome = services.poll_payment(payment_id)
    if outcome in {"TERMINAL", "APPLIED"}:
        return outcome

    following = attempt + 1
    if following >= len(PROBE_SCHEDULE):
        logger.error(
            "payment_unresolved_after_polling",
            extra={"payment_id": payment_id, "attempts": len(PROBE_SCHEDULE)},
        )
        return "FOR_REVIEW"

    gap = PROBE_SCHEDULE[following] - PROBE_SCHEDULE[attempt]
    verify_payment_status.apply_async(args=[payment_id, following], countdown=gap, queue="payments")
    return "RESCHEDULED"
