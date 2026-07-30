"""prod-12 `task_prod12_av_01` — el aviso «antivirus inalcanzable» (ADR 0105).

Última línea del checklist de `human_prod12_03`: «La notificación de "antivirus
inalcanzable" llega tras N minutos de caída». El emisor existe desde prod-12
(`workers.ingestion._track_av_availability`) y el notification-dispatcher tiene
sus plantillas es/en… pero `grep -rln antivirus_unreachable tests/` devolvía
CERO: mecanismo entregado, ningún test. Este fichero cubre el emisor entero:

  * dentro de la ventana (< 15 min de racha) NO se avisa — solo se marca el
    inicio de la racha;
  * pasado el umbral se emite EXACTAMENTE UN evento `antivirus_unreachable`,
    en la cola de notificaciones, con `tenant_id` y `context.minutes_down`;
  * el aviso es single-flight: reentrar con la racha viva no emite un segundo
    evento (el `nx` + TTL de re-aviso de 6 h);
  * cuando el AV VUELVE, la racha se borra, de modo que una caída posterior
    empieza a contar de cero y no dispara un aviso instantáneo;
  * el tracking es best-effort: un Redis que revienta NO rompe la ingesta.

El reloj se adelanta sustituyendo el módulo `time` que ve
`workers.ingestion` (no el global del proceso), así que la ventana de 15 min se
recorre de verdad sin dormir y sin tocar el reloj de asyncpg/redis.

Redis es el REAL (la DB de test), porque lo que se está probando es
precisamente la semántica de `set(nx=True)` + TTL: con un doble en memoria el
single-flight se probaría contra mi propia imitación, no contra Redis.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.integration

_NOTIFY_AFTER_S = 15 * 60


class _FakeTime:
    """Sustituto del módulo ``time`` con un reloj que yo muevo."""

    def __init__(self, now: float) -> None:
        self._now = now

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _CapturedTask:
    def __init__(self, name: str, args: Any, queue: str) -> None:
        self.name = name
        self.args = args
        self.queue = queue


@pytest.fixture()
def av_env(test_redis_url: str, monkeypatch: pytest.MonkeyPatch):
    """Redis real + `send_task` capturado + reloj controlable.

    Borra SOLO las dos claves del tracking del AV (no un `flushdb`: la DB de
    test de Redis es compartida y no me toca arrasar el estado de nadie).
    """
    from redis.asyncio import Redis
    from workers import ingestion
    from workers.celery_app import app as celery_app
    from workers.config import get_settings, reset_settings_cache

    monkeypatch.setenv("WORKERS_EVENTS_REDIS_URL", test_redis_url)
    reset_settings_cache()
    settings = get_settings()

    # Cliente en BYTES, igual que `_default_redis_factory` en producción — así
    # se recorre la rama `raw.decode()` de `_track_av_availability`.
    client: Redis = Redis.from_url(test_redis_url)

    sent: list[_CapturedTask] = []

    def _fake_send_task(name: str, args: Any = None, queue: str = "", **_: Any) -> None:
        sent.append(_CapturedTask(name, args, queue))

    monkeypatch.setattr(celery_app, "send_task", _fake_send_task)

    clock = _FakeTime(1_800_000_000.0)
    monkeypatch.setattr(ingestion, "time", clock)

    try:
        yield {
            "redis": client,
            "settings": settings,
            "sent": sent,
            "clock": clock,
            "ingestion": ingestion,
        }
    finally:
        reset_settings_cache()


async def _reset_keys(client: Any) -> None:
    from workers.ingestion import _AV_DOWN_KEY, _AV_NOTIFIED_KEY

    await client.delete(_AV_DOWN_KEY, _AV_NOTIFIED_KEY)


# ===========================================================================
# Dentro de la ventana no se avisa; pasado el umbral, sí (una sola vez)
# ===========================================================================
@pytest.mark.asyncio
async def test_notifies_only_after_the_threshold_and_only_once(av_env) -> None:
    client = av_env["redis"]
    sent: list[_CapturedTask] = av_env["sent"]
    clock: _FakeTime = av_env["clock"]
    track = av_env["ingestion"]._track_av_availability
    tenant_id = str(uuid4())

    try:
        await _reset_keys(client)

        # 1) Primer fallo: marca la racha, no avisa.
        await track(client, av_env["settings"], tenant_id=tenant_id, unavailable=True)
        assert sent == [], "no se avisa al primer fallo del AV"

        # 2) Justo ANTES del umbral: sigue sin avisar. Este es el assert que
        #    hace que el test pueda fallar si alguien pone el umbral a 0.
        clock.advance(_NOTIFY_AFTER_S - 1)
        await track(client, av_env["settings"], tenant_id=tenant_id, unavailable=True)
        assert sent == [], "no se avisa antes de los 15 min de racha"

        # 3) Cruzado el umbral: UN evento.
        clock.advance(2 * 60)
        await track(client, av_env["settings"], tenant_id=tenant_id, unavailable=True)
        assert len(sent) == 1, f"esperaba 1 aviso, hubo {len(sent)}"

        task = sent[0]
        assert task.name == "notification_dispatcher.dispatch_event"
        assert task.queue == av_env["settings"].notifications_event_queue
        payload = task.args[0]
        assert payload["event_type"] == "antivirus_unreachable"
        assert payload["tenant_id"] == tenant_id
        # `minutes_down` es la duración REAL de la racha, no una constante.
        assert payload["context"]["minutes_down"] == 16

        # 4) Single-flight: reentrar con la racha viva no emite un segundo aviso.
        clock.advance(60 * 60)
        await track(client, av_env["settings"], tenant_id=tenant_id, unavailable=True)
        assert len(sent) == 1, "el re-aviso está limitado por TTL (6 h), no por llamada"
    finally:
        await _reset_keys(client)
        await client.aclose()


# ===========================================================================
# El AV vuelve → la racha se borra y la siguiente caída cuenta de cero
# ===========================================================================
@pytest.mark.asyncio
async def test_recovery_clears_the_streak_so_the_clock_restarts(av_env) -> None:
    client = av_env["redis"]
    sent: list[_CapturedTask] = av_env["sent"]
    clock: _FakeTime = av_env["clock"]
    track = av_env["ingestion"]._track_av_availability
    from workers.ingestion import _AV_DOWN_KEY, _AV_NOTIFIED_KEY

    try:
        await _reset_keys(client)

        # Racha larga (14 min) y el AV vuelve antes del umbral.
        await track(client, av_env["settings"], tenant_id=None, unavailable=True)
        clock.advance(14 * 60)
        await track(client, av_env["settings"], tenant_id=None, unavailable=False)
        assert await client.get(_AV_DOWN_KEY) is None, "la recuperación borra la racha"
        assert sent == []

        # Nueva caída: cuenta desde CERO, así que 2 min después sigue sin avisar
        # (si la recuperación no hubiese borrado la clave, aquí ya habría aviso).
        await track(client, av_env["settings"], tenant_id=None, unavailable=True)
        clock.advance(2 * 60)
        await track(client, av_env["settings"], tenant_id=None, unavailable=True)
        assert sent == [], "tras recuperar, el reloj del aviso empieza de nuevo"

        # Y al cruzar el umbral de la NUEVA racha sí avisa (el mecanismo sigue
        # armado tras la recuperación — sin esto el test anterior pasaría
        # vacíamente con un emisor roto).
        clock.advance(_NOTIFY_AFTER_S)
        await track(client, av_env["settings"], tenant_id=None, unavailable=True)
        assert len(sent) == 1
        # Sin tenant (documento de plataforma / tenant ilegible) → cadena vacía,
        # nunca "None".
        assert sent[0].args[0]["tenant_id"] == ""
    finally:
        await client.delete(_AV_DOWN_KEY, _AV_NOTIFIED_KEY)
        await client.aclose()


# ===========================================================================
# Best-effort: el tracking nunca rompe la ingesta
# ===========================================================================
@pytest.mark.asyncio
async def test_tracking_is_best_effort_and_never_raises(av_env) -> None:
    """Un Redis que revienta se traga (la ingesta manda), y sin Redis en
    absoluto la función es un no-op."""

    class _BoomRedis:
        async def set(self, *_: Any, **__: Any) -> bool:
            raise RuntimeError("redis down")

        async def get(self, *_: Any, **__: Any) -> bytes | None:
            raise RuntimeError("redis down")

        async def delete(self, *_: Any, **__: Any) -> int:
            raise RuntimeError("redis down")

    track = av_env["ingestion"]._track_av_availability
    await track(_BoomRedis(), av_env["settings"], tenant_id=None, unavailable=True)
    await track(None, av_env["settings"], tenant_id=None, unavailable=True)
    assert av_env["sent"] == []
