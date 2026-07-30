"""El boot cablea las categorías del ToolSpec al gate de aprobación (T2, g6).

`tests/unit/test_mcp_tool_approval_category.py` (suite raíz) pinea la derivación
pura y el merge. Este fichero pinea la única pregunta que aquellos no pueden
contestar: **que `run_task` lo use**. La mitad de los hallazgos de la auditoría
que abrió esto eran motores correctos a los que nadie llamaba — el propio g1 era
literalmente eso (`GuardrailPipeline` testeado y jamás instanciado en un run).

Sin DB/Redis/Docker: modelo `scripted` que decide llamar a la tool MCP.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_runtime.__main__ import run_task
from shared_domain.approval_categories import APPROVAL_CATEGORIES

# Preset «Cliente Externo»: las 13 categorías en human_required. Se escribe
# explícito en vez de importar el seed del api-server porque el runtime está
# sandboxeado y no puede importarlo — la misma razón por la que las categorías
# viven en shared-domain.
_CUSTOMER_EXTERNAL = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "human_required")}
_SANDBOX = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "auto")}

_MCP_SPEC = {
    "name": "docling.convert",
    "implementation_type": "mcp_tool",
    "config": {},
    "input_schema": {"type": "object"},
    "description": "convert",
    "approval_category": "external_http_post",
}


def _spec(*, policy: dict[str, Any] | None, tool_specs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "task": {"id": "t-1", "title": "Convertir el PDF", "description": "usa docling"},
        "approval_policy": policy,
        "tool_specs": tool_specs,
        "model": {
            "kind": "scripted",
            "decisions": [{"kind": "act", "tool": "docling.convert", "tool_args": {"p": "x.pdf"}}],
            "reviews": [{"passed": True}],
        },
    }


def _events(spec: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    run_task(spec)
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _approval_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e["step"]
        for e in events
        if e.get("event") == "step" and e["step"].get("status") == "awaiting_human_approval"
    ]


def test_mcp_tool_is_parked_under_customer_external(capsys: pytest.CaptureFixture[str]) -> None:
    steps = _approval_steps(
        _events(_spec(policy=_CUSTOMER_EXTERNAL, tool_specs=[_MCP_SPEC]), capsys)
    )
    assert steps, "el gate no paró la tool MCP: la categoría del spec no llegó al ApprovalGate"
    assert "docling.convert" in steps[0]["summary"]
    assert "external_http_post" in steps[0]["summary"]


def test_the_regression_this_closes(capsys: pytest.CaptureFixture[str]) -> None:
    """Un spec SIN `approval_category` es el estado anterior a T2: no se para.

    Es el control negativo del test de arriba — si ambos pasaran igual, el
    primero no estaría demostrando nada.
    """
    blind = {k: v for k, v in _MCP_SPEC.items() if k != "approval_category"}
    assert not _approval_steps(
        _events(_spec(policy=_CUSTOMER_EXTERNAL, tool_specs=[blind]), capsys)
    )


def test_sandbox_preset_does_not_park_it(capsys: pytest.CaptureFixture[str]) -> None:
    assert not _approval_steps(_events(_spec(policy=_SANDBOX, tool_specs=[_MCP_SPEC]), capsys))
