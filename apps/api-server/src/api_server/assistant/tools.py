"""Cross-project READ tools for the personal assistant (Plan 10 task_10_14).

Each tool answers one slice of the tenant's global state. The binding
constraints (see the plan) make tenant isolation + RBAC non-negotiable:

  * Every tool runs through a **tenant-scoped session** — an RLS-bound
    ``AsyncSession`` opened by ``open_tenant_session`` for the asking
    principal. PostgreSQL RLS therefore filters every query to the asking
    admin's tenant. A tool can NEVER return another tenant's rows: the
    database refuses to return them, regardless of any id the model might
    pass. Since prod-13 ``task_prod13_07`` that session is opened **per tool
    call** and closed on return (see :class:`AssistantToolScope`), instead
    of being the request's session held open for the whole LLM turn.
  * Tools are READ-ONLY: they only ``SELECT``. The assistant cannot mutate
    state through them.

``tenant_budget_status`` returns the tenant's REAL budget consumption
(Plan 11.1 task_11_1_05): the tenant-wide + per-project spend vs budget in
canonical USD, with the percent used, the active period and a status. When no
budget is configured it returns a structured ``available: false`` result the
UI/LLM can render honestly (rather than fabricating numbers).

``tenant_human_workload`` / ``tenant_human_assignments_pending`` (Plan 16
task_16_14) answer the Human-Agents operational questions — "how many tasks
does user X have this week?" and "which human tasks are unaccepted for > N
hours?". Both run on the SAME tenant-scoped RLS session, so they too can only
ever see this tenant's :class:`HumanTaskAssignment` / :class:`HumanWorkSession`
rows; a user named in ``tenant_human_workload`` is resolved through the
tenant's ``user_org_memberships`` (RLS-scoped), so the asking admin can never
probe a user outside their tenant.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.memory import remember_user_fact
from api_server.db.domain import (
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    HumanWorkSession,
    Plan,
    Project,
    Task,
)
from api_server.db.models import User, UserOrganizationMembership

# Tasks still "in the team's hands" — terminal statuses excluded so the
# recent-activity / status views focus on outstanding work.
_ACTIVE_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "backlog",
        "ready",
        "in_progress",
        "in_review",
        "blocked",
        "awaiting_human_approval",
    }
)


#: Cómo se abre UNA sesión corta y tenant-scoped para atender una llamada de
#: tool. En producción el endpoint pasa ``lambda: open_tenant_session(principal)``
#: **tal cual** — ver :class:`AssistantToolScope` para por qué eso no es un
#: detalle de estilo.
ToolSessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass(frozen=True)
class AssistantToolContext:
    """Everything a tool needs to answer. The ``session`` is an RLS-bound
    session, so isolation is enforced by the database, not by the tool code.

    Es el contexto **enlazado**: toda implementación de tool recibe uno de
    éstos, con su sesión ya abierta. Quién la abrió —el request, o el despacho
    para esta única llamada— es cosa de :class:`AssistantToolScope`."""

    session: AsyncSession
    tenant_id: UUID
    # The asking admin — reserved for finer-grained RBAC scoping (e.g.
    # project-membership filters) without changing the tool signature.
    user_id: UUID


@dataclass(frozen=True)
class AssistantToolScope:
    """El alcance de un turno SIN sesión abierta: a quién sirve y cómo abrir una
    corta cuando haga falta (prod-13 ``task_prod13_07``).

    Es lo que el endpoint le da al grafo desde que el turno LLM dejó de correr
    dentro de una transacción: mientras el modelo piensa no hay ninguna conexión
    retenida, y cada llamada a tool abre la suya y la devuelve al pool.

    Por qué la fábrica es ``open_tenant_session`` y no una función propia
    --------------------------------------------------------------------
    El riesgo nº 1 de trocear la transacción es abrir un agujero de RLS: si la
    sesión corta no repite el ``set_config`` de ``app.user_id`` /
    ``app.tenant_id``, las políticas dejan de casar y el aislamiento entre
    tenants desaparece. La defensa no es recordarlo en cada sitio: es que **no
    haya un segundo sitio**. La fábrica que se pasa aquí es exactamente el mismo
    context manager que abre la sesión del request, así que no existe una segunda
    implementación que pueda olvidarse del binding."""

    tenant_id: UUID
    user_id: UUID
    session_factory: ToolSessionFactory

    def bind(self, session: AsyncSession) -> AssistantToolContext:
        """El contexto enlazado que ve la tool. Conserva tenant y usuario: un
        ``bind`` que los perdiera dejaría a las tools que filtran por
        ``ctx.tenant_id`` mirando otro tenant."""
        return AssistantToolContext(session=session, tenant_id=self.tenant_id, user_id=self.user_id)


#: Lo que el grafo transporta y el despacho acepta: un contexto ya enlazado o el
#: alcance que sabe abrir su propia sesión.
AssistantToolBinding = AssistantToolContext | AssistantToolScope


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
async def _tenant_projects_status(ctx: AssistantToolContext, **_: Any) -> dict[str, Any]:
    """Consolidated count + per-project status of the tenant's projects.

    Excluye las PLANTILLAS (verificación en vivo 2026-07-18): la policy RLS
    ``projects_template_read`` deja leer el catálogo builtin, así que sin el
    filtro el asistente contaba 9 plantillas de la plataforma como «proyectos
    activos» del tenant."""
    result = await ctx.session.execute(
        select(Project.id, Project.name, Project.status)
        .where(Project.deleted_at.is_(None), Project.is_template.is_(False))
        .order_by(Project.created_at)
    )
    rows = result.all()
    by_status: dict[str, int] = {}
    projects: list[dict[str, Any]] = []
    for pid, name, status_ in rows:
        by_status[status_] = by_status.get(status_, 0) + 1
        projects.append({"id": str(pid), "name": name, "status": status_})
    return {"total": len(projects), "by_status": by_status, "projects": projects}


async def _tenant_plans_summary(ctx: AssistantToolContext, **_: Any) -> dict[str, Any]:
    """Plans across all the tenant's projects, grouped by status, with a
    short list of titles per status."""
    result = await ctx.session.execute(
        select(Plan.id, Plan.title, Plan.status, Plan.project_id)
        .where(Plan.deleted_at.is_(None))
        .order_by(Plan.created_at)
    )
    rows = result.all()
    by_status: dict[str, int] = {}
    plans: list[dict[str, Any]] = []
    for pid, title, status_, project_id in rows:
        by_status[status_] = by_status.get(status_, 0) + 1
        plans.append(
            {
                "id": str(pid),
                "title": title,
                "status": status_,
                "project_id": str(project_id),
            }
        )
    pending = [p for p in plans if p["status"] in ("pending_approval", "pending_second_approval")]
    return {
        "total": len(plans),
        "by_status": by_status,
        "pending_approval": pending,
        "plans": plans,
    }


async def _tenant_recent_activity(
    ctx: AssistantToolContext, *, limit: int = 20, **_: Any
) -> dict[str, Any]:
    """The most recently updated non-terminal tasks across the tenant —
    a quick "what is the team working on right now" view."""
    capped = max(1, min(int(limit), 100))
    result = await ctx.session.execute(
        select(Task.id, Task.title, Task.status, Task.project_id, Task.plan_id, Task.updated_at)
        .where(Task.status.in_(_ACTIVE_TASK_STATUSES))
        .order_by(Task.updated_at.desc())
        .limit(capped)
    )
    rows = result.all()
    items = [
        {
            "id": str(tid),
            "title": title,
            "status": status_,
            "project_id": str(project_id),
            "plan_id": (str(plan_id) if plan_id is not None else None),
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
        }
        for tid, title, status_, project_id, plan_id, updated_at in rows
    ]
    # Also surface the active-task count so the assistant can answer
    # "how many open tasks do I have?" without re-querying.
    total_active = await ctx.session.scalar(
        select(func.count()).select_from(Task).where(Task.status.in_(_ACTIVE_TASK_STATUSES))
    )
    return {"active_task_count": int(total_active or 0), "recent": items}


# A4 (investigación 2026-07-11): búsqueda en las KBs del tenant. El motor RAG
# existía pero el asistente no tenía NINGUNA tool de conocimiento — «¿qué dice
# nuestra documentación sobre X?» era imposible. Cross-proyecto: cualquier
# chunk de una KB granted a algún proyecto del tenant (bajo la sesión RLS del
# request solo se ven filas del propio tenant + built-ins). BM25 con la config
# es_unaccent unificada (P0-4); el path vectorial queda para una segunda ola
# (exigiría cablear el embedder al contexto de tools).
_KB_SEARCH_LIMIT_MAX = 10
_KB_SEARCH_SNIPPET = 500


async def _search_knowledge(
    ctx: AssistantToolContext, *, query: str = "", limit: int = 5, **_: Any
) -> dict[str, Any]:
    """Pasajes relevantes de las KBs del tenant (read-only, RLS-scoped)."""
    from sqlalchemy import text as sa_text

    q = str(query or "").strip()
    if not q:
        return {"hits": [], "note": "query vacía"}
    k = max(1, min(int(limit or 5), _KB_SEARCH_LIMIT_MAX))
    sql = sa_text("""
        SELECT chunks.content, documents.kb_id, documents.title
        FROM chunks
        JOIN documents ON documents.id = chunks.document_id
             AND documents.deleted_at IS NULL
        WHERE EXISTS (SELECT 1 FROM kb_projects kp WHERE kp.kb_id = documents.kb_id)
          AND to_tsvector('public.es_unaccent', chunks.content)
              @@ plainto_tsquery('public.es_unaccent', :q)
        ORDER BY ts_rank_cd(
            to_tsvector('public.es_unaccent', chunks.content),
            plainto_tsquery('public.es_unaccent', :q)) DESC
        LIMIT :k
        """)
    rows = (await ctx.session.execute(sql, {"q": q, "k": k})).all()
    return {
        "hits": [
            {
                "document": str(row[2] or ""),
                "snippet": str(row[0] or "")[:_KB_SEARCH_SNIPPET],
            }
            for row in rows
        ]
    }


async def _tenant_budget_status(ctx: AssistantToolContext, **_: Any) -> dict[str, Any]:
    """Real tenant/project budget status (Plan 11.1 task_11_1_05).

    Sums the canonical-USD spend of the current budget period per scope (the
    tenant-wide budget + each project with one), compares it against the
    USD-converted cap, and returns the percent used, the active period and a
    coarse status. Runs on the caller's tenant-scoped RLS session, so only this
    tenant's budgets / spend are ever seen. When no budget is configured
    anywhere, returns a structured ``available: false`` result (an honest "no
    budget", never fabricated numbers)."""
    from api_server.budgets import tenant_budget_summary

    return await tenant_budget_summary(ctx.session, tenant_id=ctx.tenant_id)


# ---------------------------------------------------------------------------
# Human Agents tools (Plan 16 task_16_14)
# ---------------------------------------------------------------------------
# Open assignment states that count toward a user's live human-task load — the
# task is on the user's plate but not finished (pending_acceptance = awaiting
# accept; accepted = working). reassigned/declined/expired are closed and so
# excluded (mirrors planning_context._OPEN_ASSIGNMENT_STATES).
_OPEN_ASSIGNMENT_STATES: frozenset[str] = frozenset(
    {
        HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
        HumanTaskAssignmentStatus.ACCEPTED.value,
    }
)

# Default acceptance-age threshold for "pending too long" — mirrors the default
# ``human_agent_config.acceptance_timeout_hours`` (Plan 16 Decisiones Clave: 24h).
_DEFAULT_PENDING_THRESHOLD_HOURS = 24

# How many pending assignments / matched users a single call returns at most,
# so a chatty model can't pull an unbounded result set.
_MAX_PENDING_RESULTS = 200
_MAX_USER_MATCHES = 25


def _iso_week_start(now: datetime) -> datetime:
    """Monday 00:00 UTC of ``now``'s ISO week — the "this week" lower bound.

    Mirrors :func:`api_server.budgets.period._weekly_window` (Monday-anchored
    ISO week) so the assistant's "this week" matches the weekly budget window.
    """
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=midnight.weekday())


