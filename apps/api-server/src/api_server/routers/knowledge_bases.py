"""`/knowledge-bases` + document upload endpoints (Plan 04 task_04_09).

Three resource families on this router:

  - **KB CRUD** — POST/GET/PUT/DELETE under `/knowledge-bases`.
  - **KB ↔ Project grants** — POST/DELETE/GET under
    `/knowledge-bases/{id}/projects` and the project-side accessor
    `/projects/{id}/knowledge-bases` that lists what a project sees.
  - **Documents** — POST (multipart upload), GET (list/single),
    DELETE under `/knowledge-bases/{id}/documents`.

A document upload writes the raw bytes to MinIO under the canonical
key `kb/{tenant_id}/{kb_id}/{document_id}/{filename}` and persists
a `documents` row with ``status='pending'``. The ingestion worker
(Plan 04 Fase C, task_04_11) picks it up from there; this endpoint
returns 201 immediately.
"""

from __future__ import annotations

import contextlib
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Project
from api_server.db.knowledge import (
    Chunk,
    Document,
    KbCategory,
    KnowledgeBase,
    KnowledgeBaseProject,
)
from api_server.routers._helpers import require_tenant_id, soft_delete
from api_server.schemas.knowledge import (
    DocumentResponse,
    KnowledgeBaseCreateRequest,
    KnowledgeBaseGrantRequest,
    KnowledgeBaseGrantResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdateRequest,
    to_document_response,
    to_kb_response,
)
from api_server.storage import ObjectStorage, ObjectStorageError, get_object_storage

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])
project_kb_router = APIRouter(
    prefix="/projects/{project_id}/knowledge-bases", tags=["knowledge-bases"]
)

# Hard caps on uploaded files — keeps a runaway client from filling
# MinIO with garbage. Real virus / mime sniffing is task_04_13.
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB


# ===========================================================================
# Helpers
# ===========================================================================
async def _load_kb(session: AsyncSession, kb_id: UUID) -> KnowledgeBase:
    result = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None))
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kb not found")
    return kb


