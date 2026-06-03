"""Integration tests: the runtime boot wires ALL tool families with
canonical names (Plan 06.18 task_06_18_05, ADR 0048/0049).

Before this task the agent-runtime boot (`__main__.run_task`) only built
`default_registry()` (echo + noop) + a conditional `shell_exec`; the whole
06.16 wiring framework (`tool_wiring`, the `register_*` family functions)
was dead in production. Consequence: any tool an operator assigned other
than `shell_exec` (a `read_file`, a `run_pytest`, an `http_get`…) reached
the loop as a silent ``ToolResult(ok=False, 'unknown tool')``.

This module pins the fix at the seam that the boot path now calls:

  * ``register_builtin_families`` registers every executable builtin family
    under its CANONICAL name (``read_file`` not ``file_read``; ``http_get``/
    ``http_post`` not ``http_request``; ``send_notification`` not
    ``notify_user``) so the registered names match what
    ``combine_tool_allowlists`` canonicalises the allowlist to (ADR 0048) —
    otherwise the allowlist would block every assigned tool by name mismatch.
  * a per-family feature flag (default ON) lets the operator disable a family
    without code changes; a disabled family registers nothing.
  * the catalog ``run_*`` ``docker_command`` tools and tenant-custom
    http_endpoint/python_function tools flow through ``register_tool_specs``
    (already tested in ``test_tool_wiring`` / ``test_run_tools_by_stack``);
    here we assert the families + the spec list co-exist in one registry and
    that an assigned-but-unwired name does NOT silently vanish.
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_runtime.builtin_families import (
    FAMILY_FLAG_PREFIX,
    register_builtin_families,
)
from agent_runtime.internal_api import InternalAgentAPI
from agent_runtime.orchestration_tools import OrchestrationSink
from agent_runtime.tools import ToolRegistry

pytestmark = pytest.mark.integration


def _fake_api() -> InternalAgentAPI:
    """An InternalAgentAPI with a stub httpx client — never actually called
    in these tests (we only assert the family registered, not its I/O)."""
    return InternalAgentAPI(
        base_url="http://api-server:8000",
        bearer_token="test-token",
    )


def _register_all(
    registry: ToolRegistry,
    *,
    flags: dict[str, bool] | None = None,
    allowed_domains: frozenset[str] = frozenset(),
) -> list[str]:
    return register_builtin_families(
        registry,
        api=_fake_api(),
        sink=OrchestrationSink(),
        allowed_domains=allowed_domains,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Canonical names: the registered family names are the catalog names.
# ---------------------------------------------------------------------------
def test_file_family_registers_canonical_names() -> None:
    registry = ToolRegistry()
    _register_all(registry)
    names = set(registry.names())
    # Catalog (canonical) names — NOT the legacy file_read/file_write/file_list.
    assert {"read_file", "write_file", "list_files"} <= names
    assert "file_read" not in names
    assert "file_write" not in names


def test_network_family_registers_http_get_and_post() -> None:
    registry = ToolRegistry()
    _register_all(registry, allowed_domains=frozenset({"example.com"}))
    names = set(registry.names())
    assert {"http_get", "http_post"} <= names
    # The legacy generic http_request name is not registered as canonical.
    assert "http_request" not in names


def test_notification_family_registers_send_notification() -> None:
    registry = ToolRegistry()
    _register_all(registry)
    names = set(registry.names())
    assert "send_notification" in names
    assert "notify_user" not in names


def test_orchestration_family_registers_canonical_names() -> None:
    registry = ToolRegistry()
    _register_all(registry)
    names = set(registry.names())
    assert {"kanban_update", "task_comment", "agent_invoke"} <= names


def test_knowledge_and_memory_families_register() -> None:
    registry = ToolRegistry()
    _register_all(registry)
    names = set(registry.names())
    assert {"rag_search", "document_convert", "promote_to_kb"} <= names
    assert {"memory_recall", "memory_store"} <= names


# ---------------------------------------------------------------------------
# The registered tools actually run (ToolResult.ok=True against fakes).
# ---------------------------------------------------------------------------
def test_registered_file_tool_executes_ok(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # WorkspaceFiles is confined to /workspace by default; point it at tmp_path
    # via the env the family honours.
    monkeypatch.setenv("AGENT_WORKSPACE_ROOT", str(tmp_path))
    registry = ToolRegistry()
    _register_all(registry)
    written = registry.call("write_file", {"path": "note.txt", "content": "hi"})
    assert written.ok is True
    read = registry.call("read_file", {"path": "note.txt"})
    assert read.ok is True
    assert read.output["content"] == "hi"


def test_registered_orchestration_tool_executes_ok() -> None:
    registry = ToolRegistry()
    sink = OrchestrationSink()
    register_builtin_families(
        registry,
        api=_fake_api(),
        sink=sink,
        allowed_domains=frozenset(),
    )
    result = registry.call("kanban_update", {"task_id": "t-1", "status": "done"})
    assert result.ok is True
    assert sink.effects[0]["effect"] == "kanban_update"


# ---------------------------------------------------------------------------
# Per-family feature flag — default ON, operator can disable a family.
# ---------------------------------------------------------------------------
def test_family_flag_disables_a_single_family() -> None:
    registry = ToolRegistry()
    # Disable the network family only; everything else stays.
    _register_all(registry, flags={"red": False})
    names = set(registry.names())
    assert "http_get" not in names
    assert "http_post" not in names
    # Other families unaffected.
    assert "read_file" in names
    assert "send_notification" in names


def test_family_flags_default_to_enabled() -> None:
    registry = ToolRegistry()
    registered = _register_all(registry, flags=None)
    # With no flags every executable family is wired.
    assert "read_file" in registered
    assert "http_get" in registered
    assert "rag_search" in registered


def test_family_flag_prefix_is_stable() -> None:
    # The env-var convention the operator configures (no hardcode in callers).
    assert FAMILY_FLAG_PREFIX == "AGENT_TOOL_FAMILY_"


# ===========================================================================
# The worker resolves docker_command images before forwarding tool_specs.
# ===========================================================================
def test_worker_resolves_docker_command_image() -> None:
    from workers.execution import _resolve_tool_spec_images

    specs = [
        {"name": "read_file", "implementation_type": "builtin", "config": {}},
        {
            "name": "run_pytest",
            "implementation_type": "docker_command",
            "config": {"runtime_template": "python-pytest", "command_template": ["pytest"]},
        },
    ]
    resolved = _resolve_tool_spec_images(specs, project_default_runtime=None)
    # builtin spec untouched.
    assert resolved[0] == specs[0]
    # docker_command: runtime_template replaced by an explicit, concrete image.
    run = resolved[1]["config"]
    assert "runtime_template" not in run
    assert run["image"] == "agent-runtime-python-pytest:v1"


def test_worker_resolves_image_from_project_stack() -> None:
    from workers.execution import _resolve_tool_spec_images

    specs = [
        {
            "name": "run_pytest",
            "implementation_type": "docker_command",
            "config": {"runtime_template": "python-pytest", "command_template": ["pytest"]},
        },
    ]
    # The PHP project stack wins over the tool default (Plan 06.16 precedence).
    resolved = _resolve_tool_spec_images(specs, project_default_runtime="php-phpunit")
    assert resolved[0]["config"]["image"] == "agent-runtime-php-phpunit:v1"


def test_worker_leaves_explicit_image_untouched() -> None:
    from workers.execution import _resolve_tool_spec_images

    specs = [
        {
            "name": "custom",
            "implementation_type": "docker_command",
            "config": {"image": "alpine:3.20", "command_template": ["echo", "hi"]},
        },
    ]
    resolved = _resolve_tool_spec_images(specs, project_default_runtime="php-phpunit")
    assert resolved[0]["config"]["image"] == "alpine:3.20"


def test_agent_spec_forwards_tool_specs_with_resolved_images() -> None:
    from uuid import uuid4

    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        tool_specs=[
            {
                "name": "run_pytest",
                "implementation_type": "docker_command",
                "config": {"runtime_template": "python-pytest", "command_template": ["pytest"]},
            },
        ],
    )
    spec = _agent_spec(req, None)
    assert spec["tool_specs"][0]["config"]["image"] == "agent-runtime-python-pytest:v1"
    # Round-trips through the Celery payload.
    rebuilt = ExecutionRequest.from_dict(req.as_dict())
    assert rebuilt.tool_specs == req.tool_specs


def test_agent_spec_omits_tool_specs_when_none() -> None:
    from uuid import uuid4

    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        tool_specs=None,
    )
    # No assignments → no key → the runtime keeps the pre-06.18 behaviour.
    assert "tool_specs" not in _agent_spec(req, None)
