"""Córtex F5 D3 — migración 0142: el índice parcial del barrido de olvido.

Lo que se prueba aquí NO es «existe un índice»: eso lo cumpliría cualquier
índice, y un índice cuyo predicado no case con la query es peor que ninguno
(ocupa, se mantiene en cada escritura y no se usa). Lo que se clava es que el
índice sirve al camino concreto de ``workers.cortex_maintenance``:

  1. está, es PARCIAL y su predicado lleva las cuatro condiciones fijas del
     barrido — si alguien afloja una, el índice deja de implicar la query;
  2. el plan de la query REAL del barrido lo elige **y deja de ordenar**. Ese
     ``Sort`` es la razón de existir de la migración: el ``LIMIT`` se aplica
     después de ordenar, así que sin índice la pasada ordena la memoria privada
     viva entera del owner —incluida la que escribe el asistente, porque el único
     índice aprovechable va por ``user_id`` a secas— para quedarse con 500 filas;
  3. el ``downgrade`` lo retira de verdad y el ``upgrade`` lo repone.

El round-trip se ancla a la revisión anterior **por su nombre**, nunca con
``"-1"``: ``"-1"`` es relativo a la CABEZA del árbol, así que en cuanto se apile
otra migración encima dejaría de deshacer ésta y fallaría culpando a una
migración inocente
(docs/03-guides/gotchas/alembic-round-trip-anclado-por-nombre.md).

Y la migración que NO se escribió: el plan pedía columnas ``last_recalled_at`` y
``recall_count``. El diseño pivotó a JSONB (``metadata_.recall_count`` /
``metadata_.last_recalled_at``), con productor (``cortex.memory
._bump_recall_counters``) y consumidores (``cortex.forgetting``) ya cableados;
duplicarlo en columnas sería un backfill y una ventana con dos fuentes de verdad
para el mismo dato. El razonamiento largo está en el docstring de la migración.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

#: El índice que crea la migración 0142.
INDEX_NAME = "ix_memory_entries_cortex_sweep"

#: La revisión INMEDIATAMENTE anterior, por nombre. Ver el docstring: jamás "-1".
REVISION_BEFORE = "0141_kb_embedding_canonical"


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    """Sube a head ANTES de nada.

    No es ceremonia: un ``downgrade`` sobre una base recién creada no falla, sólo
    no hace nada, y el test reventaría tres líneas más abajo con un error que no
    menciona la migración."""
    command.upgrade(alembic_config, "head")


async def _index_row(conn: asyncpg.Connection) -> asyncpg.Record | None:
    """Definición del índice tal y como Postgres la guarda (o ``None``)."""
    return await conn.fetchrow(
        "SELECT indexdef FROM pg_indexes"
        " WHERE schemaname = 'public' AND tablename = 'memory_entries' AND indexname = $1",
        INDEX_NAME,
    )


# ---------------------------------------------------------------------------
# 1. El índice existe, es parcial, y su predicado es el del barrido
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_el_indice_es_parcial_y_su_predicado_cubre_el_barrido(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        row = await _index_row(conn)
        assert row is not None, f"la migración 0142 no creó {INDEX_NAME}"
        indexdef = row["indexdef"]

        # Las columnas, en ORDEN: el owner es igualdad y `created_at` es el orden
        # del barrido. Invertirlas dejaría el `LIMIT` volviendo a pedir un Sort.
        assert "USING btree (user_id, created_at)" in indexdef, indexdef

        # Y el predicado con las CUATRO condiciones fijas de
        # `workers.cortex_maintenance._forget_low_retention`. Si falta una, el
        # índice deja de implicar la query y Postgres vuelve a filtrar a mano.
        assert " WHERE " in indexdef, f"el índice NO es parcial: {indexdef}"
        predicate = indexdef.split(" WHERE ", 1)[1]
        for condition in (
            "deleted_at IS NULL",
            "'private'",
            "'episodic'",
            "metadata ->> 'cortex'::text",
        ):
            assert condition in predicate, f"falta {condition!r} en el predicado: {predicate}"
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 2. El plan de la query real del barrido: usa el índice y NO ordena
# ---------------------------------------------------------------------------
async def _seed_owner_memory(dsn: str) -> UUID:
    """Memoria privada del owner: la que barre el córtex y la que no.

    El ruido es la mitad del punto. Si se sembraran sólo filas del córtex, un
    índice sin predicado (o con uno más flojo) daría el mismo plan y el test no
    distinguiría el índice correcto del cómodo."""
    owner_id = uuid4()
    tenant_id = uuid4()
    base = datetime.now(UTC) - timedelta(days=400)

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE memory_entries, plans, conversations, projects, agents,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash, is_system_owner)"
            " VALUES ($1, 'owner@forget-sweep.test', 'h', true)",
            owner_id,
        )

        rows: list[tuple] = []
        for i in range(900):
            created = base + timedelta(minutes=i)
            if i % 3 == 0:
                # (a) la que barre el córtex: viva, privada, episódica, cortex=true
                rows.append(
                    (
                        uuid4(),
                        tenant_id,
                        owner_id,
                        "private",
                        "episodic",
                        created,
                        None,
                        '{"cortex": "true"}',
                    )
                )
            elif i % 3 == 1:
                # (b) memoria privada del MISMO owner que NO es del córtex — el
                #     asistente escribe aquí, y es lo que engorda el Sort de hoy.
                rows.append(
                    (uuid4(), tenant_id, owner_id, "private", "episodic", created, None, "{}")
                )
            else:
                # (c) ya olvidada (soft-delete) o semántica: fuera del predicado.
                kind = "semantic" if i % 6 == 2 else "episodic"
                deleted = None if kind == "semantic" else created
                rows.append(
                    (
                        uuid4(),
                        tenant_id,
                        owner_id,
                        "private",
                        kind,
                        created,
                        deleted,
                        '{"cortex": "true"}',
                    )
                )

        await conn.executemany(
            "INSERT INTO memory_entries"
            " (id, tenant_id, user_id, scope, type, content, created_at, updated_at,"
            "  deleted_at, metadata)"
            " VALUES ($1, $2, $3, $4, $5, 'x', $6, $6, $7, $8::jsonb)",
            rows,
        )
        # Sin estadísticas el planificador va a ciegas y elige por defaults.
        await conn.execute("ANALYZE memory_entries")
    finally:
        await conn.close()
    return owner_id


#: La query de ``_forget_low_retention``, escrita a mano tal y como la emite
#: SQLAlchemy. Si el barrido cambia de forma, este test debe cambiar con él — que
#: es exactamente lo que se quiere: el índice existe PARA esta query.
_SWEEP_SQL = (
    "SELECT id FROM memory_entries"
    " WHERE user_id = $1"
    "   AND scope = 'private'"
    "   AND deleted_at IS NULL"
    "   AND (metadata ->> 'cortex') = 'true'"
    "   AND type = 'episodic'"
    " ORDER BY created_at ASC"
    " LIMIT 500"
)


@pytest.mark.asyncio
async def test_el_plan_del_barrido_usa_el_indice_y_deja_de_ordenar(
    schema_at_head, migrations_pg_dsn: str
) -> None:
    owner_id = await _seed_owner_memory(migrations_pg_dsn)

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        plan = "\n".join(
            r["QUERY PLAN"] for r in await conn.fetch(f"EXPLAIN (COSTS OFF) {_SWEEP_SQL}", owner_id)
        )

        assert INDEX_NAME in plan, f"el planificador NO eligió el índice del barrido:\n{plan}"
        # La razón de ser de la migración: sin índice el plan lleva un `Sort`
        # sobre TODA la memoria privada viva del owner antes de aplicar el LIMIT.
        assert "Sort" not in plan, f"el barrido sigue ordenando:\n{plan}"
        # Y el predicado se da por probado: Postgres no re-comprueba a mano NADA
        # —no hay línea `Filter:`— porque el predicado del índice ya implica las
        # cuatro condiciones. Es la diferencia entre este índice y uno que sólo
        # acierte con las columnas: con `ix_memory_entries_user_id` el plan trae
        # todas las filas del owner y descarta a mano las que no son del córtex.
        assert "Filter:" not in plan, f"quedan condiciones sin cubrir por el índice:\n{plan}"

        # El plan sirve las filas correctas, no sólo rápido: 300 del córtex vivas
        # y episódicas de las 900 sembradas.
        assert len(await conn.fetch(_SWEEP_SQL, owner_id)) == 300
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 3. Round-trip: el downgrade lo quita de verdad; el upgrade lo repone
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_downgrade_retira_el_indice_y_upgrade_lo_repone(
    schema_at_head, migrations_pg_dsn: str, alembic_config
) -> None:
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _index_row(conn) is not None
    finally:
        await conn.close()

    await asyncio.to_thread(command.downgrade, alembic_config, REVISION_BEFORE)

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _index_row(conn) is None, "el downgrade dejó el índice puesto"
        # La migración sólo crea el índice: bajar no puede haberse llevado la
        # tabla ni sus otros índices por delante.
        remaining = {
            r["indexname"]
            for r in await conn.fetch(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'memory_entries'"
            )
        }
        assert {"memory_entries_pkey", "ix_memory_entries_user_id"} <= remaining, remaining
    finally:
        await conn.close()

    await asyncio.to_thread(command.upgrade, alembic_config, "head")

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        assert await _index_row(conn) is not None, "el upgrade no repuso el índice"
    finally:
        await conn.close()