async def _resolve_tenant_user(
    ctx: AssistantToolContext, query: str
) -> tuple[UUID | None, list[dict[str, str]]]:
    """Resolve a free-text user reference to ONE tenant user, RBAC-safe.

    The admin names a user by id, email or (full) name. We only ever look at
    users who are MEMBERS of the asking admin's tenant — the
    ``user_org_memberships`` join is itself RLS-scoped to ``ctx.tenant_id``, so
    a name/email belonging to a user outside the tenant resolves to nothing
    (never leaking that the user exists elsewhere). Returns ``(user_id,
    matches)``: ``user_id`` is set only on an unambiguous single match;
    ``matches`` always carries the candidate list (capped) so the caller can
    report "no match" / "ambiguous" honestly.
    """
    needle = query.strip()
    membership = UserOrganizationMembership
    stmt = (
        select(User.id, User.email, User.full_name)
        .join(membership, membership.user_id == User.id)
        .where(
            membership.tenant_id == ctx.tenant_id,
            membership.is_active.is_(True),
            membership.deleted_at.is_(None),
            User.deleted_at.is_(None),
        )
    )
    # Try an exact id match first (the model may echo back an id we returned).
    parsed_id: UUID | None = None
    try:
        parsed_id = UUID(needle)
    except (ValueError, AttributeError):
        parsed_id = None
    if parsed_id is not None:
        stmt = stmt.where(User.id == parsed_id)
    else:
        like = f"%{needle}%"
        stmt = stmt.where(
            or_(
                func.lower(User.email) == needle.lower(),
                func.lower(User.full_name) == needle.lower(),
                User.email.ilike(like),
                User.full_name.ilike(like),
            )
        )
    rows = (await ctx.session.execute(stmt.order_by(User.email).limit(_MAX_USER_MATCHES + 1))).all()
    matches = [
        {"id": str(uid), "email": email, "full_name": full_name or ""}
        for uid, email, full_name in rows[:_MAX_USER_MATCHES]
    ]
    resolved = matches[0]["id"] if len(rows) == 1 else None
    return (UUID(resolved) if resolved is not None else None), matches


