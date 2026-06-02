"""Integration tests for the per-project `shell_exec` wiring
(Plan 06.16 task_06_16_02 / 02-shell-exec).

`shell_exec` is the builtin that lets an agent run STACK commands
(`php`, `composer`, `vendor/bin/phpunit`, `npm`, …) — not just Python.
It is **deny-by-default**: it runs ONLY programs in the project's
``allowed_commands`` allowlist; an empty allowlist runs nothing.

Two layers are proven here, end to end:

  1. **The tool** (`ShellExecTool`): a command whose program is in the
     allowlist runs and captures output; a program outside it is rejected
     *before* execution, and the error surfaces the allowed set; an empty
     allowlist denies everything.
  2. **The wiring** (worker → spec → runtime): the worker forwards
     ``project.allowed_commands`` through ``ExecutionRequest`` /
     ``_agent_spec`` into the ``AGENT_TASK_SPEC`` payload, and the runtime
     entrypoint (`run_task`) registers a ``ShellExecTool`` bound to it. A
     run with an allowlist can call ``shell_exec``; a run with an empty
     allowlist registers a deny-all ``shell_exec``; a run with no key at
     all does not register ``shell_exec`` at all.

  3. **The seed**: the catalog exposes ``shell_exec`` as an assignable
     builtin (``is_builtin``, category ``command``, ``privileged``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

import asyncpg
import pytest
from agent_runtime.__main__ import run_task
from agent_runtime.shell_exec import ShellExecTool
from alembic import command

pytestmark = pytest.mark.integration

# The program we drive the real subprocess through. `python` is on PATH on
# every dev/CI box; on Windows the basename is `python.exe`, so we allow both.
_PY = Path(sys.executable).name
_PY_ALLOWLIST = frozenset({"python", "python.exe", _PY})


# ===========================================================================
# Layer 1 — the tool: allowlist + deny-by-default
# ===========================================================================
def _tool(tmp_path: Path, allowed: frozenset[str]) -> ShellExecTool:
    return ShellExecTool(allowed_commands=allowed, timeout_s=30.0, workspace=str(tmp_path))


def test_allowed_program_runs(tmp_path: Path) -> None:
    result = _tool(tmp_path, _PY_ALLOWLIST)({"command": f"{_PY} -c \"print('stack ok')\""})
    assert result.ok is True
    assert result.output["exit_code"] == 0
    assert "stack ok" in result.output["stdout"]


def test_program_outside_allowlist_is_rejected_and_lists_allowed(tmp_path: Path) -> None:
    result = _tool(tmp_path, _PY_ALLOWLIST)({"command": "rm -rf /"})
    assert result.ok is False
    assert "not allowed" in (result.error or "")
    # The error surfaces the authorised set so the operator/agent can see it.
    assert result.output is not None
    assert "allowed" in result.output
    assert sorted(_PY_ALLOWLIST) == result.output["allowed"]
    # Blocked before execution — no exit code.
    assert "exit_code" not in (result.output or {})


def test_empty_allowlist_denies_everything(tmp_path: Path) -> None:
    deny_all = _tool(tmp_path, frozenset())
    for command_str in (f'{_PY} -c "print(1)"', "ls", "echo hi", "composer install"):
        result = deny_all({"command": command_str})
        assert result.ok is False, command_str
        assert "not allowed" in (result.error or ""), command_str
        assert result.output["allowed"] == []


# ===========================================================================
# Layer 2 — wiring: spec.allowed_commands → runtime registers shell_exec
# ===========================================================================
def _scripted_shell_exec(command_str: str) -> dict[str, Any]:
    """A scripted model that does one ACT (shell_exec) then FINISHes."""
    return {
        "kind": "scripted",
        "decisions": [
            {"kind": "act", "tool": "shell_exec", "tool_args": {"command": command_str}},
            {"kind": "finish", "output": "done"},
        ],
        "reviews": [{"passed": True}],
    }


def _spec(command_str: str, *, allowed_commands: list[str] | None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "task": {"id": "t-1", "title": "run a stack command", "description": "drive shell_exec"},
        "model": _scripted_shell_exec(command_str),
    }
    if allowed_commands is not None:
        spec["allowed_commands"] = allowed_commands
    return spec


def _run_and_collect(
    spec: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> list[dict[str, Any]]:
    rc = run_task(spec)
    assert rc == 0
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _act_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e["step"] for e in events if e.get("event") == "step" and e["step"].get("node") == "act"
    ]


def test_runtime_registers_shell_exec_when_allowlist_present(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # The runtime registers a `shell_exec` bound to the spec's allowlist. We
    # drive a program IN the allowlist: the call must reach the tool and pass
    # the allowlist gate — i.e. it is NOT rejected as "unknown tool" (it was
    # registered) and NOT as "not allowed" (the program is authorised). Whether
    # the subprocess then succeeds depends on the container workspace existing,
    # which is the container's concern; the *real* subprocess execution is
    # asserted by the layer-1 `ShellExecTool` tests against a real `tmp_path`.
    events = _run_and_collect(
        _spec(f"{_PY} -c \"print('wired')\"", allowed_commands=list(_PY_ALLOWLIST)), capsys
    )
    acts = _act_steps(events)
    assert len(acts) == 1
    error = acts[0]["result"]["error"] or ""
    assert "unknown tool" not in error, "shell_exec was not registered from the spec allowlist"
    assert "not allowed" not in error, "an allowlisted program was wrongly rejected"


def test_runtime_empty_allowlist_registers_deny_all_shell_exec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Empty list IS forwarded → shell_exec is registered but denies everything.
    events = _run_and_collect(_spec(f'{_PY} -c "print(1)"', allowed_commands=[]), capsys)
    acts = _act_steps(events)
    assert len(acts) == 1
    assert acts[0]["status"] == "error"
    assert acts[0]["result"]["ok"] is False
    assert "not allowed" in acts[0]["result"]["error"]


def test_runtime_no_allowlist_key_does_not_register_shell_exec(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # No `allowed_commands` key → shell_exec is not registered at all, so the
    # call hits the unknown-tool path.
    events = _run_and_collect(_spec(f'{_PY} -c "print(1)"', allowed_commands=None), capsys)
    acts = _act_steps(events)
    assert len(acts) == 1
    assert acts[0]["status"] == "error"
    assert acts[0]["result"]["ok"] is False
    assert "unknown tool" in acts[0]["result"]["error"]


# ===========================================================================
# Wiring: the worker forwards allowed_commands through the spec
# ===========================================================================
def test_agent_spec_forwards_allowed_commands_when_set() -> None:
    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        allowed_commands=["php", "composer"],
    )
    spec = _agent_spec(req, None)
    assert spec["allowed_commands"] == ["php", "composer"]
    # Round-trips through the Celery payload.
    assert ExecutionRequest.from_dict(req.as_dict()).allowed_commands == ["php", "composer"]


def test_agent_spec_forwards_empty_allowed_commands() -> None:
    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        allowed_commands=[],
    )
    spec = _agent_spec(req, None)
    assert "allowed_commands" in spec
    assert spec["allowed_commands"] == []


def test_agent_spec_omits_allowed_commands_when_none() -> None:
    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        allowed_commands=None,
    )
    assert "allowed_commands" not in _agent_spec(req, None)


# ===========================================================================
# Layer 3 — the seed exposes shell_exec as an assignable builtin
# ===========================================================================
async def _run_seed(dsn: str) -> int:
    from api_server.seeds.builtin_tools import seed_builtin_tools
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    sa_dsn = dsn
    if not sa_dsn.startswith("postgresql+asyncpg://"):
        sa_dsn = sa_dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
            "postgresql://", "postgresql+asyncpg://", 1
        )
    engine = create_async_engine(sa_dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            return await seed_builtin_tools(session)
    finally:
        await engine.dispose()


def test_seed_exposes_shell_exec_as_assignable_builtin(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")

    async def _seed_and_fetch() -> tuple[int, asyncpg.Record | None]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE tools, organizations CASCADE")
        finally:
            await conn.close()
        n = await _run_seed(migrations_pg_dsn)
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow(
                "SELECT name, category, security_level, implementation_type,"
                " is_builtin, input_schema FROM tools WHERE name = 'shell_exec'"
            )
        finally:
            await conn.close()
        return n, row

    n, row = asyncio.run(_seed_and_fetch())
    # The catalog count went 18 -> 19.
    assert n == 19
    assert row is not None
    assert row["category"] == "command"
    assert row["security_level"] == "privileged"
    assert row["implementation_type"] == "builtin"
    assert row["is_builtin"] is True
    raw_schema = row["input_schema"]
    schema = raw_schema if isinstance(raw_schema, dict) else json.loads(raw_schema)
    assert schema["type"] == "object"
    assert "command" in schema["properties"]
    assert schema["required"] == ["command"]
