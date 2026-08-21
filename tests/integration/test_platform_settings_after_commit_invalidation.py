"""El kill-switch de egress del córtex no puede quedarse 30 s encendido.

El hallazgo
-----------
``set_platform_setting`` invalida la caché Redis del setting **dos veces**, y su
propio docstring explica por qué hacen falta las dos: la inmediata (tras el
``flush``) impide servir el valor viejo desde ya, y la de DESPUÉS DEL COMMIT
existe porque entre el flush y el commit un lector concurrente todavía ve el
valor antiguo en la base de datos y puede **repoblar la caché con él** — con lo
que el valor recién cambiado quedaría rancio otros 30 s, que es el peor de los
dos mundos.

La segunda se agenda con ``schedule_after_commit``… y hasta este fichero **sólo
la drenaba** ``open_tenant_session``. Cada ruta que escribe platform settings lo
hace con una sesión ADMIN (todas son System-Admin only), o sea que **ninguna de
las nueve** ejecutaba nunca esa segunda invalidación: el mecanismo estaba
entregado y no lo llamaba nadie (el patrón nº5 de
``docs/03-guides/verificar-antes-de-implementar.md``).

Por qué se prueba con el gate de la web del córtex
--------------------------------------------------
Porque ahí la consecuencia tiene nombre: ``PUT /owner/cortex/autonomy``
(``put_autonomy``) abre su sesión con ``get_admin_sessionmaker()``, así que en la
dirección ON → OFF el córtex podía conservar sus herramientas web **hasta 30 s
después** de que el owner cortase el gate. Un kill-switch de egress con retardo.
La misma ventana afectaba a ``max_review_retries`` (un límite de seguridad) y a
los budgets.

El test reproduce la ventana entera contra PostgreSQL y Redis de verdad: no vale
comprobar que el callback «se registra», porque registrarlo es justo lo que ya
pasaba antes del arreglo.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from alembic import command
from api_server.db import platform_settings as ps
from api_server.db.models import User
from api_server.db.platform_settings import (
    CORTEX_WEB_ENABLED_KEY,
    get_platform_setting,
    set_platform_setting,
)
from api_server.db.session import get_admin_sessionmaker, reset_engine_cache
from sqlalchemy import text
from uuid6 import uuid7

pytestmark = pytest.mark.integration


@pytest.fixture()
def _wired(
    alembic_config: object,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """La BD migrada y el proceso apuntando a ella, como en producción.

    Se usan las factorías REALES (``get_admin_sessionmaker``) y no un
    ``async_sessionmaker`` de usar y tirar: el arreglo vive precisamente en la
    clase de sesión que esas factorías construyen, así que un arnés que fabrique
    la suya no probaría nada.
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")
    monkeypatch.setenv("API_SERVER_SSO_ENCRYPTION_KEY", "test-sso-encryption-key")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings

    def _reset() -> None:
        get_settings.cache_clear()
        reset_engine_cache()
        reset_redis_cache()
        ps.reset_platform_setting_cache_binding()

    _reset()
    try:
        yield
    finally:
        _reset()


async def _seed_admin_and_value(value: bool) -> User:
    """Un System Admin y el gate ya comiteado en ``value``."""
    admin = User(
        id=uuid7(),
        email=f"owner-{uuid7()}@example.test",
        password_hash="x",
        is_system_admin=True,
    )
    sm = get_admin_sessionmaker()
    async with sm() as session, session.begin():
        await session.execute(text("TRUNCATE platform_settings"))
        session.add(admin)
        await session.flush()
        await set_platform_setting(session, CORTEX_WEB_ENABLED_KEY, value, actor=admin)
    return admin


@pytest.mark.asyncio
async def test_a_gate_turned_off_on_an_admin_session_is_not_served_stale(_wired: None) -> None:
    """ON → OFF por la vía de ``put_autonomy``: el siguiente lector ve OFF.

    La secuencia es la del hallazgo, paso a paso:

      1. el gate está en ON y comiteado;
      2. el owner lo pone en OFF sobre una sesión ADMIN — ``set_platform_setting``
         hace ``flush`` e invalida la caché;
      3. **antes del commit**, un turno concurrente lee el gate: la BD todavía
         dice ON, así que recachea ON con TTL de 30 s;
      4. el escritor comitea.

    Al terminar el paso 4 el gate DEBE leerse OFF. Sin la segunda invalidación se
    lee ON hasta que expire el TTL: treinta segundos de egress con el interruptor
    ya bajado.
    """
    admin = await _seed_admin_and_value(True)
    sm = get_admin_sessionmaker()

    async with sm() as session:
        assert await get_platform_setting(session, CORTEX_WEB_ENABLED_KEY) is True

    async with sm() as writer, writer.begin():
        actor = await writer.get(User, admin.id)
        assert actor is not None
        await set_platform_setting(writer, CORTEX_WEB_ENABLED_KEY, False, actor=actor)

        # El turno concurrente. Sesión y conexión distintas: en READ COMMITTED ve
        # la fila vieja, que es exactamente lo que hace peligrosa la ventana.
        async with sm() as reader:
            assert await get_platform_setting(reader, CORTEX_WEB_ENABLED_KEY) is True, (
                "el lector concurrente debería ver el valor viejo todavía sin "
                "comitear; si ve el nuevo, la ventana que este test reproduce no "
                "es la que se creía y el test no vale"
            )

    async with sm() as session:
        assert await get_platform_setting(session, CORTEX_WEB_ENABLED_KEY) is False, (
            "el gate se leyó ENCENDIDO después de apagarlo: la sesión admin no "
            "drenó la invalidación post-commit y el córtex conserva la web hasta "
            "30 s más"
        )


@pytest.mark.asyncio
async def test_a_rolled_back_write_does_not_invalidate_anything(_wired: None) -> None:
    """Lo que no se comitea no dispara el callback.

    La otra mitad del contrato, y la que impide el arreglo perezoso de «drenar
    siempre al cerrar»: si la transacción se deshace, el valor bueno es el viejo
    y no hay nada que invalidar. Se comprueba por el efecto observable —el gate
    sigue leyéndose ON— y no por el registro del callback.
    """
    admin = await _seed_admin_and_value(True)
    sm = get_admin_sessionmaker()

    class _BoomError(RuntimeError):
        pass

    with pytest.raises(_BoomError):
        async with sm() as writer, writer.begin():
            actor = await writer.get(User, admin.id)
            assert actor is not None
            await set_platform_setting(writer, CORTEX_WEB_ENABLED_KEY, False, actor=actor)
            raise _BoomError

    async with sm() as session:
        assert await get_platform_setting(session, CORTEX_WEB_ENABLED_KEY) is True
