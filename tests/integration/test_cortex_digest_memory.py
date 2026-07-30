"""Córtex F4 — la memoria de aprendizaje de la curiosidad: aislamiento real.

`persist_learning_memory` escribe el digest de una persecución como memoria
`semantic` de scope `private` del OWNER. La aceptación del plan pedía tres cosas y
la única sin prueba era la tercera: «la memoria nace con `user_id=owner` y NO es
visible para otro user» (auditoría 2026-07-27).

## Qué mecanismo protege qué (y por qué el test tiene DOS mitades)

Es importante no contarlo mal, porque el enunciado del plan dice «bajo RLS» y RLS
NO es lo que aísla a dos usuarios del mismo tenant:

  * **RLS de `memory_entries` aísla por TENANT**, y solo por tenant: la política
    `memory_entries_tenant_isolation` (migración 0020) compara
    `tenant_id = current_setting('app.tenant_id')`. Otro tenant no ve la fila ni
    con un `SELECT *`. Eso es la primera mitad y se comprueba con una sesión del
    usuario de aplicación (no la de migraciones, que es BYPASSRLS).
  * **Entre dos usuarios del MISMO tenant**, el aislamiento del scope `private` lo
    pone la capa de recall (`_scope_filter_sql`: `scope='private' AND user_id = :u`),
    no la base de datos. Un `SELECT` crudo del otro usuario en el mismo tenant SÍ ve
    la fila — la defensa es que ninguna consulta del producto la pide así. Eso es la
    segunda mitad, y se comprueba por donde de verdad se lee: `cortex_recall`.

Escribir un solo test que afirmase «RLS oculta la memoria al otro usuario» habría
sido un test que documenta una creencia falsa: pasaría hoy por la razón equivocada
(los dos usuarios de una prueba suelen estar en tenants distintos) y bendeciría la
idea de que no hace falta filtrar por `user_id` en el recall. Aquí se separan.

## Y la idempotencia

Se comprueba también por la vía del contrato (mismo `pursuit_id` dos veces ⇒ una
sola fila), porque es la salvaguarda que hace que un reintento de Celery —la
re-entrega por visibility timeout es de ~7h por diseño— no duplique la memoria.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def configured_app(
    alembic_config,
    app_database_url: str,
    admin_database_url: str,
    test_redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _flush_redis, _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())
    asyncio.run(_flush_redis(test_redis_url))

    monkeypatch.setenv("API_SERVER_DATABASE_URL", app_database_url)
    monkeypatch.setenv("API_SERVER_ADMIN_DATABASE_URL", admin_database_url)
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    monkeypatch.setenv("API_SERVER_JWT_SECRET", "test-secret")

    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    yield
    reset_engine_cache()
    reset_redis_cache()
    get_settings.cache_clear()


async def _seed(dsn: str) -> dict[str, UUID]:
    """Owner + otro usuario en el MISMO tenant, y un SEGUNDO tenant ajeno.

    Los tres actores son necesarios: el mismo tenant prueba el filtro de recall, el
    otro tenant prueba RLS."""
    ids = {
        "owner_id": uuid4(),
        "other_id": uuid4(),
        "tenant_id": uuid4(),
        "other_tenant_id": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, cortex_curiosity_pursuits, cortex_turns,"
            " cortex_conversations, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'Digest Tenant', 'digest-tenant'), ($2, 'Ajeno', 'digest-ajeno')",
            ids["tenant_id"],
            ids["other_tenant_id"],
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner) VALUES"
            " ($1, 'owner@digest.test', 'h', true), ($2, 'other@digest.test', 'h', false)",
            ids["owner_id"],
            ids["other_id"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role) VALUES"
            " ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            ids["tenant_id"],
            ids["owner_id"],
            uuid4(),
            ids["tenant_id"],
            ids["other_id"],
        )
    finally:
        await conn.close()
    return ids


def _admin_sessionmaker(admin_database_url: str):
    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    return session_mod.get_admin_sessionmaker()


async def _write_learning(
    admin_database_url: str,
    *,
    owner_id: UUID,
    tenant_id: UUID,
    pursuit_id: UUID,
    digest: str = "Aprendí que Rust libera memoria con ownership, sin recolector.",
) -> UUID | None:
    from api_server.cortex.curiosity import persist_learning_memory

    sessionmaker = _admin_sessionmaker(admin_database_url)
    async with sessionmaker() as session, session.begin():
        return await persist_learning_memory(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            topic="rust",
            digest=digest,
            pursuit_id=pursuit_id,
            entities=("rust",),
        )


# ---------------------------------------------------------------------------
# Forma de la fila + idempotencia
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_escribe_una_semantic_learning_del_owner_y_es_idempotente(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Una fila `semantic` / `private` / `kind='learning'` con el pursuit, y una sola.

    La segunda llamada con el MISMO `pursuit_id` devuelve el id existente sin
    escribir: es lo que evita que un reintento de Celery (la re-entrega por
    visibility timeout es de ~7h por diseño) duplique la memoria y el córtex
    "aprenda" dos veces lo mismo."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = uuid4()

    first = await _write_learning(
        admin_database_url,
        owner_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        pursuit_id=pursuit_id,
    )
    second = await _write_learning(
        admin_database_url,
        owner_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        pursuit_id=pursuit_id,
        digest="Otro texto distinto para el mismo pursuit",
    )
    assert first is not None
    assert first == second

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT scope, type, user_id, tenant_id, metadata, entities, tags"
            " FROM memory_entries WHERE metadata->>'cortex_pursuit_id' = $1",
            str(pursuit_id),
        )
    finally:
        await conn.close()

    assert len(rows) == 1  # idempotente: NO se escribió una segunda fila
    row = rows[0]
    assert row["scope"] == "private"
    assert row["type"] == "semantic"
    assert row["user_id"] == seed["owner_id"]
    assert row["tenant_id"] == seed["tenant_id"]

    import json

    meta = json.loads(row["metadata"])
    assert meta["cortex"] is True
    assert meta["kind"] == "learning"  # protegida del olvido (ADR 0077)
    assert meta["source"] == "cortex_curiosity"
    assert meta["topic"] == "rust"


@pytest.mark.asyncio
async def test_un_digest_vacio_no_escribe_nada(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Sin digest no hay memoria: `None` y cero filas.

    El bucle llega aquí cuando la búsqueda no devolvió nada útil; escribir una
    memoria vacía ensuciaría el recall del owner con ruido que nunca caduca (las
    `learning` están PROTEGIDAS del olvido, así que una fila basura se queda para
    siempre)."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = uuid4()

    written = await _write_learning(
        admin_database_url,
        owner_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        pursuit_id=pursuit_id,
        digest="   \n  ",
    )
    assert written is None

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval("SELECT count(*) FROM memory_entries")
    finally:
        await conn.close()
    assert count == 0


# ---------------------------------------------------------------------------
# Mitad 1 — RLS: otro TENANT no ve la fila (la base de datos lo impide)
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_otro_tenant_no_ve_la_memoria_de_aprendizaje_bajo_rls(
    configured_app, migrations_pg_dsn: str, admin_database_url: str, app_database_url: str
) -> None:
    """Con `app.tenant_id` de otro tenant, la fila no existe: RLS la esconde.

    Esta es la mitad que RLS sí cubre. Se usa la conexión del usuario de
    APLICACIÓN a propósito: la de migraciones es BYPASSRLS y con ella el test
    pasaría vacío (vería todo y no probaría nada). Para que no pueda pasar por
    casualidad, se comprueba primero que el MISMO SELECT con el tenant correcto sí
    la encuentra."""
    seed = await _seed(migrations_pg_dsn)
    pursuit_id = uuid4()
    await _write_learning(
        admin_database_url,
        owner_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        pursuit_id=pursuit_id,
    )

    engine = create_async_engine(app_database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        query = sa_text(
            "SELECT count(*) FROM memory_entries WHERE metadata->>'cortex_pursuit_id' = :pid"
        )

        # Tenant CORRECTO: la ve (si no, el test siguiente pasaría vacíamente).
        async with factory() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(seed["tenant_id"])},
            )
            visible = await session.scalar(query, {"pid": str(pursuit_id)})
        assert visible == 1

        # Tenant AJENO: RLS la oculta por completo.
        async with factory() as session:
            await session.execute(
                sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
                {"tid": str(seed["other_tenant_id"])},
            )
            hidden = await session.scalar(query, {"pid": str(pursuit_id)})
        assert hidden == 0
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Mitad 2 — cross-owner dentro del MISMO tenant: lo impide el filtro del recall
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_otro_usuario_del_mismo_tenant_no_recupera_el_aprendizaje_del_owner(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """El recall del OTRO usuario no devuelve la `learning` del owner, ni buscándola.

    Aquí RLS no ayuda (mismo tenant): lo que aísla es el filtro `scope='private'
    AND user_id` del recall. La consulta usa las palabras exactas del digest para
    que un fallo del filtro se manifieste como un hit, no como un empate de
    relevancia. Y se comprueba el caso simétrico —el owner SÍ la recupera— para que
    el test no pueda pasar porque el recall esté devolviendo listas vacías."""
    from api_server.cortex.memory import cortex_recall

    seed = await _seed(migrations_pg_dsn)
    digest = "Aprendí que Rust libera memoria con ownership, sin recolector"
    await _write_learning(
        admin_database_url,
        owner_id=seed["owner_id"],
        tenant_id=seed["tenant_id"],
        pursuit_id=uuid4(),
        digest=digest,
    )

    sessionmaker = _admin_sessionmaker(admin_database_url)
    async with sessionmaker() as session:
        del_owner = await cortex_recall(
            session,
            owner_user_id=seed["owner_id"],
            tenant_id=seed["tenant_id"],
            query="rust ownership memoria recolector",
            limit=8,
        )
        del_otro = await cortex_recall(
            session,
            owner_user_id=seed["other_id"],
            tenant_id=seed["tenant_id"],
            query="rust ownership memoria recolector",
            limit=8,
        )

    # El owner recupera lo que su córtex aprendió…
    assert any("ownership" in hit for hit in del_owner)
    # …y el otro usuario del mismo tenant no ve ni rastro.
    assert del_otro == [] or all("ownership" not in hit for hit in del_otro)
