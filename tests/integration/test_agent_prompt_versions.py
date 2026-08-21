"""El `system_prompt` de un agente deja de reescribirse sin rastro (`task_gov_02`).

Hasta la migración 0143, `PUT /agents/{id}` era `apply_partial_update` + `flush`:
**sin versión, sin autor y sin diff**. Si la calidad de un agente caía, no había
forma de saber qué cambió ni de volver. Verificado contra el código antes de
escribir nada, el 2026-08-19: cero apariciones de `agent_prompt_versions` en todo
el repo.

Este fichero cubre las cuatro mitades de la tarea, y ninguna sobra:

1. **La migración** — tabla, RLS `ENABLE` + `FORCE`, política por tenant, y un
   `downgrade` que de verdad revierte. Anclado a la revisión **por su nombre**,
   nunca `-1`: `-1` es relativo a la CABEZA del árbol, así que la siguiente
   migración que aterrice encima dejaría este round-trip probando otra cosa
   (`docs/03-guides/gotchas/alembic-round-trip-anclado-por-nombre.md`).
2. **La escritura** — el `PUT` abre versión, y **sólo** cuando el prompt cambió.
3. **La lectura** — el historial con su diff, que es la mitad que esta base se
   suele dejar sin hacer (`verificar-antes-de-implementar.md` §5: mecanismo
   entregado, cero consumidores).
4. **El aislamiento** — a nivel de BD, con la sesión de la aplicación y no con la
   de migraciones, que tiene `BYPASSRLS` y por tanto no prueba nada de RLS.

## Y el cableado de `task_gov_03`, que se comprueba aquí a propósito

El sello que el dispatch manda al runtime sale de este historial. La mitad del
runtime la fija `docker/agent-runtimes/agent-runtime/tests/test_prompt_version.py`;
la del servidor —que el `prompt_hash` de la fila es el mismo número que el
endpoint publica como vigente— se comprueba abajo, porque es aquí donde hay una
fila de verdad escrita por el endpoint de verdad.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from httpx import ASGITransport, AsyncClient
from uuid6 import uuid7

pytestmark = pytest.mark.integration

#: La revisión ANTERIOR a la 0143, anclada por NOMBRE (ver el docstring).
_REVISION_BEFORE = "0142_cortex_forget_sweep_index"

#: Nota para quien compare con los tests de migración hermanos: allí cada
#: `command.upgrade(alembic_config, …)` lleva un `# type: ignore[arg-type]`, porque
#: la fixture está anotada `object`. Aquí el parámetro se declara `Any` y el
#: `ignore` no hace falta. Es preferible: una anotación honesta en vez de una
#: supresión, y `tests/` no está en el alcance de `scripts/mypy_gate.py`, así que
#: aquellos `ignore` no los exige ningún gate.

_TABLE = "agent_prompt_versions"


def _plain_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# ---------------------------------------------------------------------------
# 1. La migración
# ---------------------------------------------------------------------------
def _rewind(alembic_config: Any) -> None:
    """Deja el esquema en la revisión ANTERIOR a la 0143.

    Sube a `head` PRIMERO a propósito: una base recién creada no tiene esquema
    ninguno, y un `downgrade` sobre ella no falla — no hace nada, y el test se
    encuentra sin tablas tres líneas más abajo con un error que no menciona la
    migración.
    """
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, _REVISION_BEFORE)


async def _table_exists(dsn: str, table: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", table))
    finally:
        await conn.close()


async def _rls_state(dsn: str, table: str) -> tuple[bool, bool, list[str]]:
    """`(relrowsecurity, relforcerowsecurity, [expresiones de las policies])`."""
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = $1",
            table,
        )
        policies = await conn.fetch(
            "SELECT qual, with_check FROM pg_policies"
            " WHERE tablename = $1 AND schemaname = 'public'",
            table,
        )
        expressions = [f"{p['qual']} || {p['with_check']}" for p in policies]
        return bool(row["relrowsecurity"]), bool(row["relforcerowsecurity"]), expressions
    finally:
        await conn.close()


def test_the_migration_creates_the_table_with_forced_rls(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    _rewind(alembic_config)
    assert not asyncio.run(_table_exists(dsn, _TABLE)), (
        "la tabla existe ANTES de la 0143: este test dejó de probar la migración"
        " que dice probar (¿la creó otra revisión?)"
    )

    command.upgrade(alembic_config, "head")

    assert asyncio.run(_table_exists(dsn, _TABLE))
    enabled, forced, expressions = asyncio.run(_rls_state(dsn, _TABLE))
    assert enabled, f"{_TABLE} sin ENABLE ROW LEVEL SECURITY"
    # FORCE no es redundante: sin él, el DUEÑO de la tabla (migrations_user) se
    # salta la policy, y es el rol con el que corren los backfills.
    assert forced, f"{_TABLE} sin FORCE ROW LEVEL SECURITY"
    assert expressions, f"{_TABLE} con RLS activada y NINGUNA policy: deny-all silencioso"
    assert all("app.tenant_id" in expr for expr in expressions), expressions


def test_the_downgrade_removes_the_table_and_the_policy(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    command.upgrade(alembic_config, "head")
    assert asyncio.run(_table_exists(dsn, _TABLE))

    command.downgrade(alembic_config, _REVISION_BEFORE)
    try:
        assert not asyncio.run(_table_exists(dsn, _TABLE))
        # Y volver a subir funciona: una migración que no se puede reaplicar no es
        # reversible, sólo destructiva.
        command.upgrade(alembic_config, "head")
        assert asyncio.run(_table_exists(dsn, _TABLE))
    finally:
        command.upgrade(alembic_config, "head")


def test_two_rows_cannot_share_a_version_number(
    alembic_config: Any, migrations_pg_dsn: str
) -> None:
    """El UNIQUE `(agent_id, version)`, que es lo que hace del historial una cadena.

    Sin él, dos `PUT` concurrentes escribirían dos «versión 3» y el orden del
    historial pasaría a depender de `created_at`, que con dos INSERT en la misma
    transacción es el MISMO instante.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    command.upgrade(alembic_config, "head")
    ids = asyncio.run(_seed_minimal(dsn))

    async def _insert_twice() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            for _ in range(2):
                await conn.execute(
                    f"INSERT INTO {_TABLE}"
                    " (id, tenant_id, agent_id, version, system_prompt, prompt_hash)"
                    " VALUES ($1, $2, $3, 3, 'x', $4)",
                    uuid4(),
                    ids["tenant"],
                    ids["agent"],
                    "0" * 64,
                )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.UniqueViolationError):
        asyncio.run(_insert_twice())


