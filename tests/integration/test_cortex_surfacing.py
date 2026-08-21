"""Córtex — surfacing de curiosidad: "te abro el tema en el próximo encuentro".

El lazo que faltaba de F4 (ADR 0078): un pursuit ``digested`` sin ``surfaced_at``
se inyecta al self-context del SIGUIENTE turno (tema + digest de su memoria
``learning``), se marca ``surfaced`` EN LA MISMA transacción del turno (si el LLM
falla, rollback ⇒ sigue pendiente), y no se re-inyecta después. Cross-owner
OBLIGATORIO: el pursuit de otro owner jamás aparece.
"""

from __future__ import annotations

import asyncio
import json
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from uuid6 import uuid7

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
        app.dependency_overrides.clear()
        reset_engine_cache()
        reset_redis_cache()
        get_settings.cache_clear()


async def _seed_owner(dsn: str) -> dict[str, UUID]:
    owner_id = uuid4()
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, cortex_curiosity_pursuits, cortex_identity_history,"
            " cortex_identity, cortex_affect_snapshots, cortex_turns, cortex_conversations,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Surfacing Tenant",
            "cortex-surfacing-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true)",
            owner_id,
            "owner@surfacing.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant_id,
            owner_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "tenant_id": tenant_id}


async def _seed_pursuit(
    dsn: str,
    owner_id: UUID,
    *,
    topic: str,
    learning_memory_id: UUID | None,
    status: str = "digested",
) -> UUID:
    pursuit_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_curiosity_pursuits (id, owner_user_id, topic, status,"
            " learning_memory_id, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, now(), now())",
            pursuit_id,
            owner_id,
            topic,
            status,
            learning_memory_id,
        )
    finally:
        await conn.close()
    return pursuit_id


async def _remember(owner_id: UUID, tenant_id: UUID, content: str) -> UUID:
    import api_server.db.session as session_mod
    from api_server.config import get_settings
    from api_server.cortex.memory import cortex_remember

    get_settings.cache_clear()
    session_mod.reset_engine_cache()
    async with session_mod.get_admin_sessionmaker()() as session:
        result = await cortex_remember(
            session, owner_user_id=owner_id, tenant_id=tenant_id, content=content
        )
        await session.commit()
    return UUID(str(result["id"]))


async def _mint(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    await SessionStore(get_redis()).create(
        sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600
    )
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id, is_system_owner=True)


class _CapturingModel:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def decide(self, state):
        from api_server.assistant.graph import ModelTurn

        self.prompts.append(state.system_prompt)
        return ModelTurn(content="entendido")


class _BoomModel:
    async def decide(self, state):
        from shared_llm.exceptions import LLMError

        raise LLMError("provider down")


async def _post_turn(app, token: str, message: str) -> object:
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(
            "/owner/cortex/turns",
            json={"message": message},
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.asyncio
async def test_pursuit_digested_se_inyecta_marca_y_no_se_repite(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    memory_id = await _remember(
        owner_id, tenant_id, "La arquitectura hexagonal separa dominio de adaptadores."
    )
    pursuit_id = await _seed_pursuit(
        migrations_pg_dsn, owner_id, topic="arquitectura hexagonal", learning_memory_id=memory_id
    )

    from api_server.routers.cortex import get_cortex_model

    captured = _CapturingModel()
    configured_app.dependency_overrides[get_cortex_model] = lambda: captured
    token = await _mint(owner_id, tenant_id)

    resp = await _post_turn(configured_app, token, "buenos días")
    assert resp.status_code == 200, resp.text

    # El tema aprendido viaja al prompt (dentro de DATOS, con su digest).
    prompt = captured.prompts[0]
    assert "arquitectura hexagonal" in prompt
    assert "curiosidad" in prompt

    # El pursuit queda marcado surfaced EN la transacción del turno…
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, surfaced_at FROM cortex_curiosity_pursuits WHERE id = $1",
            pursuit_id,
        )
        turn = await conn.fetchrow(
            "SELECT metadata FROM cortex_turns WHERE owner_user_id = $1 AND role = 'cortex'"
            " ORDER BY created_at DESC LIMIT 1",
            owner_id,
        )
    finally:
        await conn.close()
    assert row is not None
    assert row["status"] == "surfaced"
    assert row["surfaced_at"] is not None
    # …y auditado en la metadata del turno.
    meta = json.loads(turn["metadata"])
    assert meta["self_context"]["surfaced_pursuits"] == [str(pursuit_id)]

    # Un segundo turno NO re-inyecta el tema (ya no está pendiente).
    resp2 = await _post_turn(configured_app, token, "seguimos")
    assert resp2.status_code == 200
    assert "curiosidad" not in captured.prompts[1]


@pytest.mark.asyncio
async def test_fallo_del_llm_hace_rollback_y_el_pursuit_sigue_pendiente(
    configured_app, migrations_pg_dsn: str
) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_id = seed["owner_id"]
    tenant_id = seed["tenant_id"]
    pursuit_id = await _seed_pursuit(
        migrations_pg_dsn, owner_id, topic="postgres rls", learning_memory_id=None
    )

    from api_server.routers.cortex import get_cortex_model

    configured_app.dependency_overrides[get_cortex_model] = _BoomModel
    token = await _mint(owner_id, tenant_id)

    resp = await _post_turn(configured_app, token, "hola")
    assert resp.status_code == 502

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, surfaced_at FROM cortex_curiosity_pursuits WHERE id = $1",
            pursuit_id,
        )
    finally:
        await conn.close()
    # Rollback: sigue pendiente de contar (comportamiento correcto gratis).
    assert row["status"] == "digested"
    assert row["surfaced_at"] is None


@pytest.mark.asyncio
@pytest.mark.cross_tenant
async def test_pursuit_de_otro_owner_jamas_aparece(configured_app, migrations_pg_dsn: str) -> None:
    seed = await _seed_owner(migrations_pg_dsn)
    owner_a = seed["owner_id"]
    tenant_id = seed["tenant_id"]

    owner_b = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, $2, 'h')",
            owner_b,
            "otro@surfacing.test",
        )
    finally:
        await conn.close()
    pursuit_b = await _seed_pursuit(
        migrations_pg_dsn, owner_b, topic="tema secreto de B", learning_memory_id=None
    )

    from api_server.routers.cortex import get_cortex_model

    captured = _CapturingModel()
    configured_app.dependency_overrides[get_cortex_model] = lambda: captured
    token = await _mint(owner_a, tenant_id)

    resp = await _post_turn(configured_app, token, "hola")
    assert resp.status_code == 200
    assert "tema secreto de B" not in captured.prompts[0]

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT status, surfaced_at FROM cortex_curiosity_pursuits WHERE id = $1",
            pursuit_b,
        )
    finally:
        await conn.close()
    # El pursuit de B queda intacto: ni inyectado ni marcado por el turno de A.
    assert row["status"] == "digested"
    assert row["surfaced_at"] is None
