"""La caché de platform_settings sobrevive a un event loop nuevo.

Hallazgo suelto que anotó otro carril y nadie había recogido: **en los workers,
`get_platform_setting` no acertaba en caché NUNCA**.

La mecánica: cada tarea Celery abre su propio `asyncio.run(...)`, y `asyncio.run`
crea un event loop nuevo y lo cierra al terminar. El cliente Redis vive en un
`lru_cache(maxsize=1)` de `api_server.auth.deps`, o sea que es un singleton de
PROCESO: el que se creó dentro del primer loop conserva conexiones atadas a un
loop que ya está cerrado. Desde la segunda tarea en adelante, la primera llamada
a `redis.get()` levanta («Future attached to a different loop» / «Event loop is
closed»), `_cached_read` la captura —a propósito, porque un Redis caído no debe
romper una lectura— y devuelve `(False, None)`. Resultado: **siempre a la base de
datos**, con la caché intacta y sin un solo error en los logs.

Es pérdida de rendimiento, no incorrección, y ese es justo el motivo de que
llevara meses ahí: no rompe nada, no aparece en ninguna traza, y el código «tiene
caché». La clave de `max_review_retries` se lee en el camino caliente de cada run.

El arreglo es reconstruir el cliente cuando cambia el loop, no callar el error.
Estos tests fijan las cuatro propiedades que importan: que acierta entre loops,
que no regresa dentro del mismo loop, que la invalidación cruza loops, y que un
Redis genuinamente roto sigue degradando a la base de datos.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from api_server.db import platform_settings as ps

#: `uses_platform_cache` exime a este fichero del corte hermético que
#: `tests/unit/conftest.py` aplica al resto: aquí la caché es el SUJETO, y
#: trae su propio doble de Redis con los datos fuera del cliente.
pytestmark = [pytest.mark.unit, pytest.mark.uses_platform_cache]


class _LoopBoundRedis:
    """Cliente atado al loop en el que se creó, como el de verdad.

    Los datos viven fuera (`store`), porque Redis es un servidor: lo que muere
    con el loop son las CONEXIONES, no el contenido.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, store: dict[str, str]) -> None:
        self._loop = loop
        self._store = store

    def _assert_same_loop(self) -> None:
        if asyncio.get_running_loop() is not self._loop:
            raise RuntimeError("got Future attached to a different loop")

    async def get(self, key: str) -> str | None:
        self._assert_same_loop()
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._assert_same_loop()
        self._store[key] = value

    async def delete(self, key: str) -> None:
        self._assert_same_loop()
        self._store.pop(key, None)


@dataclass
class _FakeDeps:
    """Sustituto de `api_server.auth.deps` con la misma semántica de singleton."""

    store: dict[str, str] = field(default_factory=dict)
    client: _LoopBoundRedis | None = None
    builds: int = 0
    fail_with: Exception | None = None

    def get_redis(self) -> _LoopBoundRedis:
        if self.fail_with is not None:
            raise self.fail_with
        if self.client is None:
            self.builds += 1
            self.client = _LoopBoundRedis(asyncio.get_running_loop(), self.store)
        return self.client

    def reset_redis_cache(self) -> None:
        self.client = None

    def schedule_after_commit(self, session: Any, callback: Any) -> None:
        return None


@dataclass
class _FakeSession:
    """Sólo lo que usa `get_platform_setting`: `session.get(...)`."""

    row: Any = None
    gets: int = 0

    async def get(self, model: Any, key: str) -> Any:
        self.gets += 1
        return self.row


@pytest.fixture
def deps(monkeypatch: pytest.MonkeyPatch) -> _FakeDeps:
    import api_server.auth.deps as real_deps

    fake = _FakeDeps()
    monkeypatch.setattr(real_deps, "get_redis", fake.get_redis)
    monkeypatch.setattr(real_deps, "reset_redis_cache", fake.reset_redis_cache)
    monkeypatch.setattr(real_deps, "schedule_after_commit", fake.schedule_after_commit)
    # El estado de binding es de proceso: arrancar de cero en cada test.
    ps.reset_platform_setting_cache_binding()
    yield fake
    ps.reset_platform_setting_cache_binding()


# ---------------------------------------------------------------------------
# El hallazgo
# ---------------------------------------------------------------------------
def test_cache_hits_across_a_new_event_loop(deps: _FakeDeps) -> None:
    """Dos tareas Celery seguidas = dos `asyncio.run` = dos loops."""
    session = _FakeSession()

    async def _read() -> Any:
        return await ps.get_platform_setting(session, "max_review_retries", default=3)

    assert asyncio.run(_read()) == 3  # loop A: miss → BD, y cachea la ausencia
    assert session.gets == 1
    assert asyncio.run(_read()) == 3  # loop B: DEBE acertar en caché
    assert session.gets == 1, (
        "la segunda tarea Celery volvió a la base de datos: el cliente Redis "
        "sigue atado al loop cerrado de la primera"
    )
    # Y para que se acierte, el cliente tuvo que reconstruirse.
    assert deps.builds == 2


def test_a_present_row_is_also_served_across_loops(deps: _FakeDeps) -> None:
    """El camino con fila, no sólo el de la ausencia."""
    session = _FakeSession(row=type("Row", (), {"value": 7})())

    async def _read() -> Any:
        return await ps.get_platform_setting(session, "max_review_retries", default=3)

    assert asyncio.run(_read()) == 7
    assert asyncio.run(_read()) == 7
    assert session.gets == 1


# ---------------------------------------------------------------------------
# Sin regresión dentro del mismo loop
# ---------------------------------------------------------------------------
def test_the_client_is_not_rebuilt_within_one_loop(deps: _FakeDeps) -> None:
    """Reconstruir por cada lectura sería cambiar una pérdida por otra peor:
    en el api-server hay UN loop para todo el proceso."""
    session = _FakeSession()

    async def _read_many() -> None:
        for _ in range(5):
            await ps.get_platform_setting(session, "k", default=None)

    asyncio.run(_read_many())
    assert session.gets == 1
    assert deps.builds == 1


# ---------------------------------------------------------------------------
# La invalidación también cruza loops
# ---------------------------------------------------------------------------
def test_invalidation_from_another_loop_actually_deletes(deps: _FakeDeps) -> None:
    """Si la invalidación fallase por el mismo motivo, un valor cambiado por un
    worker seguiría cacheado — y entre estas claves hay límites de seguridad."""
    session = _FakeSession()

    asyncio.run(ps.get_platform_setting(session, "k", default=None))
    assert deps.store, "la primera lectura no llegó a cachear nada"

    asyncio.run(ps.invalidate_platform_setting_cache("k"))
    assert not deps.store, "la invalidación desde otro loop no borró la clave"


# ---------------------------------------------------------------------------
# Un Redis roto de verdad sigue degradando a la BD
# ---------------------------------------------------------------------------
def test_a_broken_redis_still_falls_back_to_the_database(deps: _FakeDeps) -> None:
    """La captura amplia de excepciones no se toca: la base de datos es la
    verdad, y una caché caída nunca puede romper una lectura."""
    deps.fail_with = RuntimeError("connection refused")
    session = _FakeSession()

    async def _read() -> Any:
        return await ps.get_platform_setting(session, "k", default="fallback")

    assert asyncio.run(_read()) == "fallback"
    assert asyncio.run(_read()) == "fallback"
    assert session.gets == 2
