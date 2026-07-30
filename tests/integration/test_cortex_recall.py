"""Córtex F1 — recall asociativo híbrido (BM25 + vector + entidad, RRF).

El recall del córtex reutiliza ``memorizer.recall.recall`` con ``scopes=('private',)``
y ``user_id=owner`` (el filtro de ``_scope_filter_sql`` garantiza el aislamiento por
owner), y filtra además ``metadata_.cortex=true`` para no mezclar memoria del córtex
con la del asistente. Este test siembra memorias del córtex del owner + memorias
``private`` de OTRO usuario en el mismo tenant y comprueba que ``cortex_recall``
(sin ``query_embedding`` → BM25+entidad) devuelve SOLO las del owner, ordenadas por
RRF, y NUNCA las del otro usuario.

Espejo de ``test_assistant_memory.py::test_recall_is_isolated_per_user`` pero con
recall híbrido y el discriminante ``cortex=true``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

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

    from api_server.main import create_app

    app = create_app()
    try:
        yield app
    finally:
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_owner_and_other(dsn: str) -> dict[str, UUID]:
    """One owner + tenant + membership; plus a second 'other' user in the SAME tenant."""
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, cortex_turns, cortex_conversations,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Recall Tenant",
            "cortex-recall-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, $3), ($4, $5, $6)",
            owner_id,
            "owner@recall.test",
            "h",
            other_id,
            "other@recall.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin'), ($4, $5, $6, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
            uuid4(),
            tenant_id,
            other_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


def _admin_sessionmaker(admin_database_url: str):
    import api_server.db.session as session_mod
    from api_server.config import get_settings

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    return session_mod.get_admin_sessionmaker()


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_recall_hybrid_owner_only(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex import memory
    from api_server.db.memory import MemoryEntry  # noqa: F401 (ensure model imported)

    seed = await _seed_owner_and_other(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]
    tenant_id = seed["tenant_id"]

    sessionmaker = _admin_sessionmaker(admin_database_url)

    # --- Seed: owner's córtex memories (cortex=true) + a non-córtex private one ---
    async with sessionmaker() as session:
        await memory.cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="Al owner le interesa la arquitectura hexagonal y los puertos",
        )
        await memory.cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="El owner prefiere arquitectura limpia en sus proyectos",
        )
        await memory.cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="Al owner le gusta el cafe por la mañana",
        )
        await session.commit()

    # A NON-córtex private memory of the owner (e.g. assistant-written) that mentions
    # the query terms — it must NOT surface (cortex=true discriminator).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id, metadata)"
            " VALUES ($1, $2, 'private', 'semantic', $3, $4, $5::jsonb)",
            uuid4(),
            tenant_id,
            "Nota del asistente sobre arquitectura hexagonal (NO del cortex)",
            owner_id,
            '{"source": "assistant"}',
        )
        # The OTHER user's private memory in the SAME tenant — same terms, must NEVER surface.
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id, metadata)"
            " VALUES ($1, $2, 'private', 'semantic', $3, $4, $5::jsonb)",
            uuid4(),
            tenant_id,
            "Al otro usuario le interesa la arquitectura hexagonal tambien",
            other_id,
            '{"source": "cortex", "cortex": true}',
        )
    finally:
        await conn.close()

    # --- Recall (no embedding → BM25 + entity path) ---
    async with sessionmaker() as session:
        hits = await memory.cortex_recall(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            query="arquitectura hexagonal del owner",
            limit=8,
        )

    # Only the owner's córtex memories that match come back.
    assert "Al owner le interesa la arquitectura hexagonal y los puertos" in hits
    assert "El owner prefiere arquitectura limpia en sus proyectos" in hits
    # The owner's NON-córtex private memory must NOT surface.
    assert "Nota del asistente sobre arquitectura hexagonal (NO del cortex)" not in hits
    # The OTHER user's memory must NEVER surface, even with cortex=true + same terms.
    assert all("otro usuario" not in h for h in hits)
    # The most relevant (most query overlap) ranks first.
    assert hits[0] == "Al owner le interesa la arquitectura hexagonal y los puertos"


# ---------------------------------------------------------------------------
# recall_frequency real: el recall instrumenta el uso (SOLO devueltos, SOLO owner)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recall_incrementa_contador_solo_en_devueltos_y_solo_owner(
    configured_app, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    from api_server.cortex.memory import cortex_recall, cortex_remember

    seed = await _seed_owner_and_other(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]
    tenant_id = seed["tenant_id"]

    sessionmaker = _admin_sessionmaker(admin_database_url)
    async with sessionmaker() as session:
        await cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="Al owner le interesa la arquitectura hexagonal",
        )
        # Memoria del owner SIN relación con la query: no debe contarse.
        await cortex_remember(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            content="zzz qqq televisor amarillo",
        )
        # Memoria del OTRO usuario con el MISMO contenido: jamás se cuenta.
        await cortex_remember(
            session,
            owner_user_id=other_id,
            tenant_id=tenant_id,
            content="Al owner le interesa la arquitectura hexagonal",
        )
        await session.commit()

    async with sessionmaker() as session:
        facts = await cortex_recall(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            query="arquitectura hexagonal",
            limit=1,
        )
        await session.commit()
    assert facts == ["Al owner le interesa la arquitectura hexagonal"]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT user_id, content, metadata FROM memory_entries WHERE deleted_at IS NULL"
        )
    finally:
        await conn.close()

    import json

    counters: dict[tuple[str, str], dict] = {}
    for row in rows:
        meta = json.loads(row["metadata"]) if isinstance(row["metadata"], str) else row["metadata"]
        counters[(str(row["user_id"]), row["content"])] = meta

    devuelta = counters[(str(owner_id), "Al owner le interesa la arquitectura hexagonal")]
    assert devuelta.get("recall_count") == 1
    assert devuelta.get("last_recalled_at")

    no_devuelta = counters[(str(owner_id), "zzz qqq televisor amarillo")]
    assert "recall_count" not in no_devuelta

    ajena = counters[(str(other_id), "Al owner le interesa la arquitectura hexagonal")]
    assert "recall_count" not in ajena
