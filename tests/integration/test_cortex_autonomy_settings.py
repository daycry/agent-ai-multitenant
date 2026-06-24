"""Córtex F4 — platform settings de autonomía (kill-switch + budget + breaker).

Los seis getters de gobierno (ADR 0078) deben devolver los DEFAULTS SEGUROS sin
ninguna fila en ``platform_settings`` (autonomía OFF por defecto) y un Tenant Admin
NO puede escribirlos (``PlatformSettingForbiddenError``). Patrón de
``test_max_review_retries_scope.py``.
"""

from __future__ import annotations

import pytest
from alembic import command
from api_server.db.models import User
from api_server.db.platform_settings import (
    CORTEX_AUTONOMY_ENABLED_KEY,
    DEFAULT_CORTEX_AUTONOMY_ENABLED,
    DEFAULT_CORTEX_CURIOSITY_CB_FAILS,
    DEFAULT_CORTEX_CURIOSITY_DAILY_SEARCHES_CAP,
    DEFAULT_CORTEX_CURIOSITY_DRIVE_THRESHOLD,
    PlatformSettingForbiddenError,
    get_cortex_autonomy_enabled,
    get_cortex_curiosity_cb_fails,
    get_cortex_curiosity_daily_searches_cap,
    get_cortex_curiosity_drive_threshold,
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
