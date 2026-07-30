"""`/projects` endpoints -- tenant-scoped CRUD (task_01_07).

Compared to other entities, projects carry significantly more state
(team assignment, budget envelopes, repo config, MCP / RAG / approval
placeholders). All of it is plain CRUD here; the orchestration that
*uses* these fields arrives in Plans 02+. Built-in projects do not
exist -- a project is always created by a tenant.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
    schedule_after_commit,
)
from api_server.capabilities import (
    CapabilitiesResponse,
    CapabilityHacer,
    CapabilityRecordar,
    CapabilitySaber,
    kbs_for_project,
    memory_counts,
)
from api_server.celery_client import (
    enqueue_clone_project_repo,
    enqueue_compose_review_runtime,
    revoke_job_callback,
)
from api_server.db.domain import Project, ProjectStatus, Team
from api_server.db.execution_repo import cancel_tasks_and_executions
from api_server.db.models import Organization
from api_server.db.review_session_repo import list_active_preview_sessions
from api_server.git_integration import project_git_secret_path
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.preview_launch import build_preview_request
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._integrity import integrity_conflict
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.projects import (
    GitConfigResponse,
    GitConfigUpdateRequest,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    to_project_response,
)
from api_server.seeds import PLATFORM_TENANT_ID
from api_server.seeds.template_adoption import apply_template_kb_grants
from api_server.slug import slugify

router = APIRouter(prefix="/projects", tags=["projects"])

# P1-10: claves de `repository_config` que escribe la PLATAFORMA (no el
# cliente) — un PUT del cliente no puede pisarlas.
_REPOSITORY_CONFIG_PLATFORM_KEYS: tuple[str, ...] = ("last_git_sync", "review_image")

# P1-01: transiciones legales del estado del proyecto. `archived` es terminal
# salvo el unarchive del admin (todo update_project ya exige tenant_admin).
_PROJECT_TRANSITIONS: dict[str, frozenset[str]] = {
    ProjectStatus.ACTIVE.value: frozenset(
        {ProjectStatus.PAUSED.value, ProjectStatus.ARCHIVED.value}
    ),
    ProjectStatus.PAUSED.value: frozenset(
        {ProjectStatus.ACTIVE.value, ProjectStatus.ARCHIVED.value}
    ),
    ProjectStatus.ARCHIVED.value: frozenset({ProjectStatus.ACTIVE.value}),
}


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
) -> Project:
    """Resolve `template_id` to a usable project template or raise 404.

    The `projects_template_read` RLS policy (FOR SELECT USING
    is_template=true) is permissive: a tenant session can read *any*
    tenant's template, not just its own + the platform catalog. So we
    cannot rely on RLS alone to scope adoption — we explicitly require
    the template to belong either to the caller's tenant or to the
    platform tenant (the built-in catalog). A template owned by a
    *different* tenant surfaces as a clean 404 and grants nothing,
    preventing cross-tenant leakage of `default_kb_grants`.

    Returns the full template row — PROJ-01: la adopción es server-side y
    hereda de aquí toda la forma del proyecto.
    """
    result = await session.execute(
        select(Project).where(
            Project.id == template_id,
            Project.is_template.is_(True),
            Project.deleted_at.is_(None),
            Project.tenant_id.in_([tenant_id, PLATFORM_TENANT_ID]),
        )
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project template not found"
        )
    return template


# PROJ-01: campos de forma que la adopción hereda del template cuando el caller
# no los fija explícitamente (payload.model_fields_set). `team_id` se trata
# aparte (fork por defecto).
_TEMPLATE_INHERITED_FIELDS: tuple[str, ...] = (
    "mcp_servers",
    "worker_config",
    "repository_config",
    "human_approval_policy",
    "allowed_commands",
    "default_runtime_template",
    "allowed_domains",
)


def _resolve_template_adoption(
    payload: ProjectCreateRequest, template: Project | None
) -> tuple[dict[str, Any], UUID | None, bool]:
    """Forma efectiva de la adopción (PROJ-01): campos heredados del template
    que el caller no envió, equipo efectivo, y si se forkea el equipo.

    `fork_team` por defecto al adoptar plantilla: el proyecto recibe SU copia
    editable del equipo builtin (agentes project_local despachables). Un
    `fork_team: false` explícito referencia el equipo tal cual (linked)."""
    sent = payload.model_fields_set
    inherited: dict[str, Any] = {}
    effective_team_id = payload.team_id
    if template is not None:
        for field_name in _TEMPLATE_INHERITED_FIELDS:
            if field_name not in sent:
                value = getattr(template, field_name)
                if value is not None:
                    inherited[field_name] = deepcopy(value)
        if effective_team_id is None and "team_id" not in sent:
            effective_team_id = template.team_id
    fork_team = payload.fork_team if "fork_team" in sent else template is not None
    return inherited, effective_team_id, fork_team


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
# App-preview on-demand (ADR 0130) — levantar la app del proyecto (rama por
# defecto) durante 24h, reutilizando la maquinaria de review-runtime. Sin
# veredicto: es solo la app en vivo.
# ---------------------------------------------------------------------------
async def _load_project_or_404(session: AsyncSession, project_id: UUID) -> Project:
    row = (
        await session.execute(
            select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return row


def _preview_session_payload(row: Any) -> dict[str, Any]:
    """Signed app URL + metadata for a live preview session (ADR 0130)."""
    from api_server.routers.review import build_review_urls

    urls = build_review_urls(row.id, row.expires_at.timestamp())
    return {
        "session_id": str(row.id),
        "status": row.status,
        "app_url": urls["app_url"],
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "app_configured": bool((row.spec or {}).get("app_configured", True)),
    }


@router.post("/{project_id}/preview", status_code=status.HTTP_202_ACCEPTED)
async def launch_project_preview(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Launch an on-demand app-preview of the project's DEFAULT branch (ADR 0130).

    Reuses the review-runtime machinery (24h, no verdict). Idempotent: if a
    preview is already live for this project, returns it instead of spawning a
    second. 409 when the project pins no app-preview image."""
    tenant_id = require_tenant_id(principal)
    project = await _load_project_or_404(session, project_id)
    existing = await list_active_preview_sessions(session, project_id=project_id)
    if existing:
        return {"status": "running", **_preview_session_payload(existing[0])}
    org = await session.get(Organization, tenant_id)
    request = build_preview_request(tenant_id=tenant_id, project=project, org=org, plan=None)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El proyecto no tiene imagen de app-preview configurada. Configúra "
                "'repository_config.review_image' en Servicios/App-preview primero."
            ),
        )
    await enqueue_compose_review_runtime(request)
    return {"status": "provisioning"}


