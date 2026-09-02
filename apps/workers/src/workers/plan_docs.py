"""Documentación de cierre de plan: el changelog deja de ser aspiracional (T8, c4).

Auditoría de plataforma 2026-07-03, hallazgo **c4**: el criterio de cierre 4 de
`CLAUDE.md` —«entrada generada en `docs/07-changelog/{plan_id}.md`»— no lo cumplía
**ningún camino automático**. `api_server.tech_writer.generate_plan_docs` y
`render_changelog` existían, estaban testeados y solo los llamaban los tests;
`_on_task_done` transicionaba el plan y arrancaba el review-runtime, nada más. El
agente Technical Writer estaba sembrado y nadie le creaba la tarea.

Este módulo es el caller que faltaba. Vive en el **worker** y no en la api-server
por la razón de siempre: la api-server no monta `agent-data`, así que cualquier
operación de git o de disco sobre el repo del proyecto tiene que pasar por aquí
(la lección del visor de diffs, 2026-07-24).

Se invoca desde `plan_pr._open_plan_pr_async`, **antes** de abrir el PR, para que
el PR contenga su propio changelog. Un único disparador: encadenarlo ahí en vez de
encolarlo aparte evita la carrera de dos tasks commiteando el mismo worktree.

Best-effort de principio a fin: cuando esto corre, el plan YA está `completed` en
BD. Reventar aquí no desharía el cierre, solo dejaría un traceback; así que todo
fallo se degrada a un string de estado que el log recoge.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.plan_docs")

#: Id del worktree dedicado a la documentación de cierre. NO es el de una tarea:
#: compartir worktree con una tarea haría que el ``git add -A`` del commit
#: barriera artefactos suyos a medio escribir.
_DOCS_WORKTREE_PREFIX = "plan-docs"

#: Marcador de los trailers para un commit que no nace de una tarea concreta.
#: `CommitTrailers` exige los tres; mentir con el id de una tarea real sería peor
#: que decir explícitamente de dónde viene.
_CLOSURE_MARKER = "plan-closure"


def _docs_worktree_id(plan_id: str) -> str:
    return f"{_DOCS_WORKTREE_PREFIX}-{str(plan_id)[:8]}"


def build_plan_meta(plan: Any, tasks: list[Any]) -> Any:
    """Proyecta la fila `Plan` + sus tareas al contrato de `render_changelog`.

    Puro (no toca BD ni reloj): recibe las filas ya cargadas. El orden de
    ``tasks`` lo fija el caller — se rinde tal cual para que el changelog siga el
    orden del plan y no el de la BD.
    """
    from api_server.tech_writer.changelog import ChangelogTask, PlanMeta

    return PlanMeta(
        plan_id=str(plan.id),
        title=plan.title or str(plan.id),
        summary=(plan.description or "").strip()
        or "Plan cerrado tras la validación humana. Sin descripción registrada.",
        tasks=tuple(
            ChangelogTask(
                task_key=str(task.id),
                title=task.title or str(task.id),
                done=str(task.status) == "done",
            )
            for task in tasks
        ),
        pr_url=plan.pr_url,
        # `plans` NO tiene columna `completed_at` (solo el frontmatter del
        # roadmap la usa). Cuando esto corre, la transición a `completed` acaba
        # de escribirse, así que `updated_at` ES la fecha de cierre — se usa esa
        # en vez de inventar un `None` que el changelog pintaría como pendiente.
        completed_at=plan.updated_at.date().isoformat() if plan.updated_at else None,
    )


def write_plan_docs_to_branch(
    *,
    data_root: str | Path,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    plan_meta: Any,
) -> str:
    """Genera, commitea y empuja al bare la documentación de cierre del plan.

    Recibe todo YA resuelto (sin BD), igual que
    :func:`~workers.plan_pr._push_branch_to_remote_gated`, para que la mecánica de
    git se pueda ejercer contra un bare de `tmp_path` sin sembrar filas.

    Devuelve ``written`` / ``skipped:already_generated`` / ``skipped:<motivo>`` /
    ``error:<msg>``. Nunca lanza.

    Idempotente por construcción: ``generate_plan_docs`` es skip-if-exists, así
    que un segundo pase no escribe nada, no commitea y **no pisa** un changelog
    que un humano haya reescrito. Esto importa porque el cierre se reintenta por
    varias vías (reconciler, segundo veredicto, backfill manual).
    """
    from api_server.tech_writer.generation import generate_plan_docs

    from workers.git_repos import BareRepoManager, GitCommandError, WorktreeManager
    from workers.plan_git import (
        CommitTrailers,
        PlanGitPolicies,
        PlanGitWorkflow,
        commit_task,
        worktree_coordinates,
    )

    layout, branch = worktree_coordinates(
        data_root=data_root,
        tenant_slug=tenant_slug,
        project_slug=project_slug,
        plan_id=plan_id,
        plan_slug=plan_slug,
    )
    bare_path = layout.bare_repo_path(project_slug)
    if not bare_path.exists():
        # Un proyecto que nunca ejecutó una tarea no tiene repo donde escribir.
        # No se crea aquí: fabricar un repo al cerrar un plan sin código sería
        # inventar historia, y el operador vería un repo que él no pidió.
        return "skipped:no_bare_repo"

    worktree_id = _docs_worktree_id(plan_id)
    wt: WorktreeManager | None = None
    try:
        mgr = BareRepoManager(layout)
        mgr.ensure_repo(project_slug)
        # Un plan cuyas tareas no produjeron código no tiene rama: `add` la crea
        # desde HEAD. El changelog no puede depender de que el plan tocara
        # ficheros — el criterio 4 de CLAUDE.md no hace esa distinción.
        mgr.seed_initial_commit_if_empty(project_slug)
        wt = WorktreeManager(layout, project_slug)
        path = wt.add(worktree_id, branch=branch)
        if wt.branch_exists(branch):
            # Traer la rama a HEAD ANTES de escribir: si una tarea hermana
            # commiteó después de crearse este worktree, escribiríamos sobre un
            # árbol viejo y el push posterior sería un non-fast-forward.
            wt.sync_to_head(worktree_id, branch=branch)

        manifest = generate_plan_docs(Path(path), plan_meta)
        if not manifest.changed:
            return "skipped:already_generated"

        commit_task(
            Path(path),
            message=f"docs(changelog): cierre del plan {plan_meta.plan_id}",
            trailers=CommitTrailers(
                plan_id=str(plan_meta.plan_id),
                task_id=_CLOSURE_MARKER,
                execution_id=_CLOSURE_MARKER,
            ),
        )
        PlanGitWorkflow(
            bare_repo_path=bare_path,
            plan_branch=branch,
            policies=PlanGitPolicies(),
        ).push_review_to_bare(Path(path))
    except GitCommandError as exc:
        _log.warning("plan_docs.git_failed", plan_id=str(plan_id), error=str(exc))
        return f"error:{exc}"
    except Exception as exc:  # best-effort: el plan ya está cerrado en BD
        _log.exception("plan_docs.failed", plan_id=str(plan_id), error=str(exc))
        return f"error:{exc}"
    finally:
        # `task_cv_42` (auditoría 2026-09-01, G-03): el worktree de docs se
        # creaba y NUNCA se retiraba — uno por plan cerrado, registrado en el
        # bare, que sólo la poda por TTL tocaba a los 30 días.
        if wt is not None:
            with contextlib.suppress(Exception):
                wt.remove(worktree_id)
    _log.info("plan_docs.written", plan_id=str(plan_id), branch=branch)
    return "written"


async def generate_plan_closure_docs_async(settings: Settings, plan_id: str) -> dict[str, Any]:
    """Resuelve el plan en BD y delega en :func:`write_plan_docs_to_branch`.

    Espeja la resolución de `_open_plan_pr_async` (slugs persistidos, nunca
    re-slugificados del título) para que las coordenadas del worktree sean las
    MISMAS que las de la ejecución y el auto-PR.
    """
    from api_server.db.domain import Plan, Project, Task
    from api_server.db.models import Organization
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from workers.db import worker_engine

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            plan = await session.get(Plan, UUID(plan_id))
            if plan is None:
                return {"plan_id": plan_id, "status": "skipped:no_plan"}
            if not plan.slug:
                return {"plan_id": plan_id, "status": "skipped:no_plan_slug"}
            # `plans.project_id` es NOT NULL en el modelo: no se comprueba (mypy
            # marca la rama como inalcanzable, y tiene razón).
            project = await session.get(Project, plan.project_id)
            if project is None or not project.slug:
                return {"plan_id": plan_id, "status": "skipped:no_project_slug"}
            org = await session.get(Organization, project.tenant_id)
            tenant_slug = (org.slug if org is not None else None) or str(project.tenant_id)
            tasks = list(
                (
                    await session.execute(
                        select(Task)
                        .where(Task.plan_id == plan.id, Task.tenant_id == plan.tenant_id)
                        .order_by(Task.created_at, Task.id)
                    )
                )
                .scalars()
                .all()
            )
            plan_meta = build_plan_meta(plan, tasks)
            project_slug = project.slug
            plan_slug = plan.slug
    finally:
        await engine.dispose()

    status = await asyncio.to_thread(
        write_plan_docs_to_branch,
        data_root=settings.data_root,
        tenant_slug=tenant_slug,
        project_slug=project_slug,
        plan_id=plan_id,
        plan_slug=plan_slug,
        plan_meta=plan_meta,
    )
    return {"plan_id": plan_id, "status": status}


@app.task(name="workers.generate_plan_closure_docs")  # type: ignore[untyped-decorator]
def generate_plan_closure_docs(plan_id: str) -> dict[str, Any]:
    """Entry point Celery para regenerar/backfillear la doc de cierre a mano.

    El camino automático la invoca INLINE desde `plan_pr` (para garantizar que el
    changelog está commiteado antes de que el PR se abra); esta task existe para
    los planes cerrados antes de T8 y para reintentos del operador.
    """
    settings = get_settings()
    try:
        return asyncio.run(generate_plan_closure_docs_async(settings, plan_id))
    except Exception as exc:  # pragma: no cover - defensivo
        _log.exception("plan_docs.task_failed", plan_id=plan_id, error=str(exc))
        return {"plan_id": plan_id, "status": f"error:{exc}"}


__all__ = [
    "build_plan_meta",
    "generate_plan_closure_docs",
    "generate_plan_closure_docs_async",
    "write_plan_docs_to_branch",
]