def test_a_version_number_below_one_is_refused(alembic_config: Any, migrations_pg_dsn: str) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    command.upgrade(alembic_config, "head")
    ids = asyncio.run(_seed_minimal(dsn))

    async def _insert_zero() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                f"INSERT INTO {_TABLE}"
                " (id, tenant_id, agent_id, version, system_prompt, prompt_hash)"
                " VALUES ($1, $2, $3, 0, 'x', $4)",
                uuid4(),
                ids["tenant"],
                ids["agent"],
                "0" * 64,
            )
        finally:
            await conn.close()

    with pytest.raises(asyncpg.CheckViolationError):
        asyncio.run(_insert_zero())


# ---------------------------------------------------------------------------
# Semilla + app
# ---------------------------------------------------------------------------
_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed_minimal(dsn: str) -> dict[str, UUID]:
    """Un tenant + un agente, por el rol de migraciones (BYPASSRLS)."""
    ids = {"tenant": uuid4(), "agent": uuid4()}
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(f"TRUNCATE {_TABLE}, agents, organizations RESTART IDENTITY CASCADE")
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ids["tenant"],
            f"t-{ids['tenant'].hex[:8]}",
            f"t-{ids['tenant'].hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config, scope)"
            " VALUES ($1, $2, 'A', 'backend_dev', 'inicial', '{}'::jsonb,"
            "         'global_tenant_template')",
            ids["agent"],
            ids["tenant"],
        )
        return ids
    finally:
        await conn.close()


