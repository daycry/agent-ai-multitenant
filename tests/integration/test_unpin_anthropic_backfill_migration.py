"""La migración 0147 contra PostgreSQL real: a quién le despinea `anthropic`.

Como en la 0145 y la 0146, la pregunta no es «¿actualiza?» sino «¿a quién NO
toca?». El banco reproduce las formas que una copia puede tener en una base viva:

1. **La copia de fábrica atrapada** — clon de un built-in con
   `provider="anthropic"` heredado del seed. Es el incidente (F-01, auditoría
   2026-09-01): el pin no existe en el catálogo cerrado y todo run suyo aborta
   `model_unresolved`. SÍ se despinea, y sólo el pin: `system_prompts` se queda.
2. **La copia re-pineada a mano** — su administrador la pasó a `claude_sdk`. NO
   se toca: ese pin funciona y es una decisión suya.
3. **La copia borrada** — `deleted_at` puesto. NO se toca.
4. **El agente propio del tenant** — sin `forked_from_agent_id`, aunque pinee
   `anthropic`. NO se toca: es suyo entero, y el pin roto es suyo también.
5. **La copia de una plantilla del tenant** — el origen no es `global_builtin`.
   NO se toca.
6. **La copia sin pin** — `model_config` sólo con prompts. NO se toca (y no
   entra en el respaldo).

Y el `downgrade` restaura EXACTAMENTE el `model_config` anterior desde el
respaldo, sin recalcular nada.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = [pytest.mark.integration]

_REVISION_BEFORE = "0146_move_file_builtin_forks"
_REVISION = "0147_unpin_anthropic_builtin_forks"
_BACKFILL_TABLE = "agents_model_config_backfill_0147"
_PLATFORM_TENANT = UUID("00000000-0000-0000-0000-000000000001")

_PROMPTS = {"es": "persona ES", "en": "persona EN"}
_PIN_FABRICA: dict[str, Any] = {
    "provider": "anthropic",
    "model": "claude-sonnet-4-6",
    "temperature": 0.2,
    "system_prompts": _PROMPTS,
}
_PIN_MANUAL: dict[str, Any] = {
    "provider": "claude_sdk",
    "model": "claude-sonnet-5",
    "system_prompts": _PROMPTS,
}
_SIN_PIN: dict[str, Any] = {"system_prompts": _PROMPTS}

#: ``(clave, origen, model_config, borrada)``.
_BANK: tuple[tuple[str, str, dict[str, Any], bool], ...] = (
    ("fabrica-atrapada", "builtin", _PIN_FABRICA, False),
    ("repineada-a-mano", "builtin", _PIN_MANUAL, False),
    ("borrada", "builtin", _PIN_FABRICA, True),
    ("propio-con-anthropic", "none", _PIN_FABRICA, False),
    ("copia-de-tenant", "tenant", _PIN_FABRICA, False),
    ("sin-pin", "builtin", _SIN_PIN, False),
)
_EXPECTED_UNPINNED = {"fabrica-atrapada"}


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE agents RESTART IDENTITY CASCADE")
        await conn.execute("TRUNCATE projects RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)"
            " ON CONFLICT (id) DO NOTHING",
            _PLATFORM_TENANT,
            "platform",
            "platform",
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            "banco",
            f"banco-{tenant_id.hex[:8]}",
        )
        builtin_src, tenant_src = uuid4(), uuid4()
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope, is_template,"
            " model_config) VALUES ($1, $2, 'origen builtin', 'backend_dev', 'x',"
            " 'global_builtin', true, $3::jsonb)",
            builtin_src,
            _PLATFORM_TENANT,
            json.dumps(_PIN_FABRICA),
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope, is_template)"
            " VALUES ($1, $2, 'origen del tenant', 'backend_dev', 'x',"
            " 'global_tenant_template', true)",
            tenant_src,
            tenant_id,
        )
        sources = {"builtin": builtin_src, "tenant": tenant_src, "none": None}
        project_id = uuid4()
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'banco')",
            project_id,
            tenant_id,
        )
        ids: dict[str, UUID] = {}
        for key, origin, config, borrada in _BANK:
            agent_id = uuid4()
            ids[key] = agent_id
            await conn.execute(
                "INSERT INTO agents (id, tenant_id, name, role, system_prompt, scope,"
                " project_id, forked_from_agent_id, model_config, deleted_at)"
                " VALUES ($1, $2, $3, 'backend_dev', 'x', 'project_local', $4, $5, $6::jsonb,"
                " CASE WHEN $7 THEN now() ELSE NULL END)",
                agent_id,
                tenant_id,
                f"banco-{key}",
                project_id,
                sources[origin],
                json.dumps(config),
                borrada,
            )
        return ids
    finally:
        await conn.close()


async def _configs(dsn: str, ids: dict[str, UUID]) -> dict[str, dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT id, model_config FROM agents")
    finally:
        await conn.close()
    by_id = {agent_id: key for key, agent_id in ids.items()}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = by_id.get(row["id"])
        if key is not None:
            raw = row["model_config"]
            out[key] = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
    return out


async def _backup_rows(dsn: str) -> list[UUID]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(f"SELECT agent_id FROM {_BACKFILL_TABLE}")
    finally:
        await conn.close()
    return [row["agent_id"] for row in rows]


def _seeded_before_the_migration(alembic_config: object, dsn: str) -> dict[str, UUID]:
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    return asyncio.run(_seed(dsn))


def test_only_factory_pins_on_builtin_copies_are_removed(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_configs(migrations_pg_dsn, ids))

    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]

    after = asyncio.run(_configs(migrations_pg_dsn, ids))
    unpinned = {key for key in ids if "provider" in before[key] and "provider" not in after[key]}
    assert unpinned == _EXPECTED_UNPINNED, (
        f"la migración despineó {sorted(unpinned)}; se esperaba {sorted(_EXPECTED_UNPINNED)}"
    )
    assert after["fabrica-atrapada"] == {"system_prompts": _PROMPTS}, (
        "se retiran SÓLO provider/model/temperature; los prompts se quedan"
    )
    for key in set(ids) - _EXPECTED_UNPINNED:
        assert after[key] == before[key], f"{key}: la migración tocó una copia que no era suya"
    respaldo = set(asyncio.run(_backup_rows(migrations_pg_dsn)))
    assert respaldo == {ids[key] for key in _EXPECTED_UNPINNED}, (
        "el respaldo tiene que contener EXACTAMENTE las copias despineadas"
    )


def test_downgrade_restores_every_model_config_verbatim(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    before = asyncio.run(_configs(migrations_pg_dsn, ids))
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]

    after = asyncio.run(_configs(migrations_pg_dsn, ids))
    assert after == before, "el downgrade no dejó los model_config como estaban"


def test_the_migration_is_idempotent(alembic_config: object, migrations_pg_dsn: str) -> None:
    ids = _seeded_before_the_migration(alembic_config, migrations_pg_dsn)
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    once = asyncio.run(_configs(migrations_pg_dsn, ids))
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    command.upgrade(alembic_config, _REVISION)  # type: ignore[arg-type]
    twice = asyncio.run(_configs(migrations_pg_dsn, ids))
    assert once == twice
