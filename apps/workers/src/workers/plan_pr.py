"""Auto-PR al cerrar un plan (ADR 0072, fase 2).

Task invocable que, para un proyecto + rama de plan, hace el push autenticado de
la rama al remoto y abre el PR/MR por el proveedor configurado. La dispara quien
detecte el cierre del plan (orchestrator) o una acción manual. Best-effort.

Reutiliza la resolución de git del proyecto (config + secreto de Vault) de
``repo_clone`` y el opener por proveedor de ``pr_openers``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.git_auth import build_git_auth_env
from workers.git_repos import BareRepoLayout, BareRepoManager
from workers.plan_git import PlanGitPolicies, PlanGitWorkflow, plan_git_identity
from workers.pr_openers import build_pr_opener
from workers.repo_clone import _vault_store

_log = structlog.get_logger("workers.plan_pr")


def _policies_from_worker_config(worker_config: dict[str, Any] | None) -> PlanGitPolicies:
    """Lee las políticas git del proyecto (projects.worker_config.git_policies);
    defaults razonables (ADR 0072): incremental + human_required + pr_required."""
    gp = (worker_config or {}).get("git_policies")
    gp = gp if isinstance(gp, dict) else {}
    return PlanGitPolicies(
        branch_push_mode=gp.get("branch_push_mode", "incremental"),
        plan_validation_mode=gp.get("plan_validation_mode", "human_required"),
        push_policy=gp.get("push_policy", "branch_only_pr_required"),
    )


def _resolve_git_secret(
    settings: Settings, project_id: UUID, auth_mode: str
) -> tuple[str | None, str | None, str | None]:
    """Read ``(username, token, ssh_key)`` from Vault for a ``pat``/``ssh`` project,
    or all-None when the mode needs no secret / Vault is unavailable."""
    if auth_mode not in ("pat", "ssh"):
        return None, None, None
    from api_server.git_integration import project_git_secret_path

    store = _vault_store(settings)
    if store is None:
        return None, None, None
    secret = store.read_secret(project_git_secret_path(project_id))
    return (
        secret.get("username") or None,
        secret.get("token") or None,
        secret.get("ssh_key") or None,
    )


#: Motivo LEGIBLE de cada `skipped:*` del auto-PR. Sin esto, un cierre sin PR no
#: dejaba rastro en el plan (solo en los logs del worker) y la ficha decía «Todavía
#: sin PR» para siempre — la ceguera que P6 denunciaba. La UI lo pinta detrás de
#: «No se pudo abrir: », así que se redacta como continuación de esa frase.
_SKIP_MESSAGES = {
    "skipped:no_git_config": "el proyecto no tiene git configurado",
    "skipped:no_project_slug": "el proyecto no tiene slug persistido",
    "skipped:no_plan_slug": "el plan no tiene slug persistido",
    "skipped:no_remote": "el proyecto no tiene remote_url en su configuración git",
}


def _skip_message(status: str) -> str:
    return _SKIP_MESSAGES.get(status, status)


async def _persist_pr_result(
    sessionmaker: Any,
    plan_id: str,
    *,
    pr_url: str | None,
    pr_branch: str | None,
    pr_error: str | None,
    keep_existing_url: bool = False,
) -> None:
    """Write the auto-PR outcome back onto the plan (P6) so the URL/branch (or the
    failure reason) is visible in the API/UI instead of living only in worker logs.
    Best-effort: a failure here never breaks the already-committed plan closure.

    ``keep_existing_url`` protects a PR that ALREADY exists: the closure runs more
    than once (re-veredicto, reintento del operador), and a later skip must not
    erase the URL of a PR that is open on the provider."""
    from api_server.db.domain import Plan

    try:
        async with sessionmaker() as session, session.begin():
            plan = await session.get(Plan, UUID(plan_id))
            if plan is None or (keep_existing_url and plan.pr_url):
                return
            plan.pr_url = pr_url
            plan.pr_branch = pr_branch
            plan.pr_error = pr_error
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning("plan_pr.persist_failed", plan_id=plan_id, error=str(exc))


async def push_plan_branch_to_remote(
    settings: Settings,
    *,
    project_id: UUID,
    plan_id: str,
    plan_slug: str,
    tenant_slug: str,
    project_slug: str,
) -> str:
    """Push the plan branch bare→remote after a task commit, gated by
    ``branch_push_mode`` (P3/T3, ADR 0085 dec.5).

    ``incremental`` (the default) pushes every time a task is accepted so the remote
    always has the latest; ``final_only`` defers to plan close (``open_plan_pr``).
    Reuses the SAME single-source identity + git-config/Vault resolution as the auto-PR.

    BEST-EFFORT and NEVER raises: the task's commit is already durable in the local
    bare, so a local-only project (no ``remote_url`` / no ``origin``) or a transient
    push failure returns a status string instead of failing the task. Returns one of
    ``pushed`` / ``skipped:<reason>`` / ``error:<msg>``.
    """
    from api_server.db.domain import Project
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from workers.db import worker_engine

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            project = await session.get(Project, project_id)
            if project is None or not project.git_config:
                return "skipped:no_git_config"
            cfg = dict(project.git_config)
            policies = _policies_from_worker_config(project.worker_config)
        remote_url = cfg.get("remote_url")
        if not remote_url:
            return "skipped:no_remote"
        return await _push_branch_to_remote_gated(
            settings,
            tenant_slug=tenant_slug,
            project_slug=project_slug,
            plan_id=plan_id,
            plan_slug=plan_slug,
            remote_url=remote_url,
            provider=cfg.get("provider", "generic"),
            auth_mode=cfg.get("auth_mode", "none"),
            project_id=project_id,
            policies=policies,
        )
    except Exception as exc:  # best-effort — a push failure never fails the task
        _log.warning("plan_pr.incremental_push_failed", plan_id=plan_id, error=str(exc))
        return f"error:{exc}"
    finally:
        await engine.dispose()


async def _push_branch_to_remote_gated(
    settings: Settings,
    *,
    tenant_slug: str,
    project_slug: str,
    plan_id: str,
    plan_slug: str,
    remote_url: str,
    provider: str,
    auth_mode: str,
    project_id: UUID,
    policies: PlanGitPolicies,
) -> str:
    """bare → remote push given ALREADY-RESOLVED config (no DB access).

    Gated by ``push_policy`` (``forbidden`` never pushes) and ``branch_push_mode``
    (``final_only`` defers to plan close); ensures ``origin`` on the single-source
    bare; resolves auth from Vault for pat/ssh. Returns ``pushed`` /
    ``skipped:push_forbidden`` / ``skipped:final_only`` / ``skipped:no_origin``.
    Split out of :func:`push_plan_branch_to_remote` so the push mechanics are
    testable against a ``file://`` remote without seeding a project row."""
    # `forbidden` is the STRONGER gate: «this project never pushes» (the same
    # reading `open_plan_pr` enforces). Without it the per-task incremental push
    # of T3 mirrored to the remote of a project configured never to push.
    if policies.push_policy == "forbidden":
        return "skipped:push_forbidden"
    if policies.branch_push_mode == "final_only":
        return "skipped:final_only"
    # SINGLE-SOURCE identity (P1/P2): SAME bare + branch as execution/clone/auto-PR.
    identity = plan_git_identity(plan_id, plan_slug, project_slug)
    username, token, ssh_key = _resolve_git_secret(settings, project_id, auth_mode)
    auth = build_git_auth_env(
        auth_mode, provider=provider, username=username, token=token, ssh_key=ssh_key
    )
    try:
        layout = BareRepoLayout(
            data_root=Path(settings.data_root),
            tenant_slug=tenant_slug,
            project_slug=identity.project_slug,
        )
        # ensure_repo is idempotent — it only (re)points origin at remote_url, so a
        # bare execution created without a remote gets one for the incremental push.
        BareRepoManager(layout).ensure_repo(identity.project_slug, remote_url=remote_url)
        wf = PlanGitWorkflow(
            bare_repo_path=layout.bare_repo_path(identity.project_slug),
            plan_branch=identity.plan_branch,
            policies=policies,
            auth_env=auth.env or None,
        )
        pushed = await asyncio.to_thread(wf.push_branch_to_remote)
        return "pushed" if pushed else "skipped:no_origin"
    finally:
        auth.cleanup()


