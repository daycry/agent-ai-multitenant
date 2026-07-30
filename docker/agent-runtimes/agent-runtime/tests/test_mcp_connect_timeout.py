"""`MCPToolRunner.connect` survives a hanging server (F23 / audit C5).

A server whose `enter_async_context` never returns used to leave a half-open
session in the SHARED exit stack, so the global `close()` blocked ~10s on it. The
fix isolates each server in its own stack and, on a connect timeout, cancels the
coroutine and discards that stack out-of-band. This pins:

  * a timeout raises `MCPTransportError` for THAT server (not a generic hang);
  * the timed-out server is not registered (no `_sessions` / `_session_stacks`
    entry) so it cannot stall teardown;
  * `close()` after such a timeout returns promptly.

A fake `MCPClient` whose connect hangs in `__aenter__` stands in for the SDK —
no subprocess, no network.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from agent_runtime import mcp_tools
from agent_runtime.mcp_tools import MCPToolRunner
from shared_mcp import MCPServerConfig, MCPTransportError


class _HangingCM:
    """Async context manager whose enter never completes."""

    async def __aenter__(self) -> Any:
        await asyncio.sleep(60)
        return object()

    async def __aexit__(self, *_: object) -> bool:
        return False


class _HangingClient:
    @staticmethod
    # task_wf_12: `connect` pasa ahora el `httpx.Auth` del OAuth. El doble replica
    # la firma REAL a propósito: hacerlo tolerante (`**kwargs`) sería quitarle
    # justo la fragilidad que avisa de un cambio de contrato.
    def connect(
        config: MCPServerConfig,  # noqa: ARG004
        vault_resolver: Any = None,  # noqa: ARG004
        *,
        auth: Any = None,  # noqa: ARG004
    ) -> _HangingCM:
        return _HangingCM()


def _config(name: str, timeout_s: float) -> MCPServerConfig:
    return MCPServerConfig(
        name=name,
        transport="stdio",
        command="toy",
        args=(),
        timeout_s=timeout_s,
    )


def test_connect_timeout_raises_transport_error_and_does_not_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server stuck in connect raises MCPTransportError and leaves no state."""
    monkeypatch.setattr(mcp_tools, "MCPClient", _HangingClient)
    runner = MCPToolRunner()
    runner.start()
    try:
        with pytest.raises(MCPTransportError, match="connect timed out"):
            runner.connect(_config("stuck", timeout_s=0.2))

        # The timed-out server registered nothing — it can never stall teardown.
        assert runner.tools("stuck") == []
        with pytest.raises(KeyError):
            runner.call_tool("stuck", "anything", {})
    finally:
        runner.close()


def test_close_is_prompt_after_a_connect_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global teardown is not penalised by the hung server (F23): close()
    returns quickly because the half-open session lived in its own stack, not
    the global one."""
    monkeypatch.setattr(mcp_tools, "MCPClient", _HangingClient)
    runner = MCPToolRunner()
    runner.start()
    with pytest.raises(MCPTransportError):
        runner.connect(_config("stuck", timeout_s=0.2))

    started = time.monotonic()
    runner.close()
    elapsed = time.monotonic() - started

    # No 10s stack-aexit block: teardown is essentially instant.
    assert elapsed < 2.0
