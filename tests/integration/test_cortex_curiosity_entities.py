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


# ---------------------------------------------------------------------------
# La mitad `cortex_turns`: de qué se está HABLANDO ahora, no solo qué se destiló
# ---------------------------------------------------------------------------
async def _insert_conversation(dsn: str, *, owner_id: UUID, tenant_id: UUID) -> UUID:
    conv_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO cortex_conversations (id, owner_user_id, tenant_id) VALUES ($1, $2, $3)",
            conv_id,
            owner_id,
            tenant_id,
        )
    finally:
        await conn.close()
    return conv_id


async def _insert_turns(
    dsn: str, *, conversation_id: UUID, owner_id: UUID, contents: list[str], role: str = "user"
) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for content in contents:
            await conn.execute(
                "INSERT INTO cortex_turns (id, conversation_id, owner_user_id, role, content)"
                " VALUES ($1, $2, $3, $4, $5)",
                uuid4(),
                conversation_id,
                owner_id,
                role,
                content,
            )
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_los_turnos_recientes_refuerzan_una_entity_ya_conocida(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Hablar mucho de algo esta semana lo sube en el ranking, aunque se destilase una vez.

    Sin esta mitad, el selector solo veía la memoria DESTILADA, que es lenta: un
    tema del que el owner lleva tres días hablando no adelanta a otro que se
    destiló hace meses, y la curiosidad va siempre por detrás de la conversación
    (F4 4.1 lo pedía por escrito: «y de `cortex_turns` recientes»).

    'docker' y 'rust' empatan a 1 en la memoria; tres turnos del owner nombrando
    docker lo ponen primero. Si la mitad de turnos no cuenta, el empate lo
    resuelve el alfabético y ambos salen a 1."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust", "docker"]'
    )
    conv = await _insert_conversation(migrations_pg_dsn, owner_id=owner_id, tenant_id=tenant_id)
    await _insert_turns(
        migrations_pg_dsn,
        conversation_id=conv,
        owner_id=owner_id,
        contents=[
            "el build de docker tarda una eternidad",
            "docker compose no levanta el stack",
            "quiero mover eso a docker",
        ],
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    assert ranked == [("docker", 4), ("rust", 1)]


@pytest.mark.asyncio
async def test_una_palabra_cualquiera_del_turno_no_se_convierte_en_tema(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Los turnos REFUERZAN entities conocidas; no inventan candidatos nuevos.

    Es la razón por la que la mitad de turnos estuvo sin hacer: `cortex_turns` no
    tiene columna `entities`, y sacarlas del texto con `query_entity_terms` —un
    matcher de recall, no un ranker— produce basura. Medido sobre turnos reales
    en castellano, el ranking salía «despliegue 2, eso 2, manana 2, necesito 2»:
    el bucle autónomo sacaría a Internet, con dinero real, la palabra 'necesito'.

    Este test fija el diseño que lo evita: el vocabulario de candidatos lo pone
    la memoria destilada y el turno solo puede votar dentro de él. Si alguien
    'simplifica' contando todos los tokens del turno, esto se pone rojo."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust"]'
    )
    conv = await _insert_conversation(migrations_pg_dsn, owner_id=owner_id, tenant_id=tenant_id)
    await _insert_turns(
        migrations_pg_dsn,
        conversation_id=conv,
        owner_id=owner_id,
        contents=[
            "necesito el despliegue para manana",
            "necesito eso listo, el despliegue de manana",
            "eso del despliegue lo necesito manana",
        ],
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    assert ranked == [("rust", 1)]
    terms = {entity for entity, _ in ranked}
    assert "necesito" not in terms
    assert "despliegue" not in terms


@pytest.mark.cross_tenant
@pytest.mark.asyncio
async def test_los_turnos_de_otro_owner_no_refuerzan_mi_ranking(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Cross-owner de la mitad nueva: lo que habla OTRO no mueve mi curiosidad.

    `cortex_turns` es tenant-less y su eje de aislamiento es el filtro explícito
    de `owner_user_id` (ADR 0074/0156), así que una consulta que se olvidase del
    predicado seguiría devolviendo filas — y el bucle investigaría, con el dinero
    del owner, aquello de lo que habla otra persona. Las dos entities de la
    memoria son del OWNER; los cinco turnos que gritan 'kubernetes' son del otro."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id, other_id = seed["tenant_id"], seed["owner_id"], seed["other_id"]
    await _insert_memory(
        migrations_pg_dsn,
        tenant_id=tenant_id,
        user_id=owner_id,
        entities='["rust", "kubernetes"]',
    )
    conv_otro = await _insert_conversation(
        migrations_pg_dsn, owner_id=other_id, tenant_id=tenant_id
    )
    await _insert_turns(
        migrations_pg_dsn,
        conversation_id=conv_otro,
        owner_id=other_id,
        contents=["kubernetes otra vez"] * 5,
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    # Empate intacto (1 y 1) resuelto por el alfabético: los turnos ajenos no votan.
    assert ranked == [("kubernetes", 1), ("rust", 1)]


@pytest.mark.asyncio
async def test_los_turnos_del_propio_cortex_no_se_votan_a_si_mismos(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Solo cuentan los turnos del OWNER (`role='user'`), no los del córtex.

    Contar también los turnos que el córtex generó cierra un bucle de
    autorrefuerzo: el córtex saca un tema → sus propios turnos lo mencionan →
    sube en el ranking → lo vuelve a investigar. El insumo de la curiosidad tiene
    que ser de qué habla el OWNER, no de qué habló el modelo."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust", "docker"]'
    )
    conv = await _insert_conversation(migrations_pg_dsn, owner_id=owner_id, tenant_id=tenant_id)
    await _insert_turns(
        migrations_pg_dsn,
        conversation_id=conv,
        owner_id=owner_id,
        contents=["te cuento lo que aprendí de docker"] * 4,
        role="cortex",
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    assert ranked == [("docker", 1), ("rust", 1)]


@pytest.mark.asyncio
async def test_un_turno_repetitivo_vota_una_sola_vez(
    schema_at_head, migrations_pg_dsn: str, admin_database_url: str
) -> None:
    """Un turno vale UN voto por entity, por muchas veces que la repita dentro.

    Sin esto, un solo mensaje largo que repita una palabra veinte veces decide el
    tema del día él solo, y la señal deja de ser «de qué se habla» para pasar a
    ser «quién escribió el párrafo más insistente».

    Honestidad sobre lo que fija este test: hoy el voto único sale gratis porque
    `query_entity_terms` ya deduplica, así que quitar el `set(...)` de la
    implementación NO lo pone rojo. Está aquí como contrato del comportamiento
    —el helper es de recall y puede cambiar sin que nadie mire a la curiosidad—,
    no como guarda de esa línea concreta."""
    seed = await _seed_two_users(migrations_pg_dsn)
    tenant_id, owner_id = seed["tenant_id"], seed["owner_id"]
    await _insert_memory(
        migrations_pg_dsn, tenant_id=tenant_id, user_id=owner_id, entities='["rust", "docker"]'
    )
    conv = await _insert_conversation(migrations_pg_dsn, owner_id=owner_id, tenant_id=tenant_id)
    await _insert_turns(
        migrations_pg_dsn,
        conversation_id=conv,
        owner_id=owner_id,
        contents=["docker docker docker docker docker docker"],
    )

    engine, sessionmaker = _sessionmaker(admin_database_url)
    try:
        async with sessionmaker() as session:
            ranked = await gather_owner_entities(session, owner_user_id=owner_id)
    finally:
        await engine.dispose()

    assert ranked == [("docker", 2), ("rust", 1)]
