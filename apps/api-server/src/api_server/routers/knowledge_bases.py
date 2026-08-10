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

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from redis.asyncio import Redis
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.celery_client import enqueue_ingestion
from api_server.db.domain import Project
from api_server.db.knowledge import (
    Chunk,
    Document,
    KbCategory,
    KnowledgeBase,
    KnowledgeBaseProject,
)
from api_server.events import delete_document_stream
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.ingestion.formats import cached_supported_formats
from api_server.logging import get_logger
from api_server.rag.search import search_kb_chunks
from api_server.routers._helpers import require_tenant_id, soft_delete
from api_server.routers._pagination import (
    MAX_PAGE_SIZE,
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers._uploads import declared_content_length, read_capped_upload
from api_server.routers.docs_viewer import get_query_embedder
from api_server.schemas.knowledge import (
    ChunkSearchHit,
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

_logger = get_logger(__name__)

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


async def _kb_has_chunks(session: AsyncSession, kb_id: UUID) -> bool:
    """True si la KB tiene al menos un chunk indexado (vía sus documentos).

    Tenant-scoped por RLS sobre `chunks`/`documents`. Usado por el guard
    de re-embedding del PUT (Plan 06.17 task_06_17_05)."""
    result = await session.execute(
        select(Chunk.id)
        .join(Document, Document.id == Chunk.document_id)
        .where(Document.kb_id == kb_id, Document.deleted_at.is_(None))
        .limit(1)
    )
    return result.scalar_one_or_none() is not None


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

    # Pre-check the (tenant_id, name) uniqueness so the happy 409 carries a
    # clean message instead of leaking the SQLAlchemy `exc.orig` to the
    # client (error-obs-logging-3). The unique index is partial on
    # `deleted_at IS NULL`, so a soft-deleted homonym does NOT collide.
    # Mirrors the proactive pattern in routers/kb_categories.py.
    existing = await session.execute(
        select(KnowledgeBase.id).where(
            KnowledgeBase.tenant_id == tenant_id,
            KnowledgeBase.name == payload.name,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="kb name already exists in tenant",
        )

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
        # Race fallback: a concurrent request inserted the same name between
        # the pre-check and the flush. Log the full driver error server-side
        # for diagnostics but return a generic message (never `exc.orig`).
        await session.rollback()
        _logger.warning(
            "kb.create_integrity_error",
            tenant_id=str(tenant_id),
            kb_name=payload.name,
            error=str(exc.orig),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="kb name already exists in tenant",
        ) from exc
    await session.refresh(kb)

    # KB Q1: auto-grant al proyecto de origen (un grant NORMAL de kb_projects —
    # auditable y revocable; nada pasa a ser visible "mágicamente"). El
    # proyecto debe ser del tenant (la sesión RLS ya lo garantiza al leerlo).
    if payload.project_id is not None:
        project = await session.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
        session.add(KnowledgeBaseProject(kb_id=kb.id, project_id=project.id, tenant_id=tenant_id))
        await session.flush()
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


@router.get("/{kb_id}/search", response_model=list[ChunkSearchHit])
async def search_kb(
    kb_id: UUID,
    q: str,
    limit: int = 8,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    embedder: Embedder = Depends(get_query_embedder),
) -> list[ChunkSearchHit]:
    """Preview/búsqueda de chunks dentro de UNA KB (Plan 06.17 task_06_17_05).

    Híbrida BM25 + vector + RRF, acotada a los documentos de esta KB y
    aislada por tenant vía RLS. Es la herramienta del operador para
    verificar *qué* indexó la KB (y que el RAG encontrará algo). El
    embedder de la query se inyecta (reutiliza ``get_query_embedder``);
    si está caído, degradamos a BM25-only en vez de fallar.

    Cross-tenant: ``_load_kb`` devuelve 404 para una KB de otro tenant
    (RLS la oculta), así que la búsqueda nunca filtra contenido ajeno.
    """
    await _load_kb(session, kb_id)
    if not q.strip():
        return []

    query_embedding: list[float] | None = None
    try:
        vectors = await embedder.embed([q])
        query_embedding = vectors[0] if vectors else None
    except EmbeddingError as exc:
        # El path vectorial es nice-to-have — BM25 sigue funcionando sin
        # embedder. Log + degradación, nunca 5xx por Ollama caído.
        _logger.warning("kb.search_embedder_failed", kb_id=str(kb_id), error=str(exc))

    hits = await search_kb_chunks(
        session,
        kb_id=kb_id,
        query=q,
        query_embedding=query_embedding,
        limit=max(1, min(limit, 50)),
    )
    return [
        ChunkSearchHit(
            chunk_id=h.chunk_id,
            document_id=h.document_id,
            kb_id=h.kb_id,
            ordinal=h.ordinal,
            content=h.content,
            bbox=h.bbox,
            bm25_rank=h.bm25_rank,
            vector_rank=h.vector_rank,
            rrf_score=h.rrf_score,
        )
        for h in hits
    ]


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
    if (
        payload.embedding_model_id is not None
        and payload.embedding_model_id != kb.embedding_model_id
    ):
        # Plan 06.17 task_06_17_05: cambiar el modelo de embedding con
        # chunks ya indexados los dejaría con vectores de OTRO modelo, que
        # el path vectorial nunca casaría → RAG roto en silencio. El
        # re-embedding real está diferido a Plan 12; hasta entonces
        # bloqueamos el cambio (409) si la KB tiene chunks. Una KB vacía sí
        # puede cambiar de modelo (no hay nada que invalidar).
        if await _kb_has_chunks(session, kb.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "no se puede cambiar embedding_model_id: la KB ya tiene chunks"
                    " indexados (re-embedding diferido a Plan 12). Crea una KB nueva"
                    " con el modelo deseado y reindexa los documentos."
                ),
            )
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
    # G-03: soft-borra en cascada los documentos de la KB para que el GC recupere
    # sus chunks + blobs; antes quedaban vivos bajo una KB muerta, eternos.
    from api_server.db.knowledge_gc import soft_delete_kb_cascade

    await soft_delete_kb_cascade(session, kb_id=kb_id)
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
    request: Request,
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

    # Rechazo por FORMATO antes de leer un byte (prod-13 task_prod13_04 / api-2).
    # La lista no está escrita aquí: se le pregunta a docling-serve al arrancar y
    # se cachea, con respaldo fijo si no contesta — ver
    # `api_server/ingestion/formats.py`. La lectura es de la caché EN PROCESO:
    # una validación de entrada no puede depender de la red.
    # Sin esto el rechazo llegaba minutos después, desde el pipeline, tras haber
    # transferido y almacenado hasta 50 MiB.
    rejection = cached_supported_formats().rejection_reason(
        filename=file.filename, content_type=file.content_type
    )
    if rejection is not None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=rejection,
        )

    # Read the upload in CHUNKS, stopping the moment it exceeds the cap (prod-13
    # task_prod13_04 / api-2): `await file.read()` used to pull the whole body
    # into the heap and size-check afterwards, so a 2 GB upload was 2 GB of RSS
    # in the process that serves every request and every WebSocket. The declared
    # `Content-Length` gives a free early reject; the chunked read is what makes
    # the cap true, because that header is written by the client.
    payload = await read_capped_upload(
        file,
        max_bytes=MAX_UPLOAD_BYTES,
        declared_content_length=declared_content_length(request.headers),
    )
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="empty upload"
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

    # Plan 06.11: hand the document to the ingestion worker. Best-effort
    # — a broker hiccup must not fail the upload (the row is persisted as
    # `pending` and the beat sweep re-enqueues it).
    # prod-06 task_prod06_beat_02: stamp the enqueue LEASE on a successful
    # enqueue so the sweep does not re-enqueue a doc that is still legitimately
    # queued. A failed enqueue leaves it NULL → the sweep claims it (after the
    # age cutoff) as a missed enqueue.
    if await enqueue_ingestion(doc.id):
        doc.enqueued_at = datetime.now(UTC)

    return to_document_response(doc)


@router.get("/{kb_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    kb_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[DocumentResponse]:
    """Los documentos de la KB, más reciente primero, PAGINADO (prod-13, api-6).

    Una KB de manuales tiene miles de documentos y este listado los devolvía
    todos. El desempate por `id` da orden total: sin él, dos documentos subidos en
    el mismo instante pueden aparecer en dos páginas o en ninguna.
    """
    await _load_kb(session, kb_id)
    stmt = (
        select(Document)
        .where(Document.kb_id == kb_id, Document.deleted_at.is_(None))
        .order_by(Document.created_at.desc(), Document.id)
    )
    result = await session.execute(apply_pagination(stmt, limit=limit, offset=offset))
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
    redis: Redis = Depends(get_redis),
) -> None:
    """Soft-delete the metadata row. El blob de MinIO lo reclama el GC.

    ORDEN (prod-04 task_prod_04_11, hallazgo db-3). Antes esto borraba el blob
    ANTES del `soft_delete`, y el commit ocurre al cerrar el request: si ese
    commit fallaba, quedaba un documento **vivo** en la base de datos cuyo
    binario ya no existía. La UI seguía ofreciéndolo, el reindex era imposible y
    la fuente estaba perdida sin vuelta atrás — un borrado «reversible» que
    destruía el dato antes de asegurarse de que la reversión era posible.

    Además contradecía la promesa de `db/knowledge.py` («soft-deletable so a
    destructive UI action can be reverted before the cleanup job kicks in»):
    revertir un soft-delete cuyo blob ya no está no revierte nada.

    Ahora el binario sobrevive a la ventana de gracia y lo hard-borra
    `workers.collect_knowledge_garbage` (G-03) cuando
    `deleted_at < now - knowledge_gc_retention_days`, junto con los chunks y la
    fila. Ese barrido ya existía; lo único que hacía falta era dejar de
    adelantarse a él.
    """
    require_tenant_id(principal)
    doc = await _load_document(session, document_id)
    if doc.kb_id != kb_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not in this kb")
    await soft_delete(session, doc)
    # Drop the ingestion stream too so no orphan progress events linger in Redis.
    await delete_document_stream(redis, str(doc.id))


@router.post("/{kb_id}/documents/{document_id}/reindex", response_model=DocumentResponse)
async def reindex_document(
    kb_id: UUID,
    document_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> DocumentResponse:
    """Re-run ingestion for a document (Plan 06.11 task_06_11_03).

    The recovery path for a `failed` document — or to re-parse after an
    upstream service was fixed. Resets the row to `pending`, clears the
    error, drops the document's stale chunks (so the re-run doesn't
    duplicate them) and re-enqueues. The source blob in MinIO is reused.
    """
    require_tenant_id(principal)
    doc = await _load_document(session, document_id)
    if doc.kb_id != kb_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not in this kb")

    # Drop stale chunks so the re-run is idempotent (the pipeline only
    # inserts; it does not clear prior chunks).
    await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    doc.status = "pending"
    doc.error_message = None
    doc.indexed_at = None
    doc.page_count = 0
    doc.enqueued_at = None
    await session.flush()
    await session.refresh(doc)

    # prod-06 task_prod06_beat_02: refresh the enqueue lease on a successful
    # re-enqueue (see upload_document); a failed enqueue leaves it NULL so the
    # sweep retries.
    if await enqueue_ingestion(doc.id):
        doc.enqueued_at = datetime.now(UTC)
    return to_document_response(doc)


# ===========================================================================
# Citation viewer support (Plan 04 task_04_25)
# ===========================================================================
documents_router = APIRouter(prefix="/documents", tags=["documents"])


@documents_router.get("/{document_id}/citations")
async def get_document_citations(
    document_id: UUID,
    limit: int = limit_query(default=MAX_PAGE_SIZE),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Return the Document + a PAGE of its chunks ordered by `ordinal`,
    suitable for the citation viewer (Plan 04 task_04_25).

    Tenant isolation rides on RLS; cross-tenant access would surface
    as 404. We deliberately do NOT require knowing the kb_id —
    document_id is enough, and the viewer is often deep-linked from
    a citation in chat where only the document_id is on hand.

    Dos arreglos de prod-13 (perf-8 + api-6):

      * **Columnas explícitas, no la entidad `Chunk`.** `select(Chunk)` traía
        también `embedding`, un `vector(768)`: ~3 KB por fila que el visor no usa
        para nada. Un PDF de 2.000 chunks eran 6 MB de vectores por el cable en
        cada apertura del visor. Se seleccionan las cinco columnas que el payload
        realmente contiene.
      * **Paginado por `ordinal`**, con `limit`/`offset` compartidos. El
        `ordinal` es único por documento, así que el orden ya es total y no hace
        falta desempate.

    Dos decisiones sobre la paginación que NO son cosméticas, porque el visor de
    citas del admin-panel llama a este endpoint SIN `limit`:

      * el default es `MAX_PAGE_SIZE`, no `DEFAULT_PAGE_SIZE`. Con 100 por
        defecto, un PDF de 2.000 chunks habría dejado al visor pintando los
        resaltados de las primeras páginas y NINGUNO del resto, sin decir nada:
        cambiar una respuesta pesada por una respuesta silenciosamente incompleta
        no es una mejora.
      * la respuesta lleva `total` y `has_more`. Aunque el cliente aún no pagine,
        la truncación pasa a ser **detectable** en vez de invisible — el visor
        puede avisar, y quien depure sabe que faltan filas. Cablear el paginado en
        el front es trabajo del admin-panel (fuera de este cambio).
    """
    doc = await _load_document(session, document_id)
    total = int(
        (
            await session.execute(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document_id)
            )
        ).scalar_one()
    )
    chunk_rows = await session.execute(
        apply_pagination(
            select(
                Chunk.id,
                Chunk.ordinal,
                Chunk.content,
                Chunk.bbox,
                Chunk.metadata_,
            )
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.ordinal),
            limit=limit,
            offset=offset,
        )
    )
    chunks = [
        {
            "id": str(row.id),
            "ordinal": row.ordinal,
            "content": row.content,
            "bbox": row.bbox,
            "metadata": row.metadata_,
        }
        for row in chunk_rows.all()
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
        # Metadatos de paginación: `total` es el recuento REAL de chunks del
        # documento, no `len(chunks)`. Sin esto, un cliente que no pagina no
        # tiene forma de saber que le faltan filas.
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(chunks) < total,
    }


__all__ = ["documents_router", "project_kb_router", "router"]
