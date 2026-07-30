"""Retry policy of the LLM layer (prod-07 task_prod07_01).

Why this exists: before it, a single 429 or a dropped TCP connection killed a
30-iteration agent run (hallazgo llm-2). The policy lives in ONE place
(``shared_llm.retry``) and the *consumers* apply it — the providers keep
raising typed errors and never decide the policy themselves (ADR 0021).

The three questions these tests answer, and that no other test in the repo
answered before:

  1. **What is worth retrying?** A rate-limit and a transport blip are; an
     ``AuthError`` and a 4xx are NOT — retrying those re-burns the token
     budget for a failure that cannot change.
  2. **How long do we wait?** Exponential backoff + jitter, and when the
     provider tells us (``Retry-After``) we obey it instead of guessing.
  3. **What happens when the budget runs out?** The LAST typed error is
     re-raised, never swallowed into a fake success.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError, RateLimitError
from shared_llm.providers._openai_compat import check_status
from shared_llm.retry import (
    RetryEvent,
    is_transient,
    retry_delay,
    with_retries,
)


# ---------------------------------------------------------------------------
# 1. Classification — what is transient
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (RateLimitError("429"), True),
        (AuthError("401"), False),
        (ProviderError("boom", status_code=500), True),
        (ProviderError("boom", status_code=502), True),
        (ProviderError("boom", status_code=503), True),
        (ProviderError("overloaded", status_code=529), True),
        (ProviderError("timeout", status_code=408), True),
        (ProviderError("bad request", status_code=400), False),
        (ProviderError("forbidden", status_code=403), False),
        (ProviderError("not found", status_code=404), False),
        (ProviderError("unprocessable", status_code=422), False),
        # A malformed 200 body: present but undecodable. NOT transient by
        # default — a broken gateway contract does not fix itself, and a
        # retry would pay for the same garbage twice.
        (ProviderError("respuesta sin choices", raw={"x": 1}), False),
        # ...unless the raiser explicitly says it IS transient (the transport
        # wrapper does: a dropped socket has no status code but IS retryable).
        (ProviderError("transport error", transient=True), True),
        (ProviderError("5xx but pinned permanent", status_code=500, transient=False), False),
        (TimeoutError("wall clock"), True),
        (ValueError("not an LLM error at all"), False),
    ],
)
def test_is_transient_classification(exc: BaseException, expected: bool) -> None:
    assert is_transient(exc) is expected


def test_transport_wrapper_marks_its_provider_error_transient() -> None:
    """The seam that turns httpx/OS errors into ProviderError must mark them
    transient — otherwise a network blip (no status code) would look permanent
    and the run would die on the first hiccup, which is llm-2 verbatim."""
    from shared_llm.providers._openai_compat import typed_transport_errors

    async def _run() -> BaseException:
        try:
            async with typed_transport_errors(provider="ollama"):
                raise httpx.ConnectError("connection reset")
        except ProviderError as exc:
            return exc
        raise AssertionError("expected a ProviderError")

    exc = asyncio.run(_run())
    assert is_transient(exc) is True


# ---------------------------------------------------------------------------
# 2. Retry-After — obey the provider instead of guessing
# ---------------------------------------------------------------------------
def test_check_status_attaches_retry_after_seconds() -> None:
    resp = httpx.Response(429, text="slow down", headers={"Retry-After": "7"})
    with pytest.raises(RateLimitError) as info:
        check_status(resp, provider="ollama")
    assert info.value.retry_after == 7.0


def test_check_status_attaches_retry_after_http_date() -> None:
    """``Retry-After`` may be an HTTP-date instead of a delta-seconds int."""
    resp = httpx.Response(
        429, text="slow", headers={"Retry-After": "Wed, 21 Oct 2099 07:28:00 GMT"}
    )
    with pytest.raises(RateLimitError) as info:
        check_status(resp, provider="ollama")
    assert info.value.retry_after is not None
    assert info.value.retry_after > 0


def test_check_status_retry_after_absent_is_none() -> None:
    resp = httpx.Response(429, text="slow down")
    with pytest.raises(RateLimitError) as info:
        check_status(resp, provider="ollama")
    assert info.value.retry_after is None


def test_check_status_attaches_retry_after_to_503() -> None:
    """A 503 with Retry-After is the other case where the provider tells us
    exactly how long to wait; the delay must come from the header, not backoff."""
    resp = httpx.Response(503, text="unavailable", headers={"Retry-After": "3"})
    with pytest.raises(ProviderError) as info:
        check_status(resp, provider="azure_foundry_apim")
    assert info.value.retry_after == 3.0


def test_check_status_ignores_garbage_retry_after() -> None:
    """A malformed header must not raise while building the error."""
    resp = httpx.Response(429, text="slow", headers={"Retry-After": "soon-ish"})
    with pytest.raises(RateLimitError) as info:
        check_status(resp, provider="ollama")
    assert info.value.retry_after is None


# ---------------------------------------------------------------------------
# 3. Delay computation — exponential + jitter, capped, Retry-After wins
# ---------------------------------------------------------------------------
def test_retry_delay_is_exponential() -> None:
    # jitter pinned to its maximum so the growth is checkable exactly.
    delays = [
        retry_delay(ProviderError("x", status_code=500), attempt=i, base_delay=1.0, jitter=_max)
        for i in range(3)
    ]
    assert delays == [1.0, 2.0, 4.0]


def test_retry_delay_applies_jitter() -> None:
    """With jitter at its minimum the wait is HALF the nominal backoff — the
    spread is what stops N parallel agents from re-hitting a 429 in lockstep."""
    low = retry_delay(RateLimitError("x"), attempt=1, base_delay=1.0, jitter=_min)
    high = retry_delay(RateLimitError("x"), attempt=1, base_delay=1.0, jitter=_max)
    assert (low, high) == (1.0, 2.0)


def test_retry_delay_is_capped() -> None:
    delay = retry_delay(
        ProviderError("x", status_code=500), attempt=10, base_delay=1.0, max_delay=5.0, jitter=_max
    )
    assert delay == 5.0


def test_retry_delay_honours_retry_after() -> None:
    exc = RateLimitError("429", retry_after=9.0)
    # attempt=0 would nominally be 1s; the provider's hint wins.
    assert retry_delay(exc, attempt=0, base_delay=1.0, max_delay=30.0, jitter=_max) == 9.0


def test_retry_delay_caps_retry_after_too() -> None:
    """An absurd hint must not stall the run past the cap (documented tradeoff:
    we may then retry too early and burn an attempt — bounded, not unbounded)."""
    exc = RateLimitError("429", retry_after=600.0)
    assert retry_delay(exc, attempt=0, max_delay=30.0, jitter=_max) == 30.0


# ---------------------------------------------------------------------------
# 4. with_retries — the helper the consumers call
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_with_retries_returns_first_success_without_sleeping() -> None:
    calls: list[int] = []
    slept: list[float] = []

    async def _ok() -> str:
        calls.append(1)
        return "ok"

    got = await with_retries(_ok, sleep=_recorder(slept))
    assert got == "ok"
    assert len(calls) == 1
    assert slept == []


@pytest.mark.asyncio
async def test_with_retries_retries_rate_limit_then_succeeds() -> None:
    attempts: list[int] = []
    slept: list[float] = []

    async def _flaky() -> str:
        attempts.append(len(attempts))
        if len(attempts) == 1:
            raise RateLimitError("429")
        return "ok"

    got = await with_retries(_flaky, base_delay=1.0, jitter=_max, sleep=_recorder(slept))
    assert got == "ok"
    assert len(attempts) == 2
    assert slept == [1.0]


@pytest.mark.asyncio
async def test_with_retries_calls_the_factory_fresh_each_attempt() -> None:
    """A coroutine cannot be awaited twice: ``with_retries`` takes a FACTORY.
    If it ever awaited the same coroutine object the 2nd attempt would blow up
    with ``RuntimeError: cannot reuse already awaited coroutine``."""
    made: list[int] = []

    async def _always_429() -> str:
        raise RateLimitError("429")

    def _factory() -> Any:
        made.append(1)
        return _always_429()

    with pytest.raises(RateLimitError):
        await with_retries(_factory, attempts=3, base_delay=0.0, sleep=_noop)
    assert len(made) == 3


@pytest.mark.asyncio
async def test_with_retries_does_not_retry_auth_error() -> None:
    calls: list[int] = []

    async def _auth() -> str:
        calls.append(1)
        raise AuthError("revoked token")

    with pytest.raises(AuthError):
        await with_retries(_auth, attempts=3, base_delay=0.0, sleep=_noop)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_with_retries_does_not_retry_4xx() -> None:
    calls: list[int] = []

    async def _bad() -> str:
        calls.append(1)
        raise ProviderError("bad request", status_code=400)

    with pytest.raises(ProviderError):
        await with_retries(_bad, attempts=3, base_delay=0.0, sleep=_noop)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_with_retries_reraises_the_last_error_after_exhausting() -> None:
    async def _always() -> str:
        raise ProviderError("upstream down", status_code=503)

    with pytest.raises(ProviderError) as info:
        await with_retries(_always, attempts=2, base_delay=0.0, sleep=_noop)
    assert info.value.status_code == 503


@pytest.mark.asyncio
async def test_with_retries_reports_every_retry() -> None:
    """Each retry must be observable: provider, attempt, delay and cause. A
    silent retry hides both a flaky provider and the duplicated token spend."""
    seen: list[RetryEvent] = []

    async def _flaky() -> str:
        if len(seen) < 2:
            raise RateLimitError("429", retry_after=2.0)
        return "ok"

    got = await with_retries(
        _flaky,
        attempts=4,
        provider="github_copilot",
        on_retry=seen.append,
        sleep=_noop,
    )
    assert got == "ok"
    assert [(e.attempt, e.attempts, e.delay) for e in seen] == [(1, 4, 2.0), (2, 4, 2.0)]
    assert {e.provider for e in seen} == {"github_copilot"}
    assert all(isinstance(e.error, RateLimitError) for e in seen)


@pytest.mark.asyncio
async def test_with_retries_never_sleeps_after_the_last_attempt() -> None:
    """Sleeping after the final failure only delays the error report."""
    slept: list[float] = []

    async def _always() -> str:
        raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        await with_retries(_always, attempts=3, base_delay=1.0, jitter=_max, sleep=_recorder(slept))
    assert slept == [1.0, 2.0]  # 3 attempts → 2 waits


@pytest.mark.asyncio
async def test_with_retries_single_attempt_never_retries() -> None:
    calls: list[int] = []

    async def _always() -> str:
        calls.append(1)
        raise RateLimitError("429")

    with pytest.raises(RateLimitError):
        await with_retries(_always, attempts=1, sleep=_noop)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_with_retries_lets_cancellation_through() -> None:
    """``CancelledError`` is control flow, not a provider failure: retrying it
    would fight the caller that is trying to shut the task down."""
    calls: list[int] = []

    async def _cancelled() -> str:
        calls.append(1)
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await with_retries(_cancelled, attempts=3, sleep=_noop)
    assert len(calls) == 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _max() -> float:
    return 1.0


def _min() -> float:
    return 0.0


async def _noop(_delay: float) -> None:
    return None


def _recorder(sink: list[float]) -> Any:
    async def _sleep(delay: float) -> None:
        sink.append(delay)

    return _sleep
