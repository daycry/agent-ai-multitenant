"""Unit: agent-runtime container env (regression 2026-06-26).

Two defects this pins down, both observed on a live ``claude_sdk`` run:

* **HOME = /workspace** made the Claude Code CLI write its ~25KB ``.claude.json``
  (and ``.claude/``) INTO the agent's project worktree. The agent then listed and
  even read that file back, polluting every model_call's context (slower + more
  expensive). The CLI's HOME must live OUTSIDE the worktree, on its own writable
  tmpfs (the rootfs is read-only).
* A single **600s** wall was too tight for ``claude_sdk`` — it spawns the Node CLI
  and its high-effort model calls are slow — while it is plenty for the fast HTTP
  providers (ollama/azure_foundry/copilot). The budget must depend on the kind.
"""

from __future__ import annotations

from workers.config import Settings
from workers.isolation import build_hardened_run_kwargs


def test_home_is_not_the_worktree() -> None:
    kw = build_hardened_run_kwargs(Settings())
    assert kw["environment"]["HOME"] != "/workspace"


def test_home_is_a_dedicated_writable_tmpfs() -> None:
    kw = build_hardened_run_kwargs(Settings())
    home = kw["environment"]["HOME"]
    # size-capped tmpfs so the read-only rootfs still lets the CLI write its config
    assert home in kw["tmpfs"]


def test_home_stays_off_the_worktree_even_with_a_bind_mount() -> None:
    kw = build_hardened_run_kwargs(Settings(), workspace_host_path="/data/ws")
    home = kw["environment"]["HOME"]
    assert home != "/workspace"
    assert home in kw["tmpfs"]  # home is its own tmpfs even when /workspace is a bind


def test_claude_sdk_gets_a_longer_container_budget() -> None:
    s = Settings()
    assert s.container_timeout_for_kind("claude_sdk") > s.container_timeout_for_kind("ollama")


def test_fast_providers_use_the_base_timeout() -> None:
    s = Settings()
    assert s.container_timeout_for_kind("ollama") == s.container_run_timeout_s
    assert s.container_timeout_for_kind(None) == s.container_run_timeout_s


def test_claude_sdk_uses_the_sdk_specific_timeout() -> None:
    s = Settings()
    assert s.container_timeout_for_kind("claude_sdk") == s.container_run_timeout_claude_sdk_s


# --- F2b.5 (auditoría 2026-07-02): presupuesto propio para review runs ----------
# El reviewer corría con presupuesto de implementador (50 iter / 2h) cuando la
# evidencia post-ADR-0095 muestra reviews convergiendo en 13-22 steps.


def test_review_runs_get_a_tighter_iteration_cap() -> None:
    s = Settings()
    review_cap = s.agent_max_iterations_for_kind("claude_sdk", is_review=True)
    implementer_cap = s.agent_max_iterations_for_kind("claude_sdk")
    assert review_cap is not None and implementer_cap is not None
    assert review_cap < implementer_cap
    assert review_cap == s.agent_max_iterations_review


def test_review_runs_get_a_shorter_wall_clock() -> None:
    s = Settings()
    assert s.container_timeout_for_kind(
        "claude_sdk", is_review=True
    ) < s.container_timeout_for_kind("claude_sdk")
    # Los providers HTTP rápidos no cambian: su budget base ya es corto.
    assert s.container_timeout_for_kind("ollama", is_review=True) == s.container_run_timeout_s


def test_review_grace_composes_with_review_budget() -> None:
    s = Settings()
    assert (
        s.container_timeout_with_grace_for_kind("claude_sdk", is_review=True)
        == s.container_timeout_for_kind("claude_sdk", is_review=True) + s.container_grace_s
    )
