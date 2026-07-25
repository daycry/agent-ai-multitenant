"""El wiring MCP deja rastro en el steps_log del run (prueba Atlassian 2026-07-18).

``_wire_mcp_servers`` emitía ``mcp.server_connected/failed`` como eventos
efímeros de Redis, pero el worker solo persiste en ``executions.steps_log``
los eventos ``{"event": "step", ...}`` — así que un servidor MCP que fallaba
al conectar no dejaba NINGÚN rastro en el visor de runs y el diagnóstico
exigió repros manuales en contenedor. Ahora cada servidor cableado (o
fallido) emite además su step ``mcp_wire``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent_runtime.__main__ import _wire_mcp_servers
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration

_TOY_SERVER = Path(__file__).resolve().parent / "_toy_mcp_server.py"


@pytest.fixture()
def emitted(monkeypatch) -> list[dict]:
    import agent_runtime.__main__ as main_mod

    events: list[dict] = []
    monkeypatch.setattr(main_mod, "_emit", events.append)
    return events


def _wire_steps(events: list[dict]) -> list[dict]:
    return [
        e["step"]
        for e in events
        if e.get("event") == "step" and e.get("step", {}).get("kind") == "mcp_wire"
    ]


def test_connected_server_emits_an_ok_wire_step(emitted: list[dict]) -> None:
    spec = {
        "mcp_servers": [
            {
                "name": "toy",
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(_TOY_SERVER), "--transport", "stdio"],
                "timeout_s": 15.0,
            }
        ]
    }
    wiring = _wire_mcp_servers(ToolRegistry(), spec)
    assert wiring.runner is not None
    try:
        steps = _wire_steps(emitted)
        assert len(steps) == 1
        assert steps[0]["status"] == "ok"
        assert steps[0]["server"] == "toy"
        assert "toy.add" in steps[0]["tools"]
        assert wiring.failures == []
    finally:
        wiring.runner.close()


def test_failed_server_emits_an_error_wire_step(emitted: list[dict]) -> None:
    spec = {
        "mcp_servers": [
            {
                "name": "muerto",
                "transport": "streamable_http",
                "url": "http://127.0.0.1:1/mcp",
                "timeout_s": 3.0,
            }
        ]
    }
    wiring = _wire_mcp_servers(ToolRegistry(), spec)
    assert wiring.runner is not None
    try:
        steps = _wire_steps(emitted)
        assert len(steps) == 1
        assert steps[0]["status"] == "error"
        assert steps[0]["server"] == "muerto"
        assert "muerto" not in [s.get("server") for s in steps if s["status"] == "ok"]
        # el evento efímero de Redis se mantiene (compat con consumidores).
        assert any(e.get("event") == "mcp.server_failed" for e in emitted)
        # task_wf_14: y además el fallo vuelve al caller para que entre en el
        # preámbulo — hasta ahora el operador lo veía y el AGENTE no.
        assert [f["server"] for f in wiring.failures] == ["muerto"]
        assert "muerto" in wiring.failures[0]["error"] or wiring.failures[0]["error"]
    finally:
        wiring.runner.close()
