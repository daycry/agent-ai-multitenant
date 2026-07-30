"""FastAPI application entry point.

Phase 0 ships a minimal app with a single tenant-scoped endpoint
(/me/memberships) so the multi-tenant middleware (and the PostgreSQL
RLS policies it sets up) can be end-to-end tested in
tests/integration/test_isolation.py.

Auth and admin routers arrive in tasks 00_10 and 00_11.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.admin_hardening import require_hardened_system_admin
from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.config import get_settings
from api_server.db.models import Organization, User, UserOrganizationMembership
from api_server.db.session import get_admin_sessionmaker
from api_server.logging import configure_logging, get_logger
from api_server.logging.context import REQUEST_ID_HEADER, RequestContextMiddleware
from api_server.middleware.security_headers import SecurityHeadersMiddleware
from api_server.routers.admin import router as admin_router
from api_server.routers.agents import router as agents_router
from api_server.routers.api_tokens import router as api_tokens_router
from api_server.routers.api_v1 import api_v1_docs_router, api_v1_router
from api_server.routers.approval_policies import router as approval_policies_router
from api_server.routers.approvals import router as approvals_router
from api_server.routers.assistant import router as assistant_router
from api_server.routers.assistant_voice import router as assistant_voice_router
from api_server.routers.auth import router as auth_router
from api_server.routers.backup import router as backup_router
from api_server.routers.budget_pause import router as budget_pause_router
from api_server.routers.conversations import (
    chat_modes_router,
    conversations_router,
    project_conversations_router,
    project_planning_roles_router,
)
from api_server.routers.copilot_device_flow import (
    admin_router as copilot_device_flow_admin_router,
)
from api_server.routers.cortex import router as cortex_router
from api_server.routers.cortex_mind import router as cortex_mind_router
from api_server.routers.cortex_voice import router as cortex_voice_router
from api_server.routers.cortex_ws import router as cortex_ws_router
from api_server.routers.cross_tenant_stats import router as cross_tenant_stats_router
from api_server.routers.dep_cache import router as dep_cache_router
from api_server.routers.docs_viewer import router as docs_viewer_router
from api_server.routers.embeddings import admin_router as embeddings_admin_router
from api_server.routers.eval_quality import router as eval_quality_router
from api_server.routers.evals import router as evals_router
from api_server.routers.executions import router as executions_router
from api_server.routers.guardrail_alerts import router as guardrail_alerts_router
from api_server.routers.guardrail_events import router as guardrail_events_router
from api_server.routers.health import router as health_router
from api_server.routers.human_agents import router as human_agents_router
from api_server.routers.human_inbox import router as human_inbox_router
from api_server.routers.human_queue import router as human_queue_router
from api_server.routers.incoming_webhook_configs import router as incoming_webhook_configs_router
from api_server.routers.incoming_webhooks import router as incoming_webhooks_router
from api_server.routers.internal_agent import router as internal_agent_router
from api_server.routers.internal_alerts import router as internal_alerts_router
from api_server.routers.kb_categories import router as kb_categories_router
from api_server.routers.knowledge_bases import (
    documents_router,
    project_kb_router,
)
from api_server.routers.knowledge_bases import (
    router as knowledge_bases_router,
)
from api_server.routers.llm_providers import admin_router as llm_providers_admin_router
from api_server.routers.marketplace import admin_router as marketplace_admin_router
from api_server.routers.marketplace import router as marketplace_router
from api_server.routers.mcp import router as mcp_router
from api_server.routers.mcp_catalog import router as mcp_catalog_router
from api_server.routers.mcp_oauth import router as mcp_oauth_router
from api_server.routers.memories import router as memories_router
from api_server.routers.mfa import router as mfa_router
from api_server.routers.model_prices import admin_router as model_prices_admin_router
from api_server.routers.model_prices import router as model_prices_router
from api_server.routers.notifications import router as notifications_router
from api_server.routers.ollama import admin_router as ollama_admin_router
from api_server.routers.plans import plans_router, project_plans_router
from api_server.routers.platform_settings import admin_router as platform_settings_admin_router
from api_server.routers.projects import router as projects_router
from api_server.routers.review import router as review_router
from api_server.routers.runs import router as runs_router
from api_server.routers.runtimes import router as runtime_templates_router
from api_server.routers.scim import router as scim_router
from api_server.routers.skills import router as skills_router
from api_server.routers.sso import discovery_router as sso_discovery_router
from api_server.routers.sso import router as sso_router
from api_server.routers.task_lifecycle import router as task_lifecycle_router
from api_server.routers.tasks import router as tasks_router
from api_server.routers.teams import router as teams_router
from api_server.routers.tenant_settings import router as tenant_settings_router
from api_server.routers.tenant_stats import router as tenant_stats_router
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

_logger = get_logger(__name__)

# CORS allow-lists (secrets-config-4). The pre-Plan-06.14 config used the
# wildcard `["*"]` for both methods and headers together with
# `allow_credentials=True`. That combination is footgun-adjacent: with
# credentials the browser refuses the literal `*` and Starlette has to
# reflect the request's Access-Control-Request-* values back, which
# effectively turns the allow-list off. We pin the methods this API
# actually serves and the headers the admin-panel actually sends so the
# preflight response is an explicit, auditable contract rather than a
# reflect-everything wildcard.
_CORS_ALLOW_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
_CORS_ALLOW_HEADERS = ["Authorization", "Content-Type", "X-Tenant-Id", "X-Request-ID"]


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled route exceptions (error-obs-logging-5).

    Logs the full traceback server-side under the request's correlation
    context and returns a generic 500 — the exception type, message and
    stack never reach the client.

    The contextvars bound by `RequestContextMiddleware` have already been
    cleared by the time this handler runs (the exception unwinds through
    that middleware's `finally` before Starlette's outer
    ServerErrorMiddleware dispatches here), so we re-read the correlation
    ids stashed on `request.state` and pass them explicitly to keep the
    log line traceable.

    Note: FastAPI/Starlette dispatch HTTPException and
    RequestValidationError to their own handlers, so this only fires on
    genuinely unexpected errors (programming bugs, driver faults).
    """
    state = request.state
    request_id = getattr(state, "request_id", None)
    _logger.error(
        "api.unhandled_exception",
        method=request.method,
        path=request.url.path,
        request_id=request_id,
        user_id=(str(uid) if (uid := getattr(state, "log_user_id", None)) else None),
        tenant_id=(str(tid) if (tid := getattr(state, "log_tenant_id", None)) else None),
        exc_info=exc,
    )
    # Echo the correlation id on the error response too. The
    # RequestContextMiddleware's send-wrapper never ran (the route raised
    # before emitting a response), so the header is set here directly so
    # the client can quote it in a bug report.
    headers = {REQUEST_ID_HEADER: request_id} if request_id else None
    return JSONResponse(
        status_code=500,
        content={"detail": "internal server error"},
        headers=headers,
    )