async def _seed_two_tenants(dsn: str) -> dict[str, UUID]:
    ids = {
        "tenant_a": uuid4(),
        "tenant_b": uuid4(),
        "user_a": uuid4(),
        "user_b": uuid4(),
        "agent_a": uuid4(),
    }
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            f"TRUNCATE {_TABLE}, agents, user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1,'A','gov02-a'),"
            " ($2,'B','gov02-b'), ($3,'P','gov02-platform')",
            ids["tenant_a"],
            ids["tenant_b"],
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash)"
            " VALUES ($1,'a@gov02.test','x'), ($2,'b@gov02.test','x')",
            ids["user_a"],
            ids["user_b"],
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1,$2,$3,'tenant_admin'), ($4,$5,$6,'tenant_admin')",
            uuid4(),
            ids["tenant_a"],
            ids["user_a"],
            uuid4(),
            ids["tenant_b"],
            ids["user_b"],
        )
        await conn.execute(
            "INSERT INTO agents (id, tenant_id, name, role, system_prompt, model_config, scope)"
            " VALUES ($1, $2, 'Backend', 'backend_dev', 'PROMPT ORIGINAL', $3::jsonb,"
            "         'global_tenant_template')",
            ids["agent_a"],
            ids["tenant_a"],
            '{"provider": "claude_sdk", "model": "sonnet"}',
        )
        return ids
    finally:
        await conn.close()


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


async def _mint_token(user_id: UUID, tenant_id: UUID) -> str:
    from api_server.auth.deps import get_redis
    from api_server.auth.jwt import encode_jwt
    from api_server.auth.sessions import SessionStore

    sid = uuid7()
    store = SessionStore(get_redis())
    await store.create(sid, user_id=user_id, tenant_id=tenant_id, ttl_seconds=3600)
    return encode_jwt(user_id=user_id, session_id=sid, tenant_id=tenant_id)


