"""FastAPI application entry point.

Phase 0 ships a minimal app with a single tenant-scoped endpoint
(/me/memberships) so the multi-tenant middleware (and the PostgreSQL
RLS policies it sets up) can be end-to-end tested in
tests/integration/test_isolation.py.

Auth and admin routers arrive in tasks 00_10 and 00_11.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.config import get_settings
from api_server.db.models import Organization, User, UserOrganizationMembership
from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging
from api_server.routers.admin import router as admin_router
from api_server.routers.agents import router as agents_router
from api_server.routers.approval_policies import router as approval_policies_router
from api_server.routers.approvals import router as approvals_router
from api_server.routers.auth import router as auth_router
from api_server.routers.conversations import (
    conversations_router,
    project_conversations_router,
)
from api_server.routers.dep_cache import router as dep_cache_router
from api_server.routers.executions import router as executions_router
from api_server.routers.internal_agent import router as internal_agent_router
from api_server.routers.knowledge_bases import (
    documents_router,
    project_kb_router,
)
from api_server.routers.knowledge_bases import (
    router as knowledge_bases_router,
)
from api_server.routers.mcp import router as mcp_router
from api_server.routers.mcp_catalog import router as mcp_catalog_router
from api_server.routers.memories import router as memories_router
from api_server.routers.plans import plans_router, project_plans_router
from api_server.routers.projects import router as projects_router
from api_server.routers.review import router as review_router
from api_server.routers.skills import router as skills_router
from api_server.routers.task_lifecycle import router as task_lifecycle_router
from api_server.routers.tasks import router as tasks_router
from api_server.routers.teams import router as teams_router
from api_server.routers.tenant_settings import router as tenant_settings_router
from api_server.routers.tools import router as tools_router
from api_server.routers.tools_diagnostic import router as tools_diagnostic_router
from api_server.routers.ws import router as ws_router
from api_server.telemetry import configure_tracing, instrument_fastapi
from api_server.telemetry.setup import add_console_exporter

configure_tracing(service_name="api-server")
# Console exporter es opt-in vía env var. Spamea stdout con JSON de
# spans, lo que en Windows + PowerShell rompe el wrapping del proceso
# (cada línea se trata como NativeCommandError y termina matando
# uvicorn). En dev queda OFF; en prod / Tempo se sustituirá por OTLP.
if os.environ.get("API_SERVER_OTEL_CONSOLE") == "1":
    add_console_exporter()
configure_logging(service="api-server")


def create_app() -> FastAPI:
    app = FastAPI(
        title="agentic-platform / api-server",
        version="0.0.0",
        docs_url="/docs",
        redoc_url=None,
    )

    settings = get_settings()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(agents_router)
    app.include_router(skills_router)
    app.include_router(tools_router)
    app.include_router(teams_router)
    app.include_router(projects_router)
    app.include_router(tasks_router)
    app.include_router(task_lifecycle_router)
    app.include_router(review_router)
    app.include_router(approval_policies_router)
    app.include_router(approvals_router)
    app.include_router(executions_router)
    app.include_router(internal_agent_router)
    app.include_router(project_conversations_router)
    app.include_router(conversations_router)
    app.include_router(project_plans_router)
    app.include_router(plans_router)
    app.include_router(memories_router)
    app.include_router(knowledge_bases_router)
    app.include_router(project_kb_router)
    app.include_router(mcp_router)
    app.include_router(mcp_catalog_router)
    app.include_router(tools_diagnostic_router)
    app.include_router(dep_cache_router)
    app.include_router(documents_router)
    app.include_router(tenant_settings_router)
    app.include_router(ws_router)

    instrument_fastapi(app)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me", response_model=None)
    async def me(
        principal: AuthPrincipal = Depends(get_principal),
    ) -> dict[str, Any]:
        """Return the current user's profile + all memberships across
        the tenants they belong to (Plan 06.8 task_06_8_04).

        The UI consumes this on load to know which buttons to show
        (`isTenantAdmin`, `isSystemAdmin`) and which tenants the
        tenant-picker should offer. `active_tenant_id` comes from the
        JWT's `tid` claim (or the `X-Tenant-Id` superadmin override).

        Implementation note: uses the BYPASSRLS admin sessionmaker so a
        user with active tenant A also sees their tenant B membership
        — the RLS policy on `user_org_memberships` filters by
        `tenant_id`, which would hide cross-tenant rows. Safety is
        preserved by constraining the query to `user_id =
        principal.user_id`.
        """
        sessionmaker = get_admin_sessionmaker()
        async with sessionmaker() as session:
            user = await session.get(User, principal.user_id)
            if user is None:
                # Theoretically impossible: get_principal validated the
                # JWT against an active Redis session. Treat as a stale
                # token.
                return {
                    "user_id": str(principal.user_id),
                    "email": None,
                    "full_name": None,
                    "is_system_admin": principal.is_system_admin,
                    "memberships": [],
                    "active_tenant_id": (
                        str(principal.tenant_id) if principal.tenant_id is not None else None
                    ),
                }

            membership_q = await session.execute(
                select(
                    UserOrganizationMembership.tenant_id,
                    UserOrganizationMembership.role,
                    UserOrganizationMembership.is_active,
                    Organization.name.label("tenant_name"),
                )
                .join(
                    Organization,
                    Organization.id == UserOrganizationMembership.tenant_id,
                )
                .where(
                    UserOrganizationMembership.user_id == principal.user_id,
                    UserOrganizationMembership.deleted_at.is_(None),
                )
                .order_by(Organization.name)
            )
            memberships = [
                {
                    "tenant_id": str(row.tenant_id),
                    "tenant_name": row.tenant_name,
                    "role": row.role,
                    "is_active": bool(row.is_active),
                }
                for row in membership_q.all()
            ]

        return {
            "user_id": str(principal.user_id),
            "email": user.email,
            "full_name": user.full_name,
            "is_system_admin": bool(user.is_system_admin),
            "memberships": memberships,
            "active_tenant_id": (
                str(principal.tenant_id) if principal.tenant_id is not None else None
            ),
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