# Every path under this prefix is the System-Admin surface and MUST carry the
# admin hardening gate (MFA + IP allowlist + short session, staging/prod only).
_ADMIN_PREFIX = "/admin"


def _is_admin_surface(router: APIRouter) -> bool:
    """True iff EVERY route of ``router`` lives under ``/admin`` (authz-1).

    The mount in :func:`_register_routers` uses this to attach
    :func:`require_hardened_system_admin` automatically, so a NEW admin router
    is hardened by the mere fact of being mounted — nobody has to remember the
    dependency (the historic failure mode: 9 of the 10 ``/admin/*`` routers,
    ``/admin/backup`` among them with its destructive restore, shipped without
    it).

    A router that MIXES admin and non-admin paths is a wiring error we refuse
    to guess about: hardening it would 403 the tenant routes, and not hardening
    it would leave the admin routes open. It raises at import time — loudly, at
    startup — instead of silently picking either failure.
    """
    paths = [str(getattr(route, "path", "")) for route in router.routes]
    admin = [p for p in paths if p == _ADMIN_PREFIX or p.startswith(f"{_ADMIN_PREFIX}/")]
    if not admin:
        return False
    if len(admin) != len(paths):
        non_admin = sorted(set(paths) - set(admin))
        raise RuntimeError(
            "router mixes /admin paths with non-admin paths, so the admin "
            f"hardening gate cannot be applied at mount time: {non_admin}. "
            "Split it into an admin router and a tenant router."
        )
    return True


