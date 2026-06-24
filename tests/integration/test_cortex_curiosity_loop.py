"""Córtex F4 — bucle de curiosidad autónoma (workers.cortex_curiosity_loop).

Ejercita el núcleo ``_run_curiosity_loop`` contra la BD real + Redis de test, con
``search_fn`` y ``llm_factory`` FALSOS (sin red, sin egress):

  * **kill-switch OFF** ⇒ no-op (no busca, no toca BD);
  * **web_enabled OFF** ⇒ no-op (la curiosidad respeta el gate de la web);
  * **drive satisfecho** (curiosity alto) ⇒ no-op;
  * **budget agotado** ⇒ pursuit ``skipped``, no busca;
  * **camino feliz**: web_search→digest→memoria ``learning``→drive saciado→pursuit
    ``digested``; el ``search_fn`` se llamó (egress simulado), el budget se consumió;
  * **cross-owner**: el bucle solo toca al owner real (filtro owner_user_id).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from redis.asyncio import Redis
from shared_llm.types import CompletionResponse, Message, StreamChunk, Usage

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fakes (sin red)
# ---------------------------------------------------------------------------
class _FakeLLM:
    name = "fake-curiosity-llm"

    def __init__(self, content: str) -> None:
        self._content = content
        self.calls = 0
        self.closed = False

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.calls += 1
        return CompletionResponse(
            content=self._content,
            model="fake-model",
            provider=self.name,
            usage=Usage(),
            tool_calls=None,
            raw={},
        )

    async def stream(
        self, messages: Sequence[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:  # pragma: no cover
        yield StreamChunk(delta="", usage=None)

    async def aclose(self) -> None:
        self.closed = True


class _FakeSearch:
    """search_fn doble: registra las llamadas y devuelve resultados fijos."""

    def __init__(self, results: list[dict[str, str]] | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self._results = (
            results
            if results is not None
            else [
                {"title": "Rust ownership", "url": "https://example.test/rust", "snippet": "borrow"}
            ]
        )

    async def __call__(self, query: str, limit: int) -> list[dict[str, str]]:
        self.calls.append((query, limit))
        return self._results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


@pytest_asyncio.fixture()
async def api_redis(test_redis_url: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("API_SERVER_REDIS_URL", test_redis_url)
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings

    get_settings.cache_clear()
    reset_redis_cache()
    client: Redis = Redis.from_url(test_redis_url, decode_responses=True)
    await client.flushdb()
    try:
        yield client
    finally:
        await client.flushdb()
        await client.aclose()
        reset_redis_cache()
        get_settings.cache_clear()


async def _set_settings(dsn: str, *, autonomy: bool, web: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE platform_settings")
        await conn.execute(
            "INSERT INTO platform_settings (key, value) VALUES"
            " ('cortex.autonomy_enabled', $1::jsonb), ('cortex.web_enabled', $2::jsonb)",
            "true" if autonomy else "false",
            "true" if web else "false",
        )
    finally:
        await conn.close()


async def _seed(dsn: str, *, curiosity: float = 0.1, with_entities: bool = True) -> dict[str, UUID]:
    """Owner + tenant + hilo + (opcional) una memoria con entities + snapshot con el
    drive curiosity al nivel pedido."""
    owner_id = uuid4()
    other_owner_id = uuid4()
    tenant_id = uuid4()
    conv_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_curiosity_pursuits, cortex_affect_snapshots, memory_entries,"
            " cortex_turns, cortex_conversations, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Cur Tenant', 'cur-tenant')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, 'owner@cur.test', 'h', true)",
            owner_id,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, 'other@cur.test', 'h', false)",
            other_owner_id,
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id) VALUES ($1, $2, $3)",
            conv_id,
            owner_id,
            tenant_id,
        )
        if with_entities:
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
                " entities, metadata) VALUES ($1, $2, 'private', 'semantic', 'le gusta rust',"
                " $3, '[\"rust\"]'::jsonb, '{\"cortex\": true}'::jsonb)",
                uuid4(),
                tenant_id,
                owner_id,
            )
            # Cross-owner: el OTRO owner menciona 'kubernetes' — no debe elegirse.
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
                " entities, metadata) VALUES ($1, $2, 'private', 'semantic', 'k8s',"
                " $3, '[\"kubernetes\"]'::jsonb, '{\"cortex\": true}'::jsonb)",
                uuid4(),
                tenant_id,
                other_owner_id,
            )
        # Snapshot con el drive curiosity al nivel pedido.
        await conn.execute(
            "INSERT INTO cortex_affect_snapshots (id, owner_user_id, valence, arousal,"
            " dominance, intensity, mood_valence, mood_arousal, mood_dominance, mood_label,"
            " drives) VALUES ($1, $2, 0.0, 0.3, 0.0, 0.0, 0.0, 0.3, 0.0, 'neutral', $3::jsonb)",
            uuid4(),
            owner_id,
            f'{{"curiosity": {curiosity}, "bonding": 0.5, "coherence": 0.5, "competence": 0.5}}',
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_owner_id": other_owner_id, "tenant_id": tenant_id}


async def _pursuits(dsn: str, owner_id: UUID) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetch(
            "SELECT topic, status, learning_memory_id FROM cortex_curiosity_pursuits"
            " WHERE owner_user_id = $1 ORDER BY created_at",
            owner_id,
        )
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kill_switch_off_is_noop(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn, curiosity=0.1)
    await _set_settings(migrations_pg_dsn, autonomy=False, web=True)
    fake = _FakeLLM("aprendí")
    search = _FakeSearch()

    from workers.cortex_curiosity import _run_curiosity_loop

    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: fake, search_fn=search
    )
    assert result == {"skipped": "disabled"}
    assert search.calls == []  # NO buscó (no egress)
    assert await _pursuits(migrations_pg_dsn, seed["owner_id"]) == []


@pytest.mark.asyncio
async def test_web_disabled_is_noop(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    await _seed(migrations_pg_dsn, curiosity=0.1)
    await _set_settings(migrations_pg_dsn, autonomy=True, web=False)
    search = _FakeSearch()

    from workers.cortex_curiosity import _run_curiosity_loop

    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: _FakeLLM("x"), search_fn=search
    )
    assert result == {"skipped": "web_disabled"}
    assert search.calls == []


@pytest.mark.asyncio
async def test_drive_satisfied_is_noop(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    # curiosity alto (0.9) → por encima del umbral (0.35) → no hay hambre.
    await _seed(migrations_pg_dsn, curiosity=0.9)
    await _set_settings(migrations_pg_dsn, autonomy=True, web=True)
    search = _FakeSearch()

    from workers.cortex_curiosity import _run_curiosity_loop

    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: _FakeLLM("x"), search_fn=search
    )
    assert result["skipped"] == "drive_satisfied"
    assert search.calls == []


@pytest.mark.asyncio
async def test_budget_exhausted_skips(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn, curiosity=0.1)
    await _set_settings(migrations_pg_dsn, autonomy=True, web=True)

    # Agota el budget de búsquedas (default cap 5) en Redis.
    from datetime import UTC, datetime

    from api_server.cortex.autonomy import record_searches

    await record_searches(
        api_redis, owner_user_id=str(seed["owner_id"]), count=5, now=datetime.now(UTC)
    )
    search = _FakeSearch()

    from workers.cortex_curiosity import _run_curiosity_loop

    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: _FakeLLM("x"), search_fn=search
    )
    assert result["skipped"] == "budget"
    assert search.calls == []
    rows = await _pursuits(migrations_pg_dsn, seed["owner_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "skipped"


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_happy_path_searches_digests_persists_and_satisfies(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn, curiosity=0.1)
    await _set_settings(migrations_pg_dsn, autonomy=True, web=True)
    fake = _FakeLLM("Aprendí que Rust usa ownership para gestionar memoria sin GC.")
    search = _FakeSearch()

    from workers.cortex_curiosity import _run_curiosity_loop

    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: fake, search_fn=search
    )
    assert result.get("digested") is True, result
    assert result["topic"] == "rust"  # la entity del owner (NO 'kubernetes' del otro)
    assert search.calls and search.calls[0][0] == "rust"  # se buscó por el egress simulado
    assert fake.calls == 1 and fake.closed is True

    # Pursuit 'digested' con learning_memory_id.
    rows = await _pursuits(migrations_pg_dsn, seed["owner_id"])
    assert len(rows) == 1
    assert rows[0]["status"] == "digested"
    assert rows[0]["learning_memory_id"] is not None

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Memoria learning escrita (semantic, kind='learning', cortex_pursuit_id).
        mem = await conn.fetchrow(
            "SELECT type, metadata FROM memory_entries WHERE user_id = $1"
            " AND metadata->>'kind' = 'learning'",
            seed["owner_id"],
        )
        # El budget se consumió (1 búsqueda).
        from datetime import UTC, datetime

        from api_server.cortex.autonomy import CURIOSITY_KIND, daily_budget_key

        budget_used = await api_redis.get(
            daily_budget_key(str(seed["owner_id"]), CURIOSITY_KIND, now=datetime.now(UTC))
        )
        # Snapshot de satisfacción del drive escrito (curiosity subió por encima de 0.1).
        snap = await conn.fetchrow(
            "SELECT drives FROM cortex_affect_snapshots WHERE owner_user_id = $1"
            " ORDER BY created_at DESC LIMIT 1",
            seed["owner_id"],
        )
    finally:
        await conn.close()

    assert mem is not None
    assert mem["type"] == "semantic"
    import json

    meta = json.loads(mem["metadata"])
    assert meta["kind"] == "learning"
    assert meta["cortex"] is True
    assert "cortex_pursuit_id" in meta
    assert int(budget_used) == 1
    drives = json.loads(snap["drives"])
    assert drives["curiosity"] > 0.1  # el drive se sació


@pytest.mark.asyncio
async def test_research_failure_marks_failed_and_trips_breaker(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Un fallo de la búsqueda/digest NO propaga (best-effort): marca el pursuit
    'failed' e incrementa el circuit-breaker; tras N fallos el bucle deja de
    intentar."""
    seed = await _seed(migrations_pg_dsn, curiosity=0.1)
    await _set_settings(migrations_pg_dsn, autonomy=True, web=True)

    async def _boom(query: str, limit: int) -> list[dict[str, str]]:
        raise RuntimeError("searxng is down")

    from workers.cortex_curiosity import _run_curiosity_loop

    # Default cb_fails = 3 → tres fallos abren el breaker. (El tema 'rust' sigue
    # eligible tras un fallo: el dedup solo excluye temas ya 'digested'.)
    for _ in range(3):
        result = await _run_curiosity_loop(
            workers_settings, llm_factory=lambda _s: _FakeLLM("x"), search_fn=_boom
        )
        assert result.get("failed") == "research_error", result

    # El breaker quedó abierto → la siguiente pasada sale 'circuit_open'.
    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: _FakeLLM("x"), search_fn=_boom
    )
    assert result["skipped"] == "circuit_open"

    rows = await _pursuits(migrations_pg_dsn, seed["owner_id"])
    assert len(rows) == 3  # tres pursuits, todos failed (la 4ª ni se insertó)
    assert all(r["status"] == "failed" for r in rows)


