"""Retry policy for the LLM layer — ONE place, applied by the consumers.

`prod-07 task_prod07_01` (hallazgo llm-2). Before this module there was no
retry anywhere in the assistant path and only a partial one in the
agent-runtime: a single HTTP 429, or a TCP reset in the middle of a
30-iteration agent run, killed the whole execution.

Why the policy lives here and not inside the providers
-----------------------------------------------------
ADR 0021 keeps the providers dumb on purpose: they translate one HTTP call
into typed errors and nothing else. Deciding *whether to pay for the same
prompt again* is a host decision — an interactive assistant chat and a
30-step autonomous run want different budgets — so the providers raise and
the **consumers** wrap their calls in :func:`with_retries`.

What is retried, and what is deliberately not
---------------------------------------------
* :class:`~shared_llm.exceptions.RateLimitError` — yes, honouring the
  provider's ``Retry-After`` hint when it sent one.
* Transport failures (reset connection, read timeout) — yes. They arrive as
  ``ProviderError(transient=True)`` from ``typed_transport_errors``.
* ``5xx`` / ``408`` / ``409`` / ``425`` / ``529`` — yes.
* :class:`~shared_llm.exceptions.AuthError` and any other 4xx — **no**. A
  revoked token or a malformed request cannot become valid by asking again;
  retrying only burns the token budget and delays the real error.
* A malformed 200 body — **no** by default. A gateway that breaks its own
  contract will break it identically on the retry, and we would pay twice.

The cost of a retry is real (riesgo 1 del plan): a timeout *after* the
provider already processed the prompt means the second attempt bills a second
time. That is why the budget is small (3 attempts) and why every retry is
reported through ``on_retry`` — visible spend, not hidden spend.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

from shared_llm.exceptions import AuthError, LLMError, ProviderError, RateLimitError

T = TypeVar("T")

#: Total attempts (the first call included), so 3 = 1 call + 2 retries.
DEFAULT_ATTEMPTS = 3
#: Nominal wait before the first retry, doubled on each subsequent one.
DEFAULT_BASE_DELAY_S = 1.0
#: Ceiling for any single wait, including a provider's ``Retry-After`` hint.
DEFAULT_MAX_DELAY_S = 30.0

# Non-5xx statuses that still mean "try again later".
#   408 Request Timeout — the upstream gave up waiting for us.
#   409 Conflict        — some gateways use it for transient lock contention.
#   425 Too Early       — replay protection; the retry is the fix.
#   429 Too Many        — also arrives as RateLimitError, kept for completeness.
_RETRYABLE_NON_5XX = frozenset({408, 409, 425, 429})


@dataclass(frozen=True, slots=True)
class RetryEvent:
    """One retry about to happen — the payload consumers log.

    ``attempt`` is 1-based and counts the attempt that just FAILED, so
    ``attempt=1, attempts=3`` reads as "the 1st of 3 failed, retrying".
    """

    provider: str
    attempt: int
    attempts: int
    delay: float
    error: BaseException

    def as_log_extra(self) -> dict[str, object]:
        """The structured fields for a log line (provider/intento/causa)."""
        return {
            "llm_provider": self.provider,
            "llm_retry_attempt": self.attempt,
            "llm_retry_attempts": self.attempts,
            "llm_retry_delay_s": round(self.delay, 3),
            "llm_retry_cause": type(self.error).__name__,
        }


def is_transient(exc: BaseException) -> bool:
    """Whether ``exc`` is worth another attempt.

    See the module docstring for the reasoning behind each branch. An error
    type this layer does not own (a ``ValueError`` from caller code, say) is
    never retried — we only re-run calls whose failure we understand.
    """
    if isinstance(exc, AuthError):
        return False
    # A rate-limit, or a wall-clock timeout around the call (asyncio.TimeoutError
    # IS TimeoutError since 3.11). NOTE: the agent-runtime's ProviderTimeout
    # subclasses LLMError, not TimeoutError, so that consumer adds it on top —
    # see `agent_runtime.providers._is_transient`.
    if isinstance(exc, RateLimitError | TimeoutError):
        return True
    if isinstance(exc, ProviderError):
        if exc.transient is not None:
            return exc.transient
        code = exc.status_code
        return code is not None and (code in _RETRYABLE_NON_5XX or 500 <= code < 600)
    return False


def retry_after_seconds(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header into seconds (``None`` when unusable).

    The header comes in two RFC-9110 flavours: delta-seconds (``"7"``) and an
    HTTP-date (``"Wed, 21 Oct 2099 07:28:00 GMT"``). A date already in the past
    yields ``0.0`` — "retry now" — and garbage yields ``None`` so the caller
    falls back to backoff instead of crashing while building an error.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(tz=UTC)).total_seconds())


def retry_delay(
    exc: BaseException,
    *,
    attempt: int,
    base_delay: float = DEFAULT_BASE_DELAY_S,
    max_delay: float = DEFAULT_MAX_DELAY_S,
    jitter: Callable[[], float] | None = None,
) -> float:
    """Seconds to wait before attempt ``attempt + 1`` (0-based ``attempt``).

    A provider hint (``Retry-After``) WINS over our own backoff — it is the
    only party that knows when its window reopens. The hint is still capped by
    ``max_delay``: a 10-minute hint would stall an agent run longer than the
    call budget itself, so we cap and accept that the retry may arrive early
    and burn one attempt. Bounded lateness beats an unbounded stall.

    Without a hint: exponential (``base * 2**attempt``), capped, then jittered
    into ``[delay/2, delay]`` (equal jitter) so N agents that hit the same
    rate-limit do not all come back at the same instant. ``jitter`` is
    injectable purely so tests can pin it.
    """
    hinted: object = getattr(exc, "retry_after", None)
    if isinstance(hinted, int | float) and not isinstance(hinted, bool) and hinted >= 0:
        return min(float(hinted), max_delay)
    nominal: float = min(base_delay * (2**attempt), max_delay)
    spread: float = jitter() if jitter is not None else random.random()  # backoff, no cripto
    return nominal * (0.5 + 0.5 * spread)


async def with_retries[T](
    call: Callable[[], Awaitable[T]],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_S,
    max_delay: float = DEFAULT_MAX_DELAY_S,
    provider: str = "",
    jitter: Callable[[], float] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    on_retry: Callable[[RetryEvent], None] | None = None,
    is_retryable: Callable[[BaseException], bool] = is_transient,
) -> T:
    """Await ``call()`` retrying transient failures with backoff + jitter.

    ``call`` is a **factory**, not a coroutine: each attempt builds a fresh
    awaitable, because a coroutine cannot be awaited twice (the second attempt
    would raise ``RuntimeError: cannot reuse already awaited coroutine``).

    When the budget is exhausted the LAST typed error is re-raised untouched —
    the caller still sees a ``RateLimitError``/``ProviderError`` and decides how
    to surface it. Nothing is ever swallowed into a fake success.

    ``asyncio.CancelledError`` (a ``BaseException``) passes straight through:
    shutdown is not a provider failure.
    """
    budget = max(1, attempts)
    sleeper = sleep or asyncio.sleep
    for index in range(budget):
        try:
            return await call()
        except (LLMError, TimeoutError) as exc:
            if index == budget - 1 or not is_retryable(exc):
                raise
            delay = retry_delay(
                exc,
                attempt=index,
                base_delay=base_delay,
                max_delay=max_delay,
                jitter=jitter,
            )
            if on_retry is not None:
                on_retry(
                    RetryEvent(
                        provider=provider,
                        attempt=index + 1,
                        attempts=budget,
                        delay=delay,
                        error=exc,
                    )
                )
            await sleeper(delay)
    # Unreachable: every iteration either returns or raises.
    raise AssertionError("with_retries exhausted its loop without returning")  # pragma: no cover


__all__ = [
    "DEFAULT_ATTEMPTS",
    "DEFAULT_BASE_DELAY_S",
    "DEFAULT_MAX_DELAY_S",
    "RetryEvent",
    "is_transient",
    "retry_after_seconds",
    "retry_delay",
    "with_retries",
]
