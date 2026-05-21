"""Unit tests for watchdog.backoff.

Pure logic, no Docker, no sleep — `now` is injected so we can fast-
forward time deterministically.
"""

from __future__ import annotations

import pytest
from watchdog.backoff import AttemptRecord, BackoffPolicy


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------
def test_default_policy_matches_plan() -> None:
    p = BackoffPolicy()
    assert p.delay_for(0) == 10.0
    assert p.delay_for(1) == 30.0
    assert p.delay_for(2) == 90.0
    assert p.delay_for(3) == 270.0
    assert p.delay_for(4) == 810.0
    assert p.max_attempts == 5


# ---------------------------------------------------------------------------
# AttemptRecord — readiness + counters
# ---------------------------------------------------------------------------
def test_first_attempt_is_immediate() -> None:
    record = AttemptRecord()
    policy = BackoffPolicy()
    assert record.ready_for_next_attempt(policy, now=0.0)


def test_second_attempt_waits_initial_delay() -> None:
    record = AttemptRecord()
    policy = BackoffPolicy(initial_seconds=10.0)
    record.record_attempt(now=0.0)
    # Nine seconds later: still waiting.
    assert not record.ready_for_next_attempt(policy, now=9.0)
    # Ten seconds later: ready.
    assert record.ready_for_next_attempt(policy, now=10.0)


def test_third_attempt_waits_multiplied_delay() -> None:
    record = AttemptRecord()
    policy = BackoffPolicy(initial_seconds=10.0, multiplier=3.0)
    record.record_attempt(now=0.0)
    record.record_attempt(now=10.0)
    # The delay before the 3rd attempt is 10 * 3 = 30.
    assert not record.ready_for_next_attempt(policy, now=39.0)
    assert record.ready_for_next_attempt(policy, now=40.0)


def test_exhausted_blocks_further_attempts() -> None:
    record = AttemptRecord()
    policy = BackoffPolicy(max_attempts=3)
    for _ in range(3):
        record.record_attempt(now=0.0)
    assert record.exhausted(policy)
    assert not record.ready_for_next_attempt(policy, now=10_000.0)


def test_reset_clears_state() -> None:
    record = AttemptRecord()
    policy = BackoffPolicy()
    for _ in range(2):
        record.record_attempt(now=0.0)
    record.alerted = True

    record.reset()

    assert record.consecutive_failures == 0
    assert record.last_attempt_at == 0.0
    assert record.alerted is False
    assert record.ready_for_next_attempt(policy, now=0.0)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "failures,expected",
    [
        (0, 10.0),
        (1, 30.0),
        (2, 90.0),
        (3, 270.0),
        (4, 810.0),
    ],
)
def test_delay_progression(failures: int, expected: float) -> None:
    assert BackoffPolicy().delay_for(failures) == pytest.approx(expected)
