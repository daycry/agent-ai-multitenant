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

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


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
    schema_at_head, migrations_pg_dsn: str, workers_settings
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
    schema_at_head, migrations_pg_dsn: str, workers_settings
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
    schema_at_head, migrations_pg_dsn: str, workers_settings
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