async def _tenant_human_workload(
    ctx: AssistantToolContext, *, user: str, **_: Any
) -> dict[str, Any]:
    """How much human-task work a tenant user has this (ISO) week.

    Resolves ``user`` (id / email / name) to ONE member of the asking admin's
    tenant, then counts that user's OPEN human-task assignments
    (pending_acceptance + accepted) and the work sessions they STARTED this
    week. RLS scopes both counts to the tenant, and the user is resolved only
    among tenant members, so a cross-tenant user is never reachable. Returns a
    typed ``resolved: false`` (with the candidate list) when the reference is
    empty / unknown / ambiguous, so the assistant answers honestly instead of
    fabricating a number."""
    needle = (user or "").strip()
    if not needle:
        return {"resolved": False, "reason": "empty_query", "matches": []}

    user_id, matches = await _resolve_tenant_user(ctx, needle)
    if user_id is None:
        reason = "ambiguous" if len(matches) > 1 else "not_found"
        return {"resolved": False, "reason": reason, "matches": matches}

    week_start = _iso_week_start(datetime.now(UTC))

    # Open assignments currently on this user (not time-bounded — "what is on
    # their plate right now"), split by status for a useful breakdown.
    assign_rows = (
        await ctx.session.execute(
            select(HumanTaskAssignment.status, func.count())
            .where(
                HumanTaskAssignment.assigned_to_user_id == user_id,
                HumanTaskAssignment.status.in_(_OPEN_ASSIGNMENT_STATES),
            )
            .group_by(HumanTaskAssignment.status)
        )
    ).all()
    by_status = {status_: int(count) for status_, count in assign_rows}
    open_assignments = sum(by_status.values())

    # Distinct tasks the user assigned work to this week + the sessions started
    # this week (the "this week" slice the question asks for).
    ws_row = (
        await ctx.session.execute(
            select(
                func.count(),
                func.count(func.distinct(HumanWorkSession.task_id)),
            ).where(
                HumanWorkSession.user_id == user_id,
                HumanWorkSession.start_at >= week_start,
            )
        )
    ).one()
    work_sessions_this_week = int(ws_row[0] or 0)
    tasks_worked_this_week = int(ws_row[1] or 0)

    matched = matches[0]
    return {
        "resolved": True,
        "user": matched,
        "week_start": week_start.isoformat(),
        "open_assignments": open_assignments,
        "open_assignments_by_status": by_status,
        "work_sessions_this_week": work_sessions_this_week,
        "tasks_worked_this_week": tasks_worked_this_week,
    }


