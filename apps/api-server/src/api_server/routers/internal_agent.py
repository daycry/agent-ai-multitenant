"""Router for `/internal/agent/*` (Plan 04.5 task_04_5_01 / 03).

The family of endpoints the agent-runtime sandbox calls back into
during a run. All routes share the dedicated auth dependency
:func:`get_agent_principal` that validates the sandbox-scoped JWT
minted by the worker; tenant-scoped routes additionally use
:func:`get_agent_tenant_session` so RLS pins the active tenant.

Endpoints:

  - ``GET  /_health``         smoke probe (task_04_5_01).
  - ``POST /memory-recall``   hybrid BM25 + vector recall (task_04_5_03).
  - ``POST /memory-store``    persist one MemoryEntry (task_04_5_03).
  - ``POST /rag-search``      project-scoped RAG over KB chunks
                              (task_04_5_04).
  - ``POST /document-convert`` structured chunks of an existing
                              Document (task_04_5_05).
  - ``POST /promote-to-kb``   copy a Document into a different KB
                              (task_04_5_05).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.internal_agent import (
    AgentPrincipal,
    get_agent_principal,
    get_agent_tenant_session,
)
from api_server.db.domain import Agent, MemoryScope, Project, Task, Team
from api_server.db.knowledge import Chunk, Document, KnowledgeBase, KnowledgeBaseProject
from api_server.db.platform_settings import (
    get_default_memory_scope,
    get_global_agent_uses_task_project,
    get_rag_reranker_enabled,
)
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.memorizer import MemoryCandidate, persist_memory_candidates
from api_server.memorizer.recall import recall
from api_server.rag.reranker import BGEReranker, Reranker
from api_server.rag.tool import rag_search
from api_server.routers.docs_viewer import get_query_embedder

router = APIRouter(prefix="/internal/agent", tags=["internal-agent"])

_BASE_CONFIG = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

_SCOPE_LITERAL = Literal["private", "team_shared", "project_shared", "global"]
_TYPE_LITERAL = Literal["episodic", "semantic"]


# ---------------------------------------------------------------------------
# /_health
# ---------------------------------------------------------------------------
@router.get("/_health")
async def health(
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> dict[str, str]:
    """Smoke endpoint that proves the agent-token auth dependency works.

    Returns the principal's `agent_id` and `tenant_id` so tests can
    assert the token was parsed correctly. No DB writes, no side
    effects — safe to keep enabled in all environments.
    """
    return {
        "status": "ok",
        "agent_id": str(principal.agent_id),
        "tenant_id": str(principal.tenant_id),
    }


# ---------------------------------------------------------------------------
# /run-stack  (ADR 0093 — stack_exec)
# ---------------------------------------------------------------------------
class RunStackRequest(BaseModel):
    model_config = _BASE_CONFIG

    task_id: UUID
    command: str = Field(min_length=1, max_length=4000)
    timeout_s: int = Field(default=600, ge=1, le=3600)
    # ADR 0093 (2026-07-24): optional working directory relative to the worktree
    # root (e.g. "ci4build") so a project scaffolded under a subdir runs its
    # toolchain there. Validated worker-side (no absolute path / no `..`).
    cwd: str | None = Field(default=None, max_length=512)


class RunStackResponse(BaseModel):
    model_config = _BASE_CONFIG

    exit_code: int
    logs: str
    timed_out: bool


@router.post("/run-stack", response_model=RunStackResponse)
async def run_stack(
    payload: RunStackRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> RunStackResponse:
    """Run a stack command (``composer install`` / ``vendor/bin/phpunit`` /
    ``php spark``) in the project's runtime template via the worker (ADR 0093).

    The agent-runtime cannot launch containers (no Docker socket — principle 2),
    so it asks the worker, which has Docker and already knows how to launch the
    stack runtime over the task's worktree. The worker gates the command against
    the project's ``allowed_commands`` (deny-by-default) before running it. The
    tenant is pinned by the minted agent token; the ``task_id`` comes from the
    runtime's own task spec.
    """
    from api_server.celery_client import run_stack_command_and_wait

    try:
        result = await run_stack_command_and_wait(
            tenant_id=principal.tenant_id,
            task_id=payload.task_id,
            command=payload.command,
            timeout_s=payload.timeout_s,
            cwd=payload.cwd,
        )
    except Exception as exc:  # broker / result-backend failure or worker timeout
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"stack command did not complete: {exc}",
        ) from exc
    return RunStackResponse(
        exit_code=int(result.get("exit_code", -1)),
        logs=str(result.get("logs", "")),
        timed_out=bool(result.get("timed_out", False)),
    )


# ---------------------------------------------------------------------------
# /memory-recall
# ---------------------------------------------------------------------------
class MemoryRecallRequest(BaseModel):
    model_config = _BASE_CONFIG

    query: str = Field(min_length=1, max_length=2000)
    scopes: list[_SCOPE_LITERAL] = Field(default_factory=list, max_length=4)
    limit: int = Field(default=5, ge=1, le=20)


class MemoryRecallHitOut(BaseModel):
    model_config = _BASE_CONFIG

    memory_id: UUID
    content: str
    scope: str
    type: str
    bm25_rank: int | None
    vector_rank: int | None
    rrf_score: float


class MemoryRecallResponse(BaseModel):
    model_config = _BASE_CONFIG

    hits: list[MemoryRecallHitOut]


class McpOAuthTokenRequest(BaseModel):
    """El sandbox pide el token de UN servidor, POR NOMBRE."""

    model_config = _BASE_CONFIG

    server: str = Field(min_length=1, max_length=200)
    # `True` solo tras un 401 real del servidor remoto: fuerza el canje del
    # refresh token. Lo dispara el 401, no un reloj (ver `issue_access_token`).
    refresh: bool = False


class McpOAuthTokenResponse(BaseModel):
    model_config = _BASE_CONFIG

    access_token: str
    token_type: str


async def _resolve_run_project(
    session: AsyncSession, *, agent: Agent, principal: AgentPrincipal
) -> Project | None:
    """El proyecto DEL RUN, resuelto siempre en el servidor.

    Distinto a propósito de :func:`_resolve_effective_project`, que sirve para
    LEER memoria/RAG y por eso tiene la regla —con flag— del agente global que
    hereda el proyecto de la tarea. Una credencial no se hereda: el proyecto es
    el de la tarea que este run ejecuta, y si el token no porta tarea, el del
    propio agente. Nunca el que diga el cliente.
    """
    if principal.task_id is not None:
        task = (
            await session.execute(
                select(Task).where(
                    Task.id == principal.task_id,
                    # Defensa en profundidad sobre RLS: una tarea de otro tenant
                    # no resuelve proyecto ninguno.
                    Task.tenant_id == principal.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if task is not None and task.project_id is not None:
            return (
                await session.execute(select(Project).where(Project.id == task.project_id))
            ).scalar_one_or_none()
    if agent.project_id is not None:
        return (
            await session.execute(select(Project).where(Project.id == agent.project_id))
        ).scalar_one_or_none()
    return None


@router.post("/mcp-oauth-token", response_model=McpOAuthTokenResponse)
async def mcp_oauth_token(
    payload: McpOAuthTokenRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
) -> McpOAuthTokenResponse:
    """El access token vigente de un servidor MCP con OAuth (ADR 0131, opción C).

    El sandbox no habla con Vault. Pide el token por aquí y la plataforma hace lo
    privilegiado: leer Vault, refrescar si el servidor remoto devolvió 401 y
    persistir el token nuevo. Al contenedor solo baja un access token acotado a
    un servidor y efímero — ni la llave del almacén, ni el refresh token.

    La frontera de tenant NO depende de lo que mande el cliente. El sandbox envía
    un NOMBRE de servidor; la ruta de Vault se construye aquí con el tenant del
    token y el proyecto del run resuelto en servidor. Aceptar la ruta ya montada
    (el ``oauth_ref`` que el runtime recibe) habría sido convertirla en una vía
    para leer credenciales de otro proyecto — o de otro tenant— con solo cambiar
    una cadena. Y el nombre se valida contra los ``mcp_servers`` DE ESE proyecto:
    un servidor que el proyecto no declara no tiene token que dar.
    """
    from shared_mcp.catalog import uses_oauth

    from api_server.mcp_oauth_flow import McpOAuthError, find_server_url, issue_access_token
    from api_server.routers.mcp import get_vault_resolver

    agent, _ = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)
    project = await _resolve_run_project(session, agent=agent, principal=principal)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="este run no tiene proyecto: no hay credenciales MCP que resolver",
        )

    servers = [s for s in (project.mcp_servers or []) if isinstance(s, dict)]
    server_url = find_server_url(servers, payload.server)
    if server_url is None or not uses_oauth(server_url):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"el proyecto no declara un servidor MCP con OAuth llamado {payload.server!r}",
        )

    resolver = get_vault_resolver()
    if resolver is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="el api-server no tiene Vault configurado: no puede resolver credenciales MCP",
        )

    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            grant = await issue_access_token(
                tenant_id=str(principal.tenant_id),
                project_id=str(project.id),
                server_name=payload.server,
                server_url=server_url,
                resolver=resolver,
                http_client=http_client,
                refresh=payload.refresh,
            )
    except McpOAuthError as exc:
        # 409: el estado guardado no sirve y lo arregla un humano reconectando —
        # no es un fallo transitorio que reintentar.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return McpOAuthTokenResponse(access_token=grant.access_token, token_type=grant.token_type)


async def _resolve_agent_context(
    session: AsyncSession, agent_id: UUID, tenant_id: UUID
) -> tuple[Agent, Project | None]:
    """Load the agent + (when scope=project_local) its project.

    Both ``team_shared`` and ``project_shared`` recall/store need the
    project to derive ``team_id`` / ``project_id`` for the owner
    filter. The agent row is also the gate that decides which scopes
    this agent is allowed to read or write.
    """
    result = await session.execute(
        select(Agent).where(
            Agent.id == agent_id,
            Agent.tenant_id == tenant_id,
            Agent.deleted_at.is_(None),
        )
    )
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent not found in tenant",
        )
    project: Project | None = None
    if agent.project_id is not None:
        project = (
            await session.execute(select(Project).where(Project.id == agent.project_id))
        ).scalar_one_or_none()
    return agent, project


async def _resolve_effective_project(
    session: AsyncSession,
    *,
    agent: Agent,
    principal: AgentPrincipal,
) -> Project | None:
    """Resuelve el proyecto EFECTIVO de LECTURA para RAG/memoria (ADR 0054).

    Reglas (Plan 06.17 task_06_17_13):

      * un agente ligado a un proyecto (``agent.project_id`` no nulo) usa SU
        proyecto — comportamiento histórico, sin cambio;
      * un agente GLOBAL (``project_id`` nulo) usa el proyecto de la TAREA en
        curso (``task.project_id``) SI el flag operator-configurable está ON y
        el token porta un ``task_id``. El proyecto se resuelve server-side y es
        **estrictamente tenant-safe**: la tarea se carga con un predicado
        explícito ``Task.tenant_id == principal.tenant_id`` (bajo RLS además),
        de modo que un ``task_id`` de OTRO tenant nunca resuelve un proyecto
        (devuelve ``None``). El alcance es ese ÚNICO proyecto de la tarea —
        jamás un conjunto, jamás otro tenant.

    Devuelve la fila ``Project`` efectiva, o ``None`` cuando no hay contexto de
    proyecto (agente global sin tarea, flag OFF, o tarea ajena al tenant). El
    proyecto NUNCA se toma del cliente: solo de ``agent.project_id`` o de la
    tarea autenticada.
    """
    if agent.project_id is not None:
        return (
            await session.execute(select(Project).where(Project.id == agent.project_id))
        ).scalar_one_or_none()

    # Agente global: solo con el flag ON y un task_id en el token.
    if principal.task_id is None:
        return None
    if not await get_global_agent_uses_task_project(session):
        return None

    task = (
        await session.execute(
            select(Task).where(
                Task.id == principal.task_id,
                # Defensa en profundidad sobre RLS: la tarea DEBE ser del tenant
                # del token; un task_id cross-tenant no resuelve nada.
                Task.tenant_id == principal.tenant_id,
            )
        )
    ).scalar_one_or_none()
    if task is None or task.project_id is None:
        return None
    return (
        await session.execute(select(Project).where(Project.id == task.project_id))
    ).scalar_one_or_none()


@router.post("/memory-recall", response_model=MemoryRecallResponse)
async def memory_recall(
    payload: MemoryRecallRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
    embedder: Embedder | None = Depends(get_query_embedder),
) -> MemoryRecallResponse:
    """Hybrid BM25 + vector recall over memory_entries.

    Scope resolution:
      - the agent picks which scopes to search via ``payload.scopes``;
      - the server resolves the owner pointers (team_id / project_id)
        from the agent's project so an agent can never look at another
        team's or project's memories;
      - ``private`` is included for forward compatibility but yields
        nothing for an AI agent (no user_id is set on AI principals).

    Plan 06.17 task_06_17_03: el query-embedder se reutiliza de
    ``docs_viewer.get_query_embedder`` (misma fuente, no se duplica) para que
    ``query_embedding`` deje de ser ``None`` y el path vectorial+RRF participe
    (``recall._vector_candidates`` devuelve candidatos). El embed es best-effort:
    si el embedder no está disponible (Ollama caído ⇒ ``EmbeddingError``) el
    recall cae a BM25 sin romper.
    """
    agent, _project = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)
    # Proyecto EFECTIVO de lectura (ADR 0054): para un agente ligado a proyecto
    # es el suyo; para un agente global es el de la tarea en curso (flag ON +
    # task_id en el token), resuelto server-side y tenant-safe. La memoria
    # ``project_shared`` (y ``team_shared`` vía ``project.team_id``) usa este
    # proyecto efectivo, de modo que read = write = ``task.project_id`` para el
    # agente global — sin la asimetría histórica.
    project = await _resolve_effective_project(session, agent=agent, principal=principal)
    # D2 (revisión memorias 2026-07-03): unos scopes explícitos se RECORTAN a la
    # escalera del agente — antes la saltaban (un agente project_shared podía
    # leer team_shared/global de su equipo/tenant). Intersección vacía → la
    # escalera completa, para que una petición mal formada no esterilice el run.
    ladder = _default_readable_scopes(agent.memory_scope)
    scopes = [s for s in payload.scopes if s in ladder] or ladder
    team_id = project.team_id if project is not None else None
    project_id = project.id if project is not None else None

    query_embedding = await _embed_query(embedder, payload.query)

    hits = await recall(
        session,
        query=payload.query,
        tenant_id=principal.tenant_id,
        scopes=scopes,
        user_id=None,  # AI agents have no user attribution
        team_id=team_id,
        project_id=project_id,
        query_embedding=query_embedding,
        limit=payload.limit,
    )
    return MemoryRecallResponse(
        hits=[
            MemoryRecallHitOut(
                memory_id=h.memory_id,
                content=h.content,
                scope=h.scope,
                type=h.type,
                bm25_rank=h.bm25_rank,
                vector_rank=h.vector_rank,
                rrf_score=h.rrf_score,
            )
            for h in hits
        ]
    )


# ---------------------------------------------------------------------------
# /memory-store
# ---------------------------------------------------------------------------
class MemoryStoreRequest(BaseModel):
    """Body of POST /internal/agent/memory-store.

    The agent provides content + type + optional tags. The scope, when
    omitted, defaults to the agent's own ``memory_scope``. When given,
    it must equal the agent's scope — an agent may not store into a
    scope wider than its own (defence in depth on top of the schema
    CHECK)."""

    model_config = _BASE_CONFIG

    content: str = Field(min_length=1, max_length=2000)
    type: _TYPE_LITERAL = "semantic"
    scope: _SCOPE_LITERAL | None = None
    tags: list[str] = Field(default_factory=list, max_length=20)


class MemoryStoreResponse(BaseModel):
    model_config = _BASE_CONFIG

    memory_id: UUID
    scope: str
    type: str


@router.post(
    "/memory-store",
    response_model=MemoryStoreResponse,
    status_code=status.HTTP_201_CREATED,
)
async def memory_store(
    payload: MemoryStoreRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
    embedder: Embedder | None = Depends(get_query_embedder),
) -> MemoryStoreResponse:
    """Persist a single memory written by the agent.

    The agent's ``memory_scope`` decides where the row lands. Owner
    pointers (team_id / project_id) are resolved from the agent's
    project, never from the request body — the agent cannot escape
    its own tenant + team + project boundary.

    Plan 06.17 task_06_17_03: el contenido se embebe EN EL MOMENTO DE CREAR
    (best-effort, reutilizando ``get_query_embedder``) para que el recall
    vectorial y "similares" funcionen sin esperar al back-fill. Si el embed
    falla (Ollama caído), la fila nace con ``embedding=NULL`` y el worker de
    back-fill la rellena después — el store no se bloquea.
    """
    agent, _ = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)
    # Resolve the EFFECTIVE project (ADR 0054 / M2): a global agent (project_id
    # NULL) operating on a task writes project_shared into the TASK's project,
    # exactly as `memory_recall` reads it — closing the store/recall asymmetry.
    # For a project-bound agent this is just its own project (no change).
    project = await _resolve_effective_project(session, agent=agent, principal=principal)

    # `task_cv_31`: el scope se calcula como en el memorizer (política del
    # equipo > agente > default de plataforma, y enrutado por tipo). El cliente
    # puede nombrarlo, pero sólo si coincide: no hay forma de ensancharlo.
    team_scope: str | None = None
    if project is not None and project.team_id is not None:
        team = await session.get(Team, project.team_id)
        team_scope = team.memory_scope if team is not None else None
    platform_default = await get_default_memory_scope(session)
    routed = _store_scope_for(
        agent_scope=agent.memory_scope,
        team_scope=team_scope,
        platform_default=platform_default,
        mem_type=payload.type,
    )
    scope = payload.scope or routed
    if scope not in {s.value for s in MemoryScope}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unsupported memory scope: {scope!r}",
        )
    if scope != routed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"agent memory_scope is {agent.memory_scope!r} and this {payload.type!r} memory "
                f"routes to {routed!r}; cannot store memories in scope {scope!r}"
            ),
        )

    owner = _resolve_store_owner(scope=scope, project=project)
    if owner is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"scope={scope!r} requires an owner pointer that the agent "
                "cannot provide (e.g. team_shared on a project without a "
                "team_id, or private for an AI agent)"
            ),
        )

    candidate = MemoryCandidate(
        content=payload.content,
        type=payload.type,
        tags=tuple(t.strip() for t in payload.tags if t.strip()),
    )
    rows = await persist_memory_candidates(
        session,
        [candidate],
        tenant_id=principal.tenant_id,
        scope=scope,
        agent_id=agent.id,
        source_execution_id=None,
        extra_metadata={"source": "agent_runtime"},
        embedder=embedder,
        **owner,
    )
    await session.flush()
    row = rows[0]
    await session.refresh(row)
    return MemoryStoreResponse(memory_id=row.id, scope=row.scope, type=row.type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _embed_query(embedder: Embedder | None, query: str) -> list[float] | None:
    """Embebe la query del recall (best-effort).

    Devuelve el vector, o ``None`` cuando no hay embedder, la query está vacía
    o el embed falla (Ollama caído). En ese caso el recall cae a BM25 — nunca
    rompe (Plan 06.17 task_06_17_03)."""
    if embedder is None or not query.strip():
        return None
    try:
        vectors = await embedder.embed([query])
    except EmbeddingError:
        return None
    return list(vectors[0]) if vectors else None


_SHARED_READ_SCOPES: tuple[str, ...] = ("team_shared", "project_shared", "global")


def _default_readable_scopes(agent_scope: str) -> list[str]:
    """The scope set an agent reads when it doesn't specify one.

    `task_cv_30` (auditoría 2026-09-01, E-01; ADR 0071): un agente de IA lee
    TODO scope compartido con puntero — `global`, el `project_shared` de su
    proyecto efectivo y el `team_shared` del equipo de ese proyecto. La versión
    anterior aplicaba una escalera (`team_shared` más estrecho que
    `project_shared`) sobre el scope de ESCRITURA del agente, así que un agente
    `project_shared` nunca leía la memoria de su equipo y uno `global` sólo leía
    `global`. Los punteros (`team_id`, `project_id`) los resuelve el endpoint
    desde el agente, nunca el cliente, así que ensanchar la lista no ensancha
    lo que se ve: `recall` filtra `team_shared` por el equipo del proyecto y
    `project_shared` por el proyecto.

    `private` (un agente humano) conserva sus filas propias delante; un scope
    fuera del catálogo canónico sigue leyendo sólo `global`. Widening reads
    never widens writes (writes go through :func:`_store_scope_for`).
    """
    if agent_scope == MemoryScope.PRIVATE.value:
        return ["private", *_SHARED_READ_SCOPES]
    if agent_scope not in {s.value for s in MemoryScope}:
        return ["global"]  # an agent opted out of canonical scopes: global only
    return list(_SHARED_READ_SCOPES)


def _store_scope_for(
    *,
    agent_scope: str | None,
    team_scope: str | None,
    platform_default: str,
    mem_type: str | None,
) -> str:
    """El scope donde `memory_store` persiste (`task_cv_31`, E-02): el MISMO
    cálculo que el memorizer — la política del equipo manda sobre la del agente
    y sobre el default de plataforma (`resolve_effective_memory_scope`), y el
    tipo enruta: `semantic` al scope efectivo, `episodic` acotado a
    `project_shared` (`route_scope_for_type`). Antes la tool escribía con
    `agent.memory_scope` crudo y el memorizer con esto: dos respuestas para la
    misma pregunta."""
    from api_server.memorizer.policy import resolve_effective_memory_scope, route_scope_for_type

    effective = resolve_effective_memory_scope(team_scope, agent_scope, platform_default)
    return route_scope_for_type(effective, mem_type)


# ---------------------------------------------------------------------------
# /rag-search
# ---------------------------------------------------------------------------
class RagSearchRequest(BaseModel):
    """Body of POST /internal/agent/rag-search.

    The agent supplies the natural-language query; the server pins
    ``project_id`` from the agent itself so an agent can never search
    a project's KBs it doesn't belong to.
    """

    model_config = _BASE_CONFIG

    query: str = Field(min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)
    recall_k: int = Field(default=20, ge=1, le=100)


class RagSearchHitOut(BaseModel):
    model_config = _BASE_CONFIG

    chunk_id: UUID
    document_id: UUID
    kb_id: UUID
    content: str
    ordinal: int
    bbox: dict[str, Any] | None
    bm25_rank: int | None
    vector_rank: int | None
    rrf_score: float
    rerank_score: float | None


class RagSearchResponse(BaseModel):
    model_config = _BASE_CONFIG

    hits: list[RagSearchHitOut]


async def get_rag_reranker(
    session: AsyncSession = Depends(get_agent_tenant_session),
) -> AsyncIterator[Reranker | None]:
    """Construye el reranker del rag-search según el flag operator-configurable
    (Plan 06.17 task_06_17_02).

    Lee ``rag.reranker_enabled`` de ``platform_settings`` (default OFF). Cuando
    está OFF devuelve ``None`` → ``rag_search`` conserva el orden RRF y no anota
    ``rerank_score`` (honestidad: no parece reranqueado si no lo está). Cuando
    está ON construye el :class:`BGEReranker` real; su import pesado (torch +
    transformers) es ``lazy``, así que un despliegue con el flag en OFF nunca
    paga el coste. Los tests sobreescriben esta dependencia para inyectar un
    reranker determinista sin tocar el flag.
    """
    if not await get_rag_reranker_enabled(session):
        yield None
        return
    reranker = BGEReranker()
    try:
        yield reranker
    finally:
        await reranker.aclose()


@router.post("/rag-search", response_model=RagSearchResponse)
async def rag_search_endpoint(
    payload: RagSearchRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
    embedder: Embedder | None = Depends(get_query_embedder),
    reranker: Reranker | None = Depends(get_rag_reranker),
) -> RagSearchResponse:
    """Project-scoped RAG over KB chunks.

    The endpoint reuses the Plan 04 `rag_search` engine
    (:func:`api_server.rag.tool.rag_search`) — hybrid BM25 + vector
    recall + reranker. The embedder + reranker live in api-server
    (not in the sandbox) so the model weights are never shipped into
    untrusted containers.

    Plan 06.17 task_06_17_02: el query-embedder se reutiliza de
    ``docs_viewer.get_query_embedder`` (misma fuente, no se duplica) para que
    ``query_embedding`` deje de ser ``None`` y el path vectorial+RRF participe.
    El reranker se activa por flag operator-configurable (``rag.reranker_enabled``,
    default OFF). Si el embedder no está disponible (Ollama caído ⇒ el embed
    lanza ``EmbeddingError`` que ``rag_search`` captura), el recall cae a BM25
    sin romper.

    Returns ``hits=[]`` (200) when the agent isn't bound to a project;
    a global/builtin agent has nothing to search and ``[]`` is the
    informative response. We could 400 instead, but returning empty
    keeps the tool contract uniform — the agent always sees a hits list.
    """
    agent, _project = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)
    # Proyecto EFECTIVO de búsqueda (ADR 0054): el del agente si está ligado a
    # uno; el de la TAREA en curso si el agente es global (flag ON + task_id en
    # el token), resuelto server-side y tenant-safe. Cuando no hay proyecto
    # efectivo (agente global sin tarea, flag OFF, o tarea ajena al tenant) se
    # conserva la respuesta informativa ``hits=[]`` (un agente sin contexto de
    # proyecto no tiene KBs que buscar).
    project = await _resolve_effective_project(session, agent=agent, principal=principal)
    if project is None:
        return RagSearchResponse(hits=[])

    # Plan 06.9: KBs visibles = union de KBs del proyecto y KBs del
    # agente template. Pasamos `agent_id` para que el resolver una
    # las dos fuentes en el visibility filter.
    hits = await rag_search(
        session,
        query=payload.query,
        tenant_id=principal.tenant_id,
        project_id=project.id,
        agent_id=principal.agent_id,
        limit=payload.limit,
        recall_k=payload.recall_k,
        embedder=embedder,
        reranker=reranker,
    )
    return RagSearchResponse(
        hits=[
            RagSearchHitOut(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                kb_id=h.kb_id,
                content=h.content,
                ordinal=h.ordinal,
                bbox=h.bbox,
                bm25_rank=h.bm25_rank,
                vector_rank=h.vector_rank,
                rrf_score=h.rrf_score,
                rerank_score=h.rerank_score,
            )
            for h in hits
        ]
    )


# ---------------------------------------------------------------------------
# /document-convert
# ---------------------------------------------------------------------------
class DocumentConvertRequest(BaseModel):
    """Body of POST /internal/agent/document-convert.

    The agent passes the ``document_id`` of a Document it already
    knows about (e.g. discovered via rag_search). The endpoint
    returns the structured chunks the ingestion pipeline produced.

    Unlike the full Docling re-parse this is a fast DB read — chunks
    are already persisted. A re-parse-from-MinIO mode arrives when
    chat-file-upload lands in Plan 07.
    """

    model_config = _BASE_CONFIG

    document_id: UUID


class DocumentChunkOut(BaseModel):
    model_config = _BASE_CONFIG

    chunk_id: UUID
    ordinal: int
    content: str
    bbox: dict[str, Any] | None


class DocumentConvertResponse(BaseModel):
    model_config = _BASE_CONFIG

    document_id: UUID
    kb_id: UUID
    title: str
    source_filename: str
    source_mime_type: str
    page_count: int
    chunks: list[DocumentChunkOut]


async def _assert_doc_visible_to_agent(
    session: AsyncSession,
    *,
    document: Document,
    agent: Agent,
) -> None:
    """The document's KB must be granted to the agent's project.

    Without a project_id the agent can't see KBs at all (mirrors
    rag_search). With one, kb_projects(kb_id, project_id) must exist.
    """
    if agent.project_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent has no project — cannot access KB documents",
        )
    grant = await session.execute(
        select(KnowledgeBaseProject.kb_id).where(
            KnowledgeBaseProject.kb_id == document.kb_id,
            KnowledgeBaseProject.project_id == agent.project_id,
        )
    )
    if grant.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent's project is not granted access to this document's KB",
        )


@router.post("/document-convert", response_model=DocumentConvertResponse)
async def document_convert(
    payload: DocumentConvertRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
) -> DocumentConvertResponse:
    """Return the structured chunks of an existing Document.

    The agent must be in a project that has been granted access to
    the document's KB; otherwise 403. RLS already pins the tenant,
    this check enforces the project-level grant on top.
    """
    agent, _project = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)
    doc_row = await session.execute(
        select(Document).where(
            Document.id == payload.document_id,
            Document.deleted_at.is_(None),
        )
    )
    document = doc_row.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    await _assert_doc_visible_to_agent(session, document=document, agent=agent)

    chunk_rows = await session.execute(
        select(Chunk).where(Chunk.document_id == document.id).order_by(Chunk.ordinal)
    )
    chunks = chunk_rows.scalars().all()
    return DocumentConvertResponse(
        document_id=document.id,
        kb_id=document.kb_id,
        title=document.title,
        source_filename=document.source_filename,
        source_mime_type=document.source_mime_type,
        page_count=document.page_count,
        chunks=[
            DocumentChunkOut(
                chunk_id=c.id,
                ordinal=c.ordinal,
                content=c.content,
                bbox=c.bbox,
            )
            for c in chunks
        ],
    )


# ---------------------------------------------------------------------------
# /promote-to-kb
# ---------------------------------------------------------------------------
class PromoteToKbRequest(BaseModel):
    """Body of POST /internal/agent/promote-to-kb.

    Copies an existing Document (and its chunks) into another KB the
    agent's project also has access to. The source KB grant gates the
    *read*; the target KB grant gates the *write*. The bytes in MinIO
    are NOT re-uploaded — the new Document points at the same
    ``source_storage_key`` (it's still a tenant-scoped path).
    """

    model_config = _BASE_CONFIG

    document_id: UUID
    target_kb_id: UUID
    title: str | None = None


class PromoteToKbResponse(BaseModel):
    model_config = _BASE_CONFIG

    document_id: UUID
    chunks_persisted: int


@router.post(
    "/promote-to-kb",
    response_model=PromoteToKbResponse,
    status_code=status.HTTP_201_CREATED,
)
async def promote_to_kb_endpoint(
    payload: PromoteToKbRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
    session: AsyncSession = Depends(get_agent_tenant_session),
) -> PromoteToKbResponse:
    """Duplicate a Document + its chunks into another KB."""
    agent, _project = await _resolve_agent_context(session, principal.agent_id, principal.tenant_id)

    source_row = await session.execute(
        select(Document).where(
            Document.id == payload.document_id,
            Document.deleted_at.is_(None),
        )
    )
    source = source_row.scalar_one_or_none()
    if source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="source document not found"
        )
    await _assert_doc_visible_to_agent(session, document=source, agent=agent)

    # Target KB must exist and be granted to the agent's project.
    target_kb_row = await session.execute(
        select(KnowledgeBase).where(
            KnowledgeBase.id == payload.target_kb_id,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    target_kb = target_kb_row.scalar_one_or_none()
    if target_kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="target KB not found")
    target_grant = await session.execute(
        select(KnowledgeBaseProject.kb_id).where(
            KnowledgeBaseProject.kb_id == payload.target_kb_id,
            KnowledgeBaseProject.project_id == agent.project_id,
        )
    )
    if target_grant.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="agent's project is not granted access to the target KB",
        )

    source_chunks = (
        (
            await session.execute(
                select(Chunk).where(Chunk.document_id == source.id).order_by(Chunk.ordinal)
            )
        )
        .scalars()
        .all()
    )

    new_document = Document(
        tenant_id=principal.tenant_id,
        kb_id=payload.target_kb_id,
        title=payload.title or source.title,
        source_filename=source.source_filename,
        source_mime_type=source.source_mime_type,
        source_storage_key=source.source_storage_key,
        source_size_bytes=source.source_size_bytes,
        status="indexed",
        page_count=source.page_count,
    )
    session.add(new_document)
    await session.flush()

    for spec in source_chunks:
        session.add(
            Chunk(
                tenant_id=principal.tenant_id,
                document_id=new_document.id,
                ordinal=spec.ordinal,
                content=spec.content,
                embedding=spec.embedding,
                bbox=spec.bbox,
                metadata_=spec.metadata_ or {},
            )
        )
    await session.flush()

    return PromoteToKbResponse(
        document_id=new_document.id,
        chunks_persisted=len(source_chunks),
    )


def _resolve_store_owner(*, scope: str, project: Project | None) -> dict[str, UUID | None] | None:
    """Compute the owner kwargs ``persist_memory_candidates`` needs.

    ``project`` is the agent's EFFECTIVE project (its own, or — for a global
    agent under ADR 0054 — the project of the task it is running). Using it for
    ``project_shared`` (rather than ``agent.project_id``) keeps store symmetric
    with recall (M2): a global agent writes into the task's project, and a
    project-bound agent writes into its own (the effective project IS its own).
    """
    if scope == MemoryScope.GLOBAL.value:
        return {"user_id": None, "team_id": None, "project_id": None}
    if scope == MemoryScope.PROJECT_SHARED.value:
        if project is None:
            return None
        return {"user_id": None, "team_id": None, "project_id": project.id}
    if scope == MemoryScope.TEAM_SHARED.value:
        if project is None or project.team_id is None:
            return None
        return {"user_id": None, "team_id": project.team_id, "project_id": None}
    # MemoryScope.PRIVATE — no user attribution for an AI agent.
    return None


# ---------------------------------------------------------------------------
# /pending-guidance (`task_wf_71`)
# ---------------------------------------------------------------------------
class PendingGuidanceRequest(BaseModel):
    task_id: UUID


class PendingGuidanceResponse(BaseModel):
    guidance: str | None = None


@router.post("/pending-guidance", response_model=PendingGuidanceResponse)
async def pending_guidance(
    payload: PendingGuidanceRequest,
    principal: AgentPrincipal = Depends(get_agent_principal),
) -> PendingGuidanceResponse:
    """La guía que un humano ha escrito para ESTE run, si la hay (`task_wf_71`).

    POST y no GET porque **consume**: la guía se borra al entregarla. Dejarla
    puesta la repetiría en cada iteración y el agente acabaría re-aplicando una
    corrección que ya hizo.

    Se busca por la tarea del token —no por un `execution_id` que el sandbox no
    conoce— y solo sobre la ejecución `running`: el run-lock garantiza que solo
    hay una. El tenant lo fija el token minteado, así que un run no puede leer
    la guía de otro.
    """
    from sqlalchemy import select

    from api_server.db.domain import Execution
    from api_server.db.session import get_sessionmaker

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session, session.begin():
        row = (
            await session.execute(
                select(Execution)
                .where(
                    Execution.task_id == payload.task_id,
                    Execution.tenant_id == principal.tenant_id,
                    Execution.status == "running",
                )
                .order_by(Execution.created_at.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or not row.pending_guidance:
            return PendingGuidanceResponse(guidance=None)
        guidance = str(row.pending_guidance)
        row.pending_guidance = None
    return PendingGuidanceResponse(guidance=guidance)
