"""Pydantic schemas for /knowledge-bases endpoints (Plan 04 task_04_09)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.knowledge import Document, KbCategory, KnowledgeBase

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# KnowledgeBase
# ---------------------------------------------------------------------------
class KnowledgeBaseCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    embedding_model_id: str | None = Field(default=None, max_length=120)
    # Plan 06.10: opcional al crear. Si se omite la KB queda sin
    # categoría hasta que el tenant la asigne en Editar.
    category_id: UUID | None = None


class KnowledgeBaseUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    embedding_model_id: str | None = Field(default=None, max_length=120)
    # Plan 06.10: cambiar a None desasigna la categoría.
    category_id: UUID | None = None


class KbCategorySummary(BaseModel):
    """Slim embed para `KnowledgeBaseResponse.category` — evita un
    fetch extra del frontend cuando lista KBs."""

    model_config = _BASE_CONFIG

    id: UUID
    slug: str
    name: str
    color: str | None
    is_builtin: bool


class KnowledgeBaseResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    embedding_model_id: str
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime
    # Plan 06.12 (ADR 0029): true = KB built-in del catálogo global
    # (read-only para el tenant; la UI oculta editar/borrar).
    is_builtin: bool = False
    # Plan 06.10: categoría embebida (puede ser null si la KB no
    # está categorizada o si la categoría fue borrada).
    category: KbCategorySummary | None = None


def to_kb_response(kb: KnowledgeBase, category: KbCategory | None = None) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=kb.id,
        tenant_id=kb.tenant_id,
        name=kb.name,
        description=kb.description,
        embedding_model_id=kb.embedding_model_id,
        created_by=kb.created_by,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        is_builtin=kb.is_builtin,
        category=to_kb_category_summary(category) if category is not None else None,
    )


# ---------------------------------------------------------------------------
# KbCategory (Plan 06.10)
# ---------------------------------------------------------------------------
class KbCategoryCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    slug: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    # Hex color con `#` opcional (`#3b82f6` o `3b82f6`).
    color: str | None = Field(default=None, max_length=16)


class KbCategoryUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    color: str | None = Field(default=None, max_length=16)


class KbCategoryResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID | None
    slug: str
    name: str
    color: str | None
    is_builtin: bool
    created_at: datetime
    updated_at: datetime


def to_kb_category_response(cat: KbCategory) -> KbCategoryResponse:
    return KbCategoryResponse(
        id=cat.id,
        tenant_id=cat.tenant_id,
        slug=cat.slug,
        name=cat.name,
        color=cat.color,
        is_builtin=cat.is_builtin,
        created_at=cat.created_at,
        updated_at=cat.updated_at,
    )


def to_kb_category_summary(cat: KbCategory) -> KbCategorySummary:
    return KbCategorySummary(
        id=cat.id,
        slug=cat.slug,
        name=cat.name,
        color=cat.color,
        is_builtin=cat.is_builtin,
    )


# ---------------------------------------------------------------------------
# KB ↔ Project grants (M:N)
# ---------------------------------------------------------------------------
class KnowledgeBaseGrantRequest(BaseModel):
    """Grant a KB to a project — adds a row to `kb_projects`."""

    model_config = _BASE_CONFIG

    project_id: UUID


class KnowledgeBaseGrantResponse(BaseModel):
    model_config = _BASE_CONFIG

    kb_id: UUID
    project_id: UUID
    tenant_id: UUID
    granted_at: datetime
    granted_by: UUID | None


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------
class DocumentResponse(BaseModel):
    model_config = _BASE_CONFIG

    id: UUID
    tenant_id: UUID
    kb_id: UUID
    title: str
    source_filename: str
    source_mime_type: str
    source_storage_key: str
    source_size_bytes: int
    status: str
    error_message: str | None
    page_count: int
    indexed_at: datetime | None
    created_by: UUID | None
    created_at: datetime
    updated_at: datetime


def to_document_response(d: Document) -> DocumentResponse:
    return DocumentResponse(
        id=d.id,
        tenant_id=d.tenant_id,
        kb_id=d.kb_id,
        title=d.title,
        source_filename=d.source_filename,
        source_mime_type=d.source_mime_type,
        source_storage_key=d.source_storage_key,
        source_size_bytes=d.source_size_bytes,
        status=d.status,
        error_message=d.error_message,
        page_count=d.page_count,
        indexed_at=d.indexed_at,
        created_by=d.created_by,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


__all__ = [
    "DocumentResponse",
    "KbCategoryCreateRequest",
    "KbCategoryResponse",
    "KbCategorySummary",
    "KbCategoryUpdateRequest",
    "KnowledgeBaseCreateRequest",
    "KnowledgeBaseGrantRequest",
    "KnowledgeBaseGrantResponse",
    "KnowledgeBaseResponse",
    "KnowledgeBaseUpdateRequest",
    "to_document_response",
    "to_kb_category_response",
    "to_kb_category_summary",
    "to_kb_response",
]
