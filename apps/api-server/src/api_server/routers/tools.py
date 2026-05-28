"""`/tools` endpoints -- tenant-scoped CRUD with built-in catalog read-through.

Same shape as /skills: tenant users CRUD their custom tools and read
platform built-ins via the `tools_builtin_read` SELECT policy
(migration 0005). Built-ins are read-only from the tenant API.

Tool execution is NOT implemented in Plan 01 (spec defers to Plan 02);
this router only models tool metadata.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Tool
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.schemas.catalog import (
    ToolCreateRequest,
    ToolResponse,
    ToolUpdateRequest,
    to_tool_response,
)

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=list[ToolResponse])
async def list_tools(
    category: str | None = Query(default=None),
    implementation_type: str | None = Query(default=None),
    is_builtin: bool | None = Query(default=None),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ToolResponse]:
    stmt = select(Tool).where(Tool.deleted_at.is_(None))
    if category is not None:
        stmt = stmt.where(Tool.category == category)
    if implementation_type is not None:
        stmt = stmt.where(Tool.implementation_type == implementation_type)
    if is_builtin is not None:
        stmt = stmt.where(Tool.is_builtin.is_(is_builtin))
    stmt = stmt.order_by(Tool.created_at)
    result = await session.execute(stmt)
    return [to_tool_response(t) for t in result.scalars().all()]


@router.get("/{tool_id}", response_model=ToolResponse)
async def get_tool(
    tool_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> ToolResponse:
    result = await session.execute(
        select(Tool).where(Tool.id == tool_id, Tool.deleted_at.is_(None))
    )
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tool not found")
    return to_tool_response(tool)


@router.post("", response_model=ToolResponse, status_code=status.HTTP_201_CREATED)
async def create_tool(
    payload: ToolCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> ToolResponse:
    tenant_id = require_tenant_id(principal)
    tool = Tool(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        category=payload.category,
        input_schema=payload.input_schema,
        output_schema=payload.output_schema,
        implementation_type=payload.implementation_type.value,
        implementation_ref=payload.implementation_ref,
        security_level=payload.security_level.value,
        timeout_seconds=payload.timeout_seconds,
        rate_limit_per_minute=payload.rate_limit_per_minute,
        is_builtin=False,
    )
    session.add(tool)
    await session.flush()
    await session.refresh(tool)
    return to_tool_response(tool)


@router.put("/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: UUID,
    payload: ToolUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> ToolResponse:
    require_tenant_id(principal)
    tool = await get_writable_or_404(
        session,
        Tool,
        tool_id,
        principal,
        not_found_detail="tool not found",
        extra_filters=(Tool.is_builtin.is_(False),),
    )

    apply_partial_update(tool, payload, enum_fields=("implementation_type", "security_level"))

    await session.flush()
    await session.refresh(tool)
    return to_tool_response(tool)


@router.delete("/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tool(
    tool_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    tool = await get_writable_or_404(
        session,
        Tool,
        tool_id,
        principal,
        not_found_detail="tool not found",
        extra_filters=(Tool.is_builtin.is_(False),),
    )
    await soft_delete(session, tool)
