"""`/skills` endpoints -- tenant-scoped CRUD with built-in catalog read-through.

Built-ins (`is_builtin=true`) are owned by the platform tenant and
visible to every tenant via the `skills_builtin_read` SELECT policy
(migration 0005). Writes go through the tenant-isolation policy, so
tenant users can only mutate their own custom rows; PUT/DELETE on a
built-in returns 404 to avoid leaking that it's read-only.
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
from api_server.db.domain import Skill, Tool
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._integrity import flush_or_conflict
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.schemas.catalog import (
    SkillCreateRequest,
    SkillResponse,
    SkillUpdateRequest,
    to_skill_response,
)

router = APIRouter(prefix="/skills", tags=["skills"])


def _str_uuid_list(values: list[UUID]) -> list[str]:
    return [str(v) for v in values]


async def _validate_required_tools(session: AsyncSession, tool_ids: list[UUID]) -> None:
    """Reject ``required_tools`` that don't resolve to a LIVE tool visible to the
    caller's tenant (built-in/global or own) — L1 of the 2026-06 audit.

    Runs under the tenant RLS session, so the SELECT only returns tools the
    tenant may see; an unknown / soft-deleted / cross-tenant UUID simply won't
    come back and is reported as a 422 (rather than silently persisting a
    dangling reference the Capability Hub would then mis-render).
    """
    if not tool_ids:
        return
    found = set(
        (
            await session.execute(
                select(Tool.id).where(Tool.id.in_(tool_ids), Tool.deleted_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    missing = [str(t) for t in tool_ids if t not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"required_tools reference tools not visible to this tenant: {missing}",
        )


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    category: str | None = Query(default=None),
    is_builtin: bool | None = Query(default=None),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[SkillResponse]:
    stmt = select(Skill).where(Skill.deleted_at.is_(None))
    if category is not None:
        stmt = stmt.where(Skill.category == category)
    if is_builtin is not None:
        stmt = stmt.where(Skill.is_builtin.is_(is_builtin))
    stmt = stmt.order_by(Skill.created_at, Skill.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_skill_response(s) for s in result.scalars().all()]


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> SkillResponse:
    result = await session.execute(
        select(Skill).where(Skill.id == skill_id, Skill.deleted_at.is_(None))
    )
    skill = result.scalar_one_or_none()
    if skill is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="skill not found")
    return to_skill_response(skill)


@router.post("", response_model=SkillResponse, status_code=status.HTTP_201_CREATED)
async def create_skill(
    payload: SkillCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SkillResponse:
    tenant_id = require_tenant_id(principal)
    await _validate_required_tools(session, payload.required_tools)
    skill = Skill(
        tenant_id=tenant_id,
        name=payload.name,
        # `category` es un SkillCategory (StrEnum); a la columna String guardamos
        # su valor plano para que el driver no reciba el objeto enum.
        category=str(payload.category),
        description=payload.description,
        prompt_fragment=payload.prompt_fragment,
        required_tools=_str_uuid_list(payload.required_tools),
        # is_builtin is server-managed; never honored from the request.
        is_builtin=False,
    )
    session.add(skill)
    await flush_or_conflict(session, context="skill.create")
    await session.refresh(skill)
    return to_skill_response(skill)


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: UUID,
    payload: SkillUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> SkillResponse:
    require_tenant_id(principal)
    if payload.required_tools is not None:
        await _validate_required_tools(session, payload.required_tools)
    skill = await get_writable_or_404(
        session,
        Skill,
        skill_id,
        principal,
        not_found_detail="skill not found",
        extra_filters=(Skill.is_builtin.is_(False),),
    )

    apply_partial_update(
        skill,
        payload,
        transform={"required_tools": _str_uuid_list, "category": str},
    )

    await flush_or_conflict(session, context="skill.update")
    await session.refresh(skill)
    return to_skill_response(skill)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    skill = await get_writable_or_404(
        session,
        Skill,
        skill_id,
        principal,
        not_found_detail="skill not found",
        extra_filters=(Skill.is_builtin.is_(False),),
    )
    await soft_delete(session, skill)
