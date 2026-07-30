"""Córtex F4 — tarea de mantenimiento de fondo (workers.cortex_maintenance).

Ejercita el núcleo ``_run_maintenance`` contra la BD real:

  * **kill-switch OFF (default)** ⇒ no-op total (no toca BD: ni snapshot, ni olvido);
  * **autonomy ON**: hace soft-delete de la episódica del córtex de BAJA retención
    pero **NUNCA** de identity / owner-model (ADR 0077); escribe un decay snapshot
    si el último es viejo; poda snapshots fuera de la ventana de retención;
  * **idempotente**: una segunda pasada no re-olvida ni duplica;
  * **fail-open**: cubierto por el ``try/except`` global (la tarea jamás propaga).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic import command
from redis.asyncio import Redis

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest_asyncio.fixture()
async def api_redis(test_redis_url: str, monkeypatch: pytest.MonkeyPatch):
    """Apunta el ``get_redis()`` del api-server a la Redis de test.

    La tarea de mantenimiento consulta el circuit-breaker de F4 (namespace
    ``cortex:cb:*``) con el mismo cliente que el resto del córtex. Sin esta
    redirección el gate hablaría con la Redis real del entorno — y como
    ``is_circuit_open`` es FAIL-SAFE (Redis inalcanzable ⇒ "abierto"), la tarea
    saldría no-op y los tests fallarían de forma fantasma.
    """
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


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


async def _set_autonomy(dsn: str, enabled: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE platform_settings")
        if enabled:
            await conn.execute(
                "INSERT INTO platform_settings (key, value) VALUES"
                " ('cortex.autonomy_enabled', 'true'::jsonb)"
            )
    finally:
        await conn.close()


async def _seed(dsn: str) -> dict[str, Any]:
    """Owner + tenant + memorias episódicas (vieja no protegida, identity, owner_model,
    reciente) + un snapshot afectivo viejo."""
    owner_id = uuid4()
    other_owner_id = uuid4()
    tenant_id = uuid4()
    old_episodic = uuid4()
    identity_mem = uuid4()
    owner_model_mem = uuid4()
    recent_episodic = uuid4()
    other_episodic = uuid4()
    snap_old = uuid4()
    snap_ancient = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE cortex_affect_snapshots, memory_entries, cortex_turns,"
            " cortex_conversations, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "Maint Tenant",
            "maint-tenant",
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, true)",
            owner_id,
            "owner@maint.test",
            "h",
        )
        # Otro owner (cross-owner): su episódica vieja NO debe tocarse cuando
        # mantenemos al primero — el filtro owner_id lo garantiza. NO es system owner
        # para no entrar en el barrido por su cuenta.
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, $2, $3, false)",
            other_owner_id,
            "other@maint.test",
            "h",
        )

        async def _mem(mid: UUID, uid: UUID, mtype: str, meta: str, days_old: int) -> None:
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
                " metadata, created_at) VALUES ($1, $2, 'private', $3, $4, $5, $6::jsonb,"
                " now() - ($7 || ' days')::interval)",
                mid,
                tenant_id,
                mtype,
                f"mem {mid}",
                uid,
                meta,
                str(days_old),
            )

        # Episódica vieja, no protegida, baja importancia → debe olvidarse.
        await _mem(old_episodic, owner_id, "episodic", '{"cortex": true, "importance": 0.3}', 400)
        # Identity (protegida) aunque sea vieja → NUNCA se olvida.
        await _mem(
            identity_mem,
            owner_id,
            "semantic",
            '{"cortex": true, "kind": "identity"}',
            400,
        )
        # Owner-model (protegida) → NUNCA se olvida.
        await _mem(
            owner_model_mem,
            owner_id,
            "episodic",
            '{"cortex": true, "kind": "owner_model"}',
            400,
        )
        # Episódica reciente → se retiene.
        await _mem(recent_episodic, owner_id, "episodic", '{"cortex": true, "importance": 0.8}', 1)
        # Episódica vieja de OTRO owner → cross-owner: no debe tocarse.
        await _mem(
            other_episodic, other_owner_id, "episodic", '{"cortex": true, "importance": 0.3}', 400
        )

        # Snapshot viejo (8 h) → permite escribir un decay snapshot nuevo.
        await conn.execute(
            "INSERT INTO cortex_affect_snapshots (id, owner_user_id, valence, arousal,"
            " dominance, intensity, mood_valence, mood_arousal, mood_dominance, mood_label,"
            " drives, created_at) VALUES ($1, $2, 0.5, 0.5, 0.1, 0.3, 0.2, 0.4, 0.1, 'calma',"
            " '{\"curiosity\":0.6}'::jsonb, now() - interval '8 hours')",
            snap_old,
            owner_id,
        )
        # Snapshot ANTIGUO (100 días) → debe podarse.
        await conn.execute(
            "INSERT INTO cortex_affect_snapshots (id, owner_user_id, valence, arousal,"
            " dominance, intensity, mood_valence, mood_arousal, mood_dominance, mood_label,"
            " drives, created_at) VALUES ($1, $2, 0.0, 0.3, 0.0, 0.0, 0.0, 0.3, 0.0, 'neutral',"
            " '{}'::jsonb, now() - interval '100 days')",
            snap_ancient,
            owner_id,
        )
    finally:
        await conn.close()
    return {
        "owner_id": owner_id,
        "other_owner_id": other_owner_id,
        "old_episodic": old_episodic,
        "identity_mem": identity_mem,
        "owner_model_mem": owner_model_mem,
        "recent_episodic": recent_episodic,
        "other_episodic": other_episodic,
        "snap_ancient": snap_ancient,
    }


async def _deleted_at(dsn: str, mem_id: UUID):  # type: ignore[no-untyped-def]
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT deleted_at FROM memory_entries WHERE id = $1", mem_id)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Kill-switch OFF → no-op
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_kill_switch_off_is_noop(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=False)

    from workers.cortex_maintenance import _run_maintenance

    result = await _run_maintenance(workers_settings)
    assert result == {"skipped": "disabled"}

    # La episódica vieja NO se olvidó (no se tocó nada).
    assert await _deleted_at(migrations_pg_dsn, seed["old_episodic"]) is None
    # El snapshot antiguo NO se podó.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM cortex_affect_snapshots WHERE id = $1", seed["snap_ancient"]
        )
    finally:
        await conn.close()
    assert n == 1


# ---------------------------------------------------------------------------
# Autonomy ON → olvido protege identity/owner-model; decay snapshot; poda
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_autonomy_on_forgets_low_retention_but_protects_core(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)
    now = datetime.now(UTC)

    from workers.cortex_maintenance import _run_maintenance

    result = await _run_maintenance(workers_settings, now=now)
    assert "results" in result, result
    owner_res = result["results"][0]
    assert owner_res["forgotten"] == 1  # solo la episódica vieja no protegida
    assert owner_res["decay_snapshot_written"] is True
    assert owner_res["pruned_snapshots"] == 1  # el snapshot de 100 días

    # La episódica vieja se soft-deleteó.
    assert await _deleted_at(migrations_pg_dsn, seed["old_episodic"]) is not None
    # identity / owner-model / reciente: INTACTAS (ADR 0077).
    assert await _deleted_at(migrations_pg_dsn, seed["identity_mem"]) is None
    assert await _deleted_at(migrations_pg_dsn, seed["owner_model_mem"]) is None
    assert await _deleted_at(migrations_pg_dsn, seed["recent_episodic"]) is None
    # Cross-owner: la episódica vieja del OTRO owner NO se tocó.
    assert await _deleted_at(migrations_pg_dsn, seed["other_episodic"]) is None


@pytest.mark.asyncio
async def test_maintenance_is_idempotent(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)
    now = datetime.now(UTC)

    from workers.cortex_maintenance import _run_maintenance

    first = await _run_maintenance(workers_settings, now=now)
    # Segunda pasada inmediata: no re-olvida (ya soft-deleted) ni re-escribe decay
    # snapshot (el recién escrito está dentro de la ventana de gap).
    second = await _run_maintenance(workers_settings, now=now)

    assert first["results"][0]["forgotten"] == 1
    assert second["results"][0]["forgotten"] == 0
    assert second["results"][0]["decay_snapshot_written"] is False

    # Una sola fila olvidada en total (no se duplicó el efecto).
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        forgotten = await conn.fetchval(
            "SELECT count(*) FROM memory_entries WHERE user_id = $1 AND deleted_at IS NOT NULL",
            seed["owner_id"],
        )
    finally:
        await conn.close()
    assert forgotten == 1


# ---------------------------------------------------------------------------
# recall_frequency real en el mantenimiento: el uso salva a la memoria
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_forget_usa_recall_count_para_retener_lo_usado(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, True)
    owner_id = seed["owner_id"]

    # Dos episódicas de 45 días (importance default 0.5): la nunca-recallada cae
    # bajo el umbral (0.5*0.35*0.5≈0.088<0.1); la recallada 5 veces se salva
    # (0.5*0.35*1.0≈0.177).
    nunca = uuid4()
    usada = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant_id = await conn.fetchval("SELECT id FROM organizations LIMIT 1")
        for mid, meta in (
            (nunca, '{"cortex": true}'),
            (usada, '{"cortex": true, "recall_count": 5}'),
        ):
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
                " metadata, created_at) VALUES ($1, $2, 'private', 'episodic', $3, $4,"
                " $5::jsonb, now() - interval '45 days')",
                mid,
                tenant_id,
                f"mem {mid}",
                owner_id,
                meta,
            )
    finally:
        await conn.close()

    from workers.cortex_maintenance import _run_maintenance

    await _run_maintenance(workers_settings)

    assert await _deleted_at(migrations_pg_dsn, nunca) is not None
    assert await _deleted_at(migrations_pg_dsn, usada) is None


# ---------------------------------------------------------------------------
# D1 end-to-end: las dos dimensiones nuevas llegan al SWEEP, no sólo al score
#
# Los unitarios de `tests/unit/test_cortex_forgetting.py` fijan la función pura;
# estos comprueban que el barrido le pasa el `metadata_` real, con los datos que
# escriben sus productores de verdad (el distilador afectivo de F2 y el recall de
# F1). Sin esta mitad, la dimensión podría estar impecable y no cambiar nada en
# producción — el patrón "mecanismo entregado, cero llamantes".
# ---------------------------------------------------------------------------
async def _insert_episodic(dsn: str, mem_id: UUID, owner_id: UUID, meta: str, days: int) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id = await conn.fetchval("SELECT id FROM organizations LIMIT 1")
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
            " metadata, created_at) VALUES ($1, $2, 'private', 'episodic', $3, $4,"
            " $5::jsonb, now() - ($6 || ' days')::interval)",
            mem_id,
            tenant_id,
            f"mem {mem_id}",
            owner_id,
            meta,
            str(days),
        )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_el_sweep_retiene_la_episodica_emocionalmente_INTENSA(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Dos episódicas de 60 días idénticas salvo el bloque ``emotion`` del distilador.

    El `metadata_.emotion` con `intensity` lo escribe
    `workers.cortex_affect._persist_emotional_episode` en cada turno del córtex, así
    que la forma del JSONB de aquí es la real, no una inventada para el test.
    """
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, True)
    owner_id = seed["owner_id"]

    apagada, intensa = uuid4(), uuid4()
    await _insert_episodic(migrations_pg_dsn, apagada, owner_id, '{"cortex": true}', 60)
    await _insert_episodic(
        migrations_pg_dsn,
        intensa,
        owner_id,
        '{"cortex": true, "emotion": {"valence": -0.7, "arousal": 0.9,'
        ' "dominance": -0.3, "intensity": 1.0, "mood_label": "miedo"}}',
        60,
    )

    from workers.cortex_maintenance import _run_maintenance

    await _run_maintenance(workers_settings)

    assert await _deleted_at(migrations_pg_dsn, apagada) is not None
    assert await _deleted_at(migrations_pg_dsn, intensa) is None