async def _rows(dsn: str, agent_id: UUID) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(dsn)
    try:
        found = await conn.fetch(
            f"SELECT version, system_prompt, persona, prompt_hash, changed_by,"
            f" parent_version_id, id FROM {_TABLE} WHERE agent_id = $1 ORDER BY version",
            agent_id,
        )
        return [dict(r) for r in found]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 2. La escritura: el PUT abre versión, y sólo cuando toca
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_first_prompt_edit_writes_the_baseline_AND_the_new_version(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Dos filas, no una — y es la decisión de diseño que hace útil el historial.

    Registrar sólo el estado NUEVO dejaría la primera edición sin nada contra lo
    que diffear, que es exactamente el momento en que alguien abre la pantalla
    («¿qué le hicieron a este agente?»). La fila de base lleva `changed_by` a NULL
    porque nadie apuntó quién escribió ese prompt: atribuírselo a quien edita hoy
    sería inventar un autor.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_a']}",
            json={"system_prompt": "PROMPT NUEVO Y MEJOR"},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text

    rows = await _rows(dsn, ids["agent_a"])
    assert [r["version"] for r in rows] == [1, 2], rows
    assert rows[0]["system_prompt"] == "PROMPT ORIGINAL"
    assert rows[0]["changed_by"] is None, (
        "la fila de base no puede atribuirse a quien hizo la primera edición"
    )
    assert rows[1]["system_prompt"] == "PROMPT NUEVO Y MEJOR"
    assert rows[1]["changed_by"] == ids["user_a"]
    assert rows[1]["parent_version_id"] == rows[0]["id"], "la cadena no está encadenada"


@pytest.mark.asyncio
async def test_the_second_edit_writes_one_row_chained_to_the_previous(
    configured_app, migrations_pg_dsn: str
) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        for texto in ("segundo", "tercero"):
            resp = await client.put(
                f"/agents/{ids['agent_a']}", json={"system_prompt": texto}, headers=headers
            )
            assert resp.status_code == 200, resp.text

    rows = await _rows(dsn, ids["agent_a"])
    assert [r["version"] for r in rows] == [1, 2, 3]
    assert [r["system_prompt"] for r in rows] == ["PROMPT ORIGINAL", "segundo", "tercero"]
    assert rows[2]["parent_version_id"] == rows[1]["id"]


@pytest.mark.asyncio
async def test_an_update_that_does_not_touch_the_prompt_opens_no_version(
    configured_app, migrations_pg_dsn: str
) -> None:
    """Un historial con filas que no cambian nada no se lee.

    Y es el fallo fácil: cablear el registro a «el `PUT` se ejecutó» en vez de a
    «el prompt cambió». Cada retoque de `max_concurrent_tasks` abriría versión, el
    diff de esas filas saldría vacío, y la pantalla se volvería inútil a la
    tercera semana.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_a']}",
            json={"max_concurrent_tasks": 4, "description": "otra cosa"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        # Y reenviar el MISMO prompt tampoco: un cliente idempotente no debe
        # ensuciar el historial.
        resp = await client.put(
            f"/agents/{ids['agent_a']}",
            json={"system_prompt": "PROMPT ORIGINAL"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text

    assert await _rows(dsn, ids["agent_a"]) == []


@pytest.mark.asyncio
async def test_editing_the_bilingual_persona_opens_a_version_too(
    configured_app, migrations_pg_dsn: str
) -> None:
    """`model_config.system_prompts` ES el prompt en un agente bilingüe.

    Vigilar sólo la columna plana dejaría sin registrar la edición que de verdad
    cambia lo que lee el modelo, que es el caso de los once agentes built-in.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_a']}",
            json={
                "model_config": {
                    "provider": "claude_sdk",
                    "model": "sonnet",
                    "system_prompts": {"es": "Eres el backend CI4.", "en": "You are CI4."},
                }
            },
            headers=headers,
        )
    assert resp.status_code == 200, resp.text

    rows = await _rows(dsn, ids["agent_a"])
    assert [r["version"] for r in rows] == [1, 2], rows
    import json as _json

    persona = rows[1]["persona"]
    persona = _json.loads(persona) if isinstance(persona, str) else persona
    assert persona == {"es": "Eres el backend CI4.", "en": "You are CI4."}
    # El campo plano NO cambió, así que la columna cruda lo conserva igual en las
    # dos filas: lo que se movió fue la persona.
    assert rows[0]["system_prompt"] == rows[1]["system_prompt"] == "PROMPT ORIGINAL"


