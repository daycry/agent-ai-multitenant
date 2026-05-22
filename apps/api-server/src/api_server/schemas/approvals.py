"""Pydantic schemas for the /approvals endpoints (Plan 02 Fase F)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from api_server.db.domain import ApprovalRequest


class ApprovalResolveRequest(BaseModel):
    """Body of POST /approvals/{id}/resolve."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    approved: bool
    reason: str | None = None


class ApprovalRequestResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: UUID
    execution_id: UUID
    task_id: UUID
    project_id: UUID
    category: str
    action: dict[str, Any]
    status: str
    reason: str | None
    requested_at: datetime
    resolved_at: datetime | None
    resolved_by: UUID | None


def to_approval_response(request: ApprovalRequest) -> ApprovalRequestResponse:
    return ApprovalRequestResponse(
        id=request.id,
        execution_id=request.execution_id,
        task_id=request.task_id,
        project_id=request.project_id,
        category=request.category,
        action=request.action,
        status=request.status,
        reason=request.reason,
        requested_at=request.requested_at,
        resolved_at=request.resolved_at,
        resolved_by=request.resolved_by,
    )
