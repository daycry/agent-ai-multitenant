"""Córtex F3 (bloque 2) — tarea de reflexión periódica (workers.cortex_reflect).

Ejercita el núcleo ``_reflect_async`` contra la BD real con un ``llm_factory``
falso (sin red):

  * happy path: lee los turnos recientes del owner + identidad → el LLM propone
    una narrativa reescrita + delta de traits/baseline; la aplicación es
    determinista y ACOTADA (un delta grande propuesto se recorta a
    ``BASELINE_MAX_DELTA_PER_REFLECTION``); versiona en ``cortex_identity_history``
    (updated_by='reflection') y persiste una memoria ``kind='reflection'``;
  * **fail-open**: un ``llm_factory`` que lanza ⇒ no-op (identidad intacta, sin
    nueva versión) y la tarea devuelve ``ok:fail_open``;
  * sin turnos recientes ⇒ no-op limpio;
  * **cross-owner**: la reflexión del owner NUNCA toca la identidad de otro.

Patrón de fixtures tomado de ``test_cortex_affect_task.py``.
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
    """Reflexión stub determinista; cuenta las llamadas a complete()."""

    name = "fake-cortex-reflection-llm"

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
async def _seed_owner_with_turns(
    dsn: str, *, n_turns: int = 2, second_owner: bool = False
) -> dict[str, UUID]:
    owner_id = uuid4()
    other_id = uuid4()
    tenant_id = uuid4()
    conv_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_identity_history, cortex_identity, cortex_affect_snapshots,"
            " cortex_turns, cortex_conversations, memory_entries, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Cortex Reflect Tenant",
            "cortex-reflect-tenant",
        )
        # El owner es el system_owner (singleton); el segundo, un usuario normal.
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true), ($4, $5, $6, false)",
            owner_id,
            "owner@reflect.test",
            "h",
            other_id,
            "other@reflect.test",
            "h",
        )
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id)"
            " VALUES ($1, $2, $3)",
            conv_id,
            owner_id,
            tenant_id,
        )
        for i in range(n_turns):
            await conn.execute(
                "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content,"
                " created_at) VALUES ($1, $2, $3, $4, $5, now() + ($6 || ' seconds')::interval)",
                uuid4(),
                conv_id,
                owner_id,
                "user" if i % 2 == 0 else "cortex",
                f"turno {i}: el owner valora el rigor y la honestidad",
                str(i),
            )
        if second_owner:
            # Identidad pre-existente del OTRO usuario, que la reflexión del owner
            # nunca debe tocar.
            await conn.execute(
                "INSERT INTO cortex_identity"
                " (id, owner_user_id, identity_state, version, updated_by, onboarded_at)"
                " VALUES ($1, $2, $3::jsonb, 3, 'reflection', now())",
                uuid4(),
                other_id,
                '{"name": "Eco", "narrative": "no tuya",'
                ' "traits": {"openness": 0.5, "conscientiousness": 0.5, "extraversion": 0.5,'
                ' "agreeableness": 0.5, "neuroticism": 0.5},'
                ' "mood_baseline": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}}',
            )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


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


# A proposal with a HUGE jump (must be clamped to ±0.05 per cycle).
_BIG_JUMP_JSON = (
    '{"narrative": "He aprendido que el owner valora el rigor y la honestidad.",'
    ' "traits": {"openness": 0.95, "conscientiousness": 0.95},'
    ' "mood_baseline": {"valence": 0.9, "arousal": 0.9, "dominance": 0.0},'
    ' "summary": "El owner valora el rigor."}'
)


# ---------------------------------------------------------------------------
# Happy path — narrativa reescrita + delta clampeado + versión + memoria
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reflection_rewrites_narrative_and_clamps_delta(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_owner_with_turns(migrations_pg_dsn, n_turns=4)
    owner_id = seed["owner_id"]

    fake = _FakeLLM(content=_BIG_JUMP_JSON)
    from workers.cortex_reflection import _reflect_async

    result = await _reflect_async(owner_id, settings=workers_settings, llm_factory=lambda _s: fake)
    assert result["reason"] == "ok", result
    assert fake.calls == 1
    assert fake.closed is True

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT version, updated_by, identity_state FROM cortex_identity"
            " WHERE owner_user_id = $1",
            owner_id,
        )
        hist = await conn.fetch(
            "SELECT version, updated_by, reason FROM cortex_identity_history"
            " WHERE owner_user_id = $1 ORDER BY version ASC",
            owner_id,
        )
        mem = await conn.fetchrow(
            "SELECT scope, user_id, type, metadata FROM memory_entries"
            " WHERE user_id = $1 ORDER BY created_at DESC LIMIT 1",
            owner_id,
        )
    finally:
        await conn.close()

    import json

    assert row is not None
    assert row["version"] == 1
    assert row["updated_by"] == "reflection"
    state = (
        json.loads(row["identity_state"])
        if isinstance(row["identity_state"], str)
        else row["identity_state"]
    )
    # Narrativa reescrita por la reflexión.
    assert "rigor" in state["narrative"]
    # Delta CLAMPEADO: 0.5 → 0.55 (no 0.95) pese al salto grande propuesto.
    assert abs(state["traits"]["openness"] - 0.55) < 1e-9
    assert abs(state["traits"]["conscientiousness"] - 0.55) < 1e-9
    # un trait no propuesto se conserva neutro.
    assert state["traits"]["neuroticism"] == 0.5
    # baseline también acotado.
    assert abs(state["mood_baseline"]["valence"] - 0.05) < 1e-9
    assert abs(state["mood_baseline"]["arousal"] - 0.05) < 1e-9

    # Versionado: una fila history v1 de la reflexión.
    assert [h["version"] for h in hist] == [1]
    assert hist[0]["updated_by"] == "reflection"

    # Memoria kind='reflection' persistida (ADR 0077: protegida del olvido).
    assert mem is not None
    assert mem["scope"] == "private"
    assert mem["type"] == "semantic"
    meta = json.loads(mem["metadata"]) if isinstance(mem["metadata"], str) else mem["metadata"]
    assert meta["cortex"] is True
    assert meta["kind"] == "reflection"


# ---------------------------------------------------------------------------
# Fail-open — el LLM lanza ⇒ no-op (identidad intacta, sin versión)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reflection_fail_open_when_llm_raises(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_owner_with_turns(migrations_pg_dsn, n_turns=2)
    owner_id = seed["owner_id"]

    boom = _BoomLLM()
    from workers.cortex_reflection import _reflect_async

    result = await _reflect_async(owner_id, settings=workers_settings, llm_factory=lambda _s: boom)
    assert result["reason"] == "ok:fail_open", result
    assert boom.closed is True

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # ensure_identity pudo crear la default (version 0), pero la reflexión NO
        # la versiona en fail-open: sin filas history.
        hist = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity_history WHERE owner_user_id = $1", owner_id
        )
        version = await conn.fetchval(
            "SELECT version FROM cortex_identity WHERE owner_user_id = $1", owner_id
        )
    finally:
        await conn.close()
    assert hist == 0
    assert version == 0  # default sin reescritura


@pytest.mark.asyncio
async def test_reflection_fail_open_on_unparseable_json(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_owner_with_turns(migrations_pg_dsn, n_turns=2)
    owner_id = seed["owner_id"]

    junk = _FakeLLM(content="no puedo ayudarte con eso")
    from workers.cortex_reflection import _reflect_async

    result = await _reflect_async(owner_id, settings=workers_settings, llm_factory=lambda _s: junk)
    assert result["reason"] == "ok:fail_open", result

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        hist = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity_history WHERE owner_user_id = $1", owner_id
        )
    finally:
        await conn.close()
    assert hist == 0


# ---------------------------------------------------------------------------
# Sin turnos recientes ⇒ no-op limpio
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reflection_no_turns_is_clean_noop(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_owner_with_turns(migrations_pg_dsn, n_turns=0)
    owner_id = seed["owner_id"]

    fake = _FakeLLM(content=_BIG_JUMP_JSON)
    from workers.cortex_reflection import _reflect_async

    result = await _reflect_async(owner_id, settings=workers_settings, llm_factory=lambda _s: fake)
    assert result["reason"] == "skipped:no_recent_turns", result
    # Sin turnos no se llama al LLM (no se gasta cuota).
    assert fake.calls == 0


# ---------------------------------------------------------------------------
# Cross-owner — la reflexión del owner nunca toca la identidad de otro
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reflection_is_cross_owner_isolated(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_owner_with_turns(migrations_pg_dsn, n_turns=2, second_owner=True)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]

    fake = _FakeLLM(content=_BIG_JUMP_JSON)
    from workers.cortex_reflection import _reflect_async

    result = await _reflect_async(owner_id, settings=workers_settings, llm_factory=lambda _s: fake)
    assert result["reason"] == "ok", result

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        eco = await conn.fetchrow(
            "SELECT version, identity_state->>'name' AS name FROM cortex_identity"
            " WHERE owner_user_id = $1",
            other_id,
        )
        other_hist = await conn.fetchval(
            "SELECT count(*) FROM cortex_identity_history WHERE owner_user_id = $1", other_id
        )
    finally:
        await conn.close()
    # La identidad del OTRO usuario quedó intacta (versión + nombre + sin nuevas
    # filas history).
    assert eco["name"] == "Eco"
    assert eco["version"] == 3
    assert other_hist == 0


# ---------------------------------------------------------------------------
# Trigger — encola y traga errores de broker
# ---------------------------------------------------------------------------
def test_trigger_enqueues(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import cortex_reflection as mod

    calls: list[Any] = []

    def _fake_apply_async(*args: Any, **kwargs: Any) -> None:
        calls.append(kwargs.get("args"))

    monkeypatch.setattr(mod.cortex_reflect, "apply_async", _fake_apply_async)
    assert mod.trigger_cortex_reflection(uuid4()) is True
    assert len(calls) == 1


def test_trigger_swallows_broker_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import cortex_reflection as mod

    def _boom(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(mod.cortex_reflect, "apply_async", _boom)
    assert mod.trigger_cortex_reflection(uuid4()) is False


# ---------------------------------------------------------------------------
# Owner model — "aprender DE MÍ": relationship_model acotado + memorias
# ---------------------------------------------------------------------------
_OWNER_MODEL_JSON = (
    '{"narrative": "He aprendido más sobre mi owner.",'
    ' "owner_model": {"prefiere": "evidencia primero", "obsoleto": ""},'
    ' "owner_facts": ["El owner construye una plataforma multi-tenant."],'
    ' "summary": "Aprendí del owner."}'
)


@pytest.mark.asyncio
async def test_reflection_actualiza_owner_model_y_persiste_memorias(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seed = await _seed_owner_with_turns(migrations_pg_dsn, n_turns=2, second_owner=True)
    owner_id = seed["owner_id"]
    other_id = seed["other_id"]

    # Identidad previa del owner con relationship_model que se actualiza y
    # des-aprende ("obsoleto": "" en la propuesta lo borra).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_identity (id, owner_user_id, identity_state, version,"
            " updated_by, created_at, updated_at) VALUES ($1, $2, $3::jsonb, 1,"
            " 'onboarding', now(), now())",
            uuid4(),
            owner_id,
            '{"name": "Lumen", "relationship_model":'
            ' {"prefiere": "brevedad", "obsoleto": "dato viejo"}}',
        )
    finally:
        await conn.close()

    fake = _FakeLLM(content=_OWNER_MODEL_JSON)
    from workers.cortex_reflection import _reflect_async

    result = await _reflect_async(owner_id, settings=workers_settings, llm_factory=lambda _s: fake)
    assert result["reason"] == "ok", result

    import json

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT identity_state FROM cortex_identity WHERE owner_user_id = $1",
            owner_id,
        )
        hist = await conn.fetchrow(
            "SELECT diff FROM cortex_identity_history WHERE owner_user_id = $1"
            " ORDER BY version DESC LIMIT 1",
            owner_id,
        )
        mems = await conn.fetch(
            "SELECT content, metadata FROM memory_entries WHERE user_id = $1"
            " AND metadata->>'kind' = 'owner_model'",
            owner_id,
        )
        other_row = await conn.fetchrow(
            "SELECT identity_state, version FROM cortex_identity WHERE owner_user_id = $1",
            other_id,
        )
    finally:
        await conn.close()

    state = (
        json.loads(row["identity_state"])
        if isinstance(row["identity_state"], str)
        else row["identity_state"]
    )
    # Actualizado + des-aprendido, sin tocar el resto del estado.
    assert state["relationship_model"] == {"prefiere": "evidencia primero"}
    assert state["name"] == "Lumen"

    # El cambio queda auditado en el diff de la history.
    diff = json.loads(hist["diff"]) if isinstance(hist["diff"], str) else hist["diff"]
    assert "relationship_model" in diff

    # La memoria kind='owner_model' se escribió y está PROTEGIDA del olvido.
    assert len(mems) == 1
    assert "plataforma multi-tenant" in mems[0]["content"]
    meta = (
        json.loads(mems[0]["metadata"])
        if isinstance(mems[0]["metadata"], str)
        else mems[0]["metadata"]
    )
    assert meta["cortex"] is True
    from api_server.cortex.forgetting import PROTECTED_KINDS

    assert meta["kind"] in PROTECTED_KINDS

    # Cross-owner: la identidad del otro usuario queda EXACTAMENTE igual.
    other_state = (
        json.loads(other_row["identity_state"])
        if isinstance(other_row["identity_state"], str)
        else other_row["identity_state"]
    )
    assert other_row["version"] == 3
    assert other_state["narrative"] == "no tuya"
    assert "relationship_model" not in other_state or not other_state.get("relationship_model")
