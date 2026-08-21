"""La migración 0141 canoniza el sello de embeddings, y su downgrade es honesto.

`task_audit14_05` / ADR 0155. La columna `knowledge_bases.embedding_model_id`
guardaba por *server default* ``'nomic-embed-text-v1.5'``, un string que **no es
un tag válido de Ollama**: pedirlo devuelve «model not found». Lo que la
plataforma manda de verdad a ``/api/embed`` es ``nomic-embed-text``. La columna
llevaba, pues, desde el Plan 04 nombrando un modelo con el que nunca se generó un
vector.

Este fichero comprueba las tres cosas que hace la 0141 y **la que a propósito no
hace**:

1. El `UPDATE` reescribe las filas con el alias heredado.
2. El *server default* de la columna pasa a ser el nombre canónico.
3. Es idempotente: aplicarla dos veces no vuelve a tocar nada.
4. El `downgrade` repone el default anterior **pero no deshace el `UPDATE`**.
   Es deliberado y aquí queda fijado: revertir los datos repondría en las filas
   una etiqueta que no identifica a ningún modelo servible. Un downgrade que
   restaura un dato roto no es reversibilidad, es simetría mal entendida — y si
   alguien «arregla» el downgrade para que sea simétrico, este test se lo dice.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

#: La revisión ANTERIOR a la 0141, anclada por NOMBRE y nunca con `-1`: `-1` es
#: relativo a la CABEZA del árbol, así que la siguiente migración que aterrice
#: encima dejaría este round-trip probando otra cosa. Es un fallo real de este
#: repo: `docs/03-guides/gotchas/alembic-round-trip-anclado-por-nombre.md`.
_REVISION_BEFORE = "0140_cortex_owner_rls"

_ALIAS_HEREDADO = "nomic-embed-text-v1.5"
_NOMBRE_CANONICO = "nomic-embed-text"


def _plain_dsn(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _rewind(alembic_config: object) -> None:
    """Deja el esquema en la revisión ANTERIOR a la 0141.

    Sube a `head` primero a propósito: una base recién creada no tiene esquema
    ninguno, y `downgrade` sobre ella no falla — no hace nada, y el test se
    encuentra sin tablas tres líneas más abajo con un error que no menciona la
    migración (`relation "tenants" does not exist`).
    """
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]


async def _column_default(dsn: str) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT column_default FROM information_schema.columns"
            " WHERE table_name = 'knowledge_bases'"
            "   AND column_name = 'embedding_model_id'"
        )
    finally:
        await conn.close()


async def _seed_kb(dsn: str, *, modelo: str) -> tuple[str, str]:
    """Un tenant + una KB sellada con `modelo`. Devuelve (kb_id, tenant_id)."""
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id = uuid4()
        kb_id = uuid4()
        # La tabla de tenants se llama `organizations` en este esquema.
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant_id,
            f"t-{tenant_id.hex[:8]}",
            f"t-{tenant_id.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id)"
            " VALUES ($1, $2, $3, $4)",
            kb_id,
            tenant_id,
            f"kb-{kb_id.hex[:8]}",
            modelo,
        )
        return str(kb_id), str(tenant_id)
    finally:
        await conn.close()


async def _sello(dsn: str, kb_id: str) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(
            await conn.fetchval(
                "SELECT embedding_model_id FROM knowledge_bases WHERE id = $1::uuid",
                kb_id,
            )
        )
    finally:
        await conn.close()


@pytest.mark.integration
def test_upgrade_canonises_the_stamp_and_the_default(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    _rewind(alembic_config)

    # Antes: el default es el alias heredado, y una KB nace con él.
    default_antes = asyncio.run(_column_default(dsn))
    assert default_antes and _ALIAS_HEREDADO in default_antes, (
        "esperaba encontrar el alias heredado como default ANTES de la 0141;"
        f" vi {default_antes!r}. Si ya no está, este test dejó de probar la"
        " migración que dice probar."
    )
    kb_id, _ = asyncio.run(_seed_kb(dsn, modelo=_ALIAS_HEREDADO))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert asyncio.run(_sello(dsn, kb_id)) == _NOMBRE_CANONICO, (
        "el UPDATE de la 0141 no reescribió la fila sembrada con el alias"
    )
    default_despues = asyncio.run(_column_default(dsn))
    assert default_despues and _NOMBRE_CANONICO in default_despues, (
        f"el default sigue siendo {default_despues!r}: las KBs nuevas volverían"
        " a nacer con una etiqueta que Ollama no conoce"
    )
    assert default_despues and _ALIAS_HEREDADO not in default_despues


@pytest.mark.integration
def test_upgrade_leaves_a_foreign_stamp_alone(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    """El `WHERE` acota: una KB sellada con OTRO modelo no se toca.

    Sin esta comprobación, un `UPDATE` sin `WHERE` —el error fácil— pasaría los
    otros tests en verde mientras re-sella KBs cuyos vectores salieron de otro
    espacio semántico, que es exactamente el daño que el ADR 0155 evita.
    """
    dsn = _plain_dsn(migrations_pg_dsn)
    _rewind(alembic_config)
    ajeno = "mxbai-embed-large"
    kb_id, _ = asyncio.run(_seed_kb(dsn, modelo=ajeno))

    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert asyncio.run(_sello(dsn, kb_id)) == ajeno, (
        "la 0141 re-selló una KB que no llevaba el alias heredado: su UPDATE perdió el WHERE"
    )


@pytest.mark.integration
def test_upgrade_is_idempotent(alembic_config: object, migrations_pg_dsn: str) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    kb_id, _ = asyncio.run(_seed_kb(dsn, modelo=_NOMBRE_CANONICO))

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]

    assert asyncio.run(_sello(dsn, kb_id)) == _NOMBRE_CANONICO


@pytest.mark.integration
def test_downgrade_restores_the_default_but_not_the_broken_data(
    alembic_config: object, migrations_pg_dsn: str
) -> None:
    dsn = _plain_dsn(migrations_pg_dsn)
    command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
    kb_id, _ = asyncio.run(_seed_kb(dsn, modelo=_NOMBRE_CANONICO))

    command.downgrade(alembic_config, _REVISION_BEFORE)  # type: ignore[arg-type]
    try:
        default = asyncio.run(_column_default(dsn))
        assert default and _ALIAS_HEREDADO in default, (
            f"el downgrade no repuso el default anterior; vi {default!r}"
        )
        assert asyncio.run(_sello(dsn, kb_id)) == _NOMBRE_CANONICO, (
            "el downgrade reescribió las FILAS al alias heredado. Es simetría mal"
            " entendida: repone un string que no identifica a ningún modelo"
            " servible. Si el cambio fue deliberado, actualiza el ADR 0155 y el"
            " docstring de la migración antes de tocar este test."
        )
    finally:
        command.upgrade(alembic_config, "head")  # type: ignore[arg-type]
