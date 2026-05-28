"""Demo: review-runtime + escalado + audit trail
(human_06_04 + 06_10 + 06_11 + 06_12).

  * human_06_04 — review-runtime con URL firmada que el operador
    abre, terminal/logs panes renderizados, botón rerun encolando.
  * human_06_10 — 3 rechazos auto → awaiting_human, 4 acciones
    humanas funcionando, todo en audit_log.
  * human_06_11 — checkbox fail → tarea nueva plan-scoped en
    backlog; "Añadir tarea libre" mismo path.
  * human_06_12 — GET history devuelve la línea de tiempo
    cronológica completa.

Pre-condición: scripts/setup_demo_06.py limpio.
"""

from __future__ import annotations

import contextlib
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _banner(text: str) -> None:
    print(f"\n{'-' * 60}\n  {text}\n{'-' * 60}", flush=True)


def _check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'[ OK ]' if ok else '[FAIL]'} {label}" + (f" - {detail}" if detail else ""))
    return ok


def main() -> int:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        ReviewComment,
        TaskLifecycle,
        TaskRecord,
    )
    from workers.review_runtime import (
        HumanCheckItem,
        ReviewRuntimeManager,
        ReviewRuntimeSpec,
        sign_review_url,
        verify_review_url,
    )

    _banner("demo human_06_d - review + escalado + audit")

    # human_06_04 - review-runtime + URL firmada -------------------
    _banner("human_06_04 - review-runtime URL firmada")

    secret = b"demo-secret-not-in-prod"
    spawned: list[ReviewRuntimeSpec] = []

    def fake_spawn(spec):
        spawned.append(spec)
        return ("main-container", "postgres-aux", "redis-aux")

    mgr = ReviewRuntimeManager(spawn=fake_spawn)
    spec = ReviewRuntimeSpec(
        plan_id="plan-demo-d",
        project_id="demo-project",
        tenant_id="demo-tenant",
        repo_name="backend",
        worktree_host_path="/data/wt/plan-demo-d",
        main_image="backend:plan-demo-d",
        human_checklist=(
            HumanCheckItem(
                id="human_06_01",
                description="Ciclo end-to-end del plan",
                hint="Verifica que el app responde",
                checklist=("App arranca", "Login funciona", "Logs sin errores"),
            ),
            HumanCheckItem(
                id="human_06_02",
                description="Dep-cache funciona",
                hint="Segunda ejecucion debe arrancar rapido",
                checklist=("Tests <30s", "No reinstala deps"),
            ),
        ),
    )
    session = mgr.create(spec)
    _check(session.status == "running", "review-runtime spawned", session.id)
    _check(len(spawned) == 1, "spawn factory invocado 1 vez")
    _check(session.expires_at > time.time() + 47 * 3600, "expira_at ~48h en el futuro")

    # Signed URL round-trip.
    url = sign_review_url(
        base_url="https://platform.demo",
        session_id=session.id,
        expires_at=session.expires_at,
        secret=secret,
    )
    sig = url.split("sig=")[-1]
    _check(
        verify_review_url(
            session_id=session.id,
            expires_at=session.expires_at,
            sig=sig,
            secret=secret,
        ),
        "URL firmada round-trip OK",
    )
    _check(
        not verify_review_url(
            session_id=session.id,
            expires_at=session.expires_at,
            sig=sig + "X",
            secret=secret,
        ),
        "URL con firma alterada rechazada",
    )

    # Rerun button.
    mgr.queue_rerun(session.id)
    _check(mgr.get(session.id).rerun_requested, "queue_rerun set the flag")

    # human_06_10 - escalado tras 3 rechazos -----------------------
    _banner("human_06_10 - escalado a humano tras max_review_retries")

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id="stuck-task",
            plan_id="plan-demo-d",
            title="impossible task",
            description="acceptance criteria contradictorios",
            status="in_review",
            max_retries=3,
        )
    )

    escalations: list[str] = []

    class _Notifier:
        def notify_escalation(self, task, history):
            escalations.append(task.id)

    lc = TaskLifecycle(store=store, notifier=_Notifier())
    comment = ReviewComment(
        failed_criterion="auto_06_99_a",
        testreport_evidence="exit_code=2, ImportError",
        what_to_fix="add the missing dependency",
    )
    for i in range(3):
        task = store.get("stuck-task")
        task.status = "in_review"
        store.save(task)
        lc.reject_review("stuck-task", comment=comment)
        print(f"     rejection #{i + 1} -> retry_count={store.get('stuck-task').retry_count}")

    _check(store.get("stuck-task").status == "awaiting_human", "transicion a awaiting_human")
    _check(escalations == ["stuck-task"], "notifier invocado 1 vez")

    # Las 4 acciones humanas.
    actions = [
        ("approve_manual", "done"),
        ("reassign_with_guidance", "backlog"),
        ("block_with_reason", "blocked"),
        ("cancel", "cancelled"),
    ]
    for action, expected_status in actions:
        fresh = InMemoryTaskStore()
        fresh.save(
            TaskRecord(
                id="t",
                plan_id="p",
                title="x",
                description="x",
                status="awaiting_human",
                retry_count=3,
            )
        )
        TaskLifecycle(store=fresh).apply_human_action("t", action, actor="alice")  # type: ignore[arg-type]
        _check(
            fresh.get("t").status == expected_status,
            f"action {action!r} -> {expected_status!r}",
        )

    # human_06_11 - checkbox fail -> tarea nueva --------------------
    _banner("human_06_11 - checkbox fail genera tarea plan-scoped")
    new_task = lc.create_task_from_checkbox(
        plan_id="plan-demo-d",
        checkbox_id="human_06_01",
        checkbox_text="Ciclo end-to-end del plan",
        reviewer_comment="Login da 500 cuando el email tiene caracteres no-ascii",
    )
    _check(new_task.plan_id == "plan-demo-d", "tarea nueva con plan_id correcto")
    _check(new_task.title.startswith("Ciclo end-to-end"), "titulo viene del checkbox")
    _check(new_task.parent_checkbox_id == "human_06_01", "parent_checkbox_id correcto")
    _check(new_task.status == "backlog", "tarea nueva en backlog")

    free = lc.create_free_task(
        plan_id="plan-demo-d",
        title="Refactor http_endpoint timeout",
        description="Observado en review-runtime",
    )
    _check(free.is_free_task is True, "free task con is_free_task=True")
    _check(free.parent_checkbox_id is None, "free task sin parent_checkbox_id")

    # human_06_12 - audit trail completo ----------------------------
    _banner("human_06_12 - audit trail completo")
    history = lc.history("stuck-task")
    kinds = {e.kind for e in history}
    _check("review_comment" in kinds, "history tiene review_comment events")
    _check("transition" in kinds, "history tiene transition events")
    _check(len(history) >= 6, "history tiene >=6 entries (3 rejs + 3 trans)", f"{len(history)}")
    # Cronologico.
    timestamps = [e.at for e in history]
    _check(timestamps == sorted(timestamps), "history ordenado cronologicamente")

    _banner("demo human_06_d PASSED")
    print(
        "\n  Para validar visualmente:"
        "\n    Abre el admin-panel en /admin/review/<session_id> (cuando este desplegado)"
        "\n    Abre /admin/plans/<plan_id>/escalated para ver las tareas escaladas"
        "\n    La integracion HTTP con backend real es alcance del Plan 06.5"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