def _register_routers(app: FastAPI) -> None:
    """Mount every API router on ``app``.

    Extracted from :func:`create_app` so the latter stays under the
    statement-count lint threshold as routers keep being added (the list
    only grows). Order is not significant — FastAPI matches by path — so
    new routers can be appended freely.

    Routers whose whole surface lives under ``/admin`` are mounted WITH the
    hardening dependency (:func:`_is_admin_surface`), so the System-Admin
    surface cannot regress by omission. ``tests/integration/
    test_admin_hardening_surface.py`` is the contract test that fails if any
    mounted ``/admin`` route ends up without the gate.
    """
    for router in (
        auth_router,
        sso_router,
        sso_discovery_router,
        scim_router,
        mfa_router,
        admin_router,
        agents_router,
        human_agents_router,
        human_inbox_router,
        api_tokens_router,
        api_v1_router,
        api_v1_docs_router,
        skills_router,
        tools_router,
        teams_router,
        projects_router,
        tasks_router,
        task_lifecycle_router,
        review_router,
        approval_policies_router,
        approvals_router,
        executions_router,
        runs_router,
        human_queue_router,
        evals_router,
        eval_quality_router,
        tenant_stats_router,
        cross_tenant_stats_router,
        internal_agent_router,
        internal_alerts_router,
        project_conversations_router,
        conversations_router,
        chat_modes_router,
        project_planning_roles_router,
        project_plans_router,
        plans_router,
        memories_router,
        knowledge_bases_router,
        kb_categories_router,
        project_kb_router,
        marketplace_router,
        marketplace_admin_router,
        model_prices_router,
        model_prices_admin_router,
        llm_providers_admin_router,
        embeddings_admin_router,
        ollama_admin_router,
        platform_settings_admin_router,
        copilot_device_flow_admin_router,
        mcp_router,
        mcp_catalog_router,
        mcp_oauth_router,
        runtime_templates_router,
        tools_diagnostic_router,
        dep_cache_router,
        docs_viewer_router,
        documents_router,
        tenant_settings_router,
        notifications_router,
        guardrail_events_router,
        guardrail_alerts_router,
        incoming_webhooks_router,
        incoming_webhook_configs_router,
        assistant_router,
        assistant_voice_router,
        cortex_router,
        cortex_mind_router,
        cortex_voice_router,
        cortex_ws_router,
        backup_router,
        budget_pause_router,
        ws_router,
    ):
        if _is_admin_surface(router):
            app.include_router(router, dependencies=[Depends(require_hardened_system_admin)])
        else:
            app.include_router(router)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001 — firma de FastAPI
    """Arranque y parada de la app (sustituye a los `@app.on_event("startup")`).

    FastAPI deprecó `on_event` en favor de `lifespan`, y el aviso salía 33 veces en
    cada corrida de integración. La conversión NO es cosmética: con `on_event` los
    handlers eran dos funciones independientes cuyo orden dependía del registro;
    aquí el orden es explícito y legible.

    Las dos son **best-effort por diseño**: ninguna puede impedir que el
    api-server arranque. Un catálogo builtin incompleto o un barrido de chat que
    falle son degradaciones, no motivos para dejar la plataforma caída — y si
    reventaran aquí, el contenedor entraría en bucle de reinicio sin decir por qué.
    """
    await _ensure_builtin_catalog()
    await _resume_chat_replies()
    yield