@pytest.mark.asyncio
async def test_a_brand_new_agent_starts_its_history_with_a_known_author(
    configured_app, migrations_pg_dsn: str
) -> None:
    """`POST /agents` escribe la versión 1 con su autor DE VERDAD.

    Los agentes que ya existían cuando llegó la tabla arrancan con el NULL honesto
    de la fila de base; los que nacen a partir de aquí no tienen por qué heredar
    esa laguna.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/agents",
            json={
                "name": "QA Lead",
                "role": "qa",
                "system_prompt": "Eres QA.",
                "scope": "global_tenant_template",
            },
            headers=headers,
        )
    assert resp.status_code == 201, resp.text
    nuevo = UUID(resp.json()["id"])

    rows = await _rows(dsn, nuevo)
    assert [r["version"] for r in rows] == [1]
    assert rows[0]["changed_by"] == ids["user_a"]
    assert rows[0]["system_prompt"] == "Eres QA."


# ---------------------------------------------------------------------------
# 3. La lectura: el historial con su diff
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_history_endpoint_returns_newest_first_with_a_diff(
    configured_app, migrations_pg_dsn: str
) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        await client.put(
            f"/agents/{ids['agent_a']}",
            json={"system_prompt": "PROMPT REESCRITO"},
            headers=headers,
        )
        resp = await client.get(f"/agents/{ids['agent_a']}/prompt-versions", headers=headers)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    versiones = body["versions"]
    assert [v["version"] for v in versiones] == [2, 1], "el historial debe venir del más reciente"
    assert "PROMPT ORIGINAL" in versiones[0]["diff"]
    assert "PROMPT REESCRITO" in versiones[0]["diff"]
    assert "-PROMPT ORIGINAL" in versiones[0]["diff"]
    assert "+PROMPT REESCRITO" in versiones[0]["diff"]
    # La más antigua no tiene contra qué compararse: diff vacío, no el prompt
    # entero repetido como adición.
    assert versiones[1]["diff"] == ""
    assert versiones[1]["changed_by"] is None


@pytest.mark.asyncio
async def test_the_endpoint_publishes_the_seal_of_the_LIVE_prompt(
    configured_app, migrations_pg_dsn: str
) -> None:
    """El puente con `task_gov_03`: el sello de la fila viva y el de la versión coinciden.

    Es la comprobación que hace accionable la etiqueta de `executions.prompt_version`:
    si el `prompt_hash` de la última fila y el del agente vivo divergieran, un run
    quedaría atribuido a una versión que no es la que corrió.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        # Antes de editar: el agente lleva un prompt que NADIE registró todavía.
        antes = await client.get(f"/agents/{ids['agent_a']}/prompt-versions", headers=headers)
        assert antes.status_code == 200, antes.text
        assert antes.json()["versions"] == []
        sello_original = antes.json()["current_prompt_hash"]
        assert len(sello_original) == 64

        await client.put(
            f"/agents/{ids['agent_a']}", json={"system_prompt": "OTRO"}, headers=headers
        )
        despues = await client.get(f"/agents/{ids['agent_a']}/prompt-versions", headers=headers)

    cuerpo = despues.json()
    assert cuerpo["current_prompt_hash"] != sello_original, (
        "cambiar el system_prompt tiene que mover el sello: es la propiedad entera de task_gov_03"
    )
    # La fila más reciente sella EXACTAMENTE el prompt vigente…
    assert cuerpo["versions"][0]["prompt_hash"] == cuerpo["current_prompt_hash"]
    # …y la fila de base sella el anterior, que es el que corrió hasta ahora.
    assert cuerpo["versions"][1]["prompt_hash"] == sello_original