@dataclass(frozen=True)
class _PrContext:
    """Lo que el auto-PR necesita de la BD, ya resuelto (o el motivo del skip).

    Extraído de :func:`_open_plan_pr_async`, que hacía cuatro cosas en una sola
    función (resolver, generar docs, empujar, abrir el PR) y se pasaba del límite
    de sentencias al añadirle T8. La resolución es la pieza que no depende de git
    ni de la red, así que es la que sale limpia.
    """

    tenant_slug: str = ""
    project_slug: str = ""
    plan_slug: str = ""
    cfg: dict[str, Any] = field(default_factory=dict)
    policies: PlanGitPolicies = field(default_factory=PlanGitPolicies)
    skip_reason: str | None = None


async def _skipped_pr_result(
    sessionmaker: Any,
    plan_id: str,
    *,
    project_id: UUID,
    status: str,
    pr_branch: str | None,
    closure_docs: str | None,
) -> dict[str, Any]:
    """Devuelve el resultado de un auto-PR que NO se abrió, dejándolo escrito en el
    plan (P6): el operador ve el motivo en la ficha, no en los logs del worker."""
    await _persist_pr_result(
        sessionmaker,
        plan_id,
        pr_url=None,
        pr_branch=pr_branch,
        pr_error=_skip_message(status),
        keep_existing_url=True,
    )
    return {"project_id": str(project_id), "status": status, "closure_docs": closure_docs}


