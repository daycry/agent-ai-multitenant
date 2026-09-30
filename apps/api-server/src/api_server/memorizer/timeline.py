"""Linea de tiempo de una memoria (task_mem_03): «que pasaba cuando se aprendio esto».

Dada una memoria (o una ejecucion), devuelve las memorias de SU ejecucion y las
de las ejecuciones inmediatamente anterior(es) y posterior(es), como **indice**
(id, titular de ~120 caracteres, tipo, ambito, fecha, agente, proyecto,
ejecucion) - nunca el contenido completo.

Reglas que este modulo no negocia:

  - **Quien pregunta decide lo visible.** El llamador pasa una clausula de
    visibilidad (:func:`agent_visibility` para el agente del runtime,
    :func:`human_visibility` para la API publica). Un agente nunca ve `private`;
    una persona solo las suyas. Toda consulta lleva ``tenant_id`` ademas de RLS.
  - **Mismo ambito y mismo dueno.** Si el ancla es una memoria, los vecinos son
    de su mismo ambito y de su mismo puntero (proyecto / equipo / persona;
    `global` no tiene puntero): una memoria de otro proyecto no cuela por haber
    nacido en una ejecucion vecina.
  - **``window`` = ejecuciones vecinas por lado** (que tengan alguna memoria
    visible), no memorias. Las ejecuciones se ordenan por ``created_at``.
  - ``executions`` esta particionada por ``created_at``: la busqueda se acota a
    ``+-HORIZON_DAYS`` alrededor del ancla para podar particiones.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, Select, and_, false, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Execution
from api_server.db.memory import MemoryEntry
from api_server.memorizer.provenance import resolve_names

HEADLINE_CHARS = 120
HORIZON_DAYS = 30
MAX_WINDOW = 20

# Puntero de dueno por ambito (`global` no tiene).
_OWNER_ATTR = {"private": "user_id", "team_shared": "team_id", "project_shared": "project_id"}


def agent_visibility(
    scopes: Sequence[str], *, team_id: UUID | None, project_id: UUID | None
) -> ColumnElement[bool]:
    """Lo que un AGENTE puede ver: nunca `private`; `team_shared` solo de su
    equipo y `project_shared` solo de su proyecto (punteros resueltos en el
    servidor, igual que `memory-recall`)."""
    clauses: list[ColumnElement[bool]] = []
    if "global" in scopes:
        clauses.append(MemoryEntry.scope == "global")
    if "team_shared" in scopes and team_id is not None:
        clauses.append(and_(MemoryEntry.scope == "team_shared", MemoryEntry.team_id == team_id))
    if "project_shared" in scopes and project_id is not None:
        clauses.append(
            and_(MemoryEntry.scope == "project_shared", MemoryEntry.project_id == project_id)
        )
    return or_(*clauses) if clauses else false()


def human_visibility(viewer_id: UUID | None) -> ColumnElement[bool]:
    """Lo que una PERSONA puede ver: todo lo no privado del tenant y sus privadas."""
    if viewer_id is None:
        return MemoryEntry.scope != "private"
    return or_(MemoryEntry.scope != "private", MemoryEntry.user_id == viewer_id)


def headline(content: str, limit: int = HEADLINE_CHARS) -> str:
    """Primeros ~``limit`` caracteres del contenido, en una sola linea."""
    flat = " ".join(content.split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


@dataclass
class TimelineExecution:
    execution_id: UUID | None
    position: int
    started_at: datetime | None
    memories: list[dict[str, Any]]


@dataclass
class Timeline:
    anchor_memory_id: UUID | None
    anchor_execution_id: UUID | None
    window: int
    executions: list[TimelineExecution]

    def as_dict(self) -> dict[str, Any]:
        return {
            "anchor_memory_id": str(self.anchor_memory_id) if self.anchor_memory_id else None,
            "anchor_execution_id": (
                str(self.anchor_execution_id) if self.anchor_execution_id else None
            ),
            "window": self.window,
            "executions": [
                {
                    "execution_id": str(e.execution_id) if e.execution_id else None,
                    "position": e.position,
                    "started_at": e.started_at.isoformat() if e.started_at else None,
                    "memories": e.memories,
                }
                for e in self.executions
            ],
        }


async def _hits(
    session: AsyncSession, tenant_id: UUID, rows: Sequence[MemoryEntry]
) -> list[dict[str, Any]]:
    names = await resolve_names(session, tenant_id, rows)
    return [
        {
            "id": str(r.id),
            "headline": headline(r.content),
            "type": r.type,
            "kind": r.kind,
            "scope": r.scope,
            "created_at": r.created_at.isoformat(),
            "agent": names.agents.get(r.agent_id) if r.agent_id else None,
            "project": names.projects.get(r.project_id) if r.project_id else None,
            "execution_id": str(r.source_execution_id) if r.source_execution_id else None,
        }
        for r in rows
    ]


def _memory_filters(
    tenant_id: UUID, visible: ColumnElement[bool], owner: ColumnElement[bool] | None
) -> list[ColumnElement[bool]]:
    filters = [MemoryEntry.tenant_id == tenant_id, MemoryEntry.deleted_at.is_(None), visible]
    if owner is not None:
        filters.append(owner)
    return filters


async def build_timeline(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    visible: ColumnElement[bool],
    memory_id: UUID | None = None,
    execution_id: UUID | None = None,
    window: int = 5,
) -> Timeline | None:
    """La linea de tiempo, o ``None`` si el ancla no existe o no es visible
    para quien pregunta (el llamador responde 404: no se distingue «no existe»
    de «no es tuya»)."""
    if (memory_id is None) == (execution_id is None):
        raise ValueError("exactly one of memory_id / execution_id is required")
    window = max(1, min(window, MAX_WINDOW))

    owner: ColumnElement[bool] | None = None
    anchor_memory: MemoryEntry | None = None
    if memory_id is not None:
        anchor_memory = (
            await session.execute(
                select(MemoryEntry).where(
                    MemoryEntry.id == memory_id, *_memory_filters(tenant_id, visible, None)
                )
            )
        ).scalar_one_or_none()
        if anchor_memory is None:
            return None
        execution_id = anchor_memory.source_execution_id
        scope_clause: ColumnElement[bool] = MemoryEntry.scope == anchor_memory.scope
        attr = _OWNER_ATTR.get(anchor_memory.scope)
        owner = (
            and_(scope_clause, getattr(MemoryEntry, attr) == getattr(anchor_memory, attr))
            if attr is not None
            else scope_clause
        )
        if execution_id is None:  # alta manual: no hay ejecucion de la que colgar vecinos
            return Timeline(
                anchor_memory_id=anchor_memory.id,
                anchor_execution_id=None,
                window=window,
                executions=[
                    TimelineExecution(
                        None, 0, None, await _hits(session, tenant_id, [anchor_memory])
                    )
                ],
            )

    assert execution_id is not None
    anchor_exec = (
        await session.execute(
            select(Execution.id, Execution.created_at).where(
                Execution.id == execution_id, Execution.tenant_id == tenant_id
            )
        )
    ).one_or_none()
    if anchor_exec is None:
        return None
    anchor_at: datetime = anchor_exec.created_at
    lo, hi = anchor_at - timedelta(days=HORIZON_DAYS), anchor_at + timedelta(days=HORIZON_DAYS)

    mem_filters = _memory_filters(tenant_id, visible, owner)

    def _executions(*conds: ColumnElement[bool]) -> Select[Any]:
        return (
            select(Execution.id, Execution.created_at)
            .join(
                MemoryEntry,
                and_(
                    MemoryEntry.source_execution_id == Execution.id,
                    MemoryEntry.tenant_id == Execution.tenant_id,
                ),
            )
            .where(
                Execution.tenant_id == tenant_id,
                Execution.created_at >= lo,
                Execution.created_at <= hi,
                *mem_filters,
                *conds,
            )
            .group_by(Execution.id, Execution.created_at)
        )

    # El ancla debe tener al menos una memoria visible (por execution_id, es lo
    # que impide sondear ejecuciones ajenas).
    if (await session.execute(_executions(Execution.id == execution_id))).first() is None:
        return None

    before = (
        await session.execute(
            _executions(Execution.created_at < anchor_at)
            .order_by(Execution.created_at.desc(), Execution.id.desc())
            .limit(window)
        )
    ).all()
    after = (
        await session.execute(
            _executions(Execution.created_at > anchor_at)
            .order_by(Execution.created_at.asc(), Execution.id.asc())
            .limit(window)
        )
    ).all()
    ordered: list[tuple[int, UUID, datetime]] = [
        *((-i, r.id, r.created_at) for i, r in reversed(list(enumerate(before, start=1)))),
        (0, execution_id, anchor_at),
        *((i, r.id, r.created_at) for i, r in enumerate(after, start=1)),
    ]

    rows = (
        (
            await session.execute(
                select(MemoryEntry)
                .where(MemoryEntry.source_execution_id.in_([e[1] for e in ordered]), *mem_filters)
                .order_by(MemoryEntry.created_at.asc(), MemoryEntry.id.asc())
            )
        )
        .scalars()
        .all()
    )
    hits = await _hits(session, tenant_id, rows)
    by_exec: dict[str, list[dict[str, Any]]] = {}
    for hit in hits:
        by_exec.setdefault(str(hit["execution_id"]), []).append(hit)
    return Timeline(
        anchor_memory_id=anchor_memory.id if anchor_memory else None,
        anchor_execution_id=execution_id,
        window=window,
        executions=[
            TimelineExecution(eid, pos, at, by_exec.get(str(eid), [])) for pos, eid, at in ordered
        ],
    )