async def _ensure_builtin_catalog() -> None:
    # G-02 (auditoría proyecto 2026-07-17): garantiza las filas del catálogo
    # builtin de KBs si un wipe/reset las dejó a 0 (sin ellas los grants de
    # plantilla apuntan a KBs inexistentes y el auto-RAG queda estéril).
    # Idempotente + advisory lock; best-effort (nunca impide arrancar).
    try:
        from api_server.db.session import get_admin_sessionmaker
        from api_server.seeds.startup import ensure_builtin_catalog

        result = await ensure_builtin_catalog(get_admin_sessionmaker())
        if result["seeded"]:
            _logger.warning("startup.builtin_catalog_reseeded", **result)
    except Exception:
        _logger.warning("startup.builtin_catalog_check_failed", exc_info=True)


async def _resume_chat_replies() -> None:
    # c9 (audit 2026-07-03): the team chat reply runs as an in-process detached
    # task, so a restart mid-turn drops it while the user's message stays durable.
    # Resume those unanswered turns on boot. Best-effort — a chat-durability sweep
    # must never stop the api-server from starting.
    try:
        from api_server.auth.deps import get_redis
        from api_server.chat.responder import resume_pending_replies
        from api_server.routers.llm_providers import get_provider_vault_store

        resumed = await resume_pending_replies(vault=get_provider_vault_store(), redis=get_redis())
        if resumed:
            _logger.info("chat.resumed_on_startup", count=resumed)
    except Exception:
        _logger.warning("chat.resume_on_startup_failed", exc_info=True)


def create_app() -> FastAPI:
    settings = get_settings()

    # `/docs` + `/openapi.json` are withdrawn outside dev (task_prod09_14,
    # api-7): the full internal schema — `/admin/*`, `/internal/agent/*`, every
    # tenant route — used to be served unauthenticated. Withdrawing the UI alone
    # would be theatre, since `/openapi.json` IS the map, so BOTH go. The public
    # `/api/v1` contract is a separate curated document and stays published.
    docs_published = settings.api_docs_published
    app = FastAPI(
        title="agentic-platform / api-server",
        version="0.0.0",
        docs_url="/docs" if docs_published else None,
        openapi_url="/openapi.json" if docs_published else None,
        redoc_url=None,
        lifespan=_lifespan,
    )

    # Request-context middleware binds request_id (+ user_id/tenant_id) to
    # every log line via structlog contextvars and echoes X-Request-ID back
    # (error-obs-logging-1). Added BEFORE CORS so CORS ends up the outermost
    # layer — its headers wrap even the generic 500 emitted by the global
    # exception handler below.
    app.add_middleware(RequestContextMiddleware)
    # Baseline response headers (task_prod09_14, api-7): nosniff, frame-deny,
    # Referrer-Policy and — only over TLS, only outside dev — HSTS. Added AFTER
    # RequestContextMiddleware and BEFORE CORS so it ends up between them: it must
    # wrap every response including the generic 500 the global handler emits, and
    # CORS must stay outermost so its own headers are never shadowed.
    app.add_middleware(SecurityHeadersMiddleware, environment=settings.environment)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=_CORS_ALLOW_METHODS,
        allow_headers=_CORS_ALLOW_HEADERS,
    )

    # Global catch-all so an unhandled error never leaks the stack to the
    # client (error-obs-logging-5). Defined at module scope and registered
    # here.
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    _register_routers(app)

    instrument_fastapi(app)

    # `/healthz` (liveness) y `/readyz` (readiness) viven juntos en
    # `routers/health.py` desde `task_audit14_08`: son un PAR y la asimetría entre
    # ellos —liveness no toca dependencias externas, readiness sí— sólo se
    # entiende leyéndolos a la vez. El contrato de `/healthz` no cambió.
    app.include_router(health_router)

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
                    "is_system_owner": principal.is_system_owner,
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
            # Córtex F0 (ADR 0074): el frontend puede gatear la futura UI del córtex.
            "is_system_owner": bool(user.is_system_owner),
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