async def _resolve_pr_context(sessionmaker: Any, project_id: UUID, plan_id: str) -> _PrContext:
    """Lee proyecto/plan/org y devuelve el contexto, o el `skipped:*` que aplique."""
    from api_server.db.domain import Plan, Project
    from api_server.db.models import Organization

    async with sessionmaker() as session:
        project = await session.get(Project, project_id)
        if project is None or not project.git_config:
            return _PrContext(skip_reason="skipped:no_git_config")
        if not project.slug:
            return _PrContext(skip_reason="skipped:no_project_slug")
        plan = await session.get(Plan, UUID(plan_id))
        if plan is None or not plan.slug:
            return _PrContext(skip_reason="skipped:no_plan_slug")
        org = await session.get(Organization, project.tenant_id)
        return _PrContext(
            tenant_slug=(org.slug if org is not None else None) or str(project.tenant_id),
            project_slug=project.slug,
            plan_slug=plan.slug,
            cfg=dict(project.git_config),
            policies=_policies_from_worker_config(project.worker_config),
        )


async def _open_plan_pr_async(
    project_id: UUID, plan_id: str, *, title: str, body: str, settings: Settings
) -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from workers.db import worker_engine
    from workers.plan_docs import generate_plan_closure_docs_async

    # T8 (c4): la documentación de cierre se genera y commitea ANTES de abrir el
    # PR, para que el PR la contenga. Va aquí arriba —por delante del corte por
    # `git_config`/`remote_url`— a propósito: el criterio 4 de CLAUDE.md dice
    # «con o sin PR», y un proyecto local sin remoto sí tiene bare donde escribir.
    # Best-effort por construcción (devuelve estado, nunca lanza): el plan ya
    # está `completed` en BD y un fallo aquí no debe tocar el auto-PR.
    docs = await generate_plan_closure_docs_async(settings, plan_id)
    _log.info("plan_pr.closure_docs", plan_id=plan_id, status=docs.get("status"))

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        ctx = await _resolve_pr_context(sessionmaker, project_id, plan_id)
        if ctx.skip_reason is not None:
            return await _skipped_pr_result(
                sessionmaker,
                plan_id,
                project_id=project_id,
                status=ctx.skip_reason,
                pr_branch=None,  # sin slugs no hay identidad que apuntar
                closure_docs=docs.get("status"),
            )
        cfg, policies = ctx.cfg, ctx.policies
        tenant_slug, project_slug, plan_slug = ctx.tenant_slug, ctx.project_slug, ctx.plan_slug

        # SINGLE-SOURCE identity (audit 2026-07-03, P1/P2): the auto-PR resolves the
        # SAME bare repo + branch as execution/clone — derived from the persisted
        # plans.slug / projects.slug, never re-slugified from the (prefixed) title.
        identity = plan_git_identity(plan_id, plan_slug, project_slug)
        plan_branch = identity.plan_branch
        remote_url = cfg.get("remote_url")
        provider = cfg.get("provider", "generic")
        base = cfg.get("default_branch", "main")
        auth_mode = cfg.get("auth_mode", "none")
        if not remote_url:
            return await _skipped_pr_result(
                sessionmaker,
                plan_id,
                project_id=project_id,
                status="skipped:no_remote",
                pr_branch=plan_branch,
                closure_docs=docs.get("status"),
            )

        username, token, ssh_key = _resolve_git_secret(settings, project_id, auth_mode)

        # El opener (API REST de PR/MR) necesita un PAT; sin token no se puede abrir
        # el PR (SSH solo sirve para el git transport, no para la API).
        pr_opener = (
            build_pr_opener(
                provider=provider, remote_url=remote_url, token=token, head=plan_branch, base=base
            )
            if token
            else None
        )

        auth = build_git_auth_env(
            auth_mode, provider=provider, username=username, token=token, ssh_key=ssh_key
        )
        pr_url: str | None = None
        pr_error: str | None = None
        try:
            layout = BareRepoLayout(
                data_root=Path(settings.data_root),
                tenant_slug=tenant_slug,
                project_slug=identity.project_slug,
            )
            # Guarantee `origin` on the bare that HOLDS the commits: execution may
            # have created it without a remote (ensure_repo is idempotent — it only
            # (re)points origin), so the PR-time push has a remote to push to.
            BareRepoManager(layout).ensure_repo(identity.project_slug, remote_url=remote_url)
            bare_path = layout.bare_repo_path(identity.project_slug)
            wf = PlanGitWorkflow(
                bare_repo_path=bare_path,
                plan_branch=plan_branch,
                policies=policies,
                pr_opener=pr_opener,
                auth_env=auth.env or None,
                # Guard de ancestro contra la base remota: una divergencia se
                # reporta con un motivo accionable en pr_error, no con el 422
                # crudo del proveedor.
                base_branch=base,
            )
            info = wf.open_plan_pr(title=title, body=body)
            pr_url = info.url
            pr_error = None if info.url else (info.skipped_reason or "no PR opened")
        except Exception as exc:  # PR opening is best-effort — record WHY it failed (P6).
            pr_error = str(exc)
            _log.exception("plan_pr.open_failed", project_id=str(project_id), error=str(exc))
        finally:
            auth.cleanup()
        # Persist the auto-PR outcome on the plan (P6) so it is visible in API/UI,
        # not just worker logs. Stops the failure from being swallowed silently.
        await _persist_pr_result(
            sessionmaker, plan_id, pr_url=pr_url, pr_branch=plan_branch, pr_error=pr_error
        )
        _log.info(
            "plan_pr.done",
            project_id=str(project_id),
            branch=plan_branch,
            url=pr_url,
            error=pr_error,
        )
        return {
            "project_id": str(project_id),
            "branch": plan_branch,
            "url": pr_url,
            "status": "ok" if pr_url else (f"error:{pr_error}" if pr_error else "skipped"),
            "closure_docs": docs.get("status"),
        }
    finally:
        await engine.dispose()


@app.task(name="workers.open_plan_pr")  # type: ignore[untyped-decorator]
def open_plan_pr(project_id: str, plan_id: str, title: str, body: str) -> dict[str, Any]:
    """Entry point Celery. Best-effort: nunca propaga.

    ``title``/``body`` son SOLO el texto del PR. La rama sale de la identidad de
    fuente única (``plan_git_identity`` sobre los slugs PERSISTIDOS), nunca del
    título: derivarla del título —que el enqueue prefija con ``"Plan: "``— es
    exactamente lo que hacía que el PR apuntara a una rama sin los commits
    (auditoría 2026-07-03, P1)."""
    settings = get_settings()
    try:
        return asyncio.run(
            _open_plan_pr_async(
                UUID(project_id), plan_id, title=title, body=body, settings=settings
            )
        )
    except Exception as exc:
        _log.exception("plan_pr.failed", project_id=project_id, plan_id=plan_id, error=str(exc))
        return {"project_id": project_id, "plan_id": plan_id, "status": f"error:{exc}"}
