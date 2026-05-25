"""Pydantic schemas for /knowledge-bases endpoints (Plan 04 task_04_09)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from api_server.db.knowledge import Document, KnowledgeBase

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# KnowledgeBase
# ---------------------------------------------------------------------------
class KnowledgeBaseCreateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    embedding_model_id: str | None = Field(default=None, max_length=120)


class KnowledgeBaseUpdateRequest(BaseModel):
    model_config = _BASE_CONFIG

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    embedding_model_id: str | None = Field(default=None, max_length=120)


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


def to_kb_response(kb: KnowledgeBase) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        id=kb.id,
        tenant_id=kb.tenant_id,
        name=kb.name,
        description=kb.description,
        embedding_model_id=kb.embedding_model_id,
        created_by=kb.created_by,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
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
    "KnowledgeBaseCreateRequest",
    "KnowledgeBaseGrantRequest",
    "KnowledgeBaseGrantResponse",
    "KnowledgeBaseResponse",
    "KnowledgeBaseUpdateRequest",
    "to_document_response",
    "to_kb_response",
]
