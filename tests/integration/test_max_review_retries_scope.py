"""Integration tests: max_review_retries is a platform-scoped setting
(task_02_13b).

`max_review_retries` is a hard platform limit (spec §7.9): only a System
Admin may change it — a Tenant Admin cannot. These tests exercise the
`platform_settings` service with real System Admin and Tenant Admin
users against real Postgres.
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from api_server.db.models import PlatformSetting, User
from api_server.db.platform_settings import (
    DEFAULT_MAX_REVIEW_RETRIES,
    MAX_REVIEW_RETRIES_KEY,
    PlatformSettingForbiddenError,
    get_max_review_retries,
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
    """A System Admin and a Tenant Admin; the platform_settings table is
    cleared so each test starts from the unset state."""
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
async def test_system_admin_can_set_max_review_retries(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)

        async with sm() as s, s.begin():
            await set_platform_setting(s, MAX_REVIEW_RETRIES_KEY, 5, actor=system_admin)

        async with sm() as s:
            assert await get_max_review_retries(s) == 5
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_tenant_admin_cannot_set_max_review_retries(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        _, tenant_admin = await _make_users(sm)

        with pytest.raises(PlatformSettingForbiddenError):
            async with sm() as s, s.begin():
                await set_platform_setting(s, MAX_REVIEW_RETRIES_KEY, 99, actor=tenant_admin)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_rejected_write_leaves_the_value_unchanged(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        _, tenant_admin = await _make_users(sm)

        with pytest.raises(PlatformSettingForbiddenError):
            async with sm() as s, s.begin():
                await set_platform_setting(s, MAX_REVIEW_RETRIES_KEY, 99, actor=tenant_admin)

        # The forbidden write must not have leaked through.
        async with sm() as s:
            assert await get_max_review_retries(s) == DEFAULT_MAX_REVIEW_RETRIES
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_max_review_retries_defaults_when_unset(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        await _make_users(sm)  # clears platform_settings

        async with sm() as s:
            assert await get_max_review_retries(s) == DEFAULT_MAX_REVIEW_RETRIES == 3
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_setting_records_the_admin_who_changed_it(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)

        async with sm() as s, s.begin():
            await set_platform_setting(s, MAX_REVIEW_RETRIES_KEY, 4, actor=system_admin)

        async with sm() as s:
            row = await s.get(PlatformSetting, MAX_REVIEW_RETRIES_KEY)
        assert row is not None
        assert row.updated_by == system_admin.id
        assert row.value == 4
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_system_admin_can_update_an_existing_setting(
    _migrated: None, admin_database_url: str
) -> None:
    engine = create_async_engine(admin_database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        system_admin, _ = await _make_users(sm)

        async with sm() as s, s.begin():
            await set_platform_setting(s, MAX_REVIEW_RETRIES_KEY, 3, actor=system_admin)
        async with sm() as s, s.begin():
            await set_platform_setting(s, MAX_REVIEW_RETRIES_KEY, 7, actor=system_admin)

        async with sm() as s:
            assert await get_max_review_retries(s) == 7
    finally:
        await engine.dispose()


async def _exec(dsn: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


def test_migration_0011_is_reversible(alembic_config: object, admin_pg_dsn: str) -> None:
    """downgrade to 0010 then back up to head must both succeed — CON DATOS.

    La bajada hasta 0010 arrastra 128 revisiones, así que esto no prueba sólo la
    0011: prueba que la cadena entera va y vuelve. Y tiene que probarlo sobre una
    base **con filas**, porque la de producción las tiene y la regla dura de
    ``CLAUDE.md`` («no desplegar sin comprobar que las migraciones son
    reversibles») no admite el matiz «reversible si la tabla está vacía».

    Se siembra a propósito la fila que rompía: una configuración **SAML**. La
    0033 introdujo SAML relajando ``issuer`` y ``client_id`` a NULL, y su
    ``downgrade`` los volvía a poner NOT NULL confiando en que «no puede haber
    filas saml al bajar». Con una sola, la bajada moría con
    ``column "client_id" … contains null values``. Arreglado el 2026-08-18 en la
    propia migración (borra lo que el esquema de destino no sabe representar).

    Antes de este cambio el test NO sembraba nada, así que sólo se ponía rojo
    cuando **otro fichero** de la sesión dejaba una fila SAML atrás
    (``test_key_rotation_drill.py``, que siembra una y no la retira; la base de
    integración es de ámbito sesión y se comparte). Es decir: pasaba en
    solitario, fallaba en la suite completa, y parecía «flaky de orden» cuando
    lo que denunciaba era un defecto real de la cadena de migraciones. Sembrar
    aquí lo vuelve determinista y deja de depender de la contaminación ajena.
    """
    command.upgrade(alembic_config, "head")

    asyncio.run(
        _exec(
            admin_pg_dsn,
            """
            INSERT INTO sso_configurations
                (id, provider, display_name, enabled, idp_entity_id,
                 idp_sso_url, idp_x509_cert)
            VALUES ($1, 'saml', 'Reversibility SAML', true, 'urn:rev:idp',
                    'https://idp.test/sso', 'MIIB-fake-cert')
            """,
            uuid7(),
        )
    )

    command.downgrade(alembic_config, "0010_executions")
    command.upgrade(alembic_config, "head")
