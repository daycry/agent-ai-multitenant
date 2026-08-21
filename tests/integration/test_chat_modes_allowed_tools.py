"""Integration tests for chat-mode `allowed_tools` enforcement
(Plan 06.14 task_06_14_07 / guardrails-1).

Before this task the `ChatModeConfig.allowed_tools` whitelist was purely
advisory: the comment claimed the runtime enforced it in a pre_tool
guardrail layer, but `ToolRegistry.call()` had no allowlist check and the
list was never forwarded to the agent-runtime. This suite proves the full
in-scope path now works end to end:

  1. the worker forwards `allowed_tools` through `ExecutionRequest` →
     `_agent_spec` into the `AGENT_TASK_SPEC` payload;
  2. the runtime entrypoint (`run_task`) applies that allowlist to the
     `ToolRegistry`, so a tool outside the set is rejected at call time;
  3. a tenant-defined custom mode's `allowed_tools` (loaded from the DB
     via the resolver) becomes the effective allowlist — and one tenant's
     custom-mode allowlist never leaks into another tenant's run
     (cross-tenant denial).

The full layered guardrail engine (pre_llm / post_llm / pre_tool /
post_tool) is Plan 11 and intentionally out of scope here.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from agent_runtime.__main__ import run_task
from alembic import command
from api_server.chat.modes import (
    BUILTIN_MODES,
    BuiltinChatMode,
    resolve_mode_config,
)
from api_server.db import domain  # noqa: F401  — register the metadata
from api_server.db.conversation import ChatMode
from api_server.db.custom_chat_mode import CustomChatMode
from api_server.db.custom_chat_mode_repo import load_tenant_custom_modes
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from workers.execution import ExecutionRequest, _agent_spec

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Spec helpers — drive the real agent loop with the deterministic scripted
# model. One ACT turn that picks `tool`, then a FINISH turn.
# ---------------------------------------------------------------------------
def _scripted_model(act_tool: str) -> dict[str, Any]:
    return {
        "kind": "scripted",
        "decisions": [
            {"kind": "act", "tool": act_tool, "tool_args": {"text": "go"}},
            {"kind": "finish", "output": "done"},
        ],
        "reviews": [{"passed": True}],
    }


def _spec(act_tool: str, *, allowed_tools: list[str] | object | None = None) -> dict[str, Any]:
    spec: dict[str, Any] = {
        "task": {"id": "t-1", "title": "exercise allowlist", "description": "drive the loop"},
        "model": _scripted_model(act_tool),
    }
    if allowed_tools is not None:
        spec["allowed_tools"] = allowed_tools
    return spec


def _run_and_collect(
    spec: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> list[dict[str, Any]]:
    """Run `run_task` and return the parsed JSON event lines from stdout."""
    rc = run_task(spec)
    assert rc == 0
    out = capsys.readouterr().out
    events: list[dict[str, Any]] = []
    for raw in out.splitlines():
        line = raw.strip()
        if line:
            events.append(json.loads(line))
    return events


def _act_observations(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The tool-call `act` steps, with their result dicts."""
    return [
        e["step"] for e in events if e.get("event") == "step" and e["step"].get("node") == "act"
    ]


# ===========================================================================
# Runtime entrypoint: the spec allowlist reaches the registry
# ===========================================================================
def test_runtime_runs_tool_inside_allowlist(capsys: pytest.CaptureFixture[str]) -> None:
    events = _run_and_collect(_spec("echo", allowed_tools=["echo"]), capsys)
    acts = _act_observations(events)
    assert len(acts) == 1
    assert acts[0]["status"] == "ok"
    assert acts[0]["result"]["ok"] is True


def test_runtime_blocks_tool_outside_allowlist(capsys: pytest.CaptureFixture[str]) -> None:
    # 'echo' is a real builtin tool, but the mode only allows 'noop'.
    events = _run_and_collect(_spec("echo", allowed_tools=["noop"]), capsys)
    acts = _act_observations(events)
    assert len(acts) == 1
    assert acts[0]["status"] == "error"
    assert acts[0]["result"]["ok"] is False
    assert acts[0]["result"]["error"] == "tool 'echo' not allowed in this mode"


def test_runtime_no_allowlist_key_is_unrestricted(capsys: pytest.CaptureFixture[str]) -> None:
    # No `allowed_tools` key at all → every builtin tool is callable.
    events = _run_and_collect(_spec("echo", allowed_tools=None), capsys)
    acts = _act_observations(events)
    assert acts[0]["status"] == "ok"


