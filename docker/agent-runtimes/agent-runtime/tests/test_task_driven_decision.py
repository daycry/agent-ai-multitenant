"""Task-driven decision context + CLI-artifact hygiene (regression 2026-06-26).

Three invariants that keep a `claude_sdk` agent converging instead of looping:

* ``file_list`` hides the Claude Code CLI's own droppings (``.claude.json`` /
  ``.claude/``) so the agent never reads 25KB of CLI state into its context.
* the decision prompt carries the task's **acceptance criteria** — the TASK's
  own definition of done — so behaviour (read / write / test) follows the task
  rather than a blanket "always write" rule baked into the system prompt.
* the base system prompt stays task-driven (mentions acceptance criteria; does
  not force file writes for every task).
"""

from __future__ import annotations

from agent_runtime.file_tools import WorkspaceFiles
from agent_runtime.providers import _DECIDE_SYSTEM, _criterion_text, _decide_messages


def test_file_list_hides_cli_artifacts(tmp_path) -> None:  # type: ignore[no-untyped-def]
    (tmp_path / ".claude.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".claude").mkdir()
    (tmp_path / "app.py").write_text("print(1)", encoding="utf-8")

    result = WorkspaceFiles(root=str(tmp_path)).file_list({"path": "."})

    assert result.ok, result.error
    names = {entry["name"] for entry in result.output["entries"]}
    assert ".claude.json" not in names
    assert ".claude" not in names
    assert "app.py" in names  # real workspace files are still listed


def test_criterion_text_renders_dict_and_string() -> None:
    assert _criterion_text("migraciones reversibles") == "migraciones reversibles"
    assert _criterion_text({"description": "seeders deterministas"}) == "seeders deterministas"
    # Unknown shape degrades to JSON rather than raising.
    assert "42" in _criterion_text({"weird": 42})


def test_decide_prompt_includes_acceptance_criteria() -> None:
    state = {
        "task": {
            "id": "t1",
            "title": "Implementar migraciones",
            "description": "Crear migraciones y seeders.",
            "acceptance_criteria": [
                {"description": "migraciones aplicables/revertibles en BD efímera"},
                "seeders deterministas para tests",
            ],
        }
    }
    messages = _decide_messages(state)
    user = next(m for m in messages if m.role == "user")
    assert "Acceptance criteria" in user.content
    assert "migraciones aplicables/revertibles en BD efímera" in user.content
    assert "seeders deterministas para tests" in user.content


def test_decide_prompt_omits_criteria_section_when_absent() -> None:
    state = {"task": {"id": "t1", "title": "Analizar", "description": "Solo leer."}}
    user = next(m for m in _decide_messages(state) if m.role == "user")
    assert "Acceptance criteria" not in user.content


def test_system_prompt_is_task_driven_not_write_only() -> None:
    lowered = _DECIDE_SYSTEM.lower()
    # task-driven: mentions the criteria as the done-definition...
    assert "acceptance criteria" in lowered
    # ...and does not impose an absolute "must write files before finishing".
    assert "do not finish before" not in lowered


# --- F1.6c (auditoría 2026-07-02): system prompt específico para reviews -------
# El run REVIEWER corría con el system prompt del IMPLEMENTADOR ("an
# implementation task means writing files… finish by calling submit_result")
# más un preámbulo que decía lo contrario ("Do NOT write files… END with
# <verdict>") — dos contratos en competencia dentro del mismo prompt.


def test_review_run_gets_review_system_prompt() -> None:
    state = {"task": {"title": "T"}, "is_review": True}
    system = next(m for m in _decide_messages(state) if m.role == "system")
    lowered = system.content.lower()
    assert "reviewer" in lowered
    assert "<verdict>" in system.content
    # Sin el contrato del implementador: ni write_file ni submit_result.
    assert "write_file" not in lowered
    assert "submit_result" not in lowered


def test_review_system_prompt_keeps_skill_preamble_first() -> None:
    state = {"task": {"title": "T"}, "is_review": True, "system_preamble": "SKILL-X primero"}
    system = next(m for m in _decide_messages(state) if m.role == "system")
    assert system.content.startswith("SKILL-X primero")
    assert "reviewer" in system.content.lower()


def test_implementer_system_prompt_unchanged_without_flag() -> None:
    state = {"task": {"title": "T"}}
    system = next(m for m in _decide_messages(state) if m.role == "system")
    assert "submit_result" in system.content  # el contrato del implementador sigue


def test_system_prompt_tells_agent_not_to_use_git() -> None:
    # Feature D: git is broken in the sandbox and the agent never commits — the
    # prompt makes that explicit so the agent doesn't waste turns on git.
    lowered = _DECIDE_SYSTEM.lower()
    assert "git" in lowered
    assert "persists" in lowered or "version control" in lowered
    # 2026-07-01: the agent tried `git status` via stack_exec (denied → exit -1);
    # the note must explicitly cover the stack_exec/shell path, not just commit/push.
    assert "stack_exec" in lowered
