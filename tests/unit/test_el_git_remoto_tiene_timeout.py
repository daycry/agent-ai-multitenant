"""Las operaciones de git contra un remoto tienen su propio timeout (`task_cv_43`).

Auditoría 2026-09-01 (hallazgo B-11): `_run_git` clavaba `timeout=120` para
TODO —un `git status` y un `git push` a un remoto que no responde— y un
`TimeoutExpired` salía sin envolver, con lo que el auto-PR o el sync de un
worktree morían con una traza en vez de con un `GitCommandError` que el
llamador sabe tratar. Ahora las operaciones remotas (`fetch`, `push`,
`ls-remote`, `clone`, `pull`) usan `WORKERS_GIT_REMOTE_TIMEOUT_S` y el
vencimiento se convierte en `GitCommandError` con el comando y el límite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from workers import git_repos
from workers.config import Settings

pytestmark = pytest.mark.unit


@pytest.fixture()
def seen(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], float | None]]:
    calls: list[tuple[list[str], float | None]] = []

    def _fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append((list(args), kw.get("timeout")))
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(git_repos.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        git_repos, "get_settings", lambda: Settings(git_remote_timeout_s=7), raising=False
    )
    return calls


def test_remote_operations_use_the_remote_timeout(seen: list, tmp_path: Path) -> None:
    git_repos._run_git("fetch", "origin", cwd=tmp_path)
    git_repos._run_git("push", "origin", "HEAD:refs/heads/x", cwd=tmp_path)
    git_repos._run_git("ls-remote", "--heads", "origin", cwd=tmp_path)

    assert [timeout for _args, timeout in seen] == [7, 7, 7]


def test_local_operations_keep_the_local_timeout(seen: list, tmp_path: Path) -> None:
    git_repos._run_git("status", "--porcelain", cwd=tmp_path)
    git_repos._run_git("-C", str(tmp_path), "rev-parse", "HEAD", cwd=tmp_path)

    assert [timeout for _args, timeout in seen] == [
        git_repos.LOCAL_GIT_TIMEOUT_S,
        git_repos.LOCAL_GIT_TIMEOUT_S,
    ]


def test_a_hung_remote_becomes_a_git_command_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _hang(args: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=list(args), timeout=float(kw.get("timeout") or 0))

    monkeypatch.setattr(git_repos.subprocess, "run", _hang)
    monkeypatch.setattr(
        git_repos, "get_settings", lambda: Settings(git_remote_timeout_s=3), raising=False
    )

    with pytest.raises(git_repos.GitCommandError) as excinfo:
        git_repos._run_git("push", "origin", "HEAD", cwd=tmp_path)

    message = str(excinfo.value)
    assert "push" in message and "3" in message, message