async def _tenant_human_assignments_pending(
    ctx: AssistantToolContext, *, older_than_hours: int = _DEFAULT_PENDING_THRESHOLD_HOURS, **_: Any
) -> dict[str, Any]:
    """Human task assignments still awaiting acceptance for longer than N hours.

    Lists the tenant's ``pending_acceptance`` :class:`HumanTaskAssignment` rows
    whose ``assigned_at`` is older than ``older_than_hours`` (default 24h — the
    default acceptance timeout). RLS scopes every row to the asking admin's
    tenant, so out-of-tenant assignments are never returned. Joins the Task for
    a human-readable title and the assigned user's email so the assistant can
    name names. Ordered oldest-first (the most overdue first)."""
    threshold_hours = max(0, int(older_than_hours))
    cutoff = datetime.now(UTC) - timedelta(hours=threshold_hours)

    stmt = (
        select(
            HumanTaskAssignment.id,
            HumanTaskAssignment.task_id,
            HumanTaskAssignment.assigned_to_user_id,
            HumanTaskAssignment.assigned_at,
            Task.title,
            Task.project_id,
            User.email,
            User.full_name,
        )
        .join(Task, Task.id == HumanTaskAssignment.task_id)
        .outerjoin(User, User.id == HumanTaskAssignment.assigned_to_user_id)
        .where(
            HumanTaskAssignment.status == HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
            HumanTaskAssignment.assigned_at < cutoff,
        )
        .order_by(HumanTaskAssignment.assigned_at)
        .limit(_MAX_PENDING_RESULTS)
    )
    rows = (await ctx.session.execute(stmt)).all()
    now = datetime.now(UTC)
    items: list[dict[str, Any]] = []
    for aid, task_id, user_id, assigned_at, title, project_id, email, full_name in rows:
        pending_hours = (
            (now - assigned_at).total_seconds() / 3600.0 if assigned_at is not None else None
        )
        items.append(
            {
                "assignment_id": str(aid),
                "task_id": str(task_id),
                "task_title": title,
                "project_id": str(project_id),
                "assigned_to_user_id": (str(user_id) if user_id is not None else None),
                "assigned_to_email": email,
                "assigned_to_name": full_name,
                "assigned_at": assigned_at.isoformat() if assigned_at is not None else None,
                "pending_hours": (round(pending_hours, 2) if pending_hours is not None else None),
            }
        )
    return {
        "older_than_hours": threshold_hours,
        "count": len(items),
        "assignments": items,
    }


