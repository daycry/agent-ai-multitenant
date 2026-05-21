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

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.db.domain import Skill
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
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


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    category: str | None = Query(default=None),
    is_builtin: bool | None = Query(default=None),
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[SkillResponse]:
    stmt = select(Skill).where(Skill.deleted_at.is_(None))
    if category is not None:
        stmt = stmt.where(Skill.category == category)
    if is_builtin is not None:
        stmt = stmt.where(Skill.is_builtin.is_(is_builtin))
    stmt = stmt.order_by(Skill.created_at)
    result = await session.execute(stmt)
    return [to_skill_response(s) for s in result.scalars().all()]


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: UUID,
    _: AuthPrincipal = Depends(get_principal),
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
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> SkillResponse:
    tenant_id = require_tenant_id(principal)
    skill = Skill(
        tenant_id=tenant_id,
        name=payload.name,
        category=payload.category,
        description=payload.description,
        prompt_fragment=payload.prompt_fragment,
        required_tools=_str_uuid_list(payload.required_tools),
        # is_builtin is server-managed; never honored from the request.
        is_builtin=False,
    )
    session.add(skill)
    await session.flush()
    await session.refresh(skill)
    return to_skill_response(skill)


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: UUID,
    payload: SkillUpdateRequest,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> SkillResponse:
    require_tenant_id(principal)
    skill = await get_writable_or_404(
        session,
        Skill,
        skill_id,
        principal,
        not_found_detail="skill not found",
        extra_filters=(Skill.is_builtin.is_(False),),
    )

    apply_partial_update(skill, payload, transform={"required_tools": _str_uuid_list})

    await session.flush()
    await session.refresh(skill)
    return to_skill_response(skill)


@router.delete("/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_skill(
    skill_id: UUID,
    principal: AuthPrincipal = Depends(get_principal),
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
