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
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from redis.asyncio import Redis
from sqlalchemy import select
from workers.review_runtime import verify_review_url

from api_server.auth.deps import get_redis
from api_server.config import get_settings
from api_server.db.models import ReviewSession as ReviewSessionRow
from api_server.db.review_session_repo import mark_rerun_requested, touch_activity
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

    return HTMLResponse(content=_SPA_HTML.format(session_id=session_id))


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
