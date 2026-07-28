"""Córtex F4 — `gather_owner_entities`: de qué habla el owner, y de nadie más.

La mitad SQL del selector de tema de curiosidad (ADR 0078) no tenía ningún test
propio: la cobertura era de rebote (el camino feliz de
`test_cortex_curiosity_loop.py` afirma que el tema sale 'rust' y no 'kubernetes').
Eso deja sin fijar justo lo que el plan pedía por escrito — «integración para
`gather_owner_entities` con **test cross-owner**: entities de OTRO owner NO
aparecen» — y deja al descubierto los cuatro filtros del `WHERE`, que son la
frontera de aislamiento de una tabla tenant-less: `user_id == owner`,
`scope == 'private'`, `metadata_.cortex == 'true'` y `deleted_at IS NULL`.

El defecto que atrapan estos tests: si cualquiera de esos predicados se cae en un
refactor, el córtex empieza a investigar temas que no son del owner (o memorias
borradas, o memorias del asistente) y **nada avisa** — el bucle es autónomo, sale
a la web y escribe una memoria de aprendizaje. Que el test del bucle mire un solo
tema hace que un `WHERE` roto pueda seguir dando 'rust' por casualidad.

BYPASSRLS a propósito (`migrations_user`): el córtex es tenant-less y su eje de
aislamiento es la clave-por-owner, no RLS (ADR 0074). Precisamente por eso el
filtro explícito de `owner_user_id` es lo único que separa a dos owners, y merece
un assert directo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.cortex.curiosity import gather_owner_entities
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_two_users(dsn: str) -> dict[str, UUID]:
    """Un tenant con DOS usuarios: el owner del córtex y otro cualquiera.

    Mismo tenant a propósito: si el aislamiento dependiese de `tenant_id` (que
    aquí no aplica) el test no probaría nada."""
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
            "INSERT INTO organizations (id, name, slug)"
            " VALUES ($1, 'Cortex Entities', 'cortex-entities')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, 'owner@entities.test', 'h', true),"
            "        ($2, 'other@entities.test', 'h', false)",
            owner_id,
            other_id,
        )
    finally:
        await conn.close()
    return {"owner_id": owner_id, "other_id": other_id, "tenant_id": tenant_id}


async def _insert_memory(
    dsn: str,
    *,
    tenant_id: UUID,
    user_id: UUID | None,
    entities: str,
    scope: str = "private",
    metadata: str = '{"cortex": true}',
    deleted: bool = False,
    content: str = "memoria del cortex",
) -> None:
    """Una fila `memory_entries` cruda (el productor real no importa aquí)."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO memory_entries (id, tenant_id, scope, type, content, user_id,"
            " entities, metadata, deleted_at) VALUES ($1, $2, $3, 'semantic', $4, $5,"
            " $6::jsonb, $7::jsonb, $8)",
            uuid4(),
            tenant_id,
            scope,
            content,
            user_id,
            entities,
            metadata,
            datetime.now(UTC) if deleted else None,
        )
    finally:
        await conn.close()


