"""Agregados de las bases para el LISTADO (`task_ui_21`).

Las cifras de una base (documentos, estado de indexado, última actualización) y
los proyectos a los que está concedida se calculan aquí, en SQL y por lotes: una
consulta agrupada por `kb_id` en vez de dos listados por fila desde el cliente.
Toda consulta lleva `tenant_id` además de la RLS.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Project
from api_server.db.knowledge import Document, KnowledgeBaseProject
from api_server.schemas.knowledge import (
    KbDocumentStats,
    KbGrantedProject,
    KnowledgeBaseResponse,
)


async def document_stats_by_kb(
    session: AsyncSession, *, tenant_id: UUID, kb_ids: Iterable[UUID]
) -> dict[UUID, KbDocumentStats]:
    """Total, conteo por estado y último `updated_at` por base (una consulta)."""
    ids = list(kb_ids)
    stats = {kb_id: KbDocumentStats() for kb_id in ids}
    if not ids:
        return stats
    rows = await session.execute(
        select(
            Document.kb_id,
            Document.status,
            func.count().label("n"),
            func.max(Document.updated_at).label("last"),
        )
        .where(
            Document.tenant_id == tenant_id,
            Document.kb_id.in_(ids),
            Document.deleted_at.is_(None),
        )
        .group_by(Document.kb_id, Document.status)
    )
    for r in rows.all():
        s = stats[r.kb_id]
        s.total += r.n
        s.by_status[r.status] = r.n
        if s.last_updated_at is None or r.last > s.last_updated_at:
            s.last_updated_at = r.last
    return stats


async def granted_projects_by_kb(
    session: AsyncSession, *, tenant_id: UUID, kb_ids: Iterable[UUID]
) -> dict[UUID, list[KbGrantedProject]]:
    """Los proyectos concedidos de cada base (una consulta, orden por nombre)."""
    ids = list(kb_ids)
    grants: dict[UUID, list[KbGrantedProject]] = {kb_id: [] for kb_id in ids}
    if not ids:
        return grants
    rows = await session.execute(
        select(KnowledgeBaseProject.kb_id, KnowledgeBaseProject.project_id, Project.name)
        .join(Project, Project.id == KnowledgeBaseProject.project_id)
        .where(
            KnowledgeBaseProject.tenant_id == tenant_id,
            KnowledgeBaseProject.kb_id.in_(ids),
            Project.tenant_id == tenant_id,
            Project.deleted_at.is_(None),
        )
        .order_by(Project.name)
    )
    for r in rows.all():
        grants[r.kb_id].append(KbGrantedProject(project_id=r.project_id, name=r.name))
    return grants


async def attach_aggregates(
    session: AsyncSession,
    responses: list[KnowledgeBaseResponse],
    *,
    tenant_id: UUID,
    with_grants: bool,
) -> list[KnowledgeBaseResponse]:
    """Rellena `document_stats` (y `granted_projects`) de un listado de bases."""
    ids = [r.id for r in responses]
    stats = await document_stats_by_kb(session, tenant_id=tenant_id, kb_ids=ids)
    grants = (
        await granted_projects_by_kb(session, tenant_id=tenant_id, kb_ids=ids)
        if with_grants
        else None
    )
    for r in responses:
        r.document_stats = stats[r.id]
        if grants is not None:
            r.granted_projects = grants[r.id]
    return responses