@pytest.mark.asyncio
async def test_el_sweep_retiene_lo_vieja_pero_recordada_ayer(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Una episódica de dos años recordada AYER sobrevive al barrido.

    `metadata_.last_recalled_at` lo escribe `cortex.memory._bump_recall_counters`
    en cada recall y nadie lo leía: el sweep medía la recencia sobre `created_at`,
    así que enterraba lo que el owner acababa de usar.
    """
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, True)
    owner_id = seed["owner_id"]

    olvidada, recordada = uuid4(), uuid4()
    await _insert_episodic(migrations_pg_dsn, olvidada, owner_id, '{"cortex": true}', 730)

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ayer = await conn.fetchval("SELECT (now() - interval '1 day')")
    finally:
        await conn.close()
    await _insert_episodic(
        migrations_pg_dsn,
        recordada,
        owner_id,
        json.dumps(
            {
                "cortex": True,
                "recall_count": 1,
                "last_recalled_at": ayer.astimezone(UTC).isoformat(),
            }
        ),
        730,
    )

    from workers.cortex_maintenance import _run_maintenance

    await _run_maintenance(workers_settings)

    assert await _deleted_at(migrations_pg_dsn, olvidada) is not None
    assert await _deleted_at(migrations_pg_dsn, recordada) is None


# ---------------------------------------------------------------------------
# Consolidación (ADR 0077) — merge-into de la episódica REPETIDA, end-to-end
# ---------------------------------------------------------------------------
def _vec(*, axis: int, jitter: float = 0.0) -> str:
    """Un embedding vector(768) casi-one-hot en `axis` (coseno ~1 entre iguales)."""
    dims = [0.0] * 768
    dims[axis] = 1.0
    dims[(axis + 1) % 768] = jitter  # ligera variación para no ser idénticos
    return "[" + ",".join(str(d) for d in dims) + "]"


@pytest.mark.asyncio
async def test_autonomy_on_consolidates_repeated_episodic(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """3 episódicas viejas MUY similares colapsan en UNA consolidada; las
    originales quedan soft-borradas con `consolidated_into` (reversible); una 4ª
    disímil se queda como está."""
    await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        tenant_id = await conn.fetchval("SELECT id FROM organizations LIMIT 1")
        owner_id = await conn.fetchval("SELECT id FROM users WHERE is_system_owner LIMIT 1")
        similares = [uuid4() for _ in range(3)]
        distinta = uuid4()
        for i, mid in enumerate(similares):
            await conn.execute(
                "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
                " metadata, embedding, created_at) VALUES ($1, $2, 'private', 'episodic', $3,"
                ' $4, \'{"cortex": true, "importance": 0.9}\'::jsonb, $5::vector,'
                " now() - interval '30 days')",
                mid,
                tenant_id,
                f"el owner prefiere REST (v{i})",
                owner_id,
                _vec(axis=0, jitter=0.01 * i),
            )
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
            " metadata, embedding, created_at) VALUES ($1, $2, 'private', 'episodic', $3,"
            ' $4, \'{"cortex": true, "importance": 0.9}\'::jsonb, $5::vector,'
            " now() - interval '30 days')",
            distinta,
            tenant_id,
            "hablamos del clima",
            owner_id,
            _vec(axis=500),
        )
    finally:
        await conn.close()

    from workers.cortex_maintenance import _run_maintenance

    result = await _run_maintenance(workers_settings)
    owner_res = next(r for r in result["results"] if r["consolidated_groups"] >= 1)
    assert owner_res["consolidated_groups"] == 1

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        # Las 3 similares: soft-borradas y con back-reference a la consolidada.
        for mid in similares:
            row = await conn.fetchrow(
                "SELECT deleted_at, metadata->>'consolidated_into' AS into"
                " FROM memory_entries WHERE id = $1",
                mid,
            )
            assert row["deleted_at"] is not None, "original consolidada debe soft-borrarse"
            assert row["into"], "debe apuntar a la memoria consolidada (reversible)"
        # La disímil: intacta.
        assert (
            await conn.fetchval("SELECT deleted_at FROM memory_entries WHERE id = $1", distinta)
            is None
        )
        # Existe UNA memoria consolidada viva, con embedding (centroide) y origen.
        consolidated = await conn.fetchrow(
            "SELECT content, embedding, metadata->'consolidated_from' AS src"
            " FROM memory_entries WHERE metadata->>'kind' = 'consolidated'"
            " AND deleted_at IS NULL AND user_id = $1",
            owner_id,
        )
        assert consolidated is not None, "debe crearse la memoria consolidada"
        assert consolidated["embedding"] is not None, "centroide → sigue siendo recuperable"
        assert "[consolidado]" in consolidated["content"]
        import json as _json

        assert len(_json.loads(consolidated["src"])) == 3
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# D2 — el gate del plan: kill-switch + CIRCUIT-BREAKER de F4 (ADR 0078)
#
# El gate sólo miraba el kill-switch global. El criterio D2 exige además
# consultar el gobierno por owner de F4, que ya existía (`cortex/autonomy.py`) y
# ya lo usaba la curiosidad. Sin esto, un owner cuyo mantenimiento falla en bucle
# (BD saturada, embeddings corruptos) seguía siendo barrido cada noche sin freno.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_circuit_breaker_abierto_salta_el_barrido_del_owner(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Con el breaker ABIERTO para ese owner, la pasada no toca nada suyo.

    Es el criterio literal de D2: el gate consulta el circuit-breaker de F4. La
    clave la escribe `record_failure` tras N fallos consecutivos; aquí se abre a
    mano para probar el GATE, no el contador.
    """
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)

    from api_server.cortex.autonomy import circuit_key
    from workers.cortex_maintenance import MAINTENANCE_KIND, _run_maintenance

    await api_redis.set(circuit_key(str(seed["owner_id"]), MAINTENANCE_KIND), "open", ex=600)

    result = await _run_maintenance(workers_settings, now=datetime.now(UTC))

    assert result["results"][0] == {
        "owner_user_id": str(seed["owner_id"]),
        "skipped": "circuit_open",
    }, result
    # NADA se tocó: ni el olvido, ni el decay snapshot, ni la poda.
    assert await _deleted_at(migrations_pg_dsn, seed["old_episodic"]) is None
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        ancient = await conn.fetchval(
            "SELECT count(*) FROM cortex_affect_snapshots WHERE id = $1", seed["snap_ancient"]
        )
    finally:
        await conn.close()
    assert ancient == 1, "la poda tampoco debe correr con el breaker abierto"