# ---------------------------------------------------------------------------
# Write tool: remember a durable fact about the asking user (ADR 0054)
# ---------------------------------------------------------------------------
async def _remember_about_me(
    ctx: AssistantToolContext,
    *,
    content: str,
    type: str = "semantic",
    tags: list[str] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Persist one durable fact about the asking user as private memory.

    Writes through the request's RLS-bound session as a ``scope='private'``
    ``memory_entries`` row owned by ``ctx.user_id`` (dedup handled in
    :func:`~api_server.assistant.memory.remember_user_fact`). This is the only
    WRITE tool the assistant has, and it can only write the asking user's own
    private memory."""
    return await remember_user_fact(
        ctx.session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        content=content,
        type=type,
        tags=tuple(tags or ()),
    )


# ---------------------------------------------------------------------------
# Registry + JSON schemas (the shape an LLM tool-calling API expects)
# ---------------------------------------------------------------------------
ToolImpl = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolEntry:
    """One entry in the assistant tool catalogue: the async implementation
    plus the JSON schema the LLM tool-calling API consumes."""

    impl: ToolImpl
    schema: dict[str, Any]


ASSISTANT_TOOLS: dict[str, ToolEntry] = {
    "tenant_projects_status": ToolEntry(
        impl=_tenant_projects_status,
        schema={
            "name": "tenant_projects_status",
            "description": (
                "Estado consolidado de todos los proyectos del tenant (conteo total y por estado)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    "search_knowledge": ToolEntry(
        impl=_search_knowledge,
        schema={
            "name": "search_knowledge",
            "description": (
                "Busca pasajes relevantes en las bases de conocimiento del "
                "tenant (documentación, guías) y devuelve fragmentos citables."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Qué buscar."},
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de pasajes (1-10).",
                        "minimum": 1,
                        "maximum": 10,
                    },
                },
                "required": ["query"],
            },
        },
    ),
    "tenant_plans_summary": ToolEntry(
        impl=_tenant_plans_summary,
        schema={
            "name": "tenant_plans_summary",
            "description": (
                "Resumen de los planes del tenant cross-proyecto, agrupados "
                "por estado, incluyendo los pendientes de aprobación."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    "tenant_recent_activity": ToolEntry(
        impl=_tenant_recent_activity,
        schema={
            "name": "tenant_recent_activity",
            "description": (
                "Tareas no terminales más recientes del tenant cross-proyecto "
                "(qué está haciendo el equipo ahora) y el total de tareas abiertas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de tareas a devolver (1-100).",
                        "minimum": 1,
                        "maximum": 100,
                    }
                },
            },
        },
    ),
    "tenant_budget_status": ToolEntry(
        impl=_tenant_budget_status,
        schema={
            "name": "tenant_budget_status",
            "description": (
                "Estado real de presupuesto del tenant y sus proyectos: gasto "
                "en USD del periodo actual, porcentaje del presupuesto, periodo "
                "y estado. Si no hay presupuesto configurado, lo indica."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    "tenant_human_workload": ToolEntry(
        impl=_tenant_human_workload,
        schema={
            "name": "tenant_human_workload",
            "description": (
                "Carga de trabajo de un usuario humano del tenant esta semana: "
                "cuántas tareas humanas tiene asignadas activas "
                "(pendientes de aceptar + aceptadas) y cuántas sesiones de "
                "trabajo ha iniciado esta semana. Identifica al usuario por "
                "nombre, email o id; si no hay coincidencia o es ambigua, lo "
                "indica con la lista de candidatos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": (
                            "Nombre, email o id del usuario humano del tenant "
                            "cuya carga se consulta."
                        ),
                    }
                },
                "required": ["user"],
            },
        },
    ),
    "tenant_human_assignments_pending": ToolEntry(
        impl=_tenant_human_assignments_pending,
        schema={
            "name": "tenant_human_assignments_pending",
            "description": (
                "Tareas humanas asignadas que siguen pendientes de aceptación "
                "desde hace más de N horas (por defecto 24h, el timeout de "
                "aceptación). Devuelve la lista ordenada de la más antigua a la "
                "más reciente con la tarea, el usuario asignado y las horas que "
                "lleva pendiente."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "older_than_hours": {
                        "type": "integer",
                        "description": (
                            "Umbral en horas de antigüedad de la asignación sin "
                            "aceptar (por defecto 24)."
                        ),
                        "minimum": 0,
                    }
                },
            },
        },
    ),
    "remember_about_me": ToolEntry(
        impl=_remember_about_me,
        schema={
            "name": "remember_about_me",
            "description": (
                "Guarda un dato personal DURADERO del usuario para recordarlo en "
                "futuras conversaciones: su nombre, una preferencia, un gusto o "
                "su estilo de comunicación. Úsalo cuando el usuario comparta algo "
                "así. No lo uses para cosas efímeras ni repitas algo que ya sabes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "El dato a recordar, en una frase breve (p. ej. "
                            "'Se llama Jose', 'Prefiere respuestas concisas')."
                        ),
                        "maxLength": 2000,
                    },
                    "type": {
                        "type": "string",
                        "enum": ["semantic", "episodic"],
                        "description": (
                            "semantic = preferencia/hecho durable (lo habitual); "
                            "episodic = un evento puntual."
                        ),
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Etiquetas opcionales para clasificar el recuerdo.",
                    },
                },
                "required": ["content"],
            },
        },
    ),
}


class UnknownToolError(KeyError):
    """The tool name is not in the assistant catalogue."""


async def run_assistant_tool(
    name: str,
    ctx: AssistantToolBinding,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch a tool by name. Raises :class:`UnknownToolError` for an
    unknown name (the graph filters to enabled tools, so this only fires
    on a programming error or a hostile model).

    Ésta es la costura ÚNICA por la que pasa toda llamada a tool del asistente,
    y por eso es aquí donde se abre la sesión corta cuando el contexto trae una
    fábrica: las once implementaciones siguen recibiendo un ``ctx.session`` ya
    enlazado y no cambian. La sesión se cierra —y commitea, que importa para la
    única tool que escribe— al volver de la tool, no al terminar el turno."""
    entry = ASSISTANT_TOOLS.get(name)
    if entry is None:
        raise UnknownToolError(f"unknown assistant tool {name!r}")
    if isinstance(ctx, AssistantToolScope):
        async with ctx.session_factory() as session:
            return await entry.impl(ctx.bind(session), **(arguments or {}))
    return await entry.impl(ctx, **(arguments or {}))


def tool_schemas(enabled: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the JSON schemas for the enabled tools, in catalogue order.
    Fed to the LLM tool-calling API."""
    return [ASSISTANT_TOOLS[name].schema for name in enabled if name in ASSISTANT_TOOLS]


__all__ = [
    "ASSISTANT_TOOLS",
    "AssistantToolBinding",
    "AssistantToolContext",
    "AssistantToolScope",
    "ToolEntry",
    "ToolSessionFactory",
    "UnknownToolError",
    "run_assistant_tool",
    "tool_schemas",
]
