"""Unit tests for the chat mode catalog (Plan 03 task_03_06).

The catalog is a pure-Python config map — no DB needed. We verify:

  - The three built-in modes are present with stable string keys.
  - `resolve_mode_config` returns the right config for each built-in.
  - Planning ships the planning sub-graph flag, the others don't.
  - Planning's tool whitelist excludes mutating tools (shell_exec,
    file_write) — that's the whole point of the mode.
  - Execution's whitelist includes the worker-side tools.
  - Discussion ships an empty tool whitelist (pure chat).
  - Custom modes resolve from a tenant registry by name.
  - Missing custom mode falls back to planning's prompt (safe default)
    rather than execution's.
  - An unknown built-in name raises ValueError.
"""

from __future__ import annotations

import pytest
from api_server.chat.modes import (
    BUILTIN_MODES,
    BuiltinChatMode,
    ChatModeConfig,
    CustomModeSpec,
    resolve_mode_config,
)
from api_server.db.conversation import ChatMode


# ---------------------------------------------------------------------------
# Catalog smoke
# ---------------------------------------------------------------------------
def test_builtin_modes_keyed_by_lowercase_string() -> None:
    assert set(BUILTIN_MODES) == {"planning", "discussion", "execution"}


@pytest.mark.parametrize("mode", list(BuiltinChatMode))
def test_each_builtin_mode_has_a_config(mode: BuiltinChatMode) -> None:
    cfg = BUILTIN_MODES[mode.value]
    assert isinstance(cfg, ChatModeConfig)
    assert cfg.name == mode.value
    assert cfg.system_prompt  # non-empty
    assert cfg.label_es and cfg.label_en


def test_planning_runs_the_planning_subgraph() -> None:
    assert BUILTIN_MODES["planning"].planning_subgraph is True


@pytest.mark.parametrize("mode", ["discussion", "execution"])
def test_non_planning_modes_do_not_run_the_planning_subgraph(mode: str) -> None:
    assert BUILTIN_MODES[mode].planning_subgraph is False


# ---------------------------------------------------------------------------
# Tool whitelist invariants
# ---------------------------------------------------------------------------
def test_planning_whitelist_excludes_mutating_tools() -> None:
    """Planning is for *designing* a plan, not for touching the world."""
    tools = set(BUILTIN_MODES["planning"].allowed_tools)
    # Never in planning:
    assert "shell_exec" not in tools
    assert "file_write" not in tools
    assert "notify_user" not in tools


def test_execution_whitelist_includes_worker_side_tools() -> None:
    """The execution mode is where real work happens — worker tools live here."""
    tools = set(BUILTIN_MODES["execution"].allowed_tools)
    assert "shell_exec" in tools
    assert "file_write" in tools
    assert "http_request" in tools
    assert "kanban_update" in tools


def test_discussion_has_no_tools() -> None:
    """Discussion is pure conversation — agents must not call tools."""
    assert BUILTIN_MODES["discussion"].allowed_tools == ()


# ---------------------------------------------------------------------------
# resolve_mode_config — built-ins
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["planning", "discussion", "execution"])
def test_resolve_builtin_returns_the_catalog_entry(mode: str) -> None:
    cfg = resolve_mode_config(mode)
    assert cfg is BUILTIN_MODES[mode]


def test_resolve_unknown_builtin_raises() -> None:
    with pytest.raises(ValueError, match="unknown chat mode"):
        resolve_mode_config("not-a-mode")


# ---------------------------------------------------------------------------
# resolve_mode_config — custom modes
# ---------------------------------------------------------------------------
def test_resolve_custom_uses_tenant_registry() -> None:
    custom = CustomModeSpec(
        name="design-review",
        label_es="Revisión de diseño",
        label_en="Design review",
        system_prompt="Estás en Design Review.",
        allowed_tools=("file_read", "task_comment"),
        planning_subgraph=False,
    )
    cfg = resolve_mode_config(
        ChatMode.CUSTOM.value,
        custom_mode_name="design-review",
        custom_modes={"design-review": custom},
    )
    assert cfg.name == "design-review"
    assert cfg.label_es == "Revisión de diseño"
    assert "Design Review" in cfg.system_prompt
    assert set(cfg.allowed_tools) == {"file_read", "task_comment"}


def test_resolve_custom_without_name_raises() -> None:
    with pytest.raises(ValueError, match="custom_mode_name"):
        resolve_mode_config(ChatMode.CUSTOM.value)


def test_resolve_missing_custom_mode_falls_back_to_planning_safely() -> None:
    """An unknown custom name must NOT silently become execution — that
    would unlock shell_exec/file_write for an unconfigured tenant mode."""
    cfg = resolve_mode_config(
        ChatMode.CUSTOM.value,
        custom_mode_name="not-registered",
        custom_modes={},
    )
    # Label/name reflect the user input, but the system prompt + tool
    # set come from planning (safe).
    assert cfg.name == "not-registered"
    assert cfg.label_es == "not-registered"
    tools = set(cfg.allowed_tools)
    assert "shell_exec" not in tools
    assert "file_write" not in tools
    # Same prompt body as planning's (the safe default).
    assert cfg.system_prompt == BUILTIN_MODES["planning"].system_prompt
