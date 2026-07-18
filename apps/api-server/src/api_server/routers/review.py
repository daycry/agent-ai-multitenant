"""`/review/{session_id}` endpoints (Plan 06.5 Fase B).

Three entry points the reviewer's browser hits, all gated by the HMAC
signature in the URL (`?exp=...&sig=...`):

  * `GET /review/{session_id}`           — task_06_5_10 (SPA shell HTML)
  * `POST /review/{session_id}/rerun`    — task_06_5_08
  * `WS  /ws/review/{session_id}/logs`   — task_06_5_09

The signed URL is minted by `workers.review_runtime.sign_review_url`
when the review-runtime spawns. The HMAC secret lives in
`Settings.review_url_signing_secret`. Verification is constant-time.

These endpoints DO NOT require a JWT — the URL signature is the
single auth factor. That's intentional: the human reviewer is invited
via email / Slack / whatever and may not have a platform account.
Tenant scope comes from the `ReviewSession` row's `tenant_id`, looked
up via the (BYPASSRLS) admin engine since the caller doesn't carry
tenant context.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Literal
from uuid import UUID

import httpx
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select
from workers.review_runtime import sign_review_url, verify_review_url

from api_server.auth.deps import get_redis
from api_server.celery_client import enqueue_event_dispatch, enqueue_open_plan_pr
from api_server.chat.plan_state_machine import transition_plan_status
from api_server.config import get_settings
from api_server.db.domain import Plan
from api_server.db.models import ReviewSession as ReviewSessionRow
from api_server.db.review_session_repo import (
    mark_other_plan_sessions_terminal,
    mark_rerun_requested,
    mark_terminal,
    touch_activity,
)
from api_server.db.session import get_admin_sessionmaker

router = APIRouter(tags=["review"])

# How many seconds the WS waits for new log lines before sending a
# keepalive ping. Long enough to not spam over the wire, short enough
# that proxies don't drop the connection as idle.
_WS_KEEPALIVE_S = 25.0
# Redis channel name the worker publishes container stdout into.
# Cabling the worker side is Plan 06.5 Fase F (task_06_5_16/17).
_LOGS_CHANNEL_FMT = "review:logs:{session_id}"


def _verify_signature(session_id: UUID, exp: int, sig: str) -> None:
    """Raise 403 if the URL signature doesn't validate."""
    secret = get_settings().review_url_signing_secret.get_secret_value().encode()
    if not verify_review_url(
        session_id=str(session_id),
        expires_at=float(exp),
        sig=sig,
        secret=secret,
    ):
        raise HTTPException(status_code=403, detail="invalid or expired review URL")


async def _load_session_cross_tenant(session_id: UUID) -> ReviewSessionRow | None:
    """Fetch the review session via the admin (BYPASSRLS) engine.

    The signed URL is the auth — the caller doesn't carry a tenant
    context, so we can't use the regular `get_tenant_session`. RLS is
    bypassed here on purpose, but the function is gated by the HMAC
    check upstream.
    """
    sm = get_admin_sessionmaker()
    async with sm() as db, db.begin():
        result = await db.execute(
            select(ReviewSessionRow).where(
                ReviewSessionRow.id == session_id,
                ReviewSessionRow.deleted_at.is_(None),
            )
        )
        return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# task_06_5_10 — GET /review/{session_id} SPA shell
# ---------------------------------------------------------------------------


