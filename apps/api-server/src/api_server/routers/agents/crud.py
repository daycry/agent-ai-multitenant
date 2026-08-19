"""CRUD de `/agents` + el selector de proveedores.

**El orden de este módulo es carga útil, no estilo**: `GET /agents/provider-options`
se declara ANTES que `GET /agents/{agent_id}`. Las dos casan con la misma URL y
FastAPI resuelve por orden de registro, así que invertirlas hace desaparecer
`provider-options` en silencio (la sirve `get_agent` y responde 422 al parsear
"provider-options" como UUID). Por eso los dos endpoints viven en el MISMO módulo
y el paquete monta éste el primero. Lo fija
`tests/unit/test_agents_router_package.py`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.agent_persona import effective_prompt_text
from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.agent_prompt_version_repo import (
    raw_prompt_snapshot,
    record_initial_version,
    record_prompt_change,
)
from api_server.db.domain import Agent, AgentScope
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._integrity import flush_or_conflict, integrity_conflict
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers.agents.common import _teams_by_agent
from api_server.schemas.agents import (
    AgentCreateRequest,
    AgentProviderOptionsResponse,
    AgentResponse,
    AgentUpdateRequest,
    to_agent_response,
)

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# GET /agents -- list with optional filters
# ---------------------------------------------------------------------------
@router.get("", response_model=list[AgentResponse])
async def list_agents(
    scope: AgentScope | None = Query(default=None, description="Filter by scope"),
    project_id: UUID | None = Query(default=None, description="Filter by project_id"),
    role: str | None = Query(default=None, description="Filter by role"),
    agent_type: str | None = Query(default=None, description="Filter by agent_type (ai|human)"),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentResponse]:
    """List agents visible to the caller.

    Visibility (enforced by RLS):
      * Tenant's own `global_tenant_template` + `project_local` rows.
      * Every `global_builtin` row, regardless of tenant_id (added by
        migration 0004 via a SELECT-only policy).

    Paged via `limit`/`offset` (task_06_14_13) so a tenant with many
    agents never produces an unbounded response.
    """
    stmt = select(Agent).where(Agent.deleted_at.is_(None))
    if scope is not None:
        stmt = stmt.where(Agent.scope == scope.value)
    if project_id is not None:
        stmt = stmt.where(Agent.project_id == project_id)
    if role is not None:
        stmt = stmt.where(Agent.role == role)
    if agent_type is not None:
        stmt = stmt.where(Agent.agent_type == agent_type)
    # Deterministic order (created_at, then id as a tiebreaker) so
    # offset paging is stable even when many rows share a timestamp.
    stmt = stmt.order_by(Agent.created_at, Agent.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    agents = list(result.scalars().all())
    teams_by_agent = await _teams_by_agent(session, [a.id for a in agents])
    return [to_agent_response(a, teams_by_agent.get(a.id, [])) for a in agents]


# ---------------------------------------------------------------------------
# GET /agents/provider-options — proveedores ACTIVOS concretos (por nombre) + modelos
# ---------------------------------------------------------------------------
# Para el selector del «Modelo del chat» (Feature B): a diferencia de model-options
# (agrega por kind, el más nuevo activo), lista CADA fila activa para que el operador
# distinga p.ej. Ollama local vs cloud y fije un provider_id concreto SOLO para el
# chat. Tenant-accesible; sin secretos (la credencial vive en Vault). DEBE ir antes
# de GET /{agent_id}.
@router.get("/provider-options", response_model=AgentProviderOptionsResponse)
async def get_agent_provider_options(
    _: AuthPrincipal = Depends(require_tenant_member),
) -> AgentProviderOptionsResponse:
    from api_server.assistant.model_config import list_available_models_for_provider
    from api_server.db.llm_providers import (
        LLM_PROVIDER_KINDS,
        REASONING_OPTIONS_BY_KIND,
        list_active_llm_providers_by_kind,
    )
    from api_server.db.session import get_admin_sessionmaker
    from api_server.schemas.agents import ProviderOption

    providers: list[ProviderOption] = []
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        for kind in LLM_PROVIDER_KINDS:
            for row in await list_active_llm_providers_by_kind(session, kind):
                models = await list_available_models_for_provider(session, row)
                providers.append(
                    ProviderOption(
                        id=row.id,
                        kind=row.kind,
                        display_name=row.display_name,
                        slug=row.slug,
                        models=sorted(set(models)),
                        reasoning_options=list(REASONING_OPTIONS_BY_KIND.get(kind, ())),
                    )
                )
    return AgentProviderOptionsResponse(providers=providers)


# ---------------------------------------------------------------------------
# GET /agents/{id}
# ---------------------------------------------------------------------------
@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    result = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    teams_by_agent = await _teams_by_agent(session, [agent.id])
    return to_agent_response(agent, teams_by_agent.get(agent.id, []))


# ---------------------------------------------------------------------------
# POST /agents
# ---------------------------------------------------------------------------
@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    payload: AgentCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    tenant_id = require_tenant_id(principal)

    if payload.scope == AgentScope.GLOBAL_BUILTIN:
        # Built-ins are owned by the platform and only created by the
        # seed scripts (task_01_09). A tenant-API caller cannot inject
        # them even if RLS would let them: refuse explicitly with 403
        # so the rejection is auditable.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="global_builtin agents cannot be created through the tenant API",
        )

    # Default de memory_scope operator-configurable (Plan 06.17 task_06_17_04):
    # cuando el body no envía ``memory_scope``, se lee de ``memory.default_scope``
    # (platform_settings) en vez de hardcodear ``private``. Un valor explícito gana.
    if payload.memory_scope is not None:
        memory_scope = payload.memory_scope.value
    else:
        from api_server.db.platform_settings import get_default_memory_scope

        memory_scope = await get_default_memory_scope(session)

    # Default EXPLÍCITO de model_config operator-configurable (Plan 06.17
    # task_06_17_10 / ADR 0055): cuando el body no envía ``model_config`` (o lo
    # envía vacío), se rellena con el default seguro de ``model.default_config``
    # (platform_settings, anclado al catálogo cerrado del ADR 0021) en vez de
    # persistir ``{}``. Así NINGÚN agente nuevo nace con un spec vacío que
    # fallaría tarde en dispatch. Un ``model_config`` no vacío ya fue validado
    # contra el catálogo por el schema (422 fuera de catálogo) y gana sobre el
    # default.
    if payload.llm_config:
        model_config_value = payload.llm_config
    else:
        from api_server.db.platform_settings import get_default_model_config

        model_config_value = await get_default_model_config(session)

    agent = Agent(
        tenant_id=tenant_id,
        name=payload.name,
        description=payload.description,
        avatar_url=payload.avatar_url,
        agent_type=payload.agent_type.value,
        role=payload.role.value,
        system_prompt=payload.system_prompt,
        model_config=model_config_value,
        memory_scope=memory_scope,
        review_capability=payload.review_capability,
        max_concurrent_tasks=payload.max_concurrent_tasks,
        is_template=payload.is_template,
        scope=payload.scope.value,
        project_id=payload.project_id,
    )
    session.add(agent)
    await flush_or_conflict(session, context="agent.create")
    # `task_gov_02`: la `version 1` del historial, con su autor DE VERDAD. Los
    # agentes que ya existían cuando llegó la tabla arrancan su historial con una
    # fila de base de autor NULL (nadie lo apuntó); los que nacen a partir de aquí
    # no tienen por qué heredar esa laguna.
    await record_initial_version(
        session, tenant_id=tenant_id, agent=agent, changed_by=principal.user_id
    )
    await session.refresh(agent)
    return to_agent_response(agent)


# ---------------------------------------------------------------------------
# PUT /agents/{id} -- partial update
# ---------------------------------------------------------------------------
@router.put("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: UUID,
    payload: AgentUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentResponse:
    tenant_id = require_tenant_id(principal)
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )

    # `task_gov_02`: el estado del prompt ANTES de escribir. Se captura aquí, con
    # el objeto todavía intacto, porque `apply_partial_update` muta en sitio y
    # después ya no hay de dónde sacarlo. Los valores CRUDOS deciden si hubo
    # cambio (ver el docstring de `agent_prompt_version_repo`); el efectivo se
    # lleva resuelto para no volver a resolverlo al sellar la fila de base.
    prompt_before = raw_prompt_snapshot(agent)
    effective_before = effective_prompt_text(agent)

    apply_partial_update(
        agent,
        payload,
        enum_fields=("agent_type", "role", "memory_scope"),
        rename={"llm_config": "model_config"},
    )

    await flush_or_conflict(session, context="agent.update")
    # Sólo cuando el prompt cambió DE VERDAD. Un `PUT` que sube
    # `max_concurrent_tasks`, o que reenvía el mismo prompt, no abre una versión:
    # un historial con filas idénticas no se lee, y el diff de esas filas sale
    # vacío, que es la forma más rápida de que nadie vuelva a abrir la pantalla.
    if raw_prompt_snapshot(agent) != prompt_before:
        try:
            await record_prompt_change(
                session,
                tenant_id=tenant_id,
                agent=agent,
                before=prompt_before,
                before_effective_prompt=effective_before,
                changed_by=principal.user_id,
            )
        except IntegrityError as exc:
            # Dos `PUT` simultáneos calculan el mismo `version` y el UNIQUE deja
            # fuera al segundo. Es una carrera entre peticiones VÁLIDAS, así que
            # el perdedor se lleva un 409 con el código estable
            # `concurrent_prompt_edit` — no un 500 con el mensaje crudo de
            # PostgreSQL, que nombra la constraint y filtra el `tenant_id`.
            await session.rollback()
            raise integrity_conflict(exc, context="agent.prompt_version") from exc
    await session.refresh(agent)
    return to_agent_response(agent)


# ---------------------------------------------------------------------------
# DELETE /agents/{id} -- soft delete
# ---------------------------------------------------------------------------
@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )
    await soft_delete(session, agent)
