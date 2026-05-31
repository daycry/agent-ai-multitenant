"""Per-agent rate-limit guardrail (Plan 11, Phase B — task_11_09).

Registers the ``rate_per_agent`` guardrail type. It triggers when a single
agent makes more than a configured number of calls within a sliding time
window, so the host can throttle a runaway / looping agent.

Hooks
-----
Most useful at ``pre_llm`` and ``pre_tool`` (rate-limit *before* the call
happens). It works at any hook; each ``check`` counts as one call for the
agent identified in :attr:`GuardrailContext.metadata`.

State (injectable — the engine itself is stateless)
---------------------------------------------------
A rate limit is inherently stateful, but the guardrails engine and its
:class:`GuardrailContext` are deliberately pure. The call history therefore
lives behind an injectable :class:`RateStore` seam:

  * the default :class:`InMemoryRateStore` keeps a per-agent deque of
    timestamps (sliding window) in-process — enough for a single worker and
    for tests;
  * the host can inject a shared / distributed store (e.g. Redis-backed) via
    the guardrail's ``store`` config key for cross-process limits, without the
    engine knowing.

A ``clock`` is also injectable so tests are deterministic (no real sleeping).

No heavy dependency — stdlib only.

The detection is side-effect-free *to the engine* (it returns a result; the
engine applies the action), but it does record the call in the store: a rate
limit must count calls to work. The suggested action is configurable,
defaulting to ``block`` (over the limit, stop the call).
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from shared_guardrails.checks._common import coerce_action, coerce_severity
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult


@runtime_checkable
class RateStore(Protocol):
    """Records a call for an agent and reports the count within the window.

    ``record_and_count`` registers a call at ``now`` for ``key`` and returns
    how many calls fall within ``[now - window, now]`` (inclusive). The store
    owns its own concurrency control.
    """

    def record_and_count(self, key: str, now: float, window: float) -> int: ...


class InMemoryRateStore:
    """Per-key sliding-window timestamp store (in-process, thread-safe)."""

    def __init__(self) -> None:
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def record_and_count(self, key: str, now: float, window: float) -> int:
        cutoff = now - window
        with self._lock:
            bucket = self._calls[key]
            bucket.append(now)
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            return len(bucket)


class RatePerAgentGuardrail:
    """Triggers when an agent exceeds a per-window call-rate threshold.

    Config:
      - ``max_calls``        int    — required, > 0. Calls allowed within the
        window before the next call trips the limit.
      - ``window_seconds``   number — required, > 0. The sliding window size.
      - ``agent_key``        str    — metadata key identifying the agent.
        Default ``"agent_id"``. The value is the rate-limit bucket key; when
        absent the call is bucketed under ``"__unknown__"``.
      - ``store``            (seam) inject a :class:`RateStore`. Default is a
        process-local :class:`InMemoryRateStore`.
      - ``clock``            (seam) inject a ``() -> float`` clock for tests.
        Default ``time.monotonic``.
      - ``severity``         str    — default ``medium``.
      - ``suggested_action`` str    — override the default action. When unset
        the guardrail suggests ``block``.

    The result ``payload`` carries the ``agent`` key, the observed ``count``
    in the window, the configured ``max_calls`` and ``window_seconds``.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        max_calls = config.get("max_calls")
        if not isinstance(max_calls, int) or isinstance(max_calls, bool) or max_calls <= 0:
            raise GuardrailConfigError(
                "rate_per_agent guardrail requires a positive integer 'max_calls'."
            )
        window = config.get("window_seconds")
        if isinstance(window, bool) or not isinstance(window, int | float) or window <= 0:
            raise GuardrailConfigError(
                "rate_per_agent guardrail requires a positive numeric 'window_seconds'."
            )
        self._max_calls = max_calls
        self._window = float(window)
        self._agent_key = str(config.get("agent_key", "agent_id"))

        store = config.get("store")
        if store is not None and not isinstance(store, RateStore):
            raise GuardrailConfigError(
                "rate_per_agent guardrail 'store' must implement the RateStore protocol."
            )
        self._store: RateStore = store or InMemoryRateStore()

        clock = config.get("clock")
        if clock is not None and not callable(clock):
            raise GuardrailConfigError("rate_per_agent guardrail 'clock' must be callable.")
        self._clock: Callable[[], float] = clock or time.monotonic

        self._severity = coerce_severity(config.get("severity"))
        self._suggested_override = coerce_action(config.get("suggested_action"))

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.BLOCK

    def check(self, context: GuardrailContext) -> GuardrailResult:
        agent = context.metadata.get(self._agent_key)
        key = str(agent) if agent not in (None, "") else "__unknown__"
        now = float(self._clock())
        count = self._store.record_and_count(key, now, self._window)

        if count <= self._max_calls:
            return GuardrailResult(triggered=False)

        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=(
                f"Agent {key!r} made {count} calls in the last {self._window:g}s, "
                f"over the limit of {self._max_calls}."
            ),
            suggested_action=self._suggested_action(),
            payload={
                "agent": key,
                "count": count,
                "max_calls": self._max_calls,
                "window_seconds": self._window,
            },
        )


@register_guardrail("rate_per_agent")
def _build_rate_per_agent(config: dict[str, Any]) -> RatePerAgentGuardrail:
    return RatePerAgentGuardrail(config)


__all__ = ["InMemoryRateStore", "RatePerAgentGuardrail", "RateStore"]
