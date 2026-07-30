"""WebSocket de telemetría del córtex — ``/ws/owner/cortex/telemetry`` (Córtex F2).

Tailea el stream Redis ``cortex:telemetry:{owner}`` y reenvía cada frame
``{type:'affect', payload:{valence, arousal, dominance, intensity, mood_label,
drives, appraisal_reason, occurred_at}}`` que el distilador publica tras cada
turno — el Panel de Mente actualiza los diales PAD en vivo (~1-2s tras la
respuesta, appraisal asíncrono, ADR 0075).

Gate (ADR 0074, DB-authoritative): el navegador no puede poner cabeceras, así que
el JWT viaja como ``?token=``; ``_resolve_principal`` (reusado de ``routers/ws.py``)
lo decodifica y confirma la sesión viva, y AQUÍ se re-verifica que el usuario es el
System Owner **contra la BD** (``_is_db_system_owner``, no sólo el claim ``own``).
Cualquier fallo cierra con 1008 (policy violation) — nunca se filtra telemetría de
otro owner: el stream es per-owner y el principal sólo tailea ``cortex:telemetry:
{su propio user_id}``.

> Honestidad (ADR 0075 §6): los frames son una simulación computacional de afecto,
> NO sentimientos reales.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, WebSocket
from redis.asyncio import Redis

from api_server.auth.deps import _is_db_system_owner, get_redis, get_session_store
from api_server.auth.sessions import SessionStore
from api_server.events import cortex_telemetry_stream_key
from api_server.routers.ws import _pump, _reject, _resolve_principal

router = APIRouter(tags=["ws"])


@router.websocket("/ws/owner/cortex/telemetry")
async def cortex_telemetry_stream(
    ws: WebSocket,
    token: str | None = Query(default=None),
    redis: Redis = Depends(get_redis),
    sessions: SessionStore = Depends(get_session_store),
) -> None:
    """Stream de la telemetría afectiva del córtex — SÓLO al System Owner.

    Acepta el socket, resuelve el principal del ``?token=`` (decodifica + sesión
    viva) y re-verifica owner contra la BD. Si algo falla, cierre 1008. Si pasa,
    tailea ``cortex:telemetry:{owner}`` (desde el inicio, así un cliente que
    conecta tarde recibe el backlog y luego el tail en vivo)."""
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    # DB-authoritative: el claim `own` es sólo una pista (ADR 0074); revocar la
    # propiedad cierra el socket en la siguiente conexión.
    if not await _is_db_system_owner(principal.user_id):
        await _reject(ws, "forbidden")
        return
    stream = cortex_telemetry_stream_key(str(principal.user_id))
    # `sessions`/`principal`/`token` alimentan la re-validación periódica del pump
    # (prod-09 task_prod09_13): el socket del córtex también se cierra con 1008 si
    # la sesión se revoca o el token caduca mientras está abierto — antes sólo se
    # comprobaba en el accept, así que un logout dejaba el panel de mente
    # recibiendo telemetría afectiva del owner indefinidamente.
    await _pump(
        ws,
        redis,
        stream,
        project_filter=None,
        sessions=sessions,
        principal=principal,
        token=token,
    )


__all__ = ["router"]
