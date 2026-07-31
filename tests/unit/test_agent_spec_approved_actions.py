"""ADR 0135 — el spec del contenedor lleva las acciones ya autorizadas.

El transporte es la mitad barata de `task_prod03_06`, pero también el sitio
donde el mecanismo se queda sin llamantes si nadie lo cablea (el patrón que
`docs/03-guides/verificar-antes-de-implementar.md` §5 documenta como dominante
en esta base). Aquí se fija el último tramo: `_build_runtime_env` —la función
pura que arma `AGENT_TASK_SPEC`— emite `approved_actions` cuando el worker las
ha leído, y NO emite la clave cuando no hay ninguna (un primer despacho queda
byte a byte como antes de este ADR).
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from workers.execution import ExecutionRequest, _build_runtime_env

_API_URL = "http://api-server:8000"

_APPROVED: list[dict[str, Any]] = [
    {
        "tool": "write_file",
        "args_hash": "a" * 64,
        "category": "code_changes",
        "resolved_at": "2026-07-31T10:00:00+00:00",
    }
]


def _request() -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        # Sin agente asignado no se mintea token: la función queda pura y el
        # test no necesita el jwt_secret del api-server.
        agent_id=None,
        task={"id": "t-1", "title": "aprobación", "description": ""},
        model={"kind": "ollama"},
    )


def _spec(approved: list[dict[str, Any]] | None) -> dict[str, Any]:
    env = _build_runtime_env(
        _request(),
        {"categories": {"code_changes": "human_required"}},
        agent_internal_api_url=_API_URL,
        approved_actions=approved,
    )
    spec: dict[str, Any] = json.loads(env["AGENT_TASK_SPEC"])
    return spec


def test_approved_actions_reach_the_container_spec() -> None:
    assert _spec(_APPROVED)["approved_actions"] == _APPROVED


def test_no_key_when_there_is_nothing_authorised() -> None:
    """«Sin clave» es el comportamiento de siempre: un primer despacho no puede
    cambiar de forma por esta feature."""
    assert "approved_actions" not in _spec(None)
    assert "approved_actions" not in _spec([])


def test_the_policy_still_travels_alongside() -> None:
    """La autorización NO sustituye a la política: el gate necesita las dos, y
    un spec que perdiera `approval_policy` dejaría de aparcar NADA."""
    spec = _spec(_APPROVED)
    assert spec["approval_policy"] == {"categories": {"code_changes": "human_required"}}
