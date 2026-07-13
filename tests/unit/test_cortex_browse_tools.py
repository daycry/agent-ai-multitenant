"""ADR 0080 — las tools de navegador del córtex y su doble gate.

Dos gates, ambos deny-by-default (espejo exacto del gate web del ADR 0067):

  1. **Kill-switch de plataforma** (`cortex.browser_enabled`, OFF por defecto):
     apagado, las tools NO aparecen en los schemas Y el despacho las trata como
     desconocidas — un modelo hostil no puede invocarlas aunque se las invente.
  2. **Aprobación humana POR SESIÓN**: encendido el kill-switch, `browse_request`
     NO navega: registra la petición y devuelve "pendiente de aprobación". El
     navegador solo se lanza cuando el owner aprueba ese guion concreto.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from api_server.cortex.tools import (
    CortexToolContext,
    UnknownCortexToolError,
    cortex_enabled_tool_names,
    cortex_tool_schemas,
    run_cortex_tool,
)

pytestmark = pytest.mark.unit

_BROWSE_TOOLS = ("browse_request", "browse_result")
_STEPS = [
    {"action": "goto", "url": "https://example.com"},
    {"action": "extract", "selector": "main"},
]


class _FakeBrowseStore:
    """Doble del repo: registra lo que se le pide sin tocar BD."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.sessions: dict[str, dict[str, Any]] = {}

    async def create_pending(self, **kw: Any) -> dict[str, Any]:
        row = {"id": str(uuid4()), "status": "pending_approval", **kw}
        self.created.append(row)
        self.sessions[row["id"]] = row
        return row

    async def get(self, session_id: str) -> dict[str, Any] | None:
        return self.sessions.get(session_id)


def _ctx(*, browser_enabled: bool, store: _FakeBrowseStore | None = None) -> CortexToolContext:
    return CortexToolContext(
        session=None,  # type: ignore[arg-type] — el store fake evita tocar BD
        owner_user_id=uuid4(),
        tenant_id=uuid4(),
        browser_enabled=browser_enabled,
        browse_store=store or _FakeBrowseStore(),
    )


def test_the_browser_tools_are_absent_while_the_kill_switch_is_off() -> None:
    names = cortex_enabled_tool_names(web_enabled=True, browser_enabled=False)
    assert not [n for n in names if n in _BROWSE_TOOLS]
    schema_names = {s["name"] for s in cortex_tool_schemas(names)}
    assert not schema_names & set(_BROWSE_TOOLS)


def test_the_browser_tools_appear_only_with_the_kill_switch_on() -> None:
    names = cortex_enabled_tool_names(web_enabled=True, browser_enabled=True)
    assert set(_BROWSE_TOOLS) <= set(names)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", _BROWSE_TOOLS)
async def test_dispatch_refuses_the_browser_tools_with_the_kill_switch_off(tool: str) -> None:
    """Defensa en profundidad: aunque el modelo se invente el nombre, no navega."""
    with pytest.raises(UnknownCortexToolError):
        await run_cortex_tool(tool, _ctx(browser_enabled=False), {"goal": "x", "steps": _STEPS})


@pytest.mark.asyncio
async def test_browse_request_parks_the_session_for_a_human_and_navigates_nothing() -> None:
    store = _FakeBrowseStore()
    out = await run_cortex_tool(
        "browse_request",
        _ctx(browser_enabled=True, store=store),
        {"goal": "leer el panel", "steps": _STEPS},
    )
    assert out["status"] == "pending_approval"
    assert out["session_id"] == store.created[0]["id"]
    assert "aprob" in out["message"].lower(), "la tool le dice al modelo que espera a un humano"
    assert len(store.created) == 1


@pytest.mark.asyncio
async def test_an_inadmissible_script_is_refused_before_bothering_the_owner() -> None:
    store = _FakeBrowseStore()
    out = await run_cortex_tool(
        "browse_request",
        _ctx(browser_enabled=True, store=store),
        {"goal": "ssrf", "steps": [{"action": "goto", "url": "http://169.254.169.254/"}]},
    )
    assert out["status"] == "rejected"
    assert store.created == [], "no se crea sesión: al owner no se le pone delante basura"


@pytest.mark.asyncio
async def test_browse_result_reports_the_state_without_leaking_other_owners() -> None:
    store = _FakeBrowseStore()
    ctx = _ctx(browser_enabled=True, store=store)
    created = await run_cortex_tool("browse_request", ctx, {"goal": "leer", "steps": _STEPS})
    out = await run_cortex_tool("browse_result", ctx, {"session_id": created["session_id"]})
    assert out["status"] == "pending_approval"

    missing = await run_cortex_tool("browse_result", ctx, {"session_id": str(uuid4())})
    assert missing["status"] == "not_found"
