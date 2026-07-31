"""El boot canjea las acciones ya aprobadas — ADR 0135, extremo a extremo.

`tests/unit/test_approval_gate_authorized_actions.py` pinea la lógica del gate;
esto pinea las DOS costuras que aquel no puede ver y que son justo donde este
repo se queda a medias una y otra vez (mecanismo entregado, cero llamantes):

  * `run_task` construye el `ApprovalGate` con la lista `approved_actions` del
    spec — si no la pasa, la autorización no llega nunca al sandbox;
  * el nodo `plan` le pasa los **args** de la decisión al gate — sin ellos la
    comparación es imposible y todo queda igual que antes del ADR.

Sin DB/Redis/Docker: modelo `scripted` que decide llamar a `memory_store`
(categoría `code_changes`, tool de familia de sistema que sin API interna falla
con gracia: lo que importa aquí es si se PARA, no si escribe).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from agent_runtime.__main__ import run_task
from shared_domain.approval_action import action_fingerprint
from shared_domain.approval_categories import APPROVAL_CATEGORIES

_CUSTOMER_EXTERNAL = {"categories": dict.fromkeys(APPROVAL_CATEGORIES, "human_required")}

_TOOL = "memory_store"
_ARGS: dict[str, Any] = {"content": "el endpoint vive en /v1/chunk", "scope": "project_shared"}


def _spec(approved: list[dict[str, Any]] | None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "task": {"id": "t-1", "title": "Guarda el aprendizaje", "description": ""},
        "approval_policy": _CUSTOMER_EXTERNAL,
        "model": {
            "kind": "scripted",
            "decisions": [
                {"kind": "act", "tool": _TOOL, "tool_args": dict(_ARGS)},
                {"kind": "finish", "output": "listo"},
            ],
            "reviews": [{"passed": True}],
        },
    }
    if approved is not None:
        spec["approved_actions"] = approved
    return spec


def _authorisation(args: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "tool": _TOOL,
            "args_hash": action_fingerprint(_TOOL, args),
            "category": "code_changes",
            "resolved_at": "2026-07-31T10:00:00+00:00",
        }
    ]


def _steps(spec: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    run_task(spec)
    out = capsys.readouterr().out
    events = [json.loads(line) for line in out.splitlines() if line.strip()]
    return [e["step"] for e in events if e.get("event") == "step"]


def _parked(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [s for s in steps if s.get("status") == "awaiting_human_approval"]


def test_without_an_authorisation_the_action_is_parked(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """El estado anterior al ADR 0135, y el control negativo de todo lo demás."""
    parked = _parked(_steps(_spec(None), capsys))
    assert parked, "el preset «Cliente Externo» tiene que parar memory_store"
    assert _TOOL in parked[0]["summary"]


def test_the_exact_approved_action_runs_without_parking(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Caso 1 del ADR: aparcar → aprobar → re-ejecutar → la tool CORRE."""
    steps = _steps(_spec(_authorisation(_ARGS)), capsys)
    assert not _parked(steps), "la acción autorizada no puede volver a aparcarse"
    assert any(s.get("node") == "act" for s in steps), "…y la tool tiene que llegar a ejecutarse"


def test_an_authorisation_for_other_args_does_not_let_it_through(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """G1, no G2: la autorización es de la acción exacta, no de la tool."""
    other = _authorisation({**_ARGS, "content": "otra cosa"})
    assert _parked(_steps(_spec(other), capsys))


def test_the_authorisation_is_spent_once(capsys: pytest.CaptureFixture[str]) -> None:
    """T1: dos llamadas idénticas en el MISMO run → la segunda se aparca."""
    spec = _spec(_authorisation(_ARGS))
    spec["model"]["decisions"] = [
        {"kind": "act", "tool": _TOOL, "tool_args": dict(_ARGS)},
        {"kind": "act", "tool": _TOOL, "tool_args": dict(_ARGS)},
        {"kind": "finish", "output": "listo"},
    ]
    assert _parked(_steps(spec, capsys))