@pytest.mark.asyncio
async def test_the_history_of_an_unknown_agent_is_a_404(
    configured_app, migrations_pg_dsn: str
) -> None:
    """404 y no «200 con lista vacía», que confirmaría que el id existe.

    La RLS ya impide LEER las filas de otro tenant; esto impide además distinguir
    «no existe» de «no es tuyo», que es un oráculo de enumeración de ids.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token_b = await _mint_token(ids["user_b"], ids["tenant_b"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        ajeno = await client.get(
            f"/agents/{ids['agent_a']}/prompt-versions",
            headers={"Authorization": f"Bearer {token_b}"},
        )
        inexistente = await client.get(
            f"/agents/{uuid4()}/prompt-versions",
            headers={"Authorization": f"Bearer {token_b}"},
        )
    assert ajeno.status_code == 404, ajeno.text
    assert inexistente.status_code == 404
    assert ajeno.json() == inexistente.json(), (
        "un agente de otro tenant y uno inexistente tienen que ser indistinguibles"
    )


@pytest.mark.asyncio
async def test_the_history_needs_authentication(configured_app) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/agents/{uuid4()}/prompt-versions")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# 4. El aislamiento, a nivel de BD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_rls_hides_another_tenants_history_from_the_app_role(
    configured_app, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Con la sesión de la APLICACIÓN (NOBYPASSRLS), no con la de migraciones.

    `migrations_user` tiene `BYPASSRLS`, así que una comprobación de aislamiento
    hecha con él pasa en verde sin política ninguna. El prompt de un agente es
    propiedad intelectual del tenant: es exactamente el dato que no puede
    filtrarse.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    ids = await _seed_two_tenants(dsn)
    token = await _mint_token(ids["user_a"], ids["tenant_a"])

    async with AsyncClient(
        transport=ASGITransport(app=configured_app), base_url="http://test"
    ) as client:
        resp = await client.put(
            f"/agents/{ids['agent_a']}",
            json={"system_prompt": "SECRETO DE A"},
            headers={"Authorization": f"Bearer {token}"},
        )
    assert resp.status_code == 200, resp.text

    app_dsn = _plain_dsn(app_database_url)
    conn = await asyncpg.connect(app_dsn)
    try:
        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tenant_a"]))
        propias = await conn.fetchval(f"SELECT count(*) FROM {_TABLE}")
        assert propias == 2, f"el propio tenant debería ver sus 2 filas, vio {propias}"

        await conn.execute("SELECT set_config('app.tenant_id', $1, false)", str(ids["tenant_b"]))
        ajenas = await conn.fetch(f"SELECT system_prompt FROM {_TABLE}")
        assert ajenas == [], f"fuga cross-tenant del historial de prompts: {ajenas}"

        # Y sin `app.tenant_id` puesto —una sesión que se olvidó del middleware—
        # tampoco se ve nada: el `NULLIF(...)::uuid` de la policy da NULL y la
        # comparación es falsa para toda fila.
        await conn.execute("SELECT set_config('app.tenant_id', '', false)")
        assert await conn.fetch(f"SELECT system_prompt FROM {_TABLE}") == []
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_the_app_role_cannot_rewrite_history(
    configured_app, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Append-only: se comprueba en la CAPA, porque en la BD el rol sí puede.

    El `ALTER DEFAULT PRIVILEGES` de producción concede `SELECT, INSERT, UPDATE,
    DELETE` a `app_user` sobre toda tabla nueva, así que un `REVOKE UPDATE` en la
    migración sería una excepción a la convención del repo — y una que la
    restauración de un backup (`workers/restore.py`, que reaplica los default
    privileges) volvería a deshacer sin avisar. O sea que la garantía real es que
    NINGÚN camino del código escriba un UPDATE, y eso es lo que se afirma aquí.
    """
    from api_server.db import agent_prompt_version_repo as repo

    source = inspect.getsource(repo)
    for prohibido in ("update(", "delete(", ".merge(", "session.delete"):
        assert prohibido not in source, (
            f"el repositorio append-only contiene {prohibido!r}: el historial dejaría"
            " de ser historial"
        )
    publicas = {name for name in dir(repo) if not name.startswith("_")}
    # El inventario de la superficie pública: si alguien añade un `update_...`,
    # esta aserción lo dice en vez de esperar a que alguien lea el diff.
    assert publicas >= {
        "record_prompt_change",
        "record_initial_version",
        "list_prompt_versions",
        "latest_prompt_version",
        "latest_prompt_version_number",
        "raw_prompt_snapshot",
    }
    escrituras = {n for n in publicas if n.startswith(("update_", "delete_", "set_", "remove_"))}
    assert not escrituras, escrituras


def test_the_dispatch_actually_sends_the_seal_to_the_worker() -> None:
    """El cableado de `task_gov_03` en el lado del servidor.

    `verificar-antes-de-implementar.md` §5: el patrón dominante de esta base es
    «mecanismo entregado, cero llamantes». El historial podría estar perfecto y el
    runtime seguiría etiquetando runs sin el prompt del agente si el dispatch no
    metiera la clave en el spec. Se busca la ASIGNACIÓN, no una mención del
    nombre: el comentario que la explica ya suma una aparición.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[2]
    dispatch = (raiz / "apps/orchestrator/src/orchestrator/dispatch.py").read_text(encoding="utf-8")
    assert 'request["agent_prompt_version"] = {' in dispatch, (
        "el dispatch ya no emite el sello: executions.prompt_version volvería a"
        " hablar sólo del andamiaje del runtime"
    )
    assert "effective_prompt_hash(agent)" in dispatch

    run_spec = (raiz / "apps/workers/src/workers/run_spec.py").read_text(encoding="utf-8")
    assert 'spec["agent_prompt_version"] = request.agent_prompt_version' in run_spec, (
        "el worker ya no lo pone en el AGENT_TASK_SPEC: el dato llegaría al worker"
        " y se quedaría ahí"
    )
