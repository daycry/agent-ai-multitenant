"""_agent_spec gives a ``claude_sdk`` run a base shell allowlist (Track 1 / ADR 0021).

The Claude Agent SDK is natively agentic; forcing it through an EMPTY shell
allowlist walled it off from reconciling the worktree (`command not allowed: git`
/ `rm` observed in a real run). The worker now UNIONs a base set of safe VCS/file
commands into the spec's ``allowed_commands`` for ``claude_sdk`` ONLY — the thin
providers (ollama/azure/copilot) keep the project's allowlist verbatim. The
container sandbox (cap-drop, read-only rootfs, no egress) stays the boundary.
"""

from __future__ import annotations

from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec


def _request(*, kind: str, allowed_commands: list[str] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": kind},
        allowed_commands=allowed_commands,
    )


def test_sdk_unions_base_commands_with_project_allowlist() -> None:
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=["composer"]), None)
    cmds = set(spec["allowed_commands"])
    assert {"git", "rm"} <= cmds  # base commands present
    assert "composer" in cmds  # project's stack command preserved


def test_sdk_gets_base_commands_when_project_allowlist_empty() -> None:
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=[]), None)
    assert {"git", "rm"} <= set(spec["allowed_commands"])


def test_sdk_registers_shell_with_base_even_when_project_allowlist_absent() -> None:
    # A claude_sdk run must ALWAYS get a shell with the base commands, even when the
    # project pinned nothing (None) — otherwise the SDK can't reconcile the worktree.
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=None), None)
    assert "allowed_commands" in spec
    assert {"git", "rm"} <= set(spec["allowed_commands"])


def test_thin_provider_allowlist_is_unchanged() -> None:
    spec = _agent_spec(_request(kind="ollama", allowed_commands=["pytest"]), None)
    assert spec["allowed_commands"] == ["pytest"]


def test_thin_provider_absent_allowlist_stays_absent() -> None:
    spec = _agent_spec(_request(kind="ollama", allowed_commands=None), None)
    assert "allowed_commands" not in spec
