"""FastAPI application entry point.

Phase 0 ships a minimal app with a single tenant-scoped endpoint
(/me/memberships) so the multi-tenant middleware (and the PostgreSQL
RLS policies it sets up) can be end-to-end tested in
tests/integration/test_isolation.py.

Auth and admin routers arrive in tasks 00_10 and 00_11.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.db.models import UserOrganizationMembership
from api_server.routers.admin import router as admin_router
from api_server.routers.auth import router as auth_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="agentic-platform / api-server",
        version="0.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    app.include_router(auth_router)
    app.include_router(admin_router)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me", response_model=None)
    async def me(principal: AuthPrincipal = Depends(get_principal)) -> dict[str, Any]:
        return {
            "user_id": str(principal.user_id),
            "tenant_id": str(principal.tenant_id) if principal.tenant_id else None,
        }

    @app.get("/me/memberships", response_model=None)
    async def my_memberships(
        session: AsyncSession = Depends(get_tenant_session),
    ) -> list[dict[str, Any]]:
        """Return memberships visible under current RLS scope.

        RLS policy `membership_tenant_isolation` filters to rows whose
        `tenant_id` matches the session's `app.tenant_id`. A user with
        a JWT for tenant A cannot see tenant B's rows even by guessing
        their UUIDs — the database itself refuses to return them.
        """
        result = await session.execute(
            select(
                UserOrganizationMembership.id,
                UserOrganizationMembership.tenant_id,
                UserOrganizationMembership.user_id,
                UserOrganizationMembership.role,
            )
        )
        return [
            {
                "id": str(row.id),
                "tenant_id": str(row.tenant_id),
                "user_id": str(row.user_id),
                "role": row.role,
            }
            for row in result.all()
        ]

    return app


app = create_app()
