"""NOTIF-2 (auditoría notificaciones 2026-07-12 / diseño prod-08 alert_ingest_01).

La cadena de alertas de infraestructura estaba MUERTA: Alertmanager entregaba a
``POST /internal/alerts/ingest`` — un endpoint que no existía (404 silencioso),
así que ninguna alerta de infra llegaba jamás a un humano. Este endpoint parsea
el webhook v4 de Alertmanager, deduplica por fingerprint+status (Redis, TTL) y
convierte cada alerta en un evento ``infra_alert`` platform-scoped
(tenant_id=None → canales del System Admin) vía el dispatcher del Plan 10.
Protegido por token Bearer compartido (mismo patrón que /internal/agent).
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration

_TOKEN = "test-alerts-token"


def _payload(fingerprint: str = "f-123", status: str = "firing") -> dict[str, Any]:
    """Un webhook v4 de Alertmanager mínimo pero realista."""
    return {
        "version": "4",
        "groupKey": '{}:{alertname="HostDiskUsageHigh"}',
        "status": status,
        "receiver": "platform-notifier",
        "alerts": [
            {
                "status": status,
                "fingerprint": fingerprint,
                "labels": {
                    "alertname": "HostDiskUsageHigh",
                    "severity": "critical",
                    "instance": "node-exporter:9100",
                },
                "annotations": {
                    "summary": "Disk almost full",
                    "description": "/data at 91%",
                },
                "startsAt": "2026-07-12T00:00:00Z",
            }
        ],
    }


@pytest.fixture()
def alerts_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_SERVER_ALERTS_INGEST_TOKEN", _TOKEN)
    from api_server.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def captured_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    async def _capture(event: dict[str, Any], **_: Any) -> bool:
        events.append(event)
        return True

    monkeypatch.setattr("api_server.routers.internal_alerts.enqueue_event_dispatch", _capture)
    return events


async def _post(app: Any, body: dict[str, Any], *, token: str | None = _TOKEN):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/internal/alerts/ingest", json=body, headers=headers)


@pytest.mark.asyncio
async def test_firing_alert_becomes_platform_scoped_infra_alert(
    configured_app, alerts_env, captured_events
) -> None:
    resp = await _post(configured_app, _payload())
    assert resp.status_code == 200, resp.text
    assert resp.json()["accepted"] == 1
    assert len(captured_events) == 1
    event = captured_events[0]
    assert event["event_type"] == "infra_alert"
    assert event["tenant_id"] is None
    ctx = event["context"]
    assert ctx["alertname"] == "HostDiskUsageHigh"
    assert ctx["severity"] == "critical"
    assert "Disk almost full" in ctx["summary"]


@pytest.mark.asyncio
async def test_repeat_interval_is_deduped_by_fingerprint(
    configured_app, alerts_env, captured_events
) -> None:
    first = await _post(configured_app, _payload(fingerprint="f-dedup"))
    second = await _post(configured_app, _payload(fingerprint="f-dedup"))
    assert first.json()["accepted"] == 1
    assert second.json()["accepted"] == 0
    assert second.json()["deduped"] == 1
    assert len(captured_events) == 1


@pytest.mark.asyncio
async def test_resolved_transition_passes_dedup(
    configured_app, alerts_env, captured_events
) -> None:
    await _post(configured_app, _payload(fingerprint="f-res", status="firing"))
    resp = await _post(configured_app, _payload(fingerprint="f-res", status="resolved"))
    assert resp.json()["accepted"] == 1
    assert len(captured_events) == 2
    assert captured_events[1]["context"]["status"] == "resolved"


@pytest.mark.asyncio
async def test_wrong_or_missing_token_is_rejected(configured_app, alerts_env) -> None:
    bad = await _post(configured_app, _payload(), token="wrong")
    missing = await _post(configured_app, _payload(), token=None)
    assert bad.status_code == 401
    assert missing.status_code == 401


@pytest.mark.asyncio
async def test_unconfigured_token_fails_closed(
    configured_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("API_SERVER_ALERTS_INGEST_TOKEN", raising=False)
    from api_server.config import get_settings

    get_settings.cache_clear()
    resp = await _post(configured_app, _payload())
    assert resp.status_code == 503
