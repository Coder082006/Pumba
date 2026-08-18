"""Tests for the §30.2 lockout policy, including TC-012."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.identity.domain.lockout import (
    LockoutDecision,
    LockoutPolicy,
    is_locked,
    register_failure,
    remaining,
)

T0 = datetime(2027, 8, 10, 12, 0, 0, tzinfo=UTC)

#: The §30.2 values. Real ones come from system_setting.
POLICY = LockoutPolicy(
    threshold=10,
    window=timedelta(minutes=15),
    base_duration=timedelta(minutes=1),
    max_duration=timedelta(hours=1),
)


def fail(
    count: int = 0,
    started: datetime | None = None,
    lockouts: int = 0,
    now: datetime = T0,
) -> LockoutDecision:
    return register_failure(
        failed_count=count,
        window_started_at=started,
        lockout_count=lockouts,
        now=now,
        policy=POLICY,
    )


class TestPolicyValidation:
    def test_a_threshold_below_one_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            LockoutPolicy(0, timedelta(minutes=15), timedelta(minutes=1), timedelta(hours=1))

    def test_a_base_longer_than_the_cap_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="cannot exceed"):
            LockoutPolicy(10, timedelta(minutes=15), timedelta(hours=2), timedelta(hours=1))


class TestCounting:
    def test_the_first_failure_starts_a_window(self) -> None:
        d = fail()
        assert d.failed_count_after == 1
        assert d.window_started_at == T0
        assert not d.is_locked

    def test_failures_accumulate_inside_the_window(self) -> None:
        d = fail(count=4, started=T0, now=T0 + timedelta(minutes=5))
        assert d.failed_count_after == 5
        assert d.window_started_at == T0
        assert not d.is_locked

    def test_the_ninth_failure_does_not_lock(self) -> None:
        assert not fail(count=8, started=T0, now=T0 + timedelta(minutes=1)).is_locked


class TestTheWindowRolls:
    """ "10 failures in 15 minutes", not "the 10th failure ever"."""

    def test_a_failure_after_the_window_starts_a_fresh_run(self) -> None:
        d = fail(count=9, started=T0, now=T0 + timedelta(minutes=16))
        assert d.failed_count_after == 1
        assert d.window_started_at == T0 + timedelta(minutes=16)
        assert not d.is_locked

    def test_the_window_boundary_is_inclusive_of_expiry(self) -> None:
        """At exactly 15 minutes the run has expired."""
        d = fail(count=9, started=T0, now=T0 + timedelta(minutes=15))
        assert d.failed_count_after == 1
        assert not d.is_locked

    def test_one_second_inside_the_window_still_counts(self) -> None:
        """One second either side of the boundary is the whole difference
        between a tenth failure and a fresh first one."""
        d = fail(count=9, started=T0, now=T0 + timedelta(minutes=15) - timedelta(seconds=1))
        assert d.is_locked
        # The counter resets on lock, so the tenth failure is observed as the
        # lock itself rather than as a count of ten.
        assert d.failed_count_after == 0

    def test_slow_failures_never_lock_the_account(self) -> None:
        """Twice a day for a week is not an attack, and a plain incrementing
        counter would lock this user out."""
        count, started, now = 0, None, T0
        for _ in range(14):
            d = register_failure(
                failed_count=count,
                window_started_at=started,
                lockout_count=0,
                now=now,
                policy=POLICY,
            )
            assert not d.is_locked
            count, started = d.failed_count_after, d.window_started_at
            now += timedelta(hours=12)


class TestTc012Lockout:
    """TC-012: 10 failures in 15 min; the 11th attempt is refused."""

    def test_the_tenth_failure_locks(self) -> None:
        d = fail(count=9, started=T0, now=T0 + timedelta(minutes=10))
        assert d.is_locked
        assert d.locked_until == T0 + timedelta(minutes=10) + POLICY.base_duration

    def test_the_owner_is_notified(self) -> None:
        assert fail(count=9, started=T0, now=T0 + timedelta(minutes=1)).notify_owner

    def test_the_owner_is_not_notified_before_the_threshold(self) -> None:
        assert not fail(count=1, started=T0, now=T0 + timedelta(minutes=1)).notify_owner

    def test_the_counter_resets_on_lock(self) -> None:
        """So the first failure after the lock lifts does not re-lock."""
        assert fail(count=9, started=T0, now=T0).failed_count_after == 0


class TestExponentialBackoff:
    @pytest.mark.parametrize(
        ("previous_lockouts", "expected_minutes"),
        [(0, 1), (1, 2), (2, 4), (3, 8), (4, 16), (5, 32), (6, 60)],
    )
    def test_duration_doubles_per_previous_lockout(
        self, previous_lockouts: int, expected_minutes: int
    ) -> None:
        d = fail(count=9, started=T0, lockouts=previous_lockouts, now=T0)
        assert d.locked_until == T0 + timedelta(minutes=expected_minutes)

    def test_the_cap_is_one_hour(self) -> None:
        """§30.2: "up to 1 hour"."""
        for lockouts in (7, 20, 500):
            d = fail(count=9, started=T0, lockouts=lockouts, now=T0)
            assert d.locked_until == T0 + timedelta(hours=1)

    def test_a_negative_lockout_count_is_treated_as_none(self) -> None:
        d = fail(count=9, started=T0, lockouts=-3, now=T0)
        assert d.locked_until == T0 + POLICY.base_duration


class TestIsLocked:
    def test_no_lock_means_open(self) -> None:
        assert not is_locked(locked_until=None, now=T0)

    def test_before_expiry_is_locked(self) -> None:
        assert is_locked(locked_until=T0 + timedelta(minutes=5), now=T0)

    def test_at_exactly_expiry_the_account_is_open(self) -> None:
        assert not is_locked(locked_until=T0, now=T0)

    def test_after_expiry_is_open(self) -> None:
        assert not is_locked(locked_until=T0, now=T0 + timedelta(seconds=1))


class TestRemaining:
    def test_reports_the_time_left_for_the_423_response(self) -> None:
        """§9.4.2: "423 ACCOUNT_LOCKED"; §24.4 shows the lockout expiry."""
        assert remaining(locked_until=T0 + timedelta(minutes=5), now=T0) == timedelta(minutes=5)

    def test_an_unlocked_account_has_nothing_remaining(self) -> None:
        assert remaining(locked_until=None, now=T0) == timedelta(0)

    def test_an_expired_lock_has_nothing_remaining(self) -> None:
        assert remaining(locked_until=T0, now=T0 + timedelta(hours=1)) == timedelta(0)
