"""Córtex F2 — distilador afectivo asíncrono (workers.cortex_distill_affect).

Ejercita el núcleo ``_distill_affect_async`` contra la BD real con un
``llm_factory`` falso (sin red) + el Redis de test:

  * happy path: parsea el JSON de Ollama → delta; el motor aplica
    apply_event/update_mood/satisfy_drive; escribe un snapshot en
    ``cortex_affect_snapshots`` (con ``source_turn_id`` + ``appraisal_reason``),
    refresca la caché Redis viva, publica un frame de telemetría y deja una
    episódica emocional en ``memory_entries`` (metadata_.cortex + emotion);
  * **fail-open**: un ``llm_factory`` que lanza ⇒ delta 0, snapshot con
    ``appraisal_reason=NULL`` y la tarea devuelve ``ok:fail_open`` (no propaga);
  * **idempotencia**: re-entregar el mismo ``turn_id`` no duplica snapshot
    (UNIQUE parcial → ``ok:already_distilled``, sin segunda llamada al LLM);
  * el trigger ``trigger_cortex_distill_affect`` encola y traga errores de broker.

Patrón de fixtures tomado de ``test_memorizer_trigger.py`` + ``test_cortex_affect_store.py``.
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
# Fakes
# ---------------------------------------------------------------------------
class _FakeLLM:
    """Distilador stub determinista; cuenta las llamadas a complete()."""

    name = "fake-cortex-affect-llm"

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


class _BoomLLM(_FakeLLM):
    """complete() lanza — ejercita el camino fail-open."""

    def __init__(self) -> None:
        super().__init__(content="")

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.calls += 1
        raise RuntimeError("ollama is down")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed_turn(dsn: str, *, cortex_text: str = "Hola owner.") -> dict[str, UUID]:
    """Owner + tenant + hilo + turno user + turno cortex. Devuelve el turn_id cortex."""
    owner_id = uuid4()
    tenant_id = uuid4()
    conv_id = uuid4()
    user_turn_id = uuid4()
    cortex_turn_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_affect_snapshots, cortex_turns, cortex_conversations,"
            " memory_entries, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Affect Tenant",
            "cortex-affect-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true)",
            owner_id,
            "owner@affect.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id)"
            " VALUES ($1, $2, $3)",
            conv_id,
            owner_id,
            tenant_id,
        )
        # user turn primero (created_at anterior), luego cortex turn.
        await conn.execute(
            "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content,"
            " created_at) VALUES ($1, $2, $3, 'user', 'me encanta tu trabajo', now())",
            user_turn_id,
            conv_id,
            owner_id,
        )
        await conn.execute(
            "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content,"
            " created_at) VALUES ($1, $2, $3, 'cortex', $4, now() + interval '1 second')",
            cortex_turn_id,
            conv_id,
            owner_id,
            cortex_text,
        )
    finally:
        await conn.close()
    return {
        "owner_id": owner_id,
        "tenant_id": tenant_id,
        "conv_id": conv_id,
        "cortex_turn_id": cortex_turn_id,
    }


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


@pytest_asyncio.fixture()
async def api_redis(test_redis_url: str, monkeypatch: pytest.MonkeyPatch):
    """Apunta el cliente Redis del api-server (que usa el distilador) al de test."""
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


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_distill_applies_delta_and_writes_snapshot(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_turn(migrations_pg_dsn)
    turn_id = seed["cortex_turn_id"]

    fake = _FakeLLM(
        content=(
            '{"delta": {"valence": 0.4, "arousal": 0.2, "dominance": 0.1, '
            '"intensity": 0.3}, "reason": "el owner me elogió", '
            '"drive_satisfied": "bonding", "drive_amount": 0.2}'
        )
    )

    from workers.cortex_affect import _distill_affect_async

    result = await _distill_affect_async(
        turn_id, settings=workers_settings, llm_factory=lambda _s: fake
    )
    assert result["reason"] == "ok", result
    assert fake.calls == 1
    assert fake.closed is True

    # Snapshot escrito con source_turn_id + appraisal_reason + delta aplicado.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT owner_user_id, valence, appraisal_reason, source_turn_id, mood_label,"
            " drives FROM cortex_affect_snapshots WHERE source_turn_id = $1",
            turn_id,
        )
        # episódica emocional escrita en memory_entries.
        mem = await conn.fetchrow(
            "SELECT scope, user_id, type, metadata FROM memory_entries"
            " WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1",
            seed["owner_id"],
        )
    finally:
        await conn.close()

    assert row is not None
    assert row["owner_user_id"] == seed["owner_id"]
    assert row["valence"] > 0.0  # el delta positivo subió valence desde baseline 0
    assert row["appraisal_reason"] == "el owner me elogió"
    assert row["mood_label"]

    assert mem is not None
    assert mem["scope"] == "private"
    assert mem["user_id"] == seed["owner_id"]
    assert mem["type"] == "episodic"
    import json

    meta = json.loads(mem["metadata"]) if isinstance(mem["metadata"], str) else mem["metadata"]
    assert meta["cortex"] is True
    assert "emotion" in meta
    assert meta["emotion"]["appraisal_reason"] == "el owner me elogió"

    # caché viva Redis poblada + frame de telemetría publicado.
    from api_server.cortex.affect_cache import affect_cache_key
    from api_server.events import cortex_telemetry_stream_key

    owner = str(seed["owner_id"])
    assert await api_redis.get(affect_cache_key(owner)) is not None
    frames = await api_redis.xrange(cortex_telemetry_stream_key(owner))
    assert len(frames) == 1
    assert frames[0][1]["type"] == "affect"


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_distill_fail_open_when_llm_raises(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_turn(migrations_pg_dsn)
    turn_id = seed["cortex_turn_id"]

    boom = _BoomLLM()
    from workers.cortex_affect import _distill_affect_async

    result = await _distill_affect_async(
        turn_id, settings=workers_settings, llm_factory=lambda _s: boom
    )
    assert result["reason"] == "ok:fail_open", result
    assert boom.closed is True  # el provider se cerró pese a la excepción

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT valence, appraisal_reason FROM cortex_affect_snapshots"
            " WHERE source_turn_id = $1",
            turn_id,
        )
    finally:
        await conn.close()
    assert row is not None
    # delta 0 desde baseline 0 ⇒ valence ≈ 0; razón NULL (fail-open).
    assert row["appraisal_reason"] is None
    assert abs(row["valence"]) < 1e-6


@pytest.mark.asyncio
async def test_distill_fail_open_on_unparseable_json(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_turn(migrations_pg_dsn)
    turn_id = seed["cortex_turn_id"]

    junk = _FakeLLM(content="lo siento, no puedo ayudarte con eso")
    from workers.cortex_affect import _distill_affect_async

    result = await _distill_affect_async(
        turn_id, settings=workers_settings, llm_factory=lambda _s: junk
    )
    assert result["reason"] == "ok:fail_open", result

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        reason = await conn.fetchval(
            "SELECT appraisal_reason FROM cortex_affect_snapshots WHERE source_turn_id = $1",
            turn_id,
        )
    finally:
        await conn.close()
    assert reason is None


# ---------------------------------------------------------------------------
# Idempotencia (re-entrega del mismo turno)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_distill_is_idempotent_per_turn(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_turn(migrations_pg_dsn)
    turn_id = seed["cortex_turn_id"]

    fake = _FakeLLM(
        content='{"delta": {"valence": 0.3, "arousal": 0.1, "dominance": 0.0, "intensity": 0.2},'
        ' "reason": "neutro", "drive_satisfied": "null", "drive_amount": 0}'
    )
    from workers.cortex_affect import _distill_affect_async

    first = await _distill_affect_async(
        turn_id, settings=workers_settings, llm_factory=lambda _s: fake
    )
    assert first["reason"] == "ok"
    assert fake.calls == 1

    # Re-entrega del MISMO turno: UNIQUE parcial → already_distilled, sin 2ª llamada.
    second = await _distill_affect_async(
        turn_id, settings=workers_settings, llm_factory=lambda _s: fake
    )
    assert second["reason"] == "ok:already_distilled", second

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM cortex_affect_snapshots WHERE source_turn_id = $1",
            turn_id,
        )
    finally:
        await conn.close()
    assert count == 1  # no duplicado


@pytest.mark.asyncio
async def test_distill_missing_turn_is_clean_skip(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    fake = _FakeLLM(content="{}")
    from workers.cortex_affect import _distill_affect_async

    result = await _distill_affect_async(
        uuid4(), settings=workers_settings, llm_factory=lambda _s: fake
    )
    assert result["reason"] == "skipped:turn_not_found"
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Trigger
# ---------------------------------------------------------------------------
def test_trigger_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import cortex_affect as mod

    calls: list[Any] = []

    def _fake_apply_async(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs.get("args"))

    monkeypatch.setattr(mod.cortex_distill_affect, "apply_async", _fake_apply_async)
    assert mod.trigger_cortex_distill_affect(uuid4()) is True
    assert len(calls) == 1


def test_trigger_swallows_broker_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import cortex_affect as mod

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(mod.cortex_distill_affect, "apply_async", _boom)
    assert mod.trigger_cortex_distill_affect(uuid4()) is False
