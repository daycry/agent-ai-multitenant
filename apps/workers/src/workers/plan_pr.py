"""Auto-PR al cerrar un plan (ADR 0072, fase 2).

Task invocable que, para un proyecto + rama de plan, hace el push autenticado de
la rama al remoto y abre el PR/MR por el proveedor configurado. La dispara quien
detecte el cierre del plan (orchestrator) o una acción manual. Best-effort.

Reutiliza la resolución de git del proyecto (config + secreto de Vault) de
``repo_clone`` y el opener por proveedor de ``pr_openers``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.git_auth import build_git_auth_env
from workers.git_repos import BareRepoLayout
from workers.plan_git import PlanGitPolicies, PlanGitWorkflow
from workers.pr_openers import build_pr_opener
from workers.repo_clone import _repo_name_from_url, _slugify, _vault_store

_log = structlog.get_logger("workers.plan_pr")


async def _open_plan_pr_async(
    project_id: UUID, plan_branch: str, *, title: str, body: str, settings: Settings
) -> dict[str, Any]:
    from api_server.db.domain import Project
    from api_server.db.models import Organization
    from api_server.git_integration import project_git_secret_path
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            project = await session.get(Project, project_id)
            if project is None or not project.git_config:
                return {"project_id": str(project_id), "status": "skipped:no_git_config"}
            org = await session.get(Organization, project.tenant_id)
            cfg = dict(project.git_config)
            tenant_slug = (org.slug if org is not None else None) or str(project.tenant_id)
            project_slug = _slugify(project.name)

        remote_url = cfg.get("remote_url")
        provider = cfg.get("provider", "generic")
        base = cfg.get("default_branch", "main")
        auth_mode = cfg.get("auth_mode", "none")
        if not remote_url:
            return {"project_id": str(project_id), "status": "skipped:no_remote"}

        username = token = ssh_key = None
        if auth_mode in ("pat", "ssh"):
            store = _vault_store(settings)
            if store is not None:
                secret = store.read_secret(project_git_secret_path(project_id))
                username = secret.get("username") or None
                token = secret.get("token") or None
                ssh_key = secret.get("ssh_key") or None

        # El opener (API REST de PR/MR) necesita un PAT; sin token no se puede abrir
        # el PR (SSH solo sirve para el git transport, no para la API).
        pr_opener = None
        if token:
            pr_opener = build_pr_opener(
                provider=provider, remote_url=remote_url, token=token, head=plan_branch, base=base
            )

        auth = build_git_auth_env(
            auth_mode, provider=provider, username=username, token=token, ssh_key=ssh_key
        )
        try:
            layout = BareRepoLayout(
                data_root=Path(settings.data_root),
                tenant_slug=tenant_slug,
                project_slug=project_slug,
            )
            bare_path = layout.bare_repo_path(_repo_name_from_url(remote_url))
            wf = PlanGitWorkflow(
                bare_repo_path=bare_path,
                plan_branch=plan_branch,
                policies=PlanGitPolicies(),
                pr_opener=pr_opener,
                auth_env=auth.env or None,
            )
            info = wf.open_plan_pr(title=title, body=body)
        finally:
            auth.cleanup()
        _log.info(
            "plan_pr.done",
            project_id=str(project_id),
            branch=plan_branch,
            url=info.url,
            skipped=info.skipped_reason,
        )
        return {
            "project_id": str(project_id),
            "branch": plan_branch,
            "url": info.url,
            "status": "ok" if info.url else f"skipped:{info.skipped_reason}",
        }
    finally:
        await engine.dispose()


@app.task(name="workers.open_plan_pr")  # type: ignore[misc]
def open_plan_pr(project_id: str, plan_branch: str, title: str, body: str) -> dict[str, Any]:
    """Entry point Celery. Best-effort: nunca propaga."""
    settings = get_settings()
    try:
        return asyncio.run(
            _open_plan_pr_async(
                UUID(project_id), plan_branch, title=title, body=body, settings=settings
            )
        )
    except Exception as exc:
        _log.exception("plan_pr.failed", project_id=project_id, branch=plan_branch, error=str(exc))
        return {"project_id": project_id, "branch": plan_branch, "status": f"error:{exc}"}
