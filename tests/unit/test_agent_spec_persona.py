"""P0-1: _agent_spec forwards `agent_persona` to the container spec.

The orchestrator resolves the agent's persona (system_prompt/role/name) and
threads it as `request["agent_persona"]`; the worker must forward it into the
AGENT_TASK_SPEC so the runtime can prepend it to the effective system prompt.
Absent → no key (backward-compat), mirroring skill_prompt_fragments.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec


def _request(persona: dict[str, Any] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
        agent_persona=persona,
    )


def test_agent_persona_forwarded_when_present() -> None:
    persona = {"prompt": "Eres el backend CI4.", "role": "backend_dev", "name": "ci4-backend"}
    spec = _agent_spec(_request(persona), None)
    assert spec["agent_persona"] == persona


def test_no_agent_persona_key_when_absent() -> None:
    spec = _agent_spec(_request(None), None)
    assert "agent_persona" not in spec


def test_roundtrip_preserves_agent_persona() -> None:
    persona = {"prompt": "p", "role": "qa"}
    rebuilt = ExecutionRequest.from_dict(_request(persona).as_dict())
    assert rebuilt.agent_persona == persona
