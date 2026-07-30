"""`InternalAgentAPI.ensure_reachable` retries transient failures (F22 / audit
C5).

A single GET to `/healthz` used to tear down the whole run on any
`httpx.HTTPError` — a momentary connect race while the api-server's network
alias settled was enough. This pins the bounded retry: a hiccup that clears
within the attempt budget succeeds; only an API that fails EVERY attempt raises
`InternalAPIUnreachableError`.

Self-contained — a fake httpx client stands in for the network.
"""

from __future__ import annotations

import httpx
import pytest
from agent_runtime.internal_api import InternalAgentAPI, InternalAPIUnreachableError


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FlakyClient:
    """Fails its first `fail_times` GETs, then answers 200."""

    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def get(self, url: str) -> _FakeResponse:  # noqa: ARG002 - mirrors httpx.Client.get
        self.calls += 1
        if self.calls <= self.fail_times:
            raise httpx.ConnectError("transient boom")
        return _FakeResponse()


def _api(client: _FlakyClient) -> InternalAgentAPI:
    return InternalAgentAPI(
        base_url="http://api-server:8000",
        bearer_token="tok",
        client=client,  # type: ignore[arg-type]
    )


def test_transient_failure_then_success_does_not_raise() -> None:
    """Two hiccups followed by a 200 within a 3-attempt budget = reachable."""
    client = _FlakyClient(fail_times=2)
    api = _api(client)

    api.ensure_reachable(attempts=3, backoff_s=0.0)

    assert client.calls == 3  # two failures retried, third succeeded


def test_persistent_failure_raises_after_exhausting_attempts() -> None:
    """An API down for every attempt raises — and only after all retries."""
    client = _FlakyClient(fail_times=99)
    api = _api(client)

    with pytest.raises(InternalAPIUnreachableError) as excinfo:
        api.ensure_reachable(attempts=3, backoff_s=0.0)

    assert client.calls == 3
    assert "after 3 attempt(s)" in str(excinfo.value)


def test_first_attempt_success_makes_only_one_call() -> None:
    """A healthy API is probed exactly once — retries are failure-only."""
    client = _FlakyClient(fail_times=0)
    api = _api(client)

    api.ensure_reachable(attempts=3, backoff_s=0.0)

    assert client.calls == 1