@pytest.mark.asyncio
async def test_learning_memory_is_idempotent_by_pursuit_id(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Re-escribir el digest con el MISMO pursuit_id es un no-op (ADR 0078): no
    duplica la memoria learning (dedup por metadata_->>'cortex_pursuit_id')."""
    seed = await _seed(migrations_pg_dsn, curiosity=0.1)
    from uuid import uuid4 as _uuid4

    from api_server.cortex.curiosity import persist_learning_memory
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    pursuit_id = _uuid4()
    eng = create_async_engine(workers_settings.database_url)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with sm() as s, s.begin():
            m1 = await persist_learning_memory(
                s,
                owner_user_id=seed["owner_id"],
                tenant_id=seed["tenant_id"],
                topic="rust",
                digest="ownership sin GC",
                pursuit_id=pursuit_id,
            )
        async with sm() as s, s.begin():
            m2 = await persist_learning_memory(
                s,
                owner_user_id=seed["owner_id"],
                tenant_id=seed["tenant_id"],
                topic="rust",
                digest="ownership sin GC (otra vez)",
                pursuit_id=pursuit_id,
            )
        assert m1 == m2  # mismo id → no se escribió una segunda fila
    finally:
        await eng.dispose()

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE metadata->>'cortex_pursuit_id' = $1",
            str(pursuit_id),
        )
    finally:
        await conn.close()
    assert n == 1


@pytest.mark.asyncio
async def test_no_topic_when_no_entities_is_noop(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    await _seed(migrations_pg_dsn, curiosity=0.1, with_entities=False)
    await _set_settings(migrations_pg_dsn, autonomy=True, web=True)
    search = _FakeSearch()

    from workers.cortex_curiosity import _run_curiosity_loop

    result = await _run_curiosity_loop(
        workers_settings, llm_factory=lambda _s: _FakeLLM("x"), search_fn=search
    )
    assert result["skipped"] == "no_topic"
    assert search.calls == []
