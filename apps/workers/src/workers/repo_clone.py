"""Clone/fetch autenticado del repo de un proyecto (ADR 0072).

Encolada por el api-server cuando se fija/actualiza la config git del proyecto.
Resuelve `git_config` (provider/remote_url/auth_mode) + el secreto de Vault
(PAT/clave SSH), configura el bare repo con el remoto y hace `fetch` autenticado
(PAT vía GIT_ASKPASS, SSH vía GIT_SSH_COMMAND). Best-effort: cualquier fallo se
loguea y se devuelve como estado, nunca propaga al worker.
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
from workers.git_repos import BareRepoLayout, BareRepoManager

_log = structlog.get_logger("workers.repo_clone")


def _vault_store(settings: Settings) -> Any | None:
    """HvacVaultStore desde la config del worker (None si no hay Vault)."""
    url = getattr(settings, "vault_url", None)
    token = getattr(settings, "vault_token", None)
    if not url or not token:
        return None
    try:
        from api_server.llm_providers.vault import HvacLLMProviderVaultStore

        # prod-10 task_prod10_07: fábrica compartida del worker (renueva el token).
        from workers.vault_client import build_worker_vault_client

        client = build_worker_vault_client(settings)
        if client is None:
            return None
        return HvacLLMProviderVaultStore(client)
    except Exception as exc:  # pragma: no cover - binding de install
        _log.warning("repo_clone.vault_unavailable", error=str(exc))
        return None


async def _clone_project_repo_async(project_id: UUID, *, settings: Settings) -> dict[str, Any]:
    from api_server.db.domain import Project
    from api_server.db.models import Organization
    from api_server.git_integration import project_git_secret_path
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from workers.db import worker_engine

    engine = worker_engine(settings)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessionmaker() as session:
            project = await session.get(Project, project_id)
            if project is None or not project.git_config:
                return {"project_id": str(project_id), "status": "skipped:no_git_config"}
            if not project.slug:
                return {"project_id": str(project_id), "status": "skipped:no_project_slug"}
            org = await session.get(Organization, project.tenant_id)
            cfg = dict(project.git_config)
            tenant_slug = (org.slug if org is not None else None) or str(project.tenant_id)
            # Persisted projects.slug (ADR 0085), NOT slugify(name): the clone must
            # land in the SAME bare that execution branches off — one bare per
            # project, named by project.slug (audit 2026-07-03, P2). Fetching the
            # remote here populates that bare, so worktrees branch off real remote
            # content instead of an empty seed.
            project_slug = project.slug

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
            # One bare per project, named by project.slug — the SAME name execution
            # and the auto-PR resolve via plan_git_identity (audit P2).
            repo_name = project_slug
            mgr.ensure_repo(repo_name, remote_url=remote_url)
            mgr.fetch_remote(repo_name, auth_env=auth.env or None)
            # La base local debe nacer de la historia del REMOTO, no de una
            # raíz sintética (visto en vivo: PR final con «no history in
            # common»). Conservador: crea/avanza (ff) la rama default local;
            # un remoto vacío o una divergencia se reportan, nunca se pisan.
            alignment = mgr.align_default_branch(
                repo_name, str(cfg.get("default_branch") or "main")
            )
            if alignment in ("remote_empty", "diverged"):
                _log.warning(
                    "repo_clone.default_branch_not_aligned",
                    project_id=str(project_id),
                    repo=repo_name,
                    alignment=alignment,
                )
        except Exception as exc:
            # Persistimos el fallo para que la UI del proyecto lo VEA (el
            # operador no sabía si la cola ejecutaba el clone) en vez de que
            # muera solo en los logs del worker.
            await _persist_sync_status(
                sessionmaker, project_id, status="error", alignment=None, error=str(exc)
            )
            _log.warning("repo_clone.git_failed", project_id=str(project_id), error=str(exc))
            return {"project_id": str(project_id), "status": f"error:{exc}"}
        finally:
            auth.cleanup()
        await _persist_sync_status(
            sessionmaker, project_id, status="ok", alignment=alignment, error=None
        )
        _log.info(
            "repo_clone.ok",
            project_id=str(project_id),
            repo=repo_name,
            default_branch_alignment=alignment,
        )
        return {
            "project_id": str(project_id),
            "status": "ok",
            "repo": repo_name,
            "default_branch_alignment": alignment,
        }
    finally:
        await engine.dispose()


async def _persist_sync_status(
    sessionmaker: Any,
    project_id: UUID,
    *,
    status: str,
    alignment: str | None,
    error: str | None,
) -> None:
    """Escribe el resultado del clone/sync en ``project.repository_config``.

    Da al operador feedback de que la cola SÍ ejecutó (era su duda) y, sobre
    todo, la ALINEACIÓN de la rama default local con el remoto: ``diverged``
    explica por qué el PR final falla con «no history in common» (el caso
    api-ci). Merge del dict (no pisa ``review_image``/``review_port``);
    best-effort — un fallo aquí nunca rompe el clone ya hecho. El worker corre
    como ``migrations_user`` (BYPASSRLS), así que la escritura no necesita
    contexto de tenant."""
    from datetime import UTC, datetime

    from api_server.db.domain import Project

    payload: dict[str, Any] = {
        "at": datetime.now(tz=UTC).isoformat(),
        "status": status,
    }
    if alignment is not None:
        payload["default_branch_alignment"] = alignment
    if error is not None:
        payload["error"] = error[:500]
    try:
        async with sessionmaker() as session, session.begin():
            project = await session.get(Project, project_id)
            if project is not None:
                project.repository_config = {
                    **(project.repository_config or {}),
                    "last_git_sync": payload,
                }
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning("repo_clone.persist_status_failed", project_id=str(project_id), error=str(exc))


@app.task(name="workers.clone_project_repo")  # type: ignore[untyped-decorator]
def clone_project_repo(project_id: str) -> dict[str, Any]:
    """Entry point Celery. Best-effort: nunca propaga (un fallo de red/credencial
    se devuelve como estado para que la UI/logs lo vean)."""
    settings = get_settings()
    try:
        return asyncio.run(_clone_project_repo_async(UUID(project_id), settings=settings))
    except Exception as exc:
        _log.exception("repo_clone.failed", project_id=project_id, error=str(exc))
        return {"project_id": project_id, "status": f"error:{exc}"}
