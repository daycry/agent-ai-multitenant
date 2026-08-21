"""Exponential backoff policy and per-service attempt tracker.

Phase-0 default: 5 attempts at 10s, 30s, 90s, 270s, 810s (x 3 each).
After the cap the monitor stops retrying and emits an alert log.
The next successful health check resets the counter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import monotonic


@dataclass(frozen=True)
class BackoffPolicy:
    initial_seconds: float = 10.0
    multiplier: float = 3.0
    max_attempts: int = 5

    def delay_for(self, failures: int) -> float:
        """Seconds to wait before the next attempt at `failures` past failures."""
        return self.initial_seconds * (self.multiplier**failures)


@dataclass
class AttemptRecord:
    """How many times we've tried to recover one service in a row.

    `last_attempt_at` is a monotonic timestamp; pass `now=` from callers
    so tests can substitute a frozen clock.
    """

    consecutive_failures: int = 0
    last_attempt_at: float = field(default=0.0)
    alerted: bool = False
    # prod-08 task_prod08_watchdog_14: `alerted` significa "ya lo escribí en el
    # log"; `alert_delivered`, "llegó a un humano". Son dos cosas distintas y
    # confundirlas fue el defecto original: el log se daba por aviso. Mientras la
    # entrega no confirme, el siguiente tick la reintenta — un api-server que
    # arranca tarde no debe costar la única notificación del episodio.
    alert_delivered: bool = False

    def reset(self) -> None:
        self.consecutive_failures = 0
        self.last_attempt_at = 0.0
        self.alerted = False
        self.alert_delivered = False

    def exhausted(self, policy: BackoffPolicy) -> bool:
        """We've reached the cap and shouldn't keep retrying."""
        return self.consecutive_failures >= policy.max_attempts

    def ready_for_next_attempt(self, policy: BackoffPolicy, *, now: float | None = None) -> bool:
        """True iff enough time has passed since the previous attempt."""
        if self.consecutive_failures == 0:
            return True
        if self.exhausted(policy):
            return False
        elapsed = (now if now is not None else monotonic()) - self.last_attempt_at
        return elapsed >= policy.delay_for(self.consecutive_failures - 1)

    def record_attempt(self, *, now: float | None = None) -> None:
        self.consecutive_failures += 1
        self.last_attempt_at = now if now is not None else monotonic()
