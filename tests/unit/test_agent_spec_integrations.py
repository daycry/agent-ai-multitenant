"""`task_mk_21` (MK-05): las anclas de integración del proyecto viajan al run.

`project.integrations` → `request["integrations"]` (dispatch) → `ExecutionRequest`
→ `_agent_spec` → `spec["integrations"]` → bloque del preámbulo del runtime. Aquí
se fija el tramo del worker (ida y vuelta por el payload de Celery y el spec) y,
a nivel de fuente, que el builder común del orchestrator emite la clave — el
arnés completo del dispatch vive en los tests de integración.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec

_REPO_ROOT = Path(__file__).resolve().parents[2]

_ANCHORS = {
    "jira": {"project_key": "PLAT", "parent_issue_key": "PLAT-120"},
    "confluence": {"space_key": "ENG", "root_page_id": "123456"},
}


def _request(integrations: dict[str, Any] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
        integrations=integrations,
    )


def test_integrations_forwarded_when_present() -> None:
    spec = _agent_spec(_request(_ANCHORS), None)
    assert spec["integrations"] == _ANCHORS


def test_no_integrations_key_when_absent_or_empty() -> None:
    assert "integrations" not in _agent_spec(_request(None), None)
    # `{}` en el proyecto = sin anclas: el preámbulo no lleva bloque, así que el
    # spec tampoco lleva clave.
    assert "integrations" not in _agent_spec(_request({}), None)


def test_roundtrip_preserves_integrations() -> None:
    rebuilt = ExecutionRequest.from_dict(_request(_ANCHORS).as_dict())
    assert rebuilt.integrations == _ANCHORS


def test_legacy_payload_without_the_key_still_parses() -> None:
    raw = _request(None).as_dict()
    raw.pop("integrations", None)
    assert ExecutionRequest.from_dict(raw).integrations is None


def test_dispatch_threads_the_project_anchors() -> None:
    source = (_REPO_ROOT / "apps/orchestrator/src/orchestrator/dispatch.py").read_text(
        encoding="utf-8"
    )
    assert 'request["integrations"]' in source
    assert 'getattr(project, "integrations", None)' in source
