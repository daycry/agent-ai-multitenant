"""Bug cazado en vivo (2026-07-18, verificación del córtex): el distilador
afectivo corre en el WORKER pero construía su cliente Redis con
`api_server.auth.deps.get_redis` — cuya env (API_SERVER_REDIS_URL) no existe
en el contenedor del worker, así que caía al default `localhost:6379` y el
caché vivo de afecto + el frame de telemetría WS fallaban SIEMPRE
(`affect_cache_write_failed`, `cortex_affect_event_publish_failed`).

El cliente debe salir de la config del WORKER (events_redis_url — la misma
DB 0 que el WS del api-server tailea, invariante H10/AUD16).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_distiller_redis_client_uses_worker_events_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", "redis://bus-de-eventos:6379/0")
    from workers.config import reset_settings_cache

    reset_settings_cache()
    try:
        from workers.cortex_affect import _get_redis

        client = _get_redis()
        pool_kwargs = client.connection_pool.connection_kwargs
        assert pool_kwargs.get("host") == "bus-de-eventos", (
            "el distilador debe conectar al Redis del bus de eventos del worker, "
            f"no a {pool_kwargs.get('host')!r} (default localhost del api-server)"
        )
    finally:
        reset_settings_cache()
