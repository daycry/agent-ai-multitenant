"""_agent_spec gives a ``claude_sdk`` run a base shell allowlist (Track 1 / ADR 0021).

The Claude Agent SDK is natively agentic; forcing it through an EMPTY shell
allowlist walled it off from inspecting the worktree (`command not allowed: ls`
observed in a real run). The worker UNIONs a base set of read/inspect commands
into the spec's ``allowed_commands`` for ``claude_sdk`` ONLY — the thin
providers (ollama/azure/copilot) keep the project's allowlist verbatim. The
container sandbox (cap-drop, read-only rootfs, no egress) stays the boundary.

**What the base deliberately does NOT carry (audit 2026-09-01): `rm` and `mv`.**
ADR 0164 guards the committed deliverable inside the `file` tool family —
`delete_file` refuses a tracked tree, `move_file` refuses to move it away or
over it — and both are audited in the `steps_log` and gated as `code_changes`.
With `rm`/`mv` in the SDK base, `shell_exec("rm -rf app")` did exactly what
`delete_file` refuses, for EVERY Claude SDK run and regardless of the project's
own allowlist. A guard with an unconditional side door is not a guard. The SDK
keeps `delete_file` / `move_file` as host tools, so nothing legitimate is lost.
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


def test_sdk_unions_base_inspect_commands_with_project_allowlist() -> None:
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=["composer"]), None)
    cmds = set(spec["allowed_commands"])
    assert {"ls", "cat", "grep", "find", "mkdir", "cp"} <= cmds  # base present
    assert "composer" in cmds  # project's stack command preserved


def test_sdk_base_excludes_git() -> None:
    # ADR 0163: the worktree's `.git` does not even exist while the agent runs, and
    # the agent never commits (the worker owns git). A useless `git` is removed —
    # `git status` returns a clean "command not allowed" instead of a cryptic
    # failure that wastes turns.
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=["composer"]), None)
    assert "git" not in set(spec["allowed_commands"])


def test_sdk_base_excludes_the_shell_side_door_around_adr_0164() -> None:
    """`rm` and `mv` are NOT in the base: the `file` family is the audited door.

    The project can still grant them explicitly through its own allowlist —
    that is the deliberate frontier ADR 0164 documents — but the platform must
    not hand them out to every SDK run behind the project's back.
    """
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=[]), None)
    cmds = set(spec["allowed_commands"])
    assert "rm" not in cmds, "`rm` in the SDK base bypasses delete_file's tracked-tree guard"
    assert "mv" not in cmds, "`mv` in the SDK base bypasses move_file's tracked-tree guard"


def test_a_project_can_still_grant_rm_explicitly() -> None:
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=["rm"]), None)
    assert "rm" in set(spec["allowed_commands"])


def test_sdk_registers_shell_with_base_even_when_project_allowlist_absent() -> None:
    # A claude_sdk run must ALWAYS get a shell with the base inspect commands, even
    # when the project pinned nothing (None) — otherwise the SDK can't look around.
    spec = _agent_spec(_request(kind="claude_sdk", allowed_commands=None), None)
    assert "allowed_commands" in spec
    assert {"ls", "cat"} <= set(spec["allowed_commands"])


# `task_cv_34` (auditoría 2026-09-01, F-02): la guía de ejecución promete
# `grep/ls/cat` por `shell_exec` a TODOS los agentes y sólo `claude_sdk` recibía
# la base: en Ollama/Copilot/Azure cada `ls` era «command not allowed». Los
# proveedores finos reciben ahora el subconjunto de SÓLO LECTURA de la base.


def test_thin_provider_gets_the_read_only_subset_on_top_of_its_allowlist() -> None:
    spec = _agent_spec(_request(kind="ollama", allowed_commands=["pytest"]), None)
    assert "pytest" in spec["allowed_commands"]
    assert {"ls", "cat", "grep", "find", "head", "tail", "wc"} <= set(spec["allowed_commands"])


def test_thin_provider_absent_allowlist_becomes_the_read_only_subset() -> None:
    spec = _agent_spec(_request(kind="ollama", allowed_commands=None), None)
    assert set(spec["allowed_commands"]) == {"ls", "cat", "grep", "find", "head", "tail", "wc"}


def test_thin_provider_never_gets_the_writing_half_of_the_sdk_base() -> None:
    """`cp`/`mkdir`/`touch`/`sed`/`awk` siguen siendo del SDK (y de la allowlist del
    proyecto): la promesa de la guía es leer, no escribir por shell."""
    spec = _agent_spec(_request(kind="ollama", allowed_commands=None), None)
    assert not {"cp", "mkdir", "rmdir", "touch", "sed", "awk", "rm", "mv"} & set(
        spec["allowed_commands"]
    )
