"""Ingesta de alertas de Alertmanager → notificaciones Plan 10 (NOTIF-2 / prod-08).

La cadena de alertas de infraestructura estaba MUERTA: ``alertmanager.yml``
entregaba su webhook a ``POST /internal/alerts/ingest`` — un endpoint que no
existía, así que cada alerta (disco lleno, OOM, backup caído) moría en un 404
silencioso y jamás llegaba a un humano. Este router la resucita según el diseño
de prod-08 (``task_prod08_alert_ingest_01``):

  * **Auth**: token Bearer compartido (``API_SERVER_ALERTS_INGEST_TOKEN``),
    mismo patrón de confianza que ``/internal/agent``. Sin token configurado el
    endpoint responde 503 (fail-closed) — nunca queda abierto.
  * **Dedup**: Alertmanager re-notifica cada ``repeat_interval`` (1h critical /
    4h resto); deduplicamos por ``fingerprint + status`` en Redis con TTL menor
    que ese intervalo, de modo que los repeats no spamean pero la transición
    firing→resolved (status distinto) sí pasa.
  * **Fan-out**: cada alerta se convierte en un evento ``infra_alert``
    platform-scoped (``tenant_id=None`` → solo canales del System Admin) y se
    encola en el dispatcher del Plan 10 (``enqueue_event_dispatch``), que posee
    preferencias, plantillas ES/EN, reintentos y DLQ.

El payload es el webhook v4 de Alertmanager (``alerts[].labels/annotations``).
Campos desconocidos se ignoran (Alertmanager añade metadatos sin avisar).
"""

from __future__ import annotations

import contextlib
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis

from api_server.celery_client import enqueue_event_dispatch
from api_server.config import get_settings

_log = structlog.get_logger("api_server.internal_alerts")

router = APIRouter(prefix="/internal/alerts", tags=["internal-alerts"])

# Prefijo de las claves de dedup en Redis (DB de sesiones del api-server).
_DEDUP_KEY_PREFIX = "alerts:ingest"
# Evento del Plan 10 al que se traduce cada alerta (EVENT_REGISTRY + builtins).
_INFRA_ALERT_EVENT = "infra_alert"


class AlertmanagerAlert(BaseModel):
    """Una alerta individual del webhook v4 (campos que consumimos)."""

    model_config = ConfigDict(extra="ignore")

    status: str = "firing"
    fingerprint: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: str | None = None  # noqa: N815 - nombre del wire format


class AlertmanagerWebhook(BaseModel):
    """El envelope v4 de Alertmanager (campos que consumimos)."""

    model_config = ConfigDict(extra="ignore")

    version: str = "4"
    status: str = "firing"
    receiver: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)


def _require_token(authorization: str | None) -> None:
    token = get_settings().alerts_ingest_token
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="alerts ingest is not configured (API_SERVER_ALERTS_INGEST_TOKEN)",
        )
    if authorization != f"Bearer {token}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing bearer token",
        )


def _alert_context(alert: AlertmanagerAlert) -> dict[str, Any]:
    """El contexto secret-free que renderizan las plantillas ES/EN."""
    labels = alert.labels or {}
    annotations = alert.annotations or {}
    return {
        "alertname": str(labels.get("alertname") or "(unknown)"),
        "severity": str(labels.get("severity") or "warning"),
        "status": str(alert.status or "firing"),
        "instance": str(labels.get("instance") or ""),
        "summary": str(annotations.get("summary") or ""),
        "description": str(annotations.get("description") or ""),
        "starts_at": str(alert.startsAt or ""),
    }


def _dedup_key(alert: AlertmanagerAlert) -> str:
    """Clave de dedup: fingerprint (o alertname+instance) + status.

    El status forma parte de la clave para que firing→resolved pase el dedup
    (es información nueva) mientras los repeats del mismo estado se tragan."""
    fingerprint = alert.fingerprint or (
        f"{alert.labels.get('alertname', '?')}:{alert.labels.get('instance', '?')}"
    )
    return f"{_DEDUP_KEY_PREFIX}:{fingerprint}:{alert.status}"


@router.post("/ingest")
async def ingest_alerts(
    payload: AlertmanagerWebhook,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, int]:
    """Recibe el webhook de Alertmanager y lo fan-outea como ``infra_alert``."""
    _require_token(authorization)
    settings = get_settings()

    accepted = 0
    deduped = 0
    redis: Redis | None = None
    try:
        redis = Redis.from_url(settings.redis_url)
        for alert in payload.alerts:
            try:
                is_new = await redis.set(
                    _dedup_key(alert), "1", nx=True, ex=settings.alerts_dedup_ttl_s
                )
            except Exception as exc:  # Redis caído → mejor duplicar que callar
                _log.warning("internal_alerts.dedup_unavailable", error=str(exc))
                is_new = True
            if not is_new:
                deduped += 1
                continue
            enqueued = await enqueue_event_dispatch(
                {
                    "event_type": _INFRA_ALERT_EVENT,
                    "tenant_id": None,  # platform-scoped → canales del System Admin
                    "context": _alert_context(alert),
                    "locale": "es",
                }
            )
            if enqueued:
                accepted += 1
            else:
                # Broker caído: la alerta se pierde ESTA vez pero Alertmanager
                # re-notifica en el próximo repeat_interval; liberar el dedup
                # para que ese reintento no se trague.
                with contextlib.suppress(Exception):
                    await redis.delete(_dedup_key(alert))
    finally:
        if redis is not None:
            with contextlib.suppress(Exception):
                await redis.aclose()

    _log.info(
        "internal_alerts.ingested",
        accepted=accepted,
        deduped=deduped,
        receiver=payload.receiver,
    )
    return {"accepted": accepted, "deduped": deduped}
