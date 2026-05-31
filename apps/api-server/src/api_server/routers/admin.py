"""`/admin/*` endpoints — System Admin only.

Every route depends on `require_system_admin` (the JWT must carry
`sys: true`) and uses the BYPASSRLS admin engine so it can read
across tenants and write `audit_log` rows with `tenant_id IS NULL`.

Tenant CRUD is the heart of the file; user listing and system-health
are read-only summaries.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_client_ip,
    get_redis,
    require_system_admin,
)
from api_server.config import get_settings
from api_server.db.models import AuditAction, Organization, User
from api_server.logging import get_logger
from api_server.schemas.admin import (
    ServiceHealth,
    SystemHealthResponse,
    TenantCreateRequest,
    TenantResponse,
    TenantUpdateRequest,
    UserListItem,
)

# 5s da margen suficiente para:
#   - asyncpg lazily abre la primera conexión del pool (~1s en Windows).
#   - el "session.begin() + SET set_config" de `get_admin_session` antes
#     de que el probe vea siquiera el yield.
#   - contención de la lock cuando varias requests llegan a la vez al
#     dashboard (auto-refresh cada 30s lo evita; un humano machacando F5
#     puede pisarlas).
# Empezó en 2s y subió a 5s; aún así el probe de postgres se cancelaba por
# timeout en el ARRANQUE en frío de la suite completa (pool sin calentar +
# alembic recién corrido + conexiones del test anterior), dejando la sesión
# en pending-rollback y volviendo flaky test_system_health. 10s es un techo
# holgado para un SELECT 1 / ping (los probes corren en paralelo vía gather,
# así que la latencia peor-caso del dashboard sigue siendo ~10s): un probe
# que tarda más de 10s es un servicio realmente caído, no una contención.
_PROBE_TIMEOUT_S = 10.0

_logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _to_tenant_response(o: Organization) -> TenantResponse:
    return TenantResponse(
        id=o.id,
        name=o.name,
        slug=o.slug,
        is_active=o.is_active,
        created_at=o.created_at,
        updated_at=o.updated_at,
        deleted_at=o.deleted_at,
    )


async def _get_tenant_or_404(session: AsyncSession, tenant_id: UUID) -> Organization:
    result = await session.execute(select(Organization).where(Organization.id == tenant_id))
    org = result.scalar_one_or_none()
    if org is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return org


# ---------------------------------------------------------------------------
# /admin/tenants — CRUD
# ---------------------------------------------------------------------------
@router.post(
    "/tenants",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant(
    payload: TenantCreateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> TenantResponse:
    org = Organization(id=uuid7(), name=payload.name, slug=payload.slug)
    session.add(org)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="slug already exists",
        ) from exc

    await write_audit_log(
        session,
        action=AuditAction.TENANT_CREATED.value,
        actor_user_id=principal.user_id,
        tenant_id=org.id,
        resource_type="tenant",
        resource_id=org.id,
        changes={"name": payload.name, "slug": payload.slug},
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )

    await session.refresh(org)
    return _to_tenant_response(org)


@router.get("/tenants", response_model=list[TenantResponse])
async def list_tenants(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[TenantResponse]:
    result = await session.execute(
        select(Organization)
        .where(Organization.deleted_at.is_(None))
        .order_by(Organization.created_at)
    )
    return [_to_tenant_response(o) for o in result.scalars().all()]


@router.get("/tenants/{tenant_id}", response_model=TenantResponse)
async def get_tenant(
    tenant_id: UUID,
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> TenantResponse:
    org = await _get_tenant_or_404(session, tenant_id)
    return _to_tenant_response(org)


@router.put("/tenants/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdateRequest,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> TenantResponse:
    org = await _get_tenant_or_404(session, tenant_id)

    changes: dict[str, object] = {}
    if payload.name is not None and payload.name != org.name:
        changes["name"] = {"from": org.name, "to": payload.name}
        org.name = payload.name
    if payload.is_active is not None and payload.is_active != org.is_active:
        changes["is_active"] = {"from": org.is_active, "to": payload.is_active}
        org.is_active = payload.is_active

    if changes:
        await write_audit_log(
            session,
            action=AuditAction.TENANT_UPDATED.value,
            actor_user_id=principal.user_id,
            tenant_id=org.id,
            resource_type="tenant",
            resource_id=org.id,
            changes=changes,
            ip_address=get_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )

    await session.flush()
    await session.refresh(org)
    return _to_tenant_response(org)


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: UUID,
    request: Request,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> None:
    """Soft-delete: stamps `deleted_at` rather than dropping the row."""
    org = await _get_tenant_or_404(session, tenant_id)
    if org.deleted_at is not None:
        return  # already deleted — idempotent

    org.deleted_at = datetime.now(tz=UTC)

    await write_audit_log(
        session,
        action=AuditAction.TENANT_DELETED.value,
        actor_user_id=principal.user_id,
        tenant_id=org.id,
        resource_type="tenant",
        resource_id=org.id,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    await session.flush()


# ---------------------------------------------------------------------------
# /admin/users — cross-tenant listing
# ---------------------------------------------------------------------------
@router.get("/users", response_model=list[UserListItem])
async def list_users(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> list[UserListItem]:
    """Cross-tenant user listing. `users` itself is un-RLSed, but this
    endpoint is BYPASSRLS-routed for symmetry with the rest of /admin."""
    result = await session.execute(
        select(User).where(User.deleted_at.is_(None)).order_by(User.created_at)
    )
    return [
        UserListItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            is_system_admin=u.is_system_admin,
            is_active=u.is_active,
        )
        for u in result.scalars().all()
    ]


# ---------------------------------------------------------------------------
# /admin/system-health — stack summary
#
# Each probe has a 2 s ceiling so a hung service can't stall the dashboard.
# Probes run concurrently with asyncio.gather. The aggregate `status` is
# driven by postgres only (the API can't function without it). Other
# services degrade individually but don't flip the overall state — letting
# the operator see them as yellow without the dashboard going "down".
#
# Probed services (mantén alineado con `docker/docker-compose.yml`):
#   postgres, redis, vault, minio, clamav     core (Plan 00)
#   docling-serve                              Plan 04 task_04_10
#   ollama                                     Plan 04 task_04_14 (externo)
#   egress-proxy                               Plan 02 task_02_35 / ADR 0019
# ---------------------------------------------------------------------------
def _safe_detail(name: str, exc: BaseException) -> str:
    """Map a probe failure to a generic, client-safe `detail` while
    logging the full exception server-side (error-obs-logging-6).

    The /admin/system-health response is consumed by dashboards and
    monitoring; the raw exception text can leak internal topology (the
    Postgres schema/role in a permission error, the Vault URL structure,
    a clamav socket path). We surface only the failure *class* and keep
    the diagnostics in the logs, which stay behind the server boundary.
    """
    _logger.warning(
        "system_health.probe_failed",
        service=name,
        error_type=type(exc).__name__,
        error=str(exc),
    )
    # NB: in Python 3.11+ `asyncio.TimeoutError is TimeoutError`, and
    # `ConnectionError` subclasses `OSError` — the bases below cover both.
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, OSError):
        return "connection failed"
    return "probe failed"


async def _check_postgres(session: AsyncSession) -> ServiceHealth:
    try:
        await asyncio.wait_for(session.execute(text("SELECT 1")), timeout=_PROBE_TIMEOUT_S)
        return ServiceHealth(name="postgres", status="ok")
    except Exception as exc:
        # `asyncio.wait_for` cancels the underlying asyncpg query mid-flight
        # on timeout, which leaves the session/connection in a "pending
        # rollback" state. We must rollback explicitly here so the
        # `get_admin_session` dep can clean up — otherwise the bad state
        # leaks into the next request through the connection pool.
        with contextlib.suppress(Exception):
            await session.rollback()
        return ServiceHealth(name="postgres", status="down", detail=_safe_detail("postgres", exc))


async def _check_redis(redis: Redis) -> ServiceHealth:
    try:
        await asyncio.wait_for(redis.ping(), timeout=_PROBE_TIMEOUT_S)
        return ServiceHealth(name="redis", status="ok")
    except Exception as exc:
        return ServiceHealth(name="redis", status="down", detail=_safe_detail("redis", exc))


async def _check_http_ok(name: str, url: str) -> ServiceHealth:
    """Probe an HTTP endpoint. 200 -> ok; other status -> degraded; no
    response -> down. Used for vault (/v1/sys/health) and minio
    (/minio/health/live)."""
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
            r = await client.get(url)
        if r.status_code == 200:
            return ServiceHealth(name=name, status="ok")
        # HTTP status codes are not sensitive — they're a coarse, public
        # signal of upstream health, unlike raw exception text.
        return ServiceHealth(name=name, status="degraded", detail=f"HTTP {r.status_code}")
    except Exception as exc:
        return ServiceHealth(name=name, status="down", detail=_safe_detail(name, exc))


async def _check_tcp(name: str, host: str, port: int) -> ServiceHealth:
    """TCP connect probe -- enough to know the daemon is accepting
    connections. Used for clamav, whose protocol is not HTTP."""
    writer: asyncio.StreamWriter | None = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=_PROBE_TIMEOUT_S
        )
        return ServiceHealth(name=name, status="ok")
    except Exception as exc:
        return ServiceHealth(name=name, status="down", detail=_safe_detail(name, exc))
    finally:
        if writer is not None:
            writer.close()
            with contextlib.suppress(Exception):  # best-effort close
                await writer.wait_closed()


@router.get("/system-health", response_model=SystemHealthResponse)
async def system_health(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    redis: Redis = Depends(get_redis),
) -> SystemHealthResponse:
    settings = get_settings()
    (
        postgres,
        redis_h,
        vault_h,
        minio_h,
        clamav_h,
        docling_h,
        ollama_h,
        egress_h,
    ) = await asyncio.gather(
        _check_postgres(session),
        _check_redis(redis),
        _check_http_ok("vault", f"{settings.vault_url}/v1/sys/health"),
        _check_http_ok("minio", f"{settings.minio_url}/minio/health/live"),
        _check_tcp("clamav", settings.clamav_host, settings.clamav_port),
        # docling-serve expone /health (200 cuando el parser está listo).
        _check_http_ok("docling-serve", f"{settings.docling_serve_url}/health"),
        # Ollama responde a /api/version (200 con su build info).
        _check_http_ok("ollama", f"{settings.ollama_url}/api/version"),
        # tinyproxy no es un servidor HTTP convencional; basta confirmar
        # que el daemon acepta conexiones — mismo patrón que clamav.
        _check_tcp("egress-proxy", settings.egress_proxy_host, settings.egress_proxy_port),
    )
    overall = "ok" if postgres.status == "ok" else "down"
    return SystemHealthResponse(
        status=overall,
        services=[
            postgres,
            redis_h,
            vault_h,
            minio_h,
            clamav_h,
            docling_h,
            ollama_h,
            egress_h,
        ],
    )