_SPA_HTML = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Review session — {session_id}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 0; background: #0b1020; color: #d6deff; }}
    header {{ padding: 1rem 1.5rem; border-bottom: 1px solid #1d2742; }}
    main {{ padding: 1.5rem; }}
    code {{ background: #1d2742; padding: 0.1em 0.3em; border-radius: 4px; }}
    .placeholder {{ color: #8a96c2; font-style: italic; }}
  </style>
</head>
<body>
  <header>
    <h1 style="margin:0;font-size:1.05rem;">Review runtime</h1>
    <p style="margin:0.25em 0 0 0;font-size:0.85rem;color:#8a96c2;">
      session <code>{session_id}</code>
    </p>
  </header>
  <main>
    {app_note}
    <p class="placeholder">
      Esta es la shell del SPA de review. El asset bundle (Plan 06
      task_06_29) montara aqui los 4 paneles (terminal / logs WS /
      rerun btn / checklist) cuando el build de Vite este cableado.
      De momento esta pagina solo confirma que la URL firmada es
      valida y la sesion existe.
    </p>
    <ul>
      <li>WS de logs: <code>/ws/review/{session_id}/logs?exp=...&sig=...</code></li>
      <li>Rerun: <code>POST /review/{session_id}/rerun?exp=...&sig=...</code></li>
    </ul>
  </main>
</body>
</html>
"""


@router.get("/review/{session_id}", response_class=HTMLResponse)
async def serve_review_spa(
    session_id: UUID,
    exp: int = Query(..., description="Unix expiry timestamp baked into the signed URL."),
    sig: str = Query(..., description="HMAC-SHA256 over `session_id|exp`, base64-urlsafe."),
) -> HTMLResponse:
    """Serve the review SPA shell.

    The HTML is intentionally minimal — the real app (panels +
    react-query) lives in `apps/admin-panel` and is mounted at
    `/admin/review/{id}`. This endpoint is the public-facing one the
    human reviewer hits via the signed URL.
    """
    _verify_signature(session_id, exp, sig)
    row = await _load_session_cross_tenant(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="review session not found")
    if row.status not in {"running", "suspended"}:
        raise HTTPException(status_code=410, detail=f"review session is {row.status}")

    # Touch activity — extends the idle window so the suspend sweep
    # doesn't catch the session while the human is actively reviewing.
    sm = get_admin_sessionmaker()
    async with sm() as db, db.begin():
        await touch_activity(db, session_id)

    # hallazgo #4: si el proyecto no tiene app-preview configurada, dilo aquí en
    # claro (la checklist y el veredicto funcionan igual sin ella).
    app_note = ""
    if (row.spec or {}).get("app_configured") is False:
        app_note = (
            '<p style="border:1px solid #7a5c1e;background:#2a2410;color:#e8c96a;'
            'padding:0.75rem 1rem;border-radius:6px;">'
            "Este proyecto no tiene app-preview configurada: define "
            "<code>repository_config.review_image</code> (una imagen construida y "
            "publicada por la CI del propio proyecto) en los ajustes del proyecto. "
            "La checklist y el veredicto de esta sesi&oacute;n funcionan sin ella.</p>"
        )
    return HTMLResponse(content=_SPA_HTML.format(session_id=session_id, app_note=app_note))


# ---------------------------------------------------------------------------
# task_06_5_08 — POST /review/{session_id}/rerun
# ---------------------------------------------------------------------------


@router.post("/review/{session_id}/rerun")
async def request_rerun(
    session_id: UUID,
    exp: int = Query(...),
    sig: str = Query(...),
) -> dict[str, object]:
    """Idempotent: marks the session as `rerun_requested=True`.

    The worker picks this flag up on its next sweep (Plan 06.5
    Fase C, `compose_review_runtime`) and re-runs the test-runtime
    inside the existing review-runtime container.
    """
    _verify_signature(session_id, exp, sig)
    sm = get_admin_sessionmaker()
    async with sm() as db, db.begin():
        row = await mark_rerun_requested(db, session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="review session not found")
    return {
        "session_id": str(session_id),
        "rerun_requested": True,
        "status": row.status,
    }


# ---------------------------------------------------------------------------
# task_06_5_09 — WS /ws/review/{session_id}/logs
# ---------------------------------------------------------------------------


@router.websocket("/ws/review/{session_id}/logs")
async def review_logs_websocket(
    websocket: WebSocket,
    session_id: UUID,
    exp: int = Query(...),
    sig: str = Query(...),
    redis: Redis = Depends(get_redis),
) -> None:
    """Stream container stdout to the reviewer's browser.

    Implementation: Redis pub/sub. The worker that spawns the
    review-runtime is responsible for piping `docker logs -f` into
    the channel `review:logs:{session_id}` — that cabling lives in
    Plan 06.5 Fase F (task_06_5_17). This endpoint subscribes and
    forwards. Until the worker side is cabled, the WS connects, sends
    one informational line ("waiting for worker to pipe logs…") and
    holds the connection open with periodic keepalive pings.

    Auth: HMAC signature is verified BEFORE `accept()` so unauthorized
    clients never see the upgrade succeed.
    """
    # FastAPI runs WS exceptions differently: we close with a 4xxx code
    # instead of returning an HTTP response.
    try:
        _verify_signature(session_id, exp, sig)
    except HTTPException:
        await websocket.close(code=4403, reason="invalid or expired review URL")
        return

    row = await _load_session_cross_tenant(session_id)
    if row is None:
        await websocket.close(code=4404, reason="review session not found")
        return

    await websocket.accept()

    channel_name = _LOGS_CHANNEL_FMT.format(session_id=session_id)
    pubsub = redis.pubsub()
    await pubsub.subscribe(channel_name)

    try:
        await websocket.send_text(f"# subscribed to {channel_name} — esperando logs del worker…")
        while True:
            try:
                # 1s poll on Redis + keepalive every _WS_KEEPALIVE_S to
                # keep proxies happy.
                message = await asyncio.wait_for(
                    pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                    timeout=_WS_KEEPALIVE_S,
                )
            except TimeoutError:
                await websocket.send_text("# keepalive")
                continue
            if message is None:
                continue
            data = message.get("data")
            if data is None:
                continue
            if isinstance(data, bytes):
                data = data.decode("utf-8", errors="replace")
            await websocket.send_text(str(data))
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel_name)
        await pubsub.aclose()  # type: ignore[no-untyped-call]


# ---------------------------------------------------------------------------
# ADR 0062 — signed reviewer URLs (SPA + app preview) for a session.
# ---------------------------------------------------------------------------


def build_review_urls(session_id: UUID | str, expires_at_unix: float) -> dict[str, str]:
    """Mint the reviewer-facing signed URLs for a session (ADR 0062).

    Returns ``{"review_url", "app_url"}``: the SPA shell URL and the app-preview
    base URL. Both carry the SAME HMAC ``?exp=&sig=`` (the signature is over
    ``session_id|exp``), so the app proxy reuses the session's signature.
    """
    settings = get_settings()
    secret = settings.review_url_signing_secret.get_secret_value().encode()
    review_url = sign_review_url(
        base_url=settings.review_public_base_url,
        session_id=str(session_id),
        expires_at=expires_at_unix,
        secret=secret,
    )
    query = review_url.split("?", 1)[1] if "?" in review_url else ""
    base = settings.review_public_base_url.rstrip("/")
    app_url = f"{base}/review/{session_id}/app/?{query}"
    verdict_url = f"{base}/review/{session_id}/verdict?{query}"
    return {"review_url": review_url, "app_url": app_url, "verdict_url": verdict_url}


def _session_json(row: ReviewSessionRow) -> dict[str, Any]:
    """Public JSON view of a review session (no secrets)."""
    spec = row.spec or {}
    return {
        "id": str(row.id),
        "plan_id": str(row.plan_id),
        "status": row.status,
        "verdict": row.verdict,
        "rejection_reason": row.rejection_reason,
        "rerun_requested": row.rerun_requested,
        "checklist": spec.get("human_checklist", []),
        # Relative path the SPA hits for the live app (same signature carried in
        # the page URL the browser already has).
        "app_path": f"/review/{row.id}/app/",
        # hallazgo #4: false = el proyecto no pineó imagen y NO hay contenedor;
        # el SPA muestra el aviso en vez de un iframe roto. Ausente en sesiones
        # legacy → true (comportamiento anterior).
        "app_configured": bool(spec.get("app_configured", True)),
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
    }


@router.get("/review/{session_id}/session.json")
async def review_session_json(
    session_id: UUID,
    exp: int = Query(...),
    sig: str = Query(...),
) -> dict[str, Any]:
    """JSON metadata for the review SPA (checklist, status, app path).

    HMAC-gated like the rest of the reviewer surface. The SPA fetches this
    instead of scraping the HTML shell.
    """
    _verify_signature(session_id, exp, sig)
    row = await _load_session_cross_tenant(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="review session not found")
    return _session_json(row)


# ---------------------------------------------------------------------------
# ADR 0062 — preview proxy: the api-server reverse-proxies the running app.
# ---------------------------------------------------------------------------

# Hop-by-hop headers MUST NOT be forwarded across a proxy (RFC 7230 §6.1).
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "content-encoding",
}


def _proxy_target(row: ReviewSessionRow) -> tuple[str, int]:
    """Where the review-runtime serves the app. The container lives on the
    internal ``agentic-agents`` network addressed by a deterministic name
    (``agentic-review-{id}``); the worker records it in ``spec.main_host``.
    Never published to the host (ADR 0062 / zero-trust)."""
    spec = row.spec or {}
    host = str(spec.get("main_host") or f"agentic-review-{row.id}")
    port = int(spec.get("main_port", 8080))
    return host, port


@router.api_route(
    "/review/{session_id}/app/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy_review_app(
    session_id: UUID,
    path: str,
    request: Request,
    exp: int = Query(...),
    sig: str = Query(...),
) -> Response:
    """Reverse-proxy the reviewer's browser to the running app (ADR 0062).

    HMAC-gated by the same signed URL as the session. The app is reachable ONLY
    through here — never directly (zero-trust). HTTP request/response only; the
    app's own WebSocket traffic is out of scope for v1.
    """
    _verify_signature(session_id, exp, sig)
    row = await _load_session_cross_tenant(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="review session not found")
    if row.status not in {"running", "suspended"}:
        raise HTTPException(status_code=410, detail=f"review session is {row.status}")
    # hallazgo #4: sin imagen configurada no hay contenedor que proxyear —
    # mensaje accionable en vez del críptico error de DNS del placeholder.
    if (row.spec or {}).get("app_configured") is False:
        raise HTTPException(
            status_code=409,
            detail=(
                "this project has no app-preview configured: set "
                "repository_config.review_image (an image built and published by "
                "the project's own CI — ADR 0063) in the project settings; the "
                "checklist and verdict of this review session work without it"
            ),
        )

    # Opening / interacting with the app counts as activity.
    sm = get_admin_sessionmaker()
    async with sm() as db, db.begin():
        await touch_activity(db, session_id)

    host, port = _proxy_target(row)
    target = f"http://{host}:{port}/{path}"
    # Forward the query string EXCEPT the signature pair (the app must not see it).
    fwd_params: list[tuple[str, str | int | float | bool | None]] = [
        (k, v) for k, v in request.query_params.multi_items() if k not in {"exp", "sig"}
    ]
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    body = await request.body()
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
            upstream = await client.request(
                request.method,
                target,
                params=fwd_params,
                content=body,
                headers=fwd_headers,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"review app unreachable: {exc}") from exc

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


# ---------------------------------------------------------------------------
# ADR 0062 — verdict: approve / reject the plan from the review session.
# ---------------------------------------------------------------------------


class VerdictRequest(BaseModel):
    verdict: Literal["approved", "rejected"]
    rejection_reason: str | None = None


@router.post("/review/{session_id}/verdict")
async def submit_verdict(
    session_id: UUID,
    body: VerdictRequest,
    exp: int = Query(...),
    sig: str = Query(...),
) -> dict[str, object]:
    """Record the human verdict + transition the plan.

    HMAC-gated. ``approved`` completes the plan (the human validation is the
    final gate in the single-machine flow); ``rejected`` moves it to
    ``rejected`` with the reason. Marks the review session terminal so the
    lifecycle sweep destroys its container.
    """
    _verify_signature(session_id, exp, sig)
    new_status = "approved" if body.verdict == "approved" else "rejected"
    sm = get_admin_sessionmaker()
    async with sm() as db, db.begin():
        row = await mark_terminal(
            db,
            session_id,
            status=new_status,
            verdict=body.verdict,
            rejection_reason=body.rejection_reason,
        )
        if row is None:
            raise HTTPException(status_code=404, detail="review session not found")
        # PROY2-07: el cierre del plan termina las OTRAS sesiones activas
        # (running/suspended de tandas previas) — sin esto quedaban zombies
        # que el autostart/reconciler contaba como activas.
        await mark_other_plan_sessions_terminal(db, row.plan_id, exclude_session_id=row.id)
        plan = await db.get(Plan, row.plan_id)
        plan_status: str | None = None
        # ADR 0072 fase 2: contexto para el auto-PR si el plan pasa a completed.
        pr_ctx: tuple[UUID, UUID, str] | None = None
        if plan is not None:
            if plan.status == "pending_human_validation":
                # c2/T3 (audit 2026-07-03): encaminar el cierre por la máquina de
                # estados (la ÚNICA puerta), no una asignación cruda de .status. La
                # transición pending_human_validation→completed|rejected es legal (la
                # misma de hoy); se PRESERVA el orden completar→encolar-PR (ADR 0072
                # fase 2), sin cambio de comportamiento.
                transition_plan_status(
                    plan, "completed" if body.verdict == "approved" else "rejected"
                )
                if plan.status == "completed" and plan.project_id is not None:
                    pr_ctx = (plan.project_id, plan.id, plan.title or "")
            plan_status = plan.status
        await db.flush()
    # ADR 0072 fase 2: al VALIDAR el plan (→ completed) se encola el auto-PR. El
    # gate humano queda respetado por construcción (solo en completed tras
    # 'approved', NUNCA en pending_human_validation). La task hace el push
    # autenticado + abre el PR/MR según push_policy (no-op si no hay remoto/PAT).
    if pr_ctx is not None:
        project_id, plan_id, plan_title = pr_ctx
        await enqueue_open_plan_pr(
            project_id,
            plan_id,
            title=f"Plan: {plan_title}" if plan_title else f"Plan {str(plan_id)[:8]}",
            body=(
                "PR automático tras la validación humana del plan.\n\n"
                f"Plan: {plan_title}\nID: {plan_id}"
            ),
        )
    # NOTIF-3 (auditoría 2026-07-12): plan_rejected estaba registrado
    # (+plantillas ES/EN) pero NADIE lo emitía. Post-commit (el begin() de
    # arriba ya cerró) y best-effort — nunca rompe el veredicto ya persistido.
    if body.verdict == "rejected" and plan is not None:
        with contextlib.suppress(Exception):
            await enqueue_event_dispatch(
                {
                    "event_type": "plan_rejected",
                    "tenant_id": str(plan.tenant_id),
                    "context": {
                        "plan_name": plan.title or "",
                        "plan_id": str(plan.id),
                        "reason": body.rejection_reason or "",
                    },
                }
            )
    return {
        "session_id": str(session_id),
        "verdict": body.verdict,
        "review_status": new_status,
        "plan_status": plan_status,
    }