def test_runtime_empty_allowlist_blocks_all_tools(capsys: pytest.CaptureFixture[str]) -> None:
    # Explicit empty list (the `discussion` mode) blocks every tool.
    events = _run_and_collect(_spec("echo", allowed_tools=[]), capsys)
    acts = _act_observations(events)
    assert acts[0]["status"] == "error"
    assert acts[0]["result"]["error"] == "tool 'echo' not allowed in this mode"


# ===========================================================================
# Worker: ExecutionRequest forwards allowed_tools into the task spec
# ===========================================================================
def _request(allowed_tools: list[str] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        allowed_tools=allowed_tools,
    )


def test_agent_spec_forwards_allowlist_when_set() -> None:
    spec = _agent_spec(_request(["file_read", "task_comment"]), None)
    assert spec["allowed_tools"] == ["file_read", "task_comment"]


def test_agent_spec_forwards_empty_allowlist() -> None:
    spec = _agent_spec(_request([]), None)
    assert "allowed_tools" in spec
    assert spec["allowed_tools"] == []


def test_agent_spec_omits_allowlist_when_none() -> None:
    spec = _agent_spec(_request(None), None)
    assert "allowed_tools" not in spec


# AUD16-02: kanban_update/agent_invoke ya NO se anuncian al LLM (sin drain
# worker-side su ok=true era éxito falso). El set anunciado vive en
# workers.agent_tool_schemas.SYSTEM_TOOL_NAMES e incluye además las
# capacidades del grafo (update_plan P1-6, ask_human ADR 0114).
_SYSTEM_TOOLS = {
    "memory_recall",
    "memory_store",
    "task_comment",
    "rag_search",
    "update_plan",
    "ask_human",
}


def test_agent_spec_injects_model_tool_schemas_for_allowlisted_tools() -> None:
    # Agentes #2 + H0: the model spec carries the OpenAI schemas of the agent's
    # ASSIGNED tools AND the always-available system family tools (memory +
    # orchestration), so the LLM can call them and recall/store memory.
    spec = _agent_spec(_request(["memory_recall", "read_file"]), None)
    tools = spec["model"].get("tools")
    assert tools is not None
    names = [t["function"]["name"] for t in tools]
    # Assigned tools come first, in order; the remaining system tools follow.
    assert names[:2] == ["memory_recall", "read_file"]
    assert set(names) >= _SYSTEM_TOOLS
    # The original model fields are preserved.
    assert spec["model"]["kind"] == "scripted"


def test_agent_spec_injects_system_tools_even_without_allowlist() -> None:
    # H0 regression: a tool-less agent (no agent_tools) still gets the memory +
    # orchestration tools advertised so it can recall/store and participate.
    spec = _agent_spec(_request(None), None)
    names = [t["function"]["name"] for t in spec["model"]["tools"]]
    assert set(names) == _SYSTEM_TOOLS


def test_agent_spec_skips_unknown_tool_but_keeps_system_tools() -> None:
    # An assigned tool with no known schema is skipped; the system family tools
    # are still advertised.
    spec = _agent_spec(_request(["totally_unknown_tool"]), None)
    names = [t["function"]["name"] for t in spec["model"]["tools"]]
    assert "totally_unknown_tool" not in names
    assert set(names) >= _SYSTEM_TOOLS


def test_agent_spec_block_all_allowlist_omits_model_tools() -> None:
    # The discussion mode's explicit empty allowlist suppresses EVERY model
    # tool — system tools are not a back door around block-all.
    spec = _agent_spec(_request([]), None)
    assert "tools" not in spec["model"]


def test_execution_request_round_trips_allowlist() -> None:
    req = _request(["file_read"])
    rebuilt = ExecutionRequest.from_dict(req.as_dict())
    assert rebuilt.allowed_tools == ["file_read"]


def test_execution_request_round_trips_none_allowlist() -> None:
    rebuilt = ExecutionRequest.from_dict(_request(None).as_dict())
    assert rebuilt.allowed_tools is None


# ===========================================================================
# Built-in modes carry the right allowlists (the source of truth)
# ===========================================================================
def test_discussion_mode_has_empty_allowlist() -> None:
    assert BUILTIN_MODES[BuiltinChatMode.DISCUSSION.value].allowed_tools == ()


def test_execution_mode_allows_shell_exec_planning_does_not() -> None:
    execution = set(BUILTIN_MODES[BuiltinChatMode.EXECUTION.value].allowed_tools)
    planning = set(BUILTIN_MODES[BuiltinChatMode.PLANNING.value].allowed_tools)
    assert "shell_exec" in execution
    assert "shell_exec" not in planning


