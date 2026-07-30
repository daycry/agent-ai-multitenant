"""`/kb-categories` endpoints (Plan 06.10 task_06_10_03).

CRUD para `kb_categories`:

  - GET    /kb-categories            list built-ins + tenant custom
  - POST   /kb-categories            create custom (tenant_admin)
  - PUT    /kb-categories/{id}       edit custom (tenant_admin)
  - DELETE /kb-categories/{id}       soft-delete custom (tenant_admin)

Built-ins (`tenant_id IS NULL`) son **read-only**: el endpoint
rechaza PUT/DELETE con 403 explícito. Las KBs siguen apuntando a la
categoría built-in mientras exista.

La lista (`GET`) une built-ins + custom del tenant en una sola
respuesta. RLS hace la unión natural: la policy
`kb_categories_builtin_read` permite SELECT en filas con `tenant_id
IS NULL` para sesiones de tenant; la policy estándar muestra las
filas custom del tenant.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.knowledge import KbCategory
from api_server.routers._helpers import require_tenant_id
from api_server.routers._integrity import integrity_conflict
from api_server.schemas.knowledge import (
    KbCategoryCreateRequest,
    KbCategoryResponse,
    KbCategoryUpdateRequest,
    to_kb_category_response,
)

router = APIRouter(prefix="/kb-categories", tags=["kb-categories"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _load_category_or_404(session: AsyncSession, category_id: UUID) -> KbCategory:
    result = await session.execute(
        select(KbCategory).where(KbCategory.id == category_id, KbCategory.deleted_at.is_(None))
    )
    cat = result.scalar_one_or_none()
    if cat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="category not found")
    return cat


def _ensure_writable(category: KbCategory) -> None:
    """Rechaza PUT/DELETE sobre built-ins."""
    if category.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "cannot modify a built-in category; built-ins are seeded by "
                "the platform and read-only from the tenant API"
            ),
        )


# ---------------------------------------------------------------------------
# GET /kb-categories
# ---------------------------------------------------------------------------
@router.get("", response_model=list[KbCategoryResponse])
async def list_kb_categories(
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[KbCategoryResponse]:
    """Lista categorías visibles al tenant: built-ins (tenant_id NULL)
    + custom del tenant. Ordenadas por (built-in primero, luego nombre)."""
    stmt = (
        select(KbCategory)
        .where(KbCategory.deleted_at.is_(None))
        .order_by(
            # Built-ins primero (is_builtin true ordena antes con desc).
            KbCategory.is_builtin.desc(),
            KbCategory.name,
        )
    )
    result = await session.execute(stmt)
    return [to_kb_category_response(c) for c in result.scalars().all()]


# ---------------------------------------------------------------------------
# POST /kb-categories
# ---------------------------------------------------------------------------
@router.post(
    "",
    response_model=KbCategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_kb_category(
    payload: KbCategoryCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> KbCategoryResponse:
    tenant_id = require_tenant_id(principal)

    # Verificar que el slug no choca con uno built-in ni con otro custom
    # del tenant. Queremos 409 con mensaje claro en lugar de un
    # IntegrityError genérico. (Built-ins viven bajo el platform tenant
    # con is_builtin=true — Plan 06.12 / ADR 0029.)
    existing = await session.execute(
        select(KbCategory).where(
            KbCategory.slug == payload.slug,
            KbCategory.deleted_at.is_(None),
            or_(KbCategory.tenant_id == tenant_id, KbCategory.is_builtin),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"slug '{payload.slug}' already exists in this tenant or as built-in",
        )

    cat = KbCategory(
        tenant_id=tenant_id,
        slug=payload.slug,
        name=payload.name,
        color=payload.color,
        created_by=principal.user_id,
    )
    session.add(cat)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise integrity_conflict(exc, context="kb_category.create") from exc
    await session.refresh(cat)
    return to_kb_category_response(cat)


# ---------------------------------------------------------------------------
# PUT /kb-categories/{id}
# ---------------------------------------------------------------------------
@router.put("/{category_id}", response_model=KbCategoryResponse)
async def update_kb_category(
    category_id: UUID,
    payload: KbCategoryUpdateRequest,
    _: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> KbCategoryResponse:
    cat = await _load_category_or_404(session, category_id)
    _ensure_writable(cat)

    if payload.name is not None:
        cat.name = payload.name
    if payload.color is not None:
        cat.color = payload.color or None

    await session.flush()
    await session.refresh(cat)
    return to_kb_category_response(cat)


# ---------------------------------------------------------------------------
# DELETE /kb-categories/{id}
# ---------------------------------------------------------------------------
@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb_category(
    category_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Soft-delete una categoría custom. Las KBs que la usaban quedan
    con `category_id = NULL` (FK ON DELETE SET NULL — pero como esto
    es soft-delete no dispara; el endpoint lo hace explícitamente).
    """
    cat = await _load_category_or_404(session, category_id)
    _ensure_writable(cat)

    from datetime import UTC, datetime

    cat.deleted_at = datetime.now(UTC)
    # Las KBs apuntando a esta categoría: nullify para que la UI no
    # las muestre con un grupo fantasma. Si la categoría se "recupera"
    # (UPDATE deleted_at = NULL), las KBs ya no tendrán el grant —
    # ese tradeoff es aceptable y simple.
    from api_server.db.knowledge import KnowledgeBase

    kbs_using = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.category_id == category_id)
    )
    for kb in kbs_using.scalars().all():
        kb.category_id = None
    await session.flush()
