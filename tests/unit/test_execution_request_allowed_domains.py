"""prod-12 Fase B (task_prod12_allow_01) — `allowed_domains` viaja proyecto →
ExecutionRequest → `_agent_spec` → runtime, con el centinela que impide emitirla
sin la defensa SSRF de la Fase A aplicada en AMBAS tools HTTP."""

from __future__ import annotations

from pathlib import Path

import pytest
from workers.run_contract import ExecutionRequest
from workers.run_spec import _agent_spec

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME = _REPO_ROOT / "docker/agent-runtimes/agent-runtime/agent_runtime"


def _request(**overrides: object) -> ExecutionRequest:
    base: dict[str, object] = {
        "tenant_id": "t1",
        "task_id": "task1",
        "agent_id": "a1",
        "task": {"id": "task1", "title": "T", "description": ""},
        "model": {"kind": "azure_foundry", "model": "gpt"},
    }
    base.update(overrides)
    return ExecutionRequest.from_dict(base)  # type: ignore[arg-type]


def test_round_trips_through_the_celery_payload() -> None:
    req = _request(allowed_domains=["api.example.com", "docs.example.com"])
    rebuilt = ExecutionRequest.from_dict(req.as_dict())
    assert rebuilt.allowed_domains == ["api.example.com", "docs.example.com"]


def test_agent_spec_emits_the_key_when_set() -> None:
    spec = _agent_spec(_request(allowed_domains=["api.example.com"]), None)
    assert spec["allowed_domains"] == ["api.example.com"]


def test_agent_spec_emits_explicit_deny_all() -> None:
    spec = _agent_spec(_request(allowed_domains=[]), None)
    assert spec["allowed_domains"] == []


def test_agent_spec_omits_the_key_for_legacy_payloads() -> None:
    spec = _agent_spec(_request(), None)
    assert "allowed_domains" not in spec


def test_dispatch_always_threads_the_project_allowlist() -> None:
    """El builder común del orchestrator emite `allowed_domains` SIEMPRE (igual
    que `allowed_commands`) — pin a nivel de fuente, el arnés completo del
    dispatch vive en tests/integration/test_in_review_dispatch.py."""
    source = (_REPO_ROOT / "apps/orchestrator/src/orchestrator/dispatch.py").read_text(
        encoding="utf-8"
    )
    assert 'request["allowed_domains"]' in source


# --- Centinela (riesgo 1 del plan prod-12): el cableado NO puede existir sin la
# defensa de la Fase A. Si alguien retira el ssrf_guard de una tool (o la
# emisión aparece sin él), este test rompe el CI.


def test_sentinel_ssrf_guard_present_in_both_http_tools() -> None:
    spec_source = (_REPO_ROOT / "apps/workers/src/workers/run_spec.py").read_text(encoding="utf-8")
    emits = '"allowed_domains"' in spec_source
    guarded = all(
        "validate_destination" in (_RUNTIME / tool).read_text(encoding="utf-8")
        for tool in ("http_tool.py", "http_endpoint_tool.py")
    )
    assert emits, "la emisión de allowed_domains desapareció de _agent_spec"
    assert guarded, (
        "allowed_domains se emite al runtime pero el ssrf_guard NO está aplicado "
        "en ambas tools HTTP — el deny-all accidental era la única protección "
        "(gap4-1/gap4-2); restaura validate_destination antes de cablear."
    )
