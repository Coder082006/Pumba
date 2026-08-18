"""Account lockout policy — SRS §30.2.

    "Account lockout after 10 failed attempts in 15 minutes, with
     exponentially increasing lockout up to 1 hour, and a notification to the
     account owner."

Three things that sentence requires and that a naive counter does not give:

* **The window rolls.** "10 failures in 15 minutes" is not "the 10th failure
  ever". A failure older than the window does not count, so an account that
  fails twice a day for a week is never locked. Implementing this as a plain
  incrementing column is the usual mistake and it locks out legitimate users
  over weeks.

* **The lockout grows per lockout, not per failure.** The exponent is how many
  times this account has already been locked, so a repeatedly targeted account
  becomes progressively more expensive to attack while a user having one bad
  morning is delayed by minutes.

* **The owner is told.** A lockout is the only signal a user gets that someone
  is trying their password, so `notify_owner` is part of the decision rather
  than an afterthought at the call site.

`now` is a parameter. The domain never reads the clock — that is what makes
every boundary here testable to the second.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

__all__ = ["LockoutPolicy", "LockoutDecision", "register_failure", "is_locked", "remaining"]


@dataclass(frozen=True, slots=True)
class LockoutPolicy:
    """§30.2 defaults live in `system_setting`, not here."""

    threshold: int
    window: timedelta
    base_duration: timedelta
    max_duration: timedelta

    def __post_init__(self) -> None:
        if self.threshold < 1:
            raise ValueError("threshold must be at least 1")
        if self.base_duration > self.max_duration:
            raise ValueError("base_duration cannot exceed max_duration")


@dataclass(frozen=True, slots=True)
class LockoutDecision:
    is_locked: bool
    locked_until: datetime | None
    failed_count_after: int
    window_started_at: datetime
    notify_owner: bool


def register_failure(
    *,
    failed_count: int,
    window_started_at: datetime | None,
    lockout_count: int,
    now: datetime,
    policy: LockoutPolicy,
) -> LockoutDecision:
    """Record one failed attempt and decide whether the account locks.

    `window_started_at` is when the current run of failures began. When it is
    older than the policy window, the run has expired and this failure starts
    a fresh one — that is the rolling behaviour.
    """
    expired = window_started_at is None or now - window_started_at >= policy.window
    if expired:
        count = 1
        started = now
    else:
        count = failed_count + 1
        assert window_started_at is not None
        started = window_started_at

    if count < policy.threshold:
        return LockoutDecision(
            is_locked=False,
            locked_until=None,
            failed_count_after=count,
            window_started_at=started,
            notify_owner=False,
        )

    return LockoutDecision(
        is_locked=True,
        locked_until=now + _duration(lockout_count, policy),
        # Reset on lock, so the next failure after the lock expires starts a
        # fresh window rather than re-locking on a single attempt.
        failed_count_after=0,
        window_started_at=now,
        notify_owner=True,
    )


def _duration(lockout_count: int, policy: LockoutPolicy) -> timedelta:
    """Exponential in the number of *previous lockouts*, capped.

    Computed by doubling rather than by `base * 2**n` so that a long-lived
    hostile account cannot produce an absurd intermediate value before the cap
    is applied.
    """
    duration = policy.base_duration
    for _ in range(max(lockout_count, 0)):
        if duration >= policy.max_duration:
            break
        duration *= 2
    return min(duration, policy.max_duration)


def is_locked(*, locked_until: datetime | None, now: datetime) -> bool:
    """Whether the account is locked at this instant.

    The boundary is exclusive: at exactly `locked_until` the account is open.
    """
    return locked_until is not None and now < locked_until


def remaining(*, locked_until: datetime | None, now: datetime) -> timedelta:
    """How long until the lock lifts — §9.4.2's 423 carries the unlock time."""
    if not is_locked(locked_until=locked_until, now=now):
        return timedelta(0)
    assert locked_until is not None
    return locked_until - now
