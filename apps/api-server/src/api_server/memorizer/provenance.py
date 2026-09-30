"""Procedencia y búsqueda de memorias para el panel (task_ui_31).

Tres piezas que el router `/memories` reutiliza:

  - :func:`resolve_names` — nombres de agente / proyecto / equipo / persona de
    un lote de filas en **cuatro** consultas (una por tabla, `IN (...)`), no una
    por memoria.
  - :func:`created_by_for` — «quién» dejó la memoria, derivado sólo de lo que el
    dominio guarda: `agent_id` (agente), `source_human_work_session_id`
    (persona, vía la sesión), `metadata.actor_user_id` (alta manual) o `user_id`
    (privada). Sin ninguna de esas huellas, `kind='unknown'`: no se inventa.
  - :func:`search_memories` — buscador híbrido (BM25 español + vector, fusión RRF
    de `recall.fuse_rankings`) sobre TODO lo visible para la persona, no sobre
    un único equipo/proyecto como el `recall` del runtime del agente.

Visibilidad (principio de ámbitos de CLAUDE.md): una `private` sólo la ve su
dueño; el resto de ámbitos, cualquier miembro del tenant. Toda consulta lleva
`tenant_id` además de RLS.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Agent, AgentScope, HumanWorkSession, Project, Team
from api_server.db.memory import MemoryEntry
from api_server.db.models import User
from api_server.memorizer.recall import fuse_rankings

# Sobre-muestreo por vía antes de fusionar.
_PATH_K = 30


@dataclass
class Names:
    """Nombres resueltos para un lote de memorias."""

    agents: dict[UUID, str] = field(default_factory=dict)
    projects: dict[UUID, str] = field(default_factory=dict)
    teams: dict[UUID, str] = field(default_factory=dict)
    users: dict[UUID, str] = field(default_factory=dict)
    hws_user: dict[UUID, UUID | None] = field(default_factory=dict)


def _actor_user_id(row: MemoryEntry) -> UUID | None:
    raw = (row.metadata_ or {}).get("actor_user_id")
    try:
        return UUID(str(raw)) if raw else None
    except ValueError:
        return None


async def resolve_names(
    session: AsyncSession, tenant_id: UUID, rows: Sequence[MemoryEntry]
) -> Names:
    """Nombres de todo lo que apuntan `rows`, en 4-5 consultas en total."""
    names = Names()
    agent_ids = {r.agent_id for r in rows if r.agent_id}
    project_ids = {r.project_id for r in rows if r.project_id}
    team_ids = {r.team_id for r in rows if r.team_id}
    hws_ids = {r.source_human_work_session_id for r in rows if r.source_human_work_session_id}
    if agent_ids:
        res = await session.execute(
            select(Agent.id, Agent.name).where(
                # Mismo criterio de visibilidad que `dashboard/project_team.py`: los del
                # tenant y los built-in de plataforma (escriben memorias bajo el tenant
                # de plataforma); nunca los de otro tenant.
                or_(Agent.tenant_id == tenant_id, Agent.scope == AgentScope.GLOBAL_BUILTIN.value),
                Agent.id.in_(agent_ids),
            )
        )
        names.agents = {row[0]: row[1] for row in res.all()}
    if project_ids:
        res = await session.execute(
            select(Project.id, Project.name).where(
                Project.tenant_id == tenant_id, Project.id.in_(project_ids)
            )
        )
        names.projects = {row[0]: row[1] for row in res.all()}
    if team_ids:
        res = await session.execute(
            select(Team.id, Team.name).where(Team.tenant_id == tenant_id, Team.id.in_(team_ids))
        )
        names.teams = {row[0]: row[1] for row in res.all()}
    if hws_ids:
        res = await session.execute(
            select(HumanWorkSession.id, HumanWorkSession.user_id).where(
                HumanWorkSession.tenant_id == tenant_id, HumanWorkSession.id.in_(hws_ids)
            )
        )
        names.hws_user = {row[0]: row[1] for row in res.all()}
    user_ids: set[UUID] = {r.user_id for r in rows if r.user_id}
    user_ids |= {u for u in names.hws_user.values() if u}
    user_ids |= {a for r in rows if (a := _actor_user_id(r))}
    if user_ids:
        res = await session.execute(
            select(User.id, User.full_name, User.email).where(User.id.in_(user_ids))
        )
        names.users = {i: (full or email) for i, full, email in res.all()}
    return names


async def resolve_agent_project_names(
    session: AsyncSession,
    tenant_id: UUID,
    *,
    agent_ids: set[UUID],
    project_ids: set[UUID],
) -> tuple[dict[UUID, str], dict[UUID, str]]:
    """``(agentes, proyectos)`` → nombre, en dos consultas (índice del recall, task_mem_01).

    Mismo criterio de visibilidad que :func:`resolve_names`: los agentes del tenant
    y los built-in de plataforma; los proyectos, sólo del tenant."""
    agents: dict[UUID, str] = {}
    projects: dict[UUID, str] = {}
    if agent_ids:
        res = await session.execute(
            select(Agent.id, Agent.name).where(
                or_(Agent.tenant_id == tenant_id, Agent.scope == AgentScope.GLOBAL_BUILTIN.value),
                Agent.id.in_(agent_ids),
            )
        )
        agents = {row[0]: row[1] for row in res.all()}
    if project_ids:
        res = await session.execute(
            select(Project.id, Project.name).where(
                Project.tenant_id == tenant_id, Project.id.in_(project_ids)
            )
        )
        projects = {row[0]: row[1] for row in res.all()}
    return agents, projects


def created_by_for(row: MemoryEntry, names: Names) -> dict[str, Any]:
    """`{kind, id, name}` de quien dejó la memoria; `unknown` si no hay huella."""
    if row.agent_id:
        return {"kind": "agent", "id": row.agent_id, "name": names.agents.get(row.agent_id)}
    if row.source_human_work_session_id:
        uid = names.hws_user.get(row.source_human_work_session_id)
        return {
            "kind": "human",
            "id": uid,
            "name": names.users.get(uid) if uid else None,
        }
    uid = _actor_user_id(row) or row.user_id
    if uid:
        return {"kind": "user", "id": uid, "name": names.users.get(uid)}
    return {"kind": "unknown", "id": None, "name": None}


def visibility_sql(prefix: str = "") -> str:
    """Cláusula SQL (binds `:tenant_id`, `:viewer_id`) de lo que la persona puede ver.

    Lo archivado (task_mem_11) queda fuera de contadores y buscador, igual que del
    listado por defecto; sólo `GET /memories?archived=true` lo enseña."""
    p = prefix
    return (
        f"{p}tenant_id = :tenant_id AND {p}deleted_at IS NULL AND {p}archived_at IS NULL"
        f" AND ({p}scope <> 'private' OR {p}user_id = :viewer_id)"
    )


async def scope_counts(
    session: AsyncSession, tenant_id: UUID, viewer_id: UUID | None
) -> dict[str, int]:
    """Contador por ámbito con la MISMA regla de visibilidad que el listado."""
    res = await session.execute(
        text(
            "SELECT scope, count(*) FROM memory_entries WHERE "
            + visibility_sql()
            + " GROUP BY scope"
        ),
        {"tenant_id": tenant_id, "viewer_id": viewer_id},
    )
    by_scope = {scope: int(n) for scope, n in res.all()}
    out = {s: by_scope.get(s, 0) for s in ("private", "team_shared", "project_shared", "global")}
    return {"all": sum(out.values()), **out}


async def search_memories(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    viewer_id: UUID | None,
    query: str,
    query_embedding: Sequence[float] | None,
    scope: str | None,
    project_id: UUID | None,
    limit: int,
) -> list[tuple[UUID, float]]:
    """Ids y score RRF (desc) de lo visible que casa con `query`."""
    params: dict[str, Any] = {
        "tenant_id": tenant_id,
        "viewer_id": viewer_id,
        "q": query,
        "k": _PATH_K,
    }
    extra = ""
    if scope is not None:
        extra += " AND scope = :scope"
        params["scope"] = scope
    if project_id is not None:
        extra += " AND project_id = :project_id"
        params["project_id"] = project_id
    base = "SELECT id FROM memory_entries WHERE " + visibility_sql() + extra
    bm25 = await session.execute(
        text(
            base + " AND to_tsvector('public.es_unaccent', content)"
            " @@ plainto_tsquery('public.es_unaccent', :q)"
            " ORDER BY ts_rank_cd(to_tsvector('public.es_unaccent', content),"
            " plainto_tsquery('public.es_unaccent', :q)) DESC LIMIT :k"
        ),
        params,
    )
    bm25_ids = [r[0] for r in bm25.all()]
    vector_ids: list[UUID] = []
    if query_embedding is not None:
        vparams = dict(params)
        vparams["qvec"] = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
        vec = await session.execute(
            text(
                base + " AND embedding IS NOT NULL"
                " ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :k"
            ),
            vparams,
        )
        vector_ids = [r[0] for r in vec.all()]
    fused = fuse_rankings(bm25_ids, vector_ids)
    ranked = sorted(fused.items(), key=lambda kv: -kv[1][0])[:limit]
    return [(mid, score) for mid, (score, _b, _v, _e) in ranked]
