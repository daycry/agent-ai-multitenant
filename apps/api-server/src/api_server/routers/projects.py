"""`/projects` endpoints -- tenant-scoped CRUD (task_01_07).

Compared to other entities, projects carry significantly more state
(team assignment, budget envelopes, repo config, MCP / RAG / approval
placeholders). All of it is plain CRUD here; the orchestration that
*uses* these fields arrives in Plans 02+. Built-in projects do not
exist -- a project is always created by a tenant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Project, ProjectStatus, Team
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.schemas.projects import (
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    to_project_response,
)
from api_server.seeds import PLATFORM_TENANT_ID
from api_server.seeds.template_adoption import apply_template_kb_grants

router = APIRouter(prefix="/projects", tags=["projects"])


async def _verify_team_visible(session: AsyncSession, team_id: UUID) -> None:
    """RLS already filters cross-tenant teams; this lookup converts a
    silent miss into an explicit 404 rather than letting Postgres raise
    the FK error message when the tenant_id-scoped SELECT returns 0."""
    result = await session.execute(
        select(Team.id).where(Team.id == team_id, Team.deleted_at.is_(None))
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team not found")


async def _verify_template_visible(
    session: AsyncSession, template_id: UUID, tenant_id: UUID
) -> None:
    """Resolve `template_id` to a usable project template or raise 404.

    The `projects_template_read` RLS policy (FOR SELECT USING
    is_template=true) is permissive: a tenant session can read *any*
    tenant's template, not just its own + the platform catalog. So we
    cannot rely on RLS alone to scope adoption — we explicitly require
    the template to belong either to the caller's tenant or to the
    platform tenant (the built-in catalog). A template owned by a
    *different* tenant surfaces as a clean 404 and grants nothing,
    preventing cross-tenant leakage of `default_kb_grants`.
    """
    result = await session.execute(
        select(Project.id).where(
            Project.id == template_id,
            Project.is_template.is_(True),
            Project.deleted_at.is_(None),
            Project.tenant_id.in_([tenant_id, PLATFORM_TENANT_ID]),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project template not found"
        )


# ---------------------------------------------------------------------------
# GET /projects
# ---------------------------------------------------------------------------
@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    status_: ProjectStatus | None = Query(
        default=None,
        alias="status",
        description=(
            "Filter by project status. Validated against the ProjectStatus "
            "enum (422 on an unknown value), matching the POST/PUT contract."
        ),
    ),
    team_id: UUID | None = Query(default=None),
    include_templates: bool = Query(
        default=False,
        description=(
            "Include platform-owned project templates (is_template=true) "
            "in the response. Off by default so tenant operators only see "
            "their real projects."
        ),
    ),
    q: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description=(
            "Case-insensitive substring match on project name. Used by the "
            "admin-panel ProjectCombobox for server-side search — pairs with "
            "`limit` to bound the candidate list as the operator types."
        ),
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=500,
        description=(
            "Max projects returned. Use a small value (e.g. 20) for typeahead "
            "comboboxes; default 100 is enough for the listing page."
        ),
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ProjectResponse]:
    stmt = select(Project).where(Project.deleted_at.is_(None))
    if not include_templates:
        stmt = stmt.where(Project.is_template.is_(False))
    if status_ is not None:
        stmt = stmt.where(Project.status == status_)
    if team_id is not None:
        stmt = stmt.where(Project.team_id == team_id)
    if q is not None:
        stmt = stmt.where(Project.name.ilike(f"%{q}%"))
    stmt = stmt.order_by(Project.created_at).limit(limit)
    result = await session.execute(stmt)
    return [to_project_response(p) for p in result.scalars().all()]


# ---------------------------------------------------------------------------
# GET /projects/{id}
# ---------------------------------------------------------------------------
@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProjectResponse:
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return to_project_response(project)


# ---------------------------------------------------------------------------
# POST /projects
# ---------------------------------------------------------------------------
@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProjectResponse:
    tenant_id = require_tenant_id(principal)

    if payload.team_id is not None:
        await _verify_team_visible(session, payload.team_id)

    if payload.template_id is not None:
        await _verify_template_visible(session, payload.template_id, tenant_id)

    project = Project(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        status=payload.status.value,
        team_id=payload.team_id,
        mcp_servers=payload.mcp_servers,
        rag_knowledge_bases=payload.rag_knowledge_bases,
        worker_config=payload.worker_config,
        repository_config=payload.repository_config,
        human_approval_policy=payload.human_approval_policy,
        secrets_vault_id=payload.secrets_vault_id,
        allowed_commands=payload.allowed_commands,
        default_runtime_template=payload.default_runtime_template,
        human_task_review_mode=payload.human_task_review_mode.value,
        budget_amount=payload.budget_amount,
        budget_currency=payload.budget_currency,
        budget_period=(payload.budget_period.value if payload.budget_period is not None else None),
        budget_period_start_day=payload.budget_period_start_day,
        budget_period_length_days=payload.budget_period_length_days,
        # paused_by_budget stays False on create -- it's flipped only by
        # the budget evaluator (Plan 11+).
    )
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc.orig)) from exc

    # Plan 06.13 task_06_13_03: adopt the template's KB grants. Runs after
    # the project is flushed (so the FK target exists) and is idempotent —
    # the helper resolves `default_kb_grants` slugs to built-in KB ids and
    # inserts kb_projects rows ON CONFLICT DO NOTHING.
    if payload.template_id is not None:
        await apply_template_kb_grants(
            session,
            template_id=payload.template_id,
            new_project_id=project.id,
            tenant_id=tenant_id,
            granted_by=principal.user_id,
        )

    await session.refresh(project)
    return to_project_response(project)


# ---------------------------------------------------------------------------
# PUT /projects/{id}
# ---------------------------------------------------------------------------
@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    payload: ProjectUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> ProjectResponse:
    require_tenant_id(principal)
    project = await get_writable_or_404(
        session, Project, project_id, principal, not_found_detail="project not found"
    )

    if "team_id" in payload.model_fields_set and payload.team_id is not None:
        await _verify_team_visible(session, payload.team_id)

    apply_partial_update(
        project,
        payload,
        enum_fields=("status", "budget_period", "human_task_review_mode"),
    )

    await session.flush()
    await session.refresh(project)
    return to_project_response(project)


# ---------------------------------------------------------------------------
# DELETE /projects/{id}
# ---------------------------------------------------------------------------
@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    project = await get_writable_or_404(
        session, Project, project_id, principal, not_found_detail="project not found"
    )
    await soft_delete(session, project)
