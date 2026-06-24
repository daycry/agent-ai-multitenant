"""Clone/fetch autenticado del repo de un proyecto (ADR 0072).

Encolada por el api-server cuando se fija/actualiza la config git del proyecto.
Resuelve `git_config` (provider/remote_url/auth_mode) + el secreto de Vault
(PAT/clave SSH), configura el bare repo con el remoto y hace `fetch` autenticado
(PAT vía GIT_ASKPASS, SSH vía GIT_SSH_COMMAND). Best-effort: cualquier fallo se
loguea y se devuelve como estado, nunca propaga al worker.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any
from uuid import UUID

import structlog

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.git_auth import build_git_auth_env
from workers.git_repos import BareRepoLayout, BareRepoManager

_log = structlog.get_logger("workers.repo_clone")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "project"


def _repo_name_from_url(url: str) -> str:
    """Nombre del repo desde la URL del remoto (basename sin .git)."""
    tail = url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    return (tail[:-4] if tail.endswith(".git") else tail) or "repo"


def _vault_store(settings: Settings) -> Any | None:
    """HvacVaultStore desde la config del worker (None si no hay Vault)."""
    url = getattr(settings, "vault_url", None)
    token = getattr(settings, "vault_token", None)
    if not url or not token:
        return None
    try:
        import hvac
        from api_server.llm_providers.vault import HvacLLMProviderVaultStore

        return HvacLLMProviderVaultStore(hvac.Client(url=url, token=token))
    except Exception as exc:  # pragma: no cover - binding de install
        _log.warning("repo_clone.vault_unavailable", error=str(exc))
        return None


async def _clone_project_repo_async(project_id: UUID, *, settings: Settings) -> dict[str, Any]:
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
        if not remote_url:
            return {"project_id": str(project_id), "status": "skipped:no_remote"}
        auth_mode = cfg.get("auth_mode", "none")

        username = token = ssh_key = None
        if auth_mode in ("pat", "ssh"):
            store = _vault_store(settings)
            if store is None:
                _log.warning("repo_clone.no_vault_for_private", project_id=str(project_id))
            else:
                secret = store.read_secret(project_git_secret_path(project_id))
                username = secret.get("username") or None
                token = secret.get("token") or None
                ssh_key = secret.get("ssh_key") or None

        auth = build_git_auth_env(
            auth_mode,
            provider=cfg.get("provider"),
            username=username,
            token=token,
            ssh_key=ssh_key,
        )
        try:
            layout = BareRepoLayout(
                data_root=Path(settings.data_root),
                tenant_slug=tenant_slug,
                project_slug=project_slug,
            )
            mgr = BareRepoManager(layout)
            repo_name = _repo_name_from_url(remote_url)
            mgr.ensure_repo(repo_name, remote_url=remote_url)
            mgr.fetch_remote(repo_name, auth_env=auth.env or None)
        finally:
            auth.cleanup()
        _log.info("repo_clone.ok", project_id=str(project_id), repo=repo_name)
        return {"project_id": str(project_id), "status": "ok", "repo": repo_name}
    finally:
        await engine.dispose()


@app.task(name="workers.clone_project_repo")  # type: ignore[misc]
def clone_project_repo(project_id: str) -> dict[str, Any]:
    """Entry point Celery. Best-effort: nunca propaga (un fallo de red/credencial
    se devuelve como estado para que la UI/logs lo vean)."""
    settings = get_settings()
    try:
        return asyncio.run(_clone_project_repo_async(UUID(project_id), settings=settings))
    except Exception as exc:
        _log.exception("repo_clone.failed", project_id=project_id, error=str(exc))
        return {"project_id": project_id, "status": f"error:{exc}"}
