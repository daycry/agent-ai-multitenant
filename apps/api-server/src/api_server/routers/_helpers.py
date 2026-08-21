"""Shared helpers for tenant-scoped CRUD routers.

After four routers (agents, skills, tools, teams) we settled into a
clear pattern: every PUT/DELETE needs a writable lookup that filters
out other tenants and soft-deleted rows, every POST needs the active
tenant id, every PUT needs to apply only the fields the client set
while remapping aliased / enum fields.

These helpers are router-only -- they raise HTTPException, so don't
import them from non-FastAPI code (use the bare SA queries instead).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, TypeVar
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Tenant context
# ---------------------------------------------------------------------------
def require_tenant_id(principal: AuthPrincipal) -> UUID:
    """Endpoints that touch tenant_id-bearing rows need an active tenant
    in the JWT. A token without `tid` (e.g. fresh-login pre-tenant
    selection) cannot write."""
    if principal.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="active tenant required (JWT missing 'tid' claim)",
        )
    return principal.tenant_id


# ---------------------------------------------------------------------------
# Project state guard (P1-01)
# ---------------------------------------------------------------------------
def require_project_active(project: Any) -> None:
    """Las operaciones que CREAN o ARRANCAN trabajo (planes, tareas, chat,
    start-execution) exigen `project.status == active` — pausar/archivar un
    proyecto dejó de ser decorativo. Las lecturas no pasan por aquí."""
    if project.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "project_not_active",
                "status": project.status,
                "message": "El proyecto no está activo; reanúdalo para operar sobre él.",
            },
        )


# ---------------------------------------------------------------------------
# Writable lookup
# ---------------------------------------------------------------------------
async def get_writable_or_404[T](
    session: AsyncSession,
    model_cls: type[T],
    obj_id: UUID,
    principal: AuthPrincipal,
    *,
    not_found_detail: str,
    extra_filters: tuple[ColumnElement[bool], ...] = (),
    soft_delete_aware: bool = True,
    for_update: bool = False,
) -> T:
    """Load a tenant-owned (and, by default, non-deleted) row for write.

    404 is preferred over 403 to avoid leaking which IDs exist in other
    tenants or as platform-owned built-ins.

    `extra_filters` lets callers exclude built-ins or apply other model-
    specific restrictions. Common case: skills/tools pass
    `(Model.is_builtin.is_(False),)`.

    `soft_delete_aware`: pass False for models without SoftDeleteMixin
    (Task uses terminal statuses instead of `deleted_at`).

    `for_update` (prod-13 task_prod13_22, hallazgo api-10) añade
    ``SELECT … FOR UPDATE``: la fila queda bloqueada hasta el final de la
    transacción, así que dos requests que la leen para decidir una TRANSICIÓN DE
    ESTADO se serializan en vez de leer las dos el mismo estado previo y escribir
    las dos. Es lo que cierra la carrera de la doble firma: sin él, dos admins
    pulsando "Aprobar" a la vez leían `first_approved_by = NULL` los dos y el plan
    quedaba aprobado con una sola firma real.

    NO es el default, y a propósito: `FOR UPDATE` serializa, y en los PUT de CRUD
    normal (renombrar un agente, editar una skill) el coste no compra nada — la
    última escritura gana y eso ya es la semántica deseada. Se activa solo donde
    la decisión depende del estado que se acaba de leer.
    """
    filters: list[ColumnElement[bool]] = [
        model_cls.id == obj_id,  # type: ignore[attr-defined]
        model_cls.tenant_id == principal.tenant_id,  # type: ignore[attr-defined]
    ]
    if soft_delete_aware:
        filters.append(model_cls.deleted_at.is_(None))  # type: ignore[attr-defined]
    filters.extend(extra_filters)

    stmt = select(model_cls).where(*filters)
    if for_update:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt)
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=not_found_detail)
    return obj


# ---------------------------------------------------------------------------
# Partial update
# ---------------------------------------------------------------------------
def apply_partial_update(
    obj: Any,
    payload: BaseModel,
    *,
    enum_fields: tuple[str, ...] = (),
    rename: dict[str, str] | None = None,
    transform: dict[str, Any] | None = None,
    exclude: tuple[str, ...] = (),
) -> None:
    """Mutate `obj` in place with the values the client actually sent.

    Behavior:
      - `model_dump(exclude_unset=True)` so a missing key is left alone
        but an explicit `null` clears the column.
      - `exclude`: campos del payload que NO son del recurso y que el
        endpoint maneja por su cuenta (`task_gov_05`:
        `eval_gate_override` es una directiva de la petición). Sin esto
        acabarían como `setattr` sobre la fila ORM — que no falla, porque
        una instancia declarativa acepta atributos arbitrarios, y por eso
        hace falta un test que lo vigile en vez de confiar en que reviente.
      - `enum_fields`: Pydantic stores StrEnum values as Enum members by
        default; the SA column expects the string. Call `.value` on
        these before assignment.
      - `rename`: `{"src_name": "dst_name"}`. Useful for fields whose
        Python name diverges from the SA column (e.g. `llm_config` ->
        `model_config`).
      - `transform`: `{"field_name": callable}` applied to the value
        before assignment. Used for list[UUID] -> list[str] coercion.
    """
    changes = payload.model_dump(exclude_unset=True)

    for field in exclude:
        changes.pop(field, None)

    if rename:
        for src, dst in rename.items():
            if src in changes:
                changes[dst] = changes.pop(src)

    for field in enum_fields:
        if field in changes and changes[field] is not None and hasattr(changes[field], "value"):
            changes[field] = changes[field].value

    if transform:
        for field, fn in transform.items():
            if field in changes and changes[field] is not None:
                changes[field] = fn(changes[field])

    for attr, value in changes.items():
        setattr(obj, attr, value)


# ---------------------------------------------------------------------------
# Soft delete
# ---------------------------------------------------------------------------
async def soft_delete(session: AsyncSession, obj: Any) -> None:
    """Stamp `deleted_at = now()` and flush. The session's commit is
    handled by the per-request transaction in `get_tenant_session`."""
    obj.deleted_at = datetime.now(tz=UTC)
    await session.flush()


def move_plan(session: AsyncSession, plan: Any, target: str, *, actor: UUID | None = None) -> None:
    """Mueve un plan por la máquina de estados Y anuncia el movimiento
    (`task_wf_32`).

    Las dos cosas juntas, en una sola llamada, a propósito: mientras fueran dos
    líneas separadas, cada endpoint nuevo tendría que acordarse de la segunda —
    y el tablero gerencial se quedaría rancio justo en la transición que a
    alguien se le olvidó. El anuncio viaja post-commit (ver
    :func:`publish_plan_transition_after_commit`) y es best-effort: un fallo de
    Redis no tumba la transición.

    Los caminos del orchestrator y del worker de mantenimiento NO pasan por
    aquí — escriben con UPDATE crudo para tener guarda atómica— y publican a
    mano tras su propio commit.
    """
    from api_server.chat.plan_state_machine import transition_plan_status
    from api_server.events import publish_plan_transition_after_commit

    old_status = plan.status
    transition_plan_status(plan, target, actor=actor)
    publish_plan_transition_after_commit(session, plan, old_status)
