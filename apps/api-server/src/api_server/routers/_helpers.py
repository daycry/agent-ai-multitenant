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
# Writable lookup
# ---------------------------------------------------------------------------
async def get_writable_or_404(
    session: AsyncSession,
    model_cls: type[T],
    obj_id: UUID,
    principal: AuthPrincipal,
    *,
    not_found_detail: str,
    extra_filters: tuple[ColumnElement[bool], ...] = (),
    soft_delete_aware: bool = True,
) -> T:
    """Load a tenant-owned (and, by default, non-deleted) row for write.

    404 is preferred over 403 to avoid leaking which IDs exist in other
    tenants or as platform-owned built-ins.

    `extra_filters` lets callers exclude built-ins or apply other model-
    specific restrictions. Common case: skills/tools pass
    `(Model.is_builtin.is_(False),)`.

    `soft_delete_aware`: pass False for models without SoftDeleteMixin
    (Task uses terminal statuses instead of `deleted_at`).
    """
    filters: list[ColumnElement[bool]] = [
        model_cls.id == obj_id,  # type: ignore[attr-defined]
        model_cls.tenant_id == principal.tenant_id,  # type: ignore[attr-defined]
    ]
    if soft_delete_aware:
        filters.append(model_cls.deleted_at.is_(None))  # type: ignore[attr-defined]
    filters.extend(extra_filters)

    result = await session.execute(select(model_cls).where(*filters))
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
) -> None:
    """Mutate `obj` in place with the values the client actually sent.

    Behavior:
      - `model_dump(exclude_unset=True)` so a missing key is left alone
        but an explicit `null` clears the column.
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
