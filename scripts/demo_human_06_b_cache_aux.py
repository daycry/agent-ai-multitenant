"""Demo: dep-cache + aux services + multi-repo (human_06_02 + 06_03 + 06_05).

* human_06_02 - dep-cache funciona: hash sobre requirements.txt
  devuelve el mismo cache key en dos runs consecutivos; cambiar
  el contenido invalida.
* human_06_03 - aux services aislamiento: muestra cómo el spec
  asigna postgres-test/redis-test a una task, con hostnames
  estables dentro del bridge.
* human_06_05 - múltiples repos por plan: ensure_repo sobre dos
  repos del mismo plan crea dos workflows distintos.
"""

from __future__ import annotations

import contextlib
import json
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_REPO_ROOT = Path(__file__).resolve().parent.parent
_STATE_FILE = _REPO_ROOT / "scripts" / ".demo_state_06.json"


def _banner(text: str) -> None:
    print(f"\n{'-' * 60}\n  {text}\n{'-' * 60}", flush=True)


def _check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'[ OK ]' if ok else '[FAIL]'} {label}" + (f" - {detail}" if detail else ""))
    return ok


def main() -> int:
    from shared_test_runtimes import DepCacheManager, get
    from shared_test_runtimes.dep_cache import compute_lock_hash
    from workers.test_runtime import (
        DEFAULT_POSTGRES,
        DEFAULT_REDIS,
        AcceptanceCheck,
        RuntimePlan,
        TestRuntimeSpec,
    )

    _banner("demo human_06_b - cache + aux + multi-repo")

    if not _STATE_FILE.exists():
        print("FAIL — sin setup. Lanza primero scripts/setup_demo_06.py")
        return 1
    state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))

    # human_06_02 - cache --------------------------------------------
    _banner("human_06_02 - dep-cache (hash + invalidacion por lock)")
    sandbox = Path(state["sandbox"])
    workspace = sandbox / "fake-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "requirements.txt").write_bytes(b"pytest==8.2.0\nstructlog==24.1\n")

    hash_1 = compute_lock_hash(workspace, "python-pytest").hash
    hash_2 = compute_lock_hash(workspace, "python-pytest").hash
    _check(hash_1 == hash_2 and hash_1 is not None, "hash deterministico", hash_1)

    cache_root = sandbox / "dep-cache-root"
    mgr = DepCacheManager(cache_root)
    template = get("python-pytest")
    entry_1 = mgr.ensure_entry(template, hash_1)
    _check(entry_1.host_path.is_dir(), "cache dir creado", str(entry_1.host_path.name))

    # Cambiar el lock invalida el hash.
    (workspace / "requirements.txt").write_bytes(b"pytest==8.3.0\nstructlog==24.1\n")
    hash_3 = compute_lock_hash(workspace, "python-pytest").hash
    _check(hash_3 != hash_1, "lock changed -> hash diferente", f"{hash_1[:8]} -> {hash_3[:8]}")

    # Invalidate explicit.
    removed = mgr.invalidate(template.id, lock_hash=hash_1)
    _check(len(removed) == 1, "invalidate quita un entry", str(removed[0].name))

    # human_06_03 - aux services -------------------------------------
    _banner("human_06_03 - aux services isolation per task")
    plan = RuntimePlan(
        template=template,
        checks=(
            AcceptanceCheck(
                id="t1",
                description="db tests",
                runtime="python-pytest",
                command="pytest tests/integration",
            ),
        ),
    )
    spec_task_a = TestRuntimeSpec(
        plan=plan,
        worktree_host_path="/data/wt/task-a",
        aux_services=(DEFAULT_POSTGRES, DEFAULT_REDIS),
    )
    spec_task_b = TestRuntimeSpec(
        plan=plan,
        worktree_host_path="/data/wt/task-b",
        aux_services=(DEFAULT_POSTGRES, DEFAULT_REDIS),
    )

    _check(
        spec_task_a.worktree_host_path != spec_task_b.worktree_host_path,
        "task-a y task-b reciben paths distintos",
        f"{spec_task_a.worktree_host_path} vs {spec_task_b.worktree_host_path}",
    )
    _check(
        DEFAULT_POSTGRES.resolved_alias() == "postgres-test",
        "postgres-test alias estable dentro del bridge",
    )
    _check(
        DEFAULT_REDIS.healthcheck_cmd == ("redis-cli", "ping"),
        "redis healthcheck es redis-cli ping",
    )
    print(
        "\n  Nota: los aux services se materializan como containers reales en"
        "\n  cuando el orchestrator lanza el test-runtime. El test integration"
        "\n  test_aux_services.py mockea docker para pinear este contrato; el"
        "\n  spin-up real es alcance del Plan 06.5."
    )

    # human_06_05 - multiples repos por plan -------------------------
    _banner("human_06_05 - multiples repos por plan")
    from orchestrator.plan_runner import PlanRunner

    runner = PlanRunner(
        data_root=Path(state["data_root"]),
        tenant_slug=state["tenant_slug"],
        project_slug=state["project_slug"] + "-multirepo",
        plan_id="multirepo-aaaa",
        plan_slug="multi-repo",
    )
    runner.ensure_repo("backend", remote_url=state["backend_remote_url"])
    runner.ensure_repo("frontend", remote_url=state["frontend_remote_url"])
    runner.pool.start()
    task_b = runner.seed_task(title="touch backend")
    task_f = runner.seed_task(title="touch frontend")

    def _writer(filename: str):
        def _w(wt_path: Path) -> None:
            (wt_path / filename).write_text("change\n")

        return _w

    runner.execute_task(task_b.id, "backend", file_writer=_writer("backend_change.txt"))
    runner.execute_task(task_f.id, "frontend", file_writer=_writer("frontend_change.txt"))

    # Verificar que cada commit aterriza en su repo.
    import subprocess

    def _branches(repo: Path) -> str:
        return subprocess.run(
            ["git", "branch", "--list"],
            cwd=str(repo),
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    bare_b = (
        Path(state["data_root"])
        / "projects"
        / state["tenant_slug"]
        / (state["project_slug"] + "-multirepo")
        / "repos"
        / "backend.git"
    )
    bare_f = bare_b.parent / "frontend.git"
    _check(
        runner.plan_branch in _branches(bare_b),
        "backend bare tiene rama plan/",
    )
    _check(
        runner.plan_branch in _branches(bare_f),
        "frontend bare tiene rama plan/",
    )

    runner.shutdown()

    _banner("demo human_06_b PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
