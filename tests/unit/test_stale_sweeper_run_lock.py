"""El sweeper libera el run-lock de los runs que acaba de declarar muertos.

Hallazgo de la revisión adversarial 2026-07-25, la otra cara de C-05
(`619f2a7b`). El run-lock impide que una re-entrega de Celery arranque un
SEGUNDO run de la misma tarea mientras el primero vive, y su TTL es la ventana
de visibilidad del broker (7 h) porque ese es el primer instante en que puede
existir un competidor — acortarlo reabre el hueco que C-05 cerró.

Pero el titular solo lo suelta en un `finally`, y un SIGKILL (OOM, límite duro,
`docker stop`) no ejecuta ninguno. El lock sobrevivía al run hasta 7 h y **vetaba
toda recuperación de la tarea** en ese intervalo, incluida la del propio sweeper:
sella la fila, deja la tarea en `blocked`, el operador la desbloquea, el dispatch
lanza el run… y `run_execution` devuelve `concurrent_run_locked` como ÉXITO, con
lo que Celery hace ack y el reintento desaparece.

El sweeper es la autoridad correcta para soltarlo porque acaba de PROBAR que el
titular está muerto. Y lo suelta con garantía de propiedad: el token es el id de
job de Celery, que está también en la fila `executions.celery_task_id`, así que
un lock readquirido por un run NUEVO y legítimo (otro token) no se toca.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Redis mínimo con la semántica compare-and-del del script de release."""

    def __init__(self, keys: dict[str, str] | None = None) -> None:
        self.keys: dict[str, str] = dict(keys or {})
        self.eval_calls: list[tuple[str, str]] = []

    async def eval(self, _script: str, _numkeys: int, key: str, token: str) -> int:
        self.eval_calls.append((key, token))
        if self.keys.get(key) == token:
            del self.keys[key]
            return 1
        return 0


class _ExplodingRedis:
    async def eval(self, *_args: Any, **_kwargs: Any) -> int:
        raise ConnectionError("redis caído")


async def _release(redis: Any, sealed: list[tuple[str, str | None]]) -> int:
    from workers.maintenance.stale_sweeper import _release_locks_of_sealed_runs

    return await _release_locks_of_sealed_runs(redis, sealed)


@pytest.mark.asyncio
async def test_el_lock_del_run_sellado_queda_libre() -> None:
    """El hallazgo, en una línea: tras sellar el run muerto su lock desaparece,
    y la tarea vuelve a ser despachable sin esperar las 7 h del TTL."""
    from workers.run_lock import run_lock_key

    key = run_lock_key("task-1")
    redis = _FakeRedis({key: "celery-job-1"})

    released = await _release(redis, [("task-1", "celery-job-1")])

    assert released == 1
    assert key not in redis.keys


@pytest.mark.asyncio
async def test_no_se_toca_el_lock_de_un_run_nuevo_y_vivo() -> None:
    """La cara contraria, que es la que protege el trabajo en vuelo: si el TTL
    ya expiró y OTRO run legítimo readquirió el lock, el sweeper no puede
    quitárselo — borrarlo dejaría a ese run sin la protección que impide que una
    re-entrega le haga `reset --hard` encima."""
    from workers.run_lock import run_lock_key

    key = run_lock_key("task-1")
    redis = _FakeRedis({key: "celery-job-2-el-nuevo"})

    released = await _release(redis, [("task-1", "celery-job-1-el-muerto")])

    assert released == 0
    assert redis.keys[key] == "celery-job-2-el-nuevo", "el lock del run vivo debe seguir intacto"


@pytest.mark.asyncio
async def test_una_fila_sin_token_no_se_fuerza() -> None:
    """Sin `celery_task_id` (invocación directa, no por Celery) la propiedad no
    se puede probar. Se deja el lock a su TTL en vez de borrarlo a ciegas: el
    coste de equivocarse es destruir trabajo en vuelo."""
    redis = _FakeRedis({"workers:run_lock:task:task-1": "algo"})

    released = await _release(redis, [("task-1", None)])

    assert released == 0
    assert redis.eval_calls == [], "no debe ni intentar el borrado sin token"


@pytest.mark.asyncio
async def test_redis_caido_no_rompe_el_barrido() -> None:
    """El barrido ya selló las filas y mató los contenedores; una Redis caída no
    puede tirar eso al suelo. El TTL sigue siendo el respaldo — y el contador no
    apunta una liberación que no ha ocurrido."""
    released = await _release(_ExplodingRedis(), [("task-1", "celery-job-1")])

    assert released == 0


@pytest.mark.asyncio
async def test_libera_los_locks_de_todas_las_filas_selladas() -> None:
    from workers.run_lock import run_lock_key

    redis = _FakeRedis(
        {
            run_lock_key("task-1"): "job-1",
            run_lock_key("task-2"): "job-2",
            run_lock_key("task-3"): "job-3-vivo",
        }
    )

    released = await _release(redis, [("task-1", "job-1"), ("task-2", "job-2")])

    assert released == 2
    assert set(redis.keys) == {run_lock_key("task-3")}
