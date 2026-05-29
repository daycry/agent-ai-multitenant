"""Integration tests: structural docs guardrail (Plan 07 task_07_02).

The guardrail is a standalone validator (NOT the Plan-11 guardrails
engine). These tests build a real docs tree with the task_07_01
bootstrap, then mutate it to exercise every violation path. They are
"integration" because they touch the filesystem (and, in the final test,
a real ``git`` working tree) rather than because they need a DB/Redis —
so they run offline with no infra.

Coverage:
  * happy path — a complete bootstrapped tree passes;
  * negative — deleting a canonical folder flags MISSING_CANONICAL_FOLDER;
  * negative — renaming a folder flags MISSING (old) + STRAY (new);
  * negative — a stray top-level folder is flagged;
  * edge — missing docs/ root, docs/ as a file, canonical folder as a
    file, loose top-level files ignored, multiple violations ordered;
  * the check() gate ok/violations contract + the CLI exit codes;
  * a real git worktree passes (proves "operates on a working-tree path").
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from api_server.docs_structure import (
    CANONICAL_DOC_FOLDERS,
    bootstrap_docs_structure,
    check_docs_structure,
    validate_docs_structure,
)
from api_server.docs_structure.__main__ import EXIT_OK, EXIT_VIOLATIONS, main
from api_server.docs_structure.validator import ViolationKind

pytestmark = pytest.mark.integration


# --- fixtures / helpers ----------------------------------------------------


@pytest.fixture()
def canonical_repo(tmp_path: Path) -> Path:
    """A repo working tree with the full canonical docs structure."""
    bootstrap_docs_structure(tmp_path)
    return tmp_path


def _docs(repo: Path) -> Path:
    return repo / "docs"


def _kinds(violations: list) -> list[ViolationKind]:  # type: ignore[type-arg]
    return [v.kind for v in violations]


# --- happy path ------------------------------------------------------------


def test_complete_tree_passes(canonical_repo: Path) -> None:
    assert validate_docs_structure(canonical_repo) == []


def test_complete_tree_check_gate_is_ok(canonical_repo: Path) -> None:
    result = check_docs_structure(canonical_repo)
    assert result.ok is True
    assert result.violations == ()
    assert "OK" in result.summary()


def test_loose_top_level_file_is_ignored(canonical_repo: Path) -> None:
    # A README/index sitting directly in docs/ is a file, not one of the
    # seven folders — the structural guardrail must not flag it.
    (_docs(canonical_repo) / "index.md").write_text("# Docs\n", encoding="utf-8")
    assert validate_docs_structure(canonical_repo) == []


# --- negative: deleted / renamed folders -----------------------------------


def test_deleting_a_folder_yields_missing_violation(canonical_repo: Path) -> None:
    victim = CANONICAL_DOC_FOLDERS[3].name  # 04-reference
    shutil.rmtree(_docs(canonical_repo) / victim)

    violations = validate_docs_structure(canonical_repo)

    assert len(violations) == 1
    v = violations[0]
    assert v.kind is ViolationKind.MISSING_CANONICAL_FOLDER
    assert v.path.endswith(f"docs/{victim}")
    assert victim in v.message


def test_renaming_a_folder_yields_missing_plus_stray(canonical_repo: Path) -> None:
    original = CANONICAL_DOC_FOLDERS[0].name  # 01-overview
    renamed = "01-introduction"
    (_docs(canonical_repo) / original).rename(_docs(canonical_repo) / renamed)

    violations = validate_docs_structure(canonical_repo)

    kinds = _kinds(violations)
    assert ViolationKind.MISSING_CANONICAL_FOLDER in kinds
    assert ViolationKind.STRAY_ENTRY in kinds
    missing = next(v for v in violations if v.kind is ViolationKind.MISSING_CANONICAL_FOLDER)
    stray = next(v for v in violations if v.kind is ViolationKind.STRAY_ENTRY)
    assert missing.path.endswith(f"docs/{original}")
    assert stray.path.endswith(f"docs/{renamed}")


def test_stray_folder_is_flagged(canonical_repo: Path) -> None:
    (_docs(canonical_repo) / "99-scratch").mkdir()

    violations = validate_docs_structure(canonical_repo)

    assert len(violations) == 1
    v = violations[0]
    assert v.kind is ViolationKind.STRAY_ENTRY
    assert v.path.endswith("docs/99-scratch")
    assert "99-scratch" in v.message


def test_multiple_violations_are_ordered(canonical_repo: Path) -> None:
    # Delete two canonical folders and add a stray. Expect: the missing
    # ones first (in canonical order), then the stray.
    shutil.rmtree(_docs(canonical_repo) / CANONICAL_DOC_FOLDERS[1].name)  # 02-...
    shutil.rmtree(_docs(canonical_repo) / CANONICAL_DOC_FOLDERS[5].name)  # 06-...
    (_docs(canonical_repo) / "zz-stray").mkdir()

    violations = validate_docs_structure(canonical_repo)

    assert _kinds(violations) == [
        ViolationKind.MISSING_CANONICAL_FOLDER,
        ViolationKind.MISSING_CANONICAL_FOLDER,
        ViolationKind.STRAY_ENTRY,
    ]
    # Canonical order preserved: 02 before 06.
    assert violations[0].path.endswith(CANONICAL_DOC_FOLDERS[1].name)
    assert violations[1].path.endswith(CANONICAL_DOC_FOLDERS[5].name)


# --- edge: docs root problems ----------------------------------------------


def test_missing_docs_root_is_a_single_violation(tmp_path: Path) -> None:
    # An empty repo (no docs/ at all) yields exactly one root violation.
    violations = validate_docs_structure(tmp_path)
    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.MISSING_DOCS_ROOT
    assert violations[0].path.endswith("docs")


def test_docs_root_as_file_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "docs").write_text("not a dir", encoding="utf-8")
    violations = validate_docs_structure(tmp_path)
    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.DOCS_ROOT_NOT_A_DIR


def test_canonical_folder_as_file_is_flagged(canonical_repo: Path) -> None:
    victim = CANONICAL_DOC_FOLDERS[2].name  # 03-guides
    target = _docs(canonical_repo) / victim
    shutil.rmtree(target)
    target.write_text("oops, a file", encoding="utf-8")

    violations = validate_docs_structure(canonical_repo)

    assert len(violations) == 1
    assert violations[0].kind is ViolationKind.CANONICAL_FOLDER_NOT_A_DIR
    assert violations[0].path.endswith(f"docs/{victim}")


# --- the check() gate + CLI exit-code contract -----------------------------


def test_check_gate_reports_violations(canonical_repo: Path) -> None:
    shutil.rmtree(_docs(canonical_repo) / CANONICAL_DOC_FOLDERS[6].name)  # 07-changelog
    result = check_docs_structure(canonical_repo)
    assert result.ok is False
    assert len(result.violations) == 1
    assert "INVALID" in result.summary()
    assert CANONICAL_DOC_FOLDERS[6].name in result.summary()


def test_cli_returns_zero_on_canonical_tree(canonical_repo: Path) -> None:
    assert main([str(canonical_repo)]) == EXIT_OK


def test_cli_returns_one_on_malformed_tree(canonical_repo: Path) -> None:
    shutil.rmtree(_docs(canonical_repo) / CANONICAL_DOC_FOLDERS[0].name)
    assert main([str(canonical_repo)]) == EXIT_VIOLATIONS


def test_cli_missing_docs_root_returns_one(tmp_path: Path) -> None:
    assert main([str(tmp_path)]) == EXIT_VIOLATIONS


# --- proves it works on a real git working tree ----------------------------


def test_validator_passes_on_a_real_git_worktree(tmp_path: Path) -> None:
    """A materialised git working tree (the worker's real artifact) with a
    bootstrapped docs tree passes the guardrail. This is the working-tree
    path the guardrail is designed to gate; no bare repo / remote needed."""
    env = {
        "GIT_AUTHOR_NAME": "T",
        "GIT_AUTHOR_EMAIL": "t@e",
        "GIT_COMMITTER_NAME": "T",
        "GIT_COMMITTER_EMAIL": "t@e",
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": __import__("os").environ.get("PATH", ""),
    }
    worktree = tmp_path / "repo"
    worktree.mkdir()
    subprocess.run(
        ["git", "init", str(worktree)],
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    bootstrap_docs_structure(worktree)
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(worktree),
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )
    subprocess.run(
        ["git", "commit", "-m", "seed docs"],
        cwd=str(worktree),
        check=True,
        capture_output=True,
        env=env,
        timeout=30,
    )

    assert validate_docs_structure(worktree) == []
    # And deleting a folder + committing makes the guardrail block it.
    shutil.rmtree(worktree / "docs" / CANONICAL_DOC_FOLDERS[4].name)
    assert check_docs_structure(worktree).ok is False
