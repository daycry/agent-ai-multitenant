"""Córtex F4 — platform settings de autonomía (kill-switch + budget + breaker).

Los SIETE getters de gobierno (ADR 0078) deben devolver los DEFAULTS SEGUROS sin
ninguna fila en ``platform_settings`` (autonomía OFF, enable OFF, **approval gate
ON**) y un Tenant Admin NO puede escribirlos (``PlatformSettingForbiddenError``).
Patrón de ``test_max_review_retries_scope.py``.

Los tres últimos (``cortex.curiosity_enabled``, ``cortex.curiosity_approval_gate``
y ``cortex.curiosity_daily_usd_cap``) faltaban por completo — la auditoría del
2026-07-27 los cazó: el docstring de este fichero decía «los seis getters» y solo
comprobaba cuatro. Sin el approval gate no hay Sub-fase 4.0: es la pieza que deja
que la PRIMERA búsqueda autónoma espere el visto bueno del owner en vez de salir
sola a la web.

El default del gate es el que más importa fijar con un test: si algún día alguien
lo pone en ``False`` «para que el bucle fluya», el córtex empieza a gastar sin que
nadie lo apruebe y ningún error avisa.
"""

from __future__ import annotations

import pytest
from alembic import command
from api_server.db.models import User
from api_server.db.platform_settings import (
    CORTEX_AUTONOMY_ENABLED_KEY,
    DEFAULT_CORTEX_AUTONOMY_ENABLED,
    DEFAULT_CORTEX_CURIOSITY_APPROVAL_GATE,
    DEFAULT_CORTEX_CURIOSITY_CB_FAILS,
    DEFAULT_CORTEX_CURIOSITY_DAILY_SEARCHES_CAP,
    DEFAULT_CORTEX_CURIOSITY_DAILY_USD_CAP,
    DEFAULT_CORTEX_CURIOSITY_DRIVE_THRESHOLD,
    DEFAULT_CORTEX_CURIOSITY_ENABLED,
    PlatformSettingForbiddenError,
    get_cortex_autonomy_enabled,
    get_cortex_curiosity_approval_gate,
    get_cortex_curiosity_cb_fails,
    get_cortex_curiosity_daily_searches_cap,
    get_cortex_curiosity_daily_usd_cap,
    get_cortex_curiosity_drive_threshold,
    get_cortex_curiosity_enabled,
    set_platform_setting,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from uuid6 import uuid7

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _make_users(session: async_sessionmaker) -> tuple[User, User]:
    system_admin = User(
        id=uuid7(),
        email=f"sysadmin-{uuid7()}@example.test",
        password_hash="x",
        is_system_admin=True,
    )
    tenant_admin = User(
        id=uuid7(),
        email=f"tadmin-{uuid7()}@example.test",
        password_hash="x",
        is_system_admin=False,
    )
    async with session() as s, s.begin():
        await s.execute(text("TRUNCATE platform_settings"))
        s.add_all([system_admin, tenant_admin])
    return system_admin, tenant_admin


@pytest.mark.asyncio
async def test_defaults_are_safe_when_unset(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as s, s.begin():
            await s.execute(text("TRUNCATE platform_settings"))
        async with sm() as s:
            # KILL-SWITCH apagado por defecto (no hay autonomía sin opt-in explícito).
            assert await get_cortex_autonomy_enabled(s) is False
            assert DEFAULT_CORTEX_AUTONOMY_ENABLED is False
            assert await get_cortex_curiosity_daily_searches_cap(s) == (
                DEFAULT_CORTEX_CURIOSITY_DAILY_SEARCHES_CAP
            )
            assert await get_cortex_curiosity_drive_threshold(s) == (
                DEFAULT_CORTEX_CURIOSITY_DRIVE_THRESHOLD
            )
            assert await get_cortex_curiosity_cb_fails(s) == DEFAULT_CORTEX_CURIOSITY_CB_FAILS
            # El enable de la curiosidad es SEPARADO del kill-switch global: se puede
            # dejar la autonomía encendida (reflexión/mantenimiento) con la curiosidad
            # —la única que gasta egress— apagada.
            assert await get_cortex_curiosity_enabled(s) is False
            assert DEFAULT_CORTEX_CURIOSITY_ENABLED is False
            # APPROVAL GATE **ON** por defecto: la primera búsqueda espera al owner.
            # Es el único default de este grupo que arranca en True, y por eso el
            # test lo afirma dos veces (el getter y la constante).
            assert await get_cortex_curiosity_approval_gate(s) is True
            assert DEFAULT_CORTEX_CURIOSITY_APPROVAL_GATE is True
            assert await get_cortex_curiosity_daily_usd_cap(s) == (
                DEFAULT_CORTEX_CURIOSITY_DAILY_USD_CAP
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_admin_can_flip_kill_switch(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)
        async with sm() as s, s.begin():
            await set_platform_setting(s, CORTEX_AUTONOMY_ENABLED_KEY, True, actor=system_admin)
        async with sm() as s:
            assert await get_cortex_autonomy_enabled(s) is True
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_admin_cannot_flip_kill_switch(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        _, tenant_admin = await _make_users(sm)
        async with sm() as s, s.begin():
            with pytest.raises(PlatformSettingForbiddenError):
                await set_platform_setting(s, CORTEX_AUTONOMY_ENABLED_KEY, True, actor=tenant_admin)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_typed_getters_sanitize_bad_values(_migrated: None, admin_database_url: str) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)
        async with sm() as s, s.begin():
            # Valores fuera de rango / no numéricos → saneados, nunca rompen.
            from api_server.db.platform_settings import (
                CORTEX_CURIOSITY_CB_FAILS_KEY,
                CORTEX_CURIOSITY_DAILY_SEARCHES_CAP_KEY,
                CORTEX_CURIOSITY_DRIVE_THRESHOLD_KEY,
            )

            await set_platform_setting(
                s, CORTEX_CURIOSITY_DAILY_SEARCHES_CAP_KEY, -3, actor=system_admin
            )
            await set_platform_setting(
                s, CORTEX_CURIOSITY_DRIVE_THRESHOLD_KEY, 5.0, actor=system_admin
            )
            await set_platform_setting(s, CORTEX_CURIOSITY_CB_FAILS_KEY, 0, actor=system_admin)
        async with sm() as s:
            assert await get_cortex_curiosity_daily_searches_cap(s) == 0  # < 0 → 0
            assert await get_cortex_curiosity_drive_threshold(s) == 1.0  # > 1 → 1
            assert await get_cortex_curiosity_cb_fails(s) == 1  # < 1 → 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_usd_cap_sanitizes_bad_values(_migrated: None, admin_database_url: str) -> None:
    """El cap de gasto no puede volverse negativo ni romper por un valor sucio.

    Un cap negativo escrito a mano (o por un cliente descuidado) tiene que
    comportarse como 0 —«no gastes»— y NO como «sin tope»: es dinero real. Un
    valor no numérico cae al default seguro, nunca levanta dentro del bucle de
    fondo (que corre sin nadie mirando)."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)
        from api_server.db.platform_settings import CORTEX_CURIOSITY_DAILY_USD_CAP_KEY

        async with sm() as s, s.begin():
            await set_platform_setting(
                s, CORTEX_CURIOSITY_DAILY_USD_CAP_KEY, -1.5, actor=system_admin
            )
        async with sm() as s:
            assert await get_cortex_curiosity_daily_usd_cap(s) == 0.0  # < 0 → 0
        async with sm() as s, s.begin():
            await set_platform_setting(
                s, CORTEX_CURIOSITY_DAILY_USD_CAP_KEY, "no-soy-un-numero", actor=system_admin
            )
        async with sm() as s:
            assert await get_cortex_curiosity_daily_usd_cap(s) == (
                DEFAULT_CORTEX_CURIOSITY_DAILY_USD_CAP
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_approval_gate_and_enable_are_flippable_by_system_admin(
    _migrated: None, admin_database_url: str
) -> None:
    """El owner (System Admin) puede encender la curiosidad y bajar el gate.

    Es la contrapartida del default seguro: si el gate no se pudiera bajar, la
    curiosidad autónoma sería inalcanzable y el bucle no pasaría nunca del paso 7."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)
        from api_server.db.platform_settings import (
            CORTEX_CURIOSITY_APPROVAL_GATE_KEY,
            CORTEX_CURIOSITY_ENABLED_KEY,
        )

        async with sm() as s, s.begin():
            await set_platform_setting(s, CORTEX_CURIOSITY_ENABLED_KEY, True, actor=system_admin)
            await set_platform_setting(
                s, CORTEX_CURIOSITY_APPROVAL_GATE_KEY, False, actor=system_admin
            )
        async with sm() as s:
            assert await get_cortex_curiosity_enabled(s) is True
            assert await get_cortex_curiosity_approval_gate(s) is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_admin_cannot_lower_the_approval_gate(
    _migrated: None, admin_database_url: str
) -> None:
    """Bajar el gate de aprobación es privilegio de System Admin, como el kill-switch.

    Sin esta comprobación, la clave nueva podría haber quedado escribible por
    cualquier Tenant Admin: el gate es una salvaguarda de coste/egress del
    despliegue entero, no una preferencia de tenant (ADR 0074/0078)."""
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        _, tenant_admin = await _make_users(sm)
        from api_server.db.platform_settings import CORTEX_CURIOSITY_APPROVAL_GATE_KEY

        async with sm() as s, s.begin():
            with pytest.raises(PlatformSettingForbiddenError):
                await set_platform_setting(
                    s, CORTEX_CURIOSITY_APPROVAL_GATE_KEY, False, actor=tenant_admin
                )
    finally:
        await engine.dispose()
