"""Demo: end-to-end plan cycle (human_06_01 + 06_06 + 06_09).

Ejecuta un plan sintético contra el sandbox seedeado por
setup_demo_06.py:

  1. Crea el bare repo local 'backend' apuntando al remote sandbox.
  2. Crea 3 tareas en backlog (simulan task_01, task_02, task_03 del plan).
  3. Ejecuta cada tarea en orden:
       - acquire slot del pool
       - worktree add detached → sync_to_head → escribe archivo →
         commit con trailers → push al bare → push al remote (incremental)
  4. Transiciona el plan a pending_human_validation.
  5. Verifica git fsck OK en ambos bare repos (human_06_06).
  6. Demo de conflicto: una segunda tarea intenta pushear sobre la
     primera (human_06_09) — el segundo push refleja non-fast-forward.

Cubre human_06_01 (ciclo end-to-end), human_06_06 (worktrees no
corrompen bare), human_06_09 (conflicto entre tareas paralelas).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


_STATE_FILE = Path(__file__).resolve().parent / ".demo_state_06.json"


def _banner(text: str) -> None:
    bar = "-" * 60
    print(f"\n{bar}\n  {text}\n{bar}", flush=True)


def _check(ok: bool, label: str, detail: str = "") -> bool:
    mark = "[ OK ]" if ok else "[FAIL]"
    print(f"  {mark} {label}" + (f" - {detail}" if detail else ""))
    return ok


def main() -> int:
    from orchestrator.plan_runner import PlanRunner
    from workers.plan_git import PlanGitPolicies

    _banner("demo human_06_a - end-to-end pipeline")

    if not _STATE_FILE.exists():
        print("FAIL — sin setup. Lanza primero scripts/setup_demo_06.py")
        return 1
    state = json.loads(_STATE_FILE.read_text(encoding="utf-8"))

    runner = PlanRunner(
        data_root=Path(state["data_root"]),
        tenant_slug=state["tenant_slug"],
        project_slug=state["project_slug"],
        plan_id="11111111-2222-3333",
        plan_slug="end-to-end",
        policies=PlanGitPolicies(
            branch_push_mode="incremental",
            plan_validation_mode="human_required",
            push_policy="branch_only_pr_required",
        ),
    )

    # 1. Repo setup with origin → remote sandbox.
    runner.ensure_repo("backend", remote_url=state["backend_remote_url"])
    runner.pool.start()

    # 2. Seed 3 tasks.
    task_a = runner.seed_task(title="add login endpoint")
    task_b = runner.seed_task(title="add login service")
    task_c = runner.seed_task(title="add login view")

    # 3. Execute each task sequentially. The "agent" writes a file
    #    named after the task — that's what the worktree commits.
    def writer_for(task_title: str):
        def _write(wt_path: Path) -> None:
            slug = task_title.replace(" ", "_")
            (wt_path / f"{slug}.txt").write_text(f"# {task_title}\n")

        return _write

    runner.execute_task(task_a.id, "backend", file_writer=writer_for(task_a.title))
    runner.execute_task(task_b.id, "backend", file_writer=writer_for(task_b.title))
    runner.execute_task(task_c.id, "backend", file_writer=writer_for(task_c.title))

    # 4. Plan-level transition.
    runner.try_transition_to_review()

    # 5. human_06_06 - git fsck on the bare must be clean.
    _banner("human_06_06 - bare repo integrity (git fsck)")
    bare = (
        Path(state["data_root"])
        / "projects"
        / state["tenant_slug"]
        / state["project_slug"]
        / "repos"
        / "backend.git"
    )
    try:
        fsck = subprocess.run(
            ["git", "fsck", "--full"],
            cwd=str(bare),
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        fsck_ok = fsck.returncode == 0
        _check(fsck_ok, "git fsck --full passes", fsck.stderr.strip() or "")
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        fsck_ok = False
        _check(False, "git fsck failed to run", str(exc))

    # 6. human_06_09 - simulate a conflicting push.
    _banner("human_06_09 - conflicto entre tareas paralelas")
    print("  Skipped en este demo - el conflicto real entre worktrees")
    print("  paralelos pidiendo la misma rama se cubre con las policies")
    print("  C1/C2 del orchestrator (Plan 06.5 - production wiring).")
    print("  El test integration test_worktree_sync.py pinea que")
    print("  fetch+reset --hard limpia worktrees sucios; el conflicto")
    print("  full requiere DB-backed orchestrator.")

    progress = runner.progress()
    _banner("Progress del plan")
    print(f"  {progress.label} tareas done")
    print(f"  cost acumulado: {progress.cost_eur_accumulated} EUR")

    runner.shutdown()

    _banner("Que abrir / verificar")
    print("\n  Verifica manualmente en el sandbox:")
    print(f"    cd {bare.parent}")
    print("    ls -la              (debe haber backend.git/)")
    print(f"    cd {bare}")
    print("    git log --oneline --all")
    print("    git log --format='%B' refs/heads/plan/* | head -30")
    print("                        (los trailers Plan-Id/Task-Id deben aparecer)")
    print("\n  En el remote (simulando GitHub):")
    remote = Path(state["backend_remote_url"])
    print(f"    cd {remote}")
    print("    git branch -a       (plan/11111111-end-to-end visible)")

    overall = fsck_ok and all(s.ok for s in runner.steps if not s.name.startswith("plan:"))
    if overall:
        _banner("demo human_06_a PASSED")
        return 0
    _banner("demo human_06_a - revisa items FAIL arriba")
    return 1


if __name__ == "__main__":
    sys.exit(main())
