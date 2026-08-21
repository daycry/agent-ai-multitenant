"""Seed the shared scenario for Plan 06 human tests.

Creates a sandbox directory under ``scripts/.demo_06/`` with:

  * Two bare repos: backend.git + frontend.git (covers human_06_05).
  * A "remote" bare for each, so push tests can verify the remote
    side too without going through real GitHub.
  * State file ``scripts/.demo_state_06.json`` with paths the demos
    read.

Idempotent: re-running cleans the sandbox and rebuilds it.

Usage:
    .venv/Scripts/python scripts/setup_demo_06.py
"""

from __future__ import annotations

import contextlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_REPO_ROOT = Path(__file__).resolve().parents[2]
_SANDBOX = Path(__file__).resolve().parent / ".demo_06"
_STATE_FILE = Path(__file__).resolve().parent / ".demo_state_06.json"


def _banner(text: str) -> None:
    bar = "=" * 72
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def _force_rmtree(path: Path) -> None:
    """``shutil.rmtree`` with read-only-flag handling.

    Git pack files on Windows are marked read-only after creation,
    and `shutil.rmtree` cannot delete them without first clearing
    the flag. The standard ``onerror`` recipe does this. See:
    https://docs.python.org/3/library/shutil.html#rmtree-example
    """
    import os
    import stat

    def _on_rm_error(func, p, _exc):  # type: ignore[no-untyped-def]
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except FileNotFoundError:
            pass

    shutil.rmtree(path, onerror=_on_rm_error)


def _git(*args: str, cwd: Path) -> None:
    import os

    # Inherit os.environ — on Windows git needs HOMEDRIVE/USERPROFILE/
    # SYSTEMROOT to read its own config; replacing the env from scratch
    # makes `git clone` exit 128 with no useful message.
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Demo",
        "GIT_AUTHOR_EMAIL": "demo@demo.test",
        "GIT_COMMITTER_NAME": "Demo",
        "GIT_COMMITTER_EMAIL": "demo@demo.test",
        "GIT_TERMINAL_PROMPT": "0",
    }
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (rc={result.returncode}):\n"
            f"  stdout: {result.stdout.strip()}\n"
            f"  stderr: {result.stderr.strip()}"
        )


def _seed_remote_bare(name: str) -> Path:
    """Build a 'remote' bare with an initial main commit, return its path."""
    remote_root = _SANDBOX / "remote"
    bare = remote_root / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    _git("init", "--bare", "--initial-branch", "main", str(bare), cwd=bare.parent)
    scratch = remote_root / f"{name}.seed"
    if scratch.exists():
        # Windows leaves stale handles after a partial run; defensive
        # rmtree avoids the "destination path already exists" git error.
        _force_rmtree(scratch)
    _git("clone", str(bare), str(scratch), cwd=remote_root)
    (scratch / "README.md").write_text(f"# {name}\n\nseed for plan-06 demos\n")
    _git("add", "README.md", cwd=scratch)
    _git("commit", "-m", "seed", cwd=scratch)
    _git("push", "-u", "origin", "main", cwd=scratch)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=bare)
    _force_rmtree(scratch)
    return bare


def main() -> int:
    _banner("setup demo - escenario compartido Plan 06")

    # Clean previous sandbox. Windows leaves read-only flags on git
    # pack files which break plain rmtree — force-clear them first.
    if _SANDBOX.exists():
        print(f"  cleaning previous sandbox at {_SANDBOX}")
        _force_rmtree(_SANDBOX)
    _SANDBOX.mkdir(parents=True, exist_ok=True)

    # 1. Build two "remote" bares.
    print("\n=> creating remote bares (simulating GitHub)")
    backend_remote = _seed_remote_bare("backend")
    frontend_remote = _seed_remote_bare("frontend")
    print(f"     backend remote  : {backend_remote.relative_to(_REPO_ROOT)}")
    print(f"     frontend remote : {frontend_remote.relative_to(_REPO_ROOT)}")

    # 2. The local "data_root" the worker would use is just under
    #    the sandbox — the PlanRunner builds bare repos lazily from
    #    here on demand.
    data_root = _SANDBOX / "data"
    data_root.mkdir(parents=True, exist_ok=True)

    state = {
        "sandbox": str(_SANDBOX),
        "data_root": str(data_root),
        "tenant_slug": "demo-tenant",
        "project_slug": "demo-project",
        "backend_remote_url": str(backend_remote),
        "frontend_remote_url": str(frontend_remote),
    }
    _STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    print(f"\n  state -> {_STATE_FILE.relative_to(_REPO_ROOT)}")

    _banner("Setup OK. Lanza los demos:")
    print(
        "\n  .venv/Scripts/python scripts/demo_human_06_a_endtoend.py"
        "\n  .venv/Scripts/python scripts/demo_human_06_b_cache_aux.py"
        "\n  .venv/Scripts/python scripts/demo_human_06_c_pool_policies.py"
        "\n  .venv/Scripts/python scripts/demo_human_06_d_review_audit.py"
        "\n\n  O todos a la vez:"
        "\n  .\\scripts\\dev\\run-human-tests-06.ps1"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
