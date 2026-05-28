"""Dep-cache invalidation endpoint (Plan 06 task_06_12).

The admin-panel's "Invalidar caché" button (see
``apps/admin-panel/app/admin/projects/[id]/dep-cache/page.tsx``) calls
this endpoint. The endpoint resolves the project's data root, builds
a :class:`DepCacheManager` against ``<data_root>/dep-cache``, and
invalidates either a single entry or every entry of a runtime.

Auth: the endpoint is tenant-scoped (uses ``get_tenant_session``) so
only members of the tenant can wipe its cache.

This is a *destructive* operation — the worker re-populates the cache
on the next test run, but until then ``pre_install`` pays the full
cost.  We log every invalidation so operators have an audit trail.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from shared_test_runtimes import CATALOG, DepCacheManager, get
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.config import Settings, get_settings
from api_server.db.domain import Project

router = APIRouter(prefix="/projects/{project_id}/dep-cache", tags=["dep-cache"])
_log = structlog.get_logger("api_server.dep_cache")


class InvalidateRequest(BaseModel):
    """One invalidation request.

    ``runtime`` is a catalog id (``"python-pytest"``, etc.).
    ``lock_hash`` is optional — when present only that single entry
    gets wiped; when absent every cached entry for the runtime is
    swept.
    """

    runtime: str = Field(
        description="Runtime template id (must be in the catalog).",
        examples=["python-pytest", "node-jest"],
    )
    lock_hash: str | None = Field(
        default=None,
        description=(
            "When set, invalidate only the entry whose lock hash matches. "
            "When None, invalidate every entry of this runtime."
        ),
    )


class InvalidateResponse(BaseModel):
    """How many entries got wiped + their on-host paths.

    The UI uses the count for the toast and the path list for the
    "Detalles" expandable section."""

    runtime: str
    invalidated_count: int
    invalidated_paths: list[str]


async def _resolve_project(session: AsyncSession, project_id: UUID) -> Project:
    """Look up the project under the current tenant; 404 otherwise."""
    stmt = select(Project).where(Project.id == project_id)
    project = (await session.execute(stmt)).scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"project {project_id} not found",
        )
    return project


def _build_manager(settings: Settings) -> DepCacheManager:
    """Build a :class:`DepCacheManager` rooted at ``<data_root>/dep-cache``."""
    return DepCacheManager(Path(settings.data_root) / "dep-cache")


@router.post(
    "/invalidate",
    response_model=InvalidateResponse,
    summary="Invalidate dep-cache entries for a runtime",
)
async def invalidate_dep_cache(
    project_id: UUID,
    payload: InvalidateRequest,
    session: AsyncSession = Depends(get_tenant_session),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    settings: Settings = Depends(get_settings),
) -> InvalidateResponse:
    """Invalidate one or all cache entries for a runtime."""
    if payload.runtime not in CATALOG:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown runtime {payload.runtime!r}; not in the catalog",
        )
    await _resolve_project(session, project_id)
    template = get(payload.runtime)

    manager = _build_manager(settings)
    removed = manager.invalidate(template.id, lock_hash=payload.lock_hash)

    _log.info(
        "dep_cache.invalidate",
        project_id=str(project_id),
        runtime=payload.runtime,
        lock_hash=payload.lock_hash,
        actor=principal.user_id,
        removed_count=len(removed),
    )
    return InvalidateResponse(
        runtime=payload.runtime,
        invalidated_count=len(removed),
        invalidated_paths=[str(p) for p in removed],
    )


__all__ = ["router", "InvalidateRequest", "InvalidateResponse"]