# ===========================================================================
# End to end: a tenant custom mode's allowlist becomes the effective set
# ===========================================================================
async def _seed_tenants(dsn: str) -> tuple[UUID, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE custom_chat_modes, messages, conversations, projects,"
            " agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_a,
            "Tenant A",
            "tenant-a-at",
            tenant_b,
            "Tenant B",
            "tenant-b-at",
        )
    finally:
        await conn.close()
    return tenant_a, tenant_b


@pytest.fixture()
def schema_ready(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


def test_custom_mode_allowlist_enforced_by_registry(
    schema_ready,
    admin_database_url: str,
    migrations_pg_dsn: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A tenant's custom mode allows only 'noop'; resolving it and feeding
    its allowlist to the runtime must block 'echo' but permit 'noop'."""
    tenant_a, _ = asyncio.run(_seed_tenants(migrations_pg_dsn))

    async def _resolve() -> tuple[str, ...]:
        engine = create_async_engine(admin_database_url, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                session.add(
                    CustomChatMode(
                        tenant_id=tenant_a,
                        name="locked",
                        label_es="Bloqueado",
                        label_en="Locked",
                        system_prompt="Solo herramientas seguras.",
                        allowed_tools=["noop"],
                    )
                )
                await session.commit()
            async with session_factory() as session:
                registry = await load_tenant_custom_modes(session, tenant_a)
            cfg = resolve_mode_config(
                ChatMode.CUSTOM.value, custom_mode_name="locked", custom_modes=registry
            )
            return cfg.allowed_tools
        finally:
            await engine.dispose()

    allowed = list(asyncio.run(_resolve()))
    assert allowed == ["noop"]

    # 'echo' is blocked under this mode's allowlist...
    blocked = _act_observations(_run_and_collect(_spec("echo", allowed_tools=allowed), capsys))
    assert blocked[0]["result"]["ok"] is False
    assert blocked[0]["result"]["error"] == "tool 'echo' not allowed in this mode"
    # ...but the listed 'noop' runs fine.
    permitted = _act_observations(_run_and_collect(_spec("noop", allowed_tools=allowed), capsys))
    assert permitted[0]["result"]["ok"] is True


@pytest.mark.cross_tenant
def test_one_tenant_allowlist_does_not_leak_to_another(
    schema_ready,
    admin_database_url: str,
    migrations_pg_dsn: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Tenant A and Tenant B both define a mode named 'team-mode' but with
    DIFFERENT allowlists. Resolving for B must yield B's allowlist, and a
    run scoped to B must enforce B's set — never A's. This guards against
    a cross-tenant bleed where B's run could call a tool only A permitted."""
    tenant_a, tenant_b = asyncio.run(_seed_tenants(migrations_pg_dsn))

    async def _seed_and_resolve() -> tuple[tuple[str, ...], tuple[str, ...]]:
        engine = create_async_engine(admin_database_url, echo=False)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                # A's 'team-mode' permits 'echo'; B's permits only 'noop'.
                session.add(
                    CustomChatMode(
                        tenant_id=tenant_a,
                        name="team-mode",
                        label_es="A",
                        label_en="A",
                        system_prompt="A permite echo.",
                        allowed_tools=["echo"],
                    )
                )
                session.add(
                    CustomChatMode(
                        tenant_id=tenant_b,
                        name="team-mode",
                        label_es="B",
                        label_en="B",
                        system_prompt="B solo permite noop.",
                        allowed_tools=["noop"],
                    )
                )
                await session.commit()
            async with session_factory() as session:
                registry_a = await load_tenant_custom_modes(session, tenant_a)
            async with session_factory() as session:
                registry_b = await load_tenant_custom_modes(session, tenant_b)
            cfg_a = resolve_mode_config(
                ChatMode.CUSTOM.value, custom_mode_name="team-mode", custom_modes=registry_a
            )
            cfg_b = resolve_mode_config(
                ChatMode.CUSTOM.value, custom_mode_name="team-mode", custom_modes=registry_b
            )
            return cfg_a.allowed_tools, cfg_b.allowed_tools
        finally:
            await engine.dispose()

    allowed_a, allowed_b = asyncio.run(_seed_and_resolve())
    assert list(allowed_a) == ["echo"]
    assert list(allowed_b) == ["noop"]

    # B's run uses B's allowlist: 'echo' (allowed only for A) is DENIED.
    denied = _act_observations(
        _run_and_collect(_spec("echo", allowed_tools=list(allowed_b)), capsys)
    )
    assert denied[0]["result"]["ok"] is False
    assert denied[0]["result"]["error"] == "tool 'echo' not allowed in this mode"
