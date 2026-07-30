"""El MCPToolRunner con transporte HTTP real (prueba MCP Atlassian, 2026-07-18).

Los tests del adapter cubrían el runner solo por stdio; con
``streamable_http`` (el transporte de los MCP remotos tipo Atlassian) el
runner reventaba con ``RuntimeError: Attempted to exit cancel scope in a
different task than it was entered in``: los context managers anyio del
transporte se ENTRABAN en la task de ``connect()`` y se SALÍAN en la de
``close()`` — anyio exige la misma task. Cazado en vivo: el run del agente
nunca conectó el server (``mcp.server_failed``) y el caso Confluence/Jira
moría en silencio.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from agent_runtime.mcp_tools import MCPToolRunner, register_mcp_server
from agent_runtime.tools import ToolRegistry
from shared_mcp import MCPServerConfig

pytestmark = pytest.mark.integration

_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def http_server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, str(_TOY_SERVER), "--transport", "streamable_http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                # El endpoint MCP responde (aunque sea 4xx al GET pelado).
                httpx.get(url, timeout=1.0)
                break
            except Exception:
                time.sleep(0.2)
        yield url
    finally:
        proc.kill()
        proc.wait(timeout=10)


def test_runner_connect_call_close_over_streamable_http(http_server: str) -> None:
    """Ciclo COMPLETO del runner por HTTP: connect → tools → call → close,
    sin RuntimeError de cancel scope y con la llamada llegando al server."""
    config = MCPServerConfig(
        name="toy",
        transport="streamable_http",
        url=http_server,
        timeout_s=15.0,
    )
    runner = MCPToolRunner()
    runner.start()
    try:
        tools = runner.connect(config)
        assert {t.name for t in tools} >= {"add"}
        registry = ToolRegistry()
        names = register_mcp_server(registry, runner, "toy", tools)
        assert "toy.add" in names
        result = registry.call("toy.add", {"a": 19, "b": 23})
        assert result.ok is True, result.error
        assert "42" in str(result.output)
    finally:
        runner.close()


def test_runner_close_with_live_http_session_is_clean(http_server: str, monkeypatch) -> None:
    """El close (CM exit) tras un connect HTTP no puede reventar por salir el
    cancel scope en otra task — era el modo de fallo original.

    OJO: ``close()`` TRAGA las excepciones de teardown con
    ``logger.exception`` (una sesión que no cierra no debe tumbar el run), así
    que afirmar "no lanzó" no basta — hay que afirmar que el teardown no
    REGISTRÓ ningún error. Logger monkeypatcheado, no caplog (gotcha:
    caplog es frágil ante el orden de tests / logging.disable)."""
    import agent_runtime.mcp_tools as mcp_tools_mod

    teardown_errors: list[str] = []

    class _SpyLogger:
        def __getattr__(self, attr: str):
            def _log(msg: str, *args: object, **_: object) -> None:
                if attr in ("exception", "error"):
                    teardown_errors.append(msg % args if args else msg)

            return _log

    monkeypatch.setattr(mcp_tools_mod, "logger", _SpyLogger())

    config = MCPServerConfig(
        name="toy",
        transport="streamable_http",
        url=http_server,
        timeout_s=15.0,
    )
    with MCPToolRunner() as runner:
        runner.connect(config)
        # sin llamadas: el close inmediato con la sesión viva era el crash.
    assert not teardown_errors, f"teardown registró errores: {teardown_errors}"