def _sessionmaker(admin_database_url: str):
    engine = create_async_engine(admin_database_url)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# Cross-owner: el criterio de aceptación del plan
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_no_devuelve_entities_de_otro_usuario(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Las entities del OTRO usuario no aparecen ni cuando son las más frecuentes.

    Aceptación literal del plan («test cross-owner en verde, aislamiento por
    `owner_user_id`»). El otro usuario menciona 'kubernetes' TRES veces contra el
    único 'rust' del owner: si el filtro de `user_id` se cayera, 'kubernetes'
    ganaría el ranking y el córtex investigaría el tema de otra persona."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id = seed["tenant_id"]

    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=seed["owner_id"], entities='["rust"]'
    )
    for _ in range(3):
        await _insert_memory(
            migrations_pg_dsn,
            tenant_id=tenant_id,
            user_id=seed["other_id"],
            entities='["kubernetes"]',
        )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=seed["owner_id"])
    finally:
        await engine.dispose()

    assert ranked == [("rust", 1)]
    assert all(entity != "kubernetes" for entity, _ in ranked)


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_cada_owner_ve_solo_lo_suyo(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Simétrico: la misma función con el otro `owner_user_id` devuelve lo del otro.

    Sin el caso simétrico, un `WHERE user_id = <constante>` (o un filtro que
    devolviese siempre el primer usuario) pasaría el test anterior."""
    seed = await _seed_two_users(migrations_pg_dsn)
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=seed["tenant_id"],
        user_id=seed["owner_id"],
        entities='["rust"]',
    )
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=seed["tenant_id"],
        user_id=seed["other_id"],
        entities='["kubernetes"]',
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            del_owner = await gather_owner_entities(session, owner_user_id=seed["owner_id"])
            del_otro = await gather_owner_entities(session, owner_user_id=seed["other_id"])
    finally:
        await engine.dispose()

    assert del_owner == [("rust", 1)]
    assert del_otro == [("kubernetes", 1)]


# ---------------------------------------------------------------------------
# Ranking determinista
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ordena_por_frecuencia_y_desempata_alfabeticamente(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Frecuencia desc y, a igual frecuencia, alfabético.

    El desempate importa porque `pick_topic` se queda con el primero: sin orden
    total, dos entities empatadas harían que el tema elegido dependiese del orden
    que devuelva Postgres, y el bucle dejaría de ser reproducible (el plan pide
    'determinista')."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]

    # rust ×3, docker ×2, ansible ×2 (empate), python ×1.
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust", "docker"]'
    )
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust", "ansible"]'
    )
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["rust", "docker", "ansible", "python"]',
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    assert ranked == [("rust", 3), ("ansible", 2), ("docker", 2), ("python", 1)]


@pytest.mark.asyncio
async def test_normaliza_mayusculas_y_espacios_y_descarta_vacias(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """'Rust', ' rust ' y 'RUST' son el MISMO tema; las cadenas vacías se caen.

    Sin la normalización, la misma tecnología escrita de tres formas se reparte
    la frecuencia en tres candidatos débiles y pierde contra un tema marginal
    escrito siempre igual."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]

    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["Rust", " rust ", "RUST", "", "   "]',
    )
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["docker", "docker"]'
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    # Las tres grafías cuentan como un solo 'rust' con frecuencia 3; ni "" ni "   "
    # entran como candidatos (un tema vacío haría una búsqueda web sin query).
    assert ranked == [("rust", 3), ("docker", 2)]


@pytest.mark.asyncio
async def test_respeta_el_limite_del_ranking(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """`limit` recorta el ranking QUEDÁNDOSE con las más frecuentes, no al azar."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["rust", "rust", "docker", "python"]',
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id, limit=2)
    finally:
        await engine.dispose()

    assert ranked == [("rust", 2), ("docker", 1)]


# ---------------------------------------------------------------------------
# Los otros tres filtros del WHERE
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ignora_memorias_ajenas_al_cortex_borradas_y_no_privadas(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Solo memoria VIVA, PRIVADA y DEL CÓRTEX del owner alimenta la curiosidad.

    Los tres contraejemplos son del PROPIO owner, así que el filtro de
    `user_id` no los salva: cada uno depende de su predicado.
      * `metadata.cortex` ausente ⇒ nota del asistente, no del córtex (mezclarlas
        haría que el córtex investigue temas de trabajo del usuario, ADR 0074).
      * `deleted_at` puesto ⇒ el owner la borró; investigarla sería resucitarla.
      * `scope='global'` ⇒ memoria de organización, fuera del hilo privado."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]

    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust"]'
    )
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["nota-del-asistente"]',
        metadata='{"source": "assistant"}',
    )
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["memoria-borrada"]',
        deleted=True,
    )
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["memoria-global"]',
        scope="global",
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    assert ranked == [("rust", 1)]


@pytest.mark.asyncio
async def test_sin_memorias_del_cortex_devuelve_lista_vacia(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Owner recién nacido ⇒ `[]` (y `pick_topic([])` ⇒ no-op del bucle).

    La guarda contra el arranque en frío: sin este caso, un `gather` que
    levantase o devolviese `None` con la tabla vacía tumbaría la primera pasada
    del beat."""
    seed = await _seed_two_users(migrations_pg_dsn)
    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=seed["owner_id"])
    finally:
        await engine.dispose()

    assert ranked == []