async def _load_document(session: AsyncSession, document_id: UUID) -> Document:
    result = await session.execute(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    return doc


async def _verify_project_in_tenant(session: AsyncSession, project_id: UUID) -> None:
    result = await session.execute(
        select(Project.id).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")


def _storage_key(*, tenant_id: UUID, kb_id: UUID, document_id: UUID, filename: str) -> str:
    """Canonical MinIO key for a document. Tenant id is the first
    path component so cross-tenant access through the storage layer
    (i.e. if RLS ever leaked) would still require guessing UUIDs."""
    return f"kb/{tenant_id}/{kb_id}/{document_id}/{filename}"


# ===========================================================================
# KB CRUD
# ===========================================================================
@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_kb(
    payload: KnowledgeBaseCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> KnowledgeBaseResponse:
    tenant_id = require_tenant_id(principal)
    kb = KnowledgeBase(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        embedding_model_id=payload.embedding_model_id or "nomic-embed-text-v1.5",
        created_by=principal.user_id,
        category_id=payload.category_id,
    )
    session.add(kb)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"kb name already exists in tenant: {exc.orig}",
        ) from exc
    await session.refresh(kb)
    return to_kb_response(kb, await _load_category_for_kb(session, kb))


async def _load_category_for_kb(session: AsyncSession, kb: KnowledgeBase) -> KbCategory | None:
    """Carga la categoría de una KB para embedirla en el response.
    None si la KB no tiene categoría o la categoría fue borrada."""
    if kb.category_id is None:
        return None
    result = await session.execute(
        select(KbCategory).where(KbCategory.id == kb.category_id, KbCategory.deleted_at.is_(None))
    )
    return result.scalar_one_or_none()


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_kbs(
    q: str | None = None,
    category: str | None = None,
    limit: int = 100,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KnowledgeBaseResponse]:
    """List KBs visible to the tenant.

    Plan 06.9: `?q=` enables server-side typeahead so the admin-panel
    `<KbCombobox>` (used in the agent → KB grant dialog) can search
    by KB name without dragging every row through the browser.

    Plan 06.10: `?category=` filtra por categoría — acepta UUID o
    slug. Combinable con `?q=`.

    `limit` caps the response — keep it small for typeahead (20),
    default 100 is fine for the listing page.
    """
    from api_server.db.knowledge import KbCategory

    stmt = select(KnowledgeBase).where(KnowledgeBase.deleted_at.is_(None))
    if q is not None and q.strip():
        stmt = stmt.where(KnowledgeBase.name.ilike(f"%{q.strip()}%"))
    if category is not None and category.strip():
        cat_filter = category.strip()
        try:
            cat_uuid = UUID(cat_filter)
        except ValueError:
            # Trata como slug — resuelve al id (built-in o tenant).
            cat_row = await session.execute(
                select(KbCategory.id).where(
                    KbCategory.slug == cat_filter,
                    KbCategory.deleted_at.is_(None),
                )
            )
            cat_id = cat_row.scalar_one_or_none()
            if cat_id is None:
                # Slug no existe — listado vacío en lugar de 404; mejora UX
                # del combobox cuando el cliente está fuera de sync.
                return []
            stmt = stmt.where(KnowledgeBase.category_id == cat_id)
        else:
            stmt = stmt.where(KnowledgeBase.category_id == cat_uuid)
    stmt = stmt.order_by(KnowledgeBase.created_at.desc()).limit(max(1, min(limit, 500)))
    result = await session.execute(stmt)
    kbs = list(result.scalars().all())

    # Una query batch para todas las categorías referenciadas (evita N+1).
    cat_ids = {kb.category_id for kb in kbs if kb.category_id is not None}
    cats_by_id: dict[UUID, KbCategory] = {}
    if cat_ids:
        cat_rows = await session.execute(
            select(KbCategory).where(KbCategory.id.in_(cat_ids), KbCategory.deleted_at.is_(None))
        )
        cats_by_id = {c.id: c for c in cat_rows.scalars().all()}

    return [
        to_kb_response(kb, cats_by_id.get(kb.category_id) if kb.category_id is not None else None)
        for kb in kbs
    ]


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_kb(
    kb_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> KnowledgeBaseResponse:
    kb = await _load_kb(session, kb_id)
    return to_kb_response(kb, await _load_category_for_kb(session, kb))


@router.put("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_kb(
    kb_id: UUID,
    payload: KnowledgeBaseUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> KnowledgeBaseResponse:
    require_tenant_id(principal)
    kb = await _load_kb(session, kb_id)
    if payload.name is not None:
        kb.name = payload.name
    if payload.description is not None:
        kb.description = payload.description
    if payload.embedding_model_id is not None:
        kb.embedding_model_id = payload.embedding_model_id
    # Plan 06.10: model_fields_set para distinguir "category_id no
    # enviado" (no tocar) de "category_id explícitamente null" (limpiar).
    if "category_id" in payload.model_fields_set:
        kb.category_id = payload.category_id
    await session.flush()
    await session.refresh(kb)
    return to_kb_response(kb, await _load_category_for_kb(session, kb))


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    kb_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    kb = await _load_kb(session, kb_id)
    await soft_delete(session, kb)


# ===========================================================================
# KB ↔ Project grants (M:N)
# ===========================================================================
@router.post(
    "/{kb_id}/projects",
    response_model=KnowledgeBaseGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def grant_kb_to_project(
    kb_id: UUID,
    payload: KnowledgeBaseGrantRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> KnowledgeBaseGrantResponse:
    """Make the KB visible to a project. Idempotent: re-granting
    returns the existing row."""
    tenant_id = require_tenant_id(principal)
    kb = await _load_kb(session, kb_id)
    await _verify_project_in_tenant(session, payload.project_id)

    existing = await session.execute(
        select(KnowledgeBaseProject).where(
            KnowledgeBaseProject.kb_id == kb.id,
            KnowledgeBaseProject.project_id == payload.project_id,
        )
    )
    grant = existing.scalar_one_or_none()
    if grant is None:
        grant = KnowledgeBaseProject(
            kb_id=kb.id,
            project_id=payload.project_id,
            tenant_id=tenant_id,
            granted_by=principal.user_id,
        )
        session.add(grant)
        await session.flush()
        await session.refresh(grant)

    return KnowledgeBaseGrantResponse(
        kb_id=grant.kb_id,
        project_id=grant.project_id,
        tenant_id=grant.tenant_id,
        granted_at=grant.granted_at,
        granted_by=grant.granted_by,
    )


@router.delete("/{kb_id}/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_kb_from_project(
    kb_id: UUID,
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    await _load_kb(session, kb_id)
    result = await session.execute(
        select(KnowledgeBaseProject).where(
            KnowledgeBaseProject.kb_id == kb_id,
            KnowledgeBaseProject.project_id == project_id,
        )
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        # Idempotent — revoking a non-existent grant is a no-op.
        return
    await session.delete(grant)
    await session.flush()


# ---------------------------------------------------------------------------
# Plan 06.9 task_06_9_05 — inverse listings (used by the KB detail panel)
# ---------------------------------------------------------------------------
@router.get("/{kb_id}/projects", response_model=list[dict[str, object]])
async def list_projects_for_kb(
    kb_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, object]]:
    """List the projects that have been granted this KB.

    Used by the `Asignaciones` panel in the admin-panel KB detail
    page. Mirror of `list_kbs_for_project` but in the other direction.
    """
    from api_server.db.domain import Project

    await _load_kb(session, kb_id)
    rows = await session.execute(
        select(
            KnowledgeBaseProject.project_id,
            KnowledgeBaseProject.granted_at,
            KnowledgeBaseProject.granted_by,
            Project.name,
        )
        .join(Project, Project.id == KnowledgeBaseProject.project_id)
        .where(
            KnowledgeBaseProject.kb_id == kb_id,
            Project.deleted_at.is_(None),
        )
        .order_by(Project.name)
    )
    return [
        {
            "project_id": str(r.project_id),
            "name": r.name,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "granted_by": str(r.granted_by) if r.granted_by else None,
        }
        for r in rows.all()
    ]


@router.get("/{kb_id}/agents", response_model=list[dict[str, object]])
async def list_agents_for_kb(
    kb_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, object]]:
    """List the agents that have been granted this KB.

    Used by the `Asignaciones` panel — the agent half. Joins
    `agent_knowledge_bases` to `agents` to surface the human-readable
    name + scope (so the UI can render a "this is a fork" marker).
    """
    from api_server.db.domain import Agent
    from api_server.db.knowledge import AgentKnowledgeBase

    await _load_kb(session, kb_id)
    rows = await session.execute(
        select(
            AgentKnowledgeBase.agent_id,
            AgentKnowledgeBase.granted_at,
            AgentKnowledgeBase.granted_by,
            Agent.name,
            Agent.scope,
            Agent.role,
        )
        .join(Agent, Agent.id == AgentKnowledgeBase.agent_id)
        .where(
            AgentKnowledgeBase.kb_id == kb_id,
            Agent.deleted_at.is_(None),
        )
        .order_by(Agent.name)
    )
    return [
        {
            "agent_id": str(r.agent_id),
            "name": r.name,
            "scope": r.scope,
            "role": r.role,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "granted_by": str(r.granted_by) if r.granted_by else None,
        }
        for r in rows.all()
    ]


@project_kb_router.get("", response_model=list[KnowledgeBaseResponse])
async def list_kbs_for_project(
    project_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KnowledgeBaseResponse]:
    """List the KBs a project has been granted."""
    await _verify_project_in_tenant(session, project_id)
    stmt = (
        select(KnowledgeBase)
        .join(KnowledgeBaseProject, KnowledgeBaseProject.kb_id == KnowledgeBase.id)
        .where(
            KnowledgeBaseProject.project_id == project_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .order_by(KnowledgeBase.created_at.desc())
    )
    result = await session.execute(stmt)
    return [to_kb_response(kb) for kb in result.scalars().all()]


# ===========================================================================
# Document upload + CRUD
# ===========================================================================
@router.post(
    "/{kb_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    kb_id: UUID,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> DocumentResponse:
    """Upload a file into a KB. The bytes go to MinIO; the metadata
    row lands in `documents` with ``status='pending'``."""
    tenant_id = require_tenant_id(principal)
    kb = await _load_kb(session, kb_id)

    # Read the upload up-front so we can size-check before we touch MinIO.
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="empty upload")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"upload exceeds {MAX_UPLOAD_BYTES} bytes",
        )

    document_id = uuid4()
    filename = file.filename or "unknown"
    storage_key = _storage_key(
        tenant_id=tenant_id, kb_id=kb.id, document_id=document_id, filename=filename
    )

    try:
        await storage.put_object(
            key=storage_key,
            data=payload,
            content_type=file.content_type or "application/octet-stream",
        )
    except ObjectStorageError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"storage backend failed: {exc}",
        ) from exc

    doc = Document(
        id=document_id,
        tenant_id=tenant_id,
        kb_id=kb.id,
        title=title or filename,
        source_filename=filename,
        source_mime_type=file.content_type or "application/octet-stream",
        source_storage_key=storage_key,
        source_size_bytes=len(payload),
        status="pending",
        created_by=principal.user_id,
    )
    session.add(doc)
    await session.flush()
    await session.refresh(doc)
    return to_document_response(doc)


@router.get("/{kb_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    kb_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[DocumentResponse]:
    await _load_kb(session, kb_id)
    result = await session.execute(
        select(Document)
        .where(Document.kb_id == kb_id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc())
    )
    return [to_document_response(d) for d in result.scalars().all()]


@router.get("/{kb_id}/documents/{document_id}", response_model=DocumentResponse)
async def get_document(
    kb_id: UUID,
    document_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> DocumentResponse:
    doc = await _load_document(session, document_id)
    if doc.kb_id != kb_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not in this kb")
    return to_document_response(doc)


@router.delete("/{kb_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: UUID,
    document_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    storage: ObjectStorage = Depends(get_object_storage),
) -> None:
    """Soft-delete the metadata row + drop the MinIO blob. We do the
    blob deletion best-effort — a 503 from the storage backend
    shouldn't block the audit-trail update on the DB row."""
    require_tenant_id(principal)
    doc = await _load_document(session, document_id)
    if doc.kb_id != kb_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not in this kb")
    # Best-effort blob drop — metadata is the source of truth. A
    # storage hiccup leaves an orphan that the GC job sweeps later.
    with contextlib.suppress(ObjectStorageError):
        await storage.delete_object(key=doc.source_storage_key)
    await soft_delete(session, doc)


# ===========================================================================
# Citation viewer support (Plan 04 task_04_25)
# ===========================================================================
documents_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.get("/{document_id}/citations")
async def get_document_citations(
    document_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Return the Document + all its chunks ordered by `ordinal`,
    suitable for the citation viewer (Plan 04 task_04_25).

    Tenant isolation rides on RLS; cross-tenant access would surface
    as 404. We deliberately do NOT require knowing the kb_id —
    document_id is enough, and the viewer is often deep-linked from
    a citation in chat where only the document_id is on hand.
    """
    doc = await _load_document(session, document_id)
    chunk_rows = await session.execute(
        select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.ordinal)
    )
    chunks = [
        {
            "id": str(c.id),
            "ordinal": c.ordinal,
            "content": c.content,
            "bbox": c.bbox,
            "metadata": c.metadata_,
        }
        for c in chunk_rows.scalars().all()
    ]
    return {
        "document": {
            "id": str(doc.id),
            "kb_id": str(doc.kb_id),
            "title": doc.title,
            "source_filename": doc.source_filename,
            "source_mime_type": doc.source_mime_type,
            "page_count": doc.page_count,
            "status": doc.status,
            # error_message es null en el feliz path; la UI de
            # /ingestion lo usa para pintar la causa del fallo cuando
            # status == "failed" sin tener que mantener una segunda
            # llamada a la API.
            "error_message": doc.error_message,
        },
        "chunks": chunks,
    }


__all__ = ["documents_router", "project_kb_router", "router"]