@router.get("/{project_id}/preview-session")
async def get_project_preview_session(
    project_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Latest live on-demand preview of a project + a freshly-signed app URL
    (ADR 0130). 404 while none is live (the UI polls this after launching)."""
    await _load_project_or_404(session, project_id)
    sessions = await list_active_preview_sessions(session, project_id=project_id)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no live preview for this project"
        )
    return _preview_session_payload(sessions[0])


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

    # PROJ-01: adopción SERVER-SIDE. El wizard era quien copiaba la forma de la
    # plantilla (equipo, allowlist, runtime, …); la API directa creaba proyectos
    # inertes. Ahora el servidor hereda del template todo campo que el caller no
    # fije explícitamente; el wizard pasa a ser un consumidor más.
    template: Project | None = None
    if payload.template_id is not None:
        template = await _verify_template_visible(session, payload.template_id, tenant_id)
    inherited, effective_team_id, fork_team = _resolve_template_adoption(payload, template)

    # P1-02: el slug identifica el bare repo del proyecto en disco — dos
    # proyectos vivos del mismo tenant no pueden compartirlo. En colisión se
    # añade -{id8} del proyecto nuevo; el índice único parcial (migración
    # 0114) es el backstop contra carreras.
    project_id = uuid4()
    slug = slugify(payload.name)
    collision = (
        await session.execute(
            select(Project.id).where(
                Project.tenant_id == tenant_id,
                Project.slug == slug,
                Project.deleted_at.is_(None),
            )
        )
    ).first()
    if collision is not None:
        slug = f"{slug}-{project_id.hex[:8]}"

    project = Project(
        id=project_id,
        tenant_id=tenant_id,
        name=payload.name,
        # prod-18 / ADR 0085: stable worktree slug, generated once at creation.
        slug=slug,
        description=payload.description,
        status=payload.status.value,
        team_id=effective_team_id,
        mcp_servers=payload.mcp_servers,
        rag_knowledge_bases=payload.rag_knowledge_bases,
        worker_config=payload.worker_config,
        repository_config=payload.repository_config,
        human_approval_policy=payload.human_approval_policy,
        allowed_commands=payload.allowed_commands,
        default_runtime_template=payload.default_runtime_template,
        allowed_domains=payload.allowed_domains,
        human_task_review_mode=payload.human_task_review_mode.value,
        budget_amount=payload.budget_amount,
        budget_currency=payload.budget_currency,
        budget_period=(payload.budget_period.value if payload.budget_period is not None else None),
        budget_period_start_day=payload.budget_period_start_day,
        budget_period_length_days=payload.budget_period_length_days,
        # paused_by_budget stays False on create -- it's flipped only by
        # the budget evaluator (Plan 11+).
    )
    # PROJ-01: aplicar la forma heredada de la plantilla (solo campos que el
    # caller no envió). Copias profundas ya hechas arriba.
    for field_name, value in inherited.items():
        setattr(project, field_name, value)
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise integrity_conflict(exc, context="project.create") from exc

    # Ola C / ADR 0068: fork opt-in del equipo. Si el wizard pide personalizar el
    # equipo para este proyecto, forkeamos el equipo referenciado (built-in de la
    # plantilla, normalmente) a una copia editable del tenant con agentes
    # `project_local`, y repuntamos `project.team_id` al fork. El equipo original
    # queda intacto. `fork_team=False` (default) referencia el equipo tal cual.
    if fork_team and project.team_id is not None:
        from api_server.db.domain import AgentScope
        from api_server.routers.teams import fork_team_into

        source_team = (
            await session.execute(
                select(Team).where(Team.id == project.team_id, Team.deleted_at.is_(None))
            )
        ).scalar_one_or_none()
        if source_team is not None:
            forked = await fork_team_into(
                session,
                source_team,
                tenant_id=tenant_id,
                scope=AgentScope.PROJECT_LOCAL.value,
                project_id=project.id,
                name=f"{project.name} — equipo",
                llm_config=None,
                granted_by=principal.user_id,
            )
            project.team_id = forked.id
            await session.flush()

    # Plan 06.13 task_06_13_03: adopt the template's KB grants. Runs after
    # the project is flushed (so the FK target exists) and is idempotent —
    # the helper resolves `default_kb_grants` slugs to built-in KB ids and
    # inserts kb_projects rows ON CONFLICT DO NOTHING.
    #
    # Plan 06.17 task_06_17_14: the wizard can opt out of the auto-grant via
    # `apply_template_kb_grants=False` — the template is still validated/
    # adopted (its team/config shape is inherited by the wizard front-end)
    # but no kb_projects rows are created. "Proyecto en blanco" (no
    # template_id) never reaches this branch.
    if payload.template_id is not None and payload.apply_template_kb_grants:
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

    # P1-01: máquina mínima de estados del proyecto. active<->paused->archived;
    # archived es terminal salvo el unarchive del admin (->active).
    old_status = project.status
    if payload.status is not None and payload.status.value != old_status:
        allowed = _PROJECT_TRANSITIONS.get(old_status, frozenset())
        if payload.status.value not in allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid_project_transition",
                    "from": old_status,
                    "to": payload.status.value,
                },
            )

    # P1-10: `repository_config` mezcla claves del CLIENTE (language,
    # framework, …) con claves de PLATAFORMA que escribe el sistema
    # (last_git_sync, review_image). Un PUT del cliente las pisaba — merge
    # server-side: las claves de plataforma presentes se preservan salvo que
    # el payload las traiga explícitamente.
    if (
        "repository_config" in payload.model_fields_set
        and payload.repository_config is not None
        and isinstance(project.repository_config, dict)
    ):
        for platform_key in _REPOSITORY_CONFIG_PLATFORM_KEYS:
            if platform_key in project.repository_config and (
                platform_key not in payload.repository_config
            ):
                payload.repository_config[platform_key] = project.repository_config[platform_key]

    apply_partial_update(
        project,
        payload,
        enum_fields=("status", "budget_period", "human_task_review_mode"),
        # `llm_config` (JSON `model_config`) → columna `model_config` (Ola A);
        # `chat_llm_config` (JSON `chat_model_config`) → columna `chat_model_config`.
        rename={"llm_config": "model_config", "chat_llm_config": "chat_model_config"},
    )

    # P1-01: archivar cancela el trabajo en vuelo (tareas + runs) — espejo de la
    # cascada del soft-delete, sin el soft-delete.
    if (
        old_status != ProjectStatus.ARCHIVED.value
        and project.status == ProjectStatus.ARCHIVED.value
    ):
        for execution in await cancel_tasks_and_executions(session, project_id=project.id):
            if execution.celery_task_id:
                schedule_after_commit(session, revoke_job_callback(execution.celery_task_id))

    await session.flush()
    await session.refresh(project)
    return to_project_response(project)


# ---------------------------------------------------------------------------
# PUT /projects/{id}/git — config git + credencial (ADR 0072)
# ---------------------------------------------------------------------------
@router.put("/{project_id}/git", response_model=GitConfigResponse)
async def set_project_git(
    project_id: UUID,
    payload: GitConfigUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> GitConfigResponse:
    """Fija el remoto + credencial (PAT/SSH) del proyecto y encola el clone.

    La config no-secreta va a ``projects.git_config``; el secreto (token/ssh_key)
    va a Vault (`projects/{id}/git`) y NUNCA se devuelve. Un update que solo
    cambia metadatos puede omitir la credencial si ya hay una guardada.
    """
    require_tenant_id(principal)
    project = await get_writable_or_404(
        session, Project, project_id, principal, not_found_detail="project not found"
    )

    path = project_git_secret_path(project_id)
    existing = vault.read_secret(path) if vault is not None else {}

    new_secret: dict[str, str] | None = None
    if payload.auth_mode == "pat" and payload.token:
        new_secret = {"username": payload.username or "", "token": payload.token}
    elif payload.auth_mode == "ssh" and payload.ssh_key:
        new_secret = {"ssh_key": payload.ssh_key}

    has_credential = bool(new_secret) or bool(existing)
    if payload.auth_mode in ("pat", "ssh") and not has_credential:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"auth_mode={payload.auth_mode!r} requiere credencial (token o ssh_key)",
        )

    project.git_config = payload.config_dict()
    # Políticas del flujo git del plan → worker_config.git_policies (preserva el resto).
    project.worker_config = {
        **(project.worker_config or {}),
        "git_policies": payload.git_policies(),
    }

    if new_secret is not None:
        if vault is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Vault no disponible para guardar la credencial git",
            )
        vault.write_secret(path, new_secret)
    elif payload.auth_mode == "none" and existing and vault is not None:
        vault.delete_secret(path)  # sin auth → no dejes la credencial colgada
        has_credential = False

    await session.flush()
    await enqueue_clone_project_repo(project_id)
    return GitConfigResponse(
        provider=payload.provider,
        remote_url=payload.remote_url,
        default_branch=payload.default_branch,
        auth_mode=payload.auth_mode,
        has_credential=has_credential,
        branch_push_mode=payload.branch_push_mode,
        plan_validation_mode=payload.plan_validation_mode,
        push_policy=payload.push_policy,
    )


# ---------------------------------------------------------------------------
# POST /projects/{id}/git/sync — re-sync manual del remoto (P5/T6, ADR 0072)
# ---------------------------------------------------------------------------
@router.post("/{project_id}/git/sync", status_code=status.HTTP_202_ACCEPTED)
async def sync_project_git(
    project_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, str]:
    """Re-sincroniza el bare del proyecto con su remoto (el botón «Sincronizar»).

    Encola ``workers.clone_project_repo`` (idempotente: ``ensure_repo`` + ``git fetch
    --prune origin``). P5/T6 (audit 2026-07-03): antes el bare solo se re-sincronizaba
    al RE-guardar la config git; no hay beat periódico ni webhook (el docstring de
    ``fetch_remote`` lo prometía en falso). El re-sync periódico + el webhook con
    verificación de firma quedan para el ADR 0098 (gated).
    """
    require_tenant_id(principal)
    project = await get_writable_or_404(
        session, Project, project_id, principal, not_found_detail="project not found"
    )
    if not project.git_config:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="el proyecto no tiene configuración git (remoto) que sincronizar",
        )
    enqueued = await enqueue_clone_project_repo(project_id)
    return {
        "project_id": str(project_id),
        "status": "enqueued" if enqueued else "enqueue_failed",
    }


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
    # prod-06 task_prod06_cancel_02: soft-deleting a project cascades — cancel its
    # non-terminal tasks and the running executions still in flight (the worker
    # kills the containers), then revoke the queued jobs after commit. The dispatch
    # hot path also skips deleted projects (budget_03), but in-flight work must stop.
    for execution in await cancel_tasks_and_executions(session, project_id=project.id):
        if execution.celery_task_id:
            schedule_after_commit(session, revoke_job_callback(execution.celery_task_id))


# ---------------------------------------------------------------------------
# Plan 06.17 task_06_17_08: GET /projects/{id}/capabilities
# ---------------------------------------------------------------------------
#
# El Hub de Capacidad por proyecto: SABER (KBs del stack vía `kb_projects`,
# nivel stack/plataforma) y RECORDAR (memoria `project_shared` del proyecto +
# `global`). HACER queda vacío a nivel de proyecto (las tools son del agente;
# la capacidad efectiva de tools la da `/agents/{id}/effective-tools` y, agregada,
# `/teams/{id}/capabilities`). Read-only, tenant-scoped: RLS oculta proyectos
# cross-tenant, así que un proyecto oculto/inexistente → 404.
@router.get("/{project_id}/capabilities", response_model=CapabilitiesResponse)
async def get_project_capabilities(
    project_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> CapabilitiesResponse:
    """Devuelve la capacidad efectiva del proyecto (SABER + RECORDAR).

    El proyecto no tiene persona ni tools propias (eso vive en sus agentes),
    así que ``ser`` es ``None`` y ``hacer`` no es restrictivo a este nivel.
    """
    project_q = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = project_q.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    saber = CapabilitySaber(knowledge_bases=await kbs_for_project(session, project_id=project_id))
    recordar = CapabilityRecordar(
        memory_scope=None,
        memory=await memory_counts(session, project_id=project_id),
    )

    return CapabilitiesResponse(
        entity_type="project",
        entity_id=project.id,
        saber=saber,
        recordar=recordar,
        ser=None,
        hacer=CapabilityHacer(unrestricted=True),
        warnings=[],
    )