@pytest.mark.asyncio
async def test_breaker_de_la_curiosidad_no_frena_el_mantenimiento(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Los breakers están separados por `kind`: uno no debe cerrar la puerta al otro.

    Si el mantenimiento reusara el `kind` por defecto (`curiosity`), una racha de
    fallos de las búsquedas web dejaría además la memoria del owner sin barrer —
    dos subsistemas independientes acoplados por una clave compartida.
    """
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)

    from api_server.cortex.autonomy import CURIOSITY_KIND, circuit_key
    from workers.cortex_maintenance import _run_maintenance

    await api_redis.set(circuit_key(str(seed["owner_id"]), CURIOSITY_KIND), "open", ex=600)

    result = await _run_maintenance(workers_settings, now=datetime.now(UTC))

    assert result["results"][0]["forgotten"] == 1, result
    assert await _deleted_at(migrations_pg_dsn, seed["old_episodic"]) is not None


@pytest.mark.asyncio
async def test_una_pasada_limpia_resetea_la_racha_de_fallos(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis
) -> None:
    """Un mantenimiento sin errores borra el contador de fallos consecutivos.

    Sin `record_success` el breaker sería un gate que sólo puede cerrarse: dos
    fallos separados por semanas de éxitos acabarían abriéndolo. Y sin
    `record_failure` sería un gate que NUNCA se abre — un mecanismo sin productor,
    verde y muerto.
    """
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)

    from api_server.cortex.autonomy import circuit_fails_key
    from workers.cortex_maintenance import MAINTENANCE_KIND, _run_maintenance

    fails_key = circuit_fails_key(str(seed["owner_id"]), MAINTENANCE_KIND)
    await api_redis.set(fails_key, "2")

    await _run_maintenance(workers_settings, now=datetime.now(UTC))

    assert await api_redis.get(fails_key) is None, "una pasada limpia resetea la racha"


@pytest.mark.asyncio
async def test_un_owner_con_fallos_acaba_abriendo_el_breaker(
    schema_at_head, migrations_pg_dsn: str, workers_settings, api_redis: Redis, monkeypatch
) -> None:
    """El productor del breaker existe: N pasadas con error lo ABREN.

    El mantenimiento es best-effort por diseño (cada paso traga su excepción), así
    que el fallo tiene que viajar hasta el gobierno de F4 explícitamente. Se
    fuerza un fallo del paso de olvido y se comprueba que, al alcanzar el umbral,
    la clave abierta aparece y la pasada siguiente ya sale por el gate.
    """
    seed = await _seed(migrations_pg_dsn)
    await _set_autonomy(migrations_pg_dsn, enabled=True)

    from api_server.cortex.autonomy import circuit_key
    from workers import cortex_maintenance as mod

    async def _boom(*_a, **_kw):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(mod, "_forget_low_retention", _boom)

    key = circuit_key(str(seed["owner_id"]), mod.MAINTENANCE_KIND)
    for _ in range(mod.MAINTENANCE_CB_FAILS):
        result = await mod._run_maintenance(workers_settings, now=datetime.now(UTC))
        # Fail-open: la tarea nunca propaga, aunque un paso reviente.
        assert "error" not in result, result

    assert await api_redis.exists(key), "tras la racha el breaker debe quedar abierto"
    after = await mod._run_maintenance(workers_settings, now=datetime.now(UTC))
    assert after["results"][0]["skipped"] == "circuit_open", after
