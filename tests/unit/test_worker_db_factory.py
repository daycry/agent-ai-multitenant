"""Factoría única de engines/sesiones de los workers (`task_audit14_06`).

Hallazgo **AUD14-04** de la auditoría integral del 2026-07-14: los módulos de
`apps/workers/` abrían su propio `create_async_engine(settings.database_url)` —35
llamadas fuera de `maintenance/`, 49 contando el paquete entero— cada una con el
`QueuePool` por defecto de SQLAlchemy. Una task Celery vive segundos: monta el
pool (5 conexiones + 10 de overflow), usa UNA, y lo tira. Ni `pool_pre_ping`, ni
un sitio donde tocar timeouts, ni forma de auditar el contrato.

Este fichero fija el contrato de :mod:`workers.db`:

1. El engine se crea con `NullPool` — conexión corta, sin pool que sobreviva a la
   task. Es la decisión 4 del plan de remediación.
2. `worker_sessionmaker()` es un context manager que **garantiza el dispose**,
   también cuando el cuerpo revienta. Antes cada llamante repetía su propio
   `try/finally`; los 29 módulos lo tenían bien, pero era 29 veces la misma
   oportunidad de olvidarlo.
3. `override=` es el seam de test que ya usaban `browse_task` y
   `git_remote_sweep` (`sessionmaker` inyectable): si viene un sessionmaker de
   fuera, NO se abre engine ninguno y NO se dispone nada — el dueño del
   sessionmaker inyectado es quien lo cierra.

Cómo se observa el dispose: `AsyncEngine.dispose()` cierra el pool y lo
**sustituye por uno nuevo** (`pool.recreate()`), así que la identidad del objeto
pool cambia. Es una señal observable sin conectar a ninguna BD — comprobado
contra SQLAlchemy 2.x antes de escribir estos asserts.

No se prueba aquí que las tasks lo usen: eso es
`tests/unit/test_worker_engines_nullpool.py`, la guarda estática.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import NullPool

pytestmark = pytest.mark.unit

_URL = "postgresql+asyncpg://u:p@127.0.0.1:5432/does_not_need_to_exist"


class _FakeSettings:
    """Sólo el atributo que la factoría lee. Nada de red: SQLAlchemy no conecta
    al construir el engine, así que la URL puede apuntar a la nada."""

    def __init__(self, url: str = _URL) -> None:
        self.database_url = url


def test_worker_engine_uses_nullpool() -> None:
    from workers.db import worker_engine

    engine = worker_engine(_FakeSettings())
    pool_name = type(engine.pool).__name__
    assert isinstance(engine.pool, NullPool), (
        "el engine de una task Celery no debe montar un pool que muera con la task; "
        f"pool={pool_name}"
    )


def test_worker_engine_honours_explicit_url_without_settings() -> None:
    """`restore_per_tenant` restaura contra una BD que NO es `database_url`, y su
    config (`PerTenantRestoreConfig`) no tiene `database_url` ninguno."""
    from workers.db import worker_engine

    other = "postgresql+asyncpg://u:p@127.0.0.1:5432/restored_copy"
    assert worker_engine(url=other).url.database == "restored_copy"
    # y `url` gana sobre los settings cuando llegan los dos
    assert worker_engine(_FakeSettings(), url=other).url.database == "restored_copy"


def test_worker_engine_without_settings_nor_url_is_a_loud_error() -> None:
    """Un engine contra `None` fallaría mucho más tarde y con menos contexto."""
    from workers.db import worker_engine

    with pytest.raises(ValueError, match="settings"):
        worker_engine()


@pytest.mark.asyncio
async def test_worker_sessionmaker_yields_bound_factory_and_disposes() -> None:
    from workers.db import worker_sessionmaker

    async with worker_sessionmaker(_FakeSettings()) as sm:
        assert isinstance(sm, async_sessionmaker)
        assert sm.class_ is AsyncSession
        expire = sm.kw["expire_on_commit"]
        assert expire is False, (
            "los llamantes leen atributos del objeto tras el commit; "
            "expire_on_commit=True los volvería a cargar fuera de sesión"
        )
        engine = sm.kw["bind"]
        assert isinstance(engine.pool, NullPool)
        pool_before = engine.sync_engine.pool

    assert engine.sync_engine.pool is not pool_before, (
        "el engine debe quedar dispuesto al salir del `async with`"
    )


@pytest.mark.asyncio
async def test_worker_sessionmaker_disposes_on_exception() -> None:
    """El `finally` de los 29 llamantes existía; aquí queda garantizado una vez."""
    from workers.db import worker_sessionmaker

    captured: dict[str, Any] = {}

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with worker_sessionmaker(_FakeSettings()) as sm:
            engine = sm.kw["bind"]
            captured["engine"] = engine
            captured["pool"] = engine.sync_engine.pool
            raise _BoomError("la task falla a media faena")

    leaked = captured["engine"].sync_engine.pool is captured["pool"]
    assert not leaked, "un fallo de la task no puede filtrar el engine sin dispose"


def test_worker_engine_does_not_pre_ping() -> None:
    """Decisión explícita, no olvido: con `NullPool` cada checkout abre conexión
    nueva, así que el pre-ping sería un `SELECT 1` por sesión a cambio de nada."""
    from workers.db import worker_engine

    engine = worker_engine(_FakeSettings())
    assert engine.pool._pre_ping is False


@pytest.mark.asyncio
async def test_worker_sessionmaker_override_opens_no_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El seam de test: con `override` la factoría no toca la BD ni dispone nada."""
    import workers.db as worker_db

    created: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        created.append(args)
        raise AssertionError("no debería llegar aquí")

    monkeypatch.setattr(worker_db, "create_async_engine", _spy)

    injected: async_sessionmaker[AsyncSession] = async_sessionmaker(expire_on_commit=False)
    async with worker_db.worker_sessionmaker(_FakeSettings(), override=injected) as sm:
        assert sm is injected
    assert created == [], "con override no debe abrirse ningún engine"


@pytest.mark.asyncio
async def test_worker_session_opens_one_session() -> None:
    """Azúcar para el caso mayoritario: una sola sesión y fuera."""
    from workers.db import worker_session

    opened: list[Any] = []

    class _FakeSession:
        def __init__(self) -> None:
            opened.append(self)
            self.closed = False

        async def __aenter__(self) -> _FakeSession:
            return self

        async def __aexit__(self, *exc: Any) -> None:
            self.closed = True

    injected = _FakeFactory(_FakeSession)
    async with worker_session(_FakeSettings(), override=injected) as session:  # type: ignore[arg-type]
        assert isinstance(session, _FakeSession)
    assert len(opened) == 1
    assert opened[0].closed is True, "la sesión debe cerrarse al salir"


class _FakeFactory:
    """Sessionmaker de pega: llamarlo devuelve un context manager de sesión."""

    def __init__(self, session_cls: Any) -> None:
        self._session_cls = session_cls

    def __call__(self) -> Any:
        return self._session_cls()
