"""El barrido de olvido del córtex deja de ordenar la memoria entera del owner.

Córtex F5, tarea **D3**. El plan pedía dos columnas (``last_recalled_at``,
``recall_count``) sobre ``memory_entries`` más un índice parcial. Las columnas
**no se escriben**, y no por descuido: el diseño pivotó a JSONB y ese pivote está
entero y cableado de punta a punta desde hace meses.

  * **Productor**: ``api_server.cortex.memory._bump_recall_counters`` hace un
    ``jsonb_set`` anidado que incrementa ``metadata_.recall_count`` y sella
    ``metadata_.last_recalled_at`` en cada recall, re-filtrado por
    ``user_id``+``scope='private'`` (cross-owner safe).
  * **Consumidores**: ``api_server.cortex.forgetting`` lee las dos claves —
    ``recency`` sale de ``metadata_.last_recalled_at`` y ``recall_frequency`` de
    ``recall_frequency_factor(metadata_.recall_count)``.

Convertir eso a columnas hoy costaría un backfill, reescribir el productor y los
dos consumidores, y una ventana con dos fuentes de verdad para el mismo dato —
sobre una tabla de **321 filas** (medidas contra la BD del stack el 2026-08-19).
Sería trabajo puro de simetría con el enunciado. La tarea se cierra
**documentando el pivote**, que es la salida honesta, y entregando lo único del
enunciado original que sigue faltando y sigue siendo buena idea: **el índice
parcial**.

## Por qué el índice sí hace falta (medido, no supuesto)

El plan del barrido de ``workers.cortex_maintenance`` contra la BD viva, HOY:

    Limit
      -> Sort  (Sort Key: created_at, quicksort)
           -> Index Scan using ix_memory_entries_user_id
                Filter: scope='private' AND type='episodic'
                        AND (metadata->>'cortex')='true'

Ese ``Sort`` es el problema, y es el que ``_FORGET_SCAN_LIMIT = 500`` **no**
acota: el ``LIMIT`` se aplica DESPUÉS de ordenar, así que la pasada trae y ordena
toda la memoria privada viva del owner para quedarse con las 500 más antiguas. Y
crece más rápido de lo que parece, porque el eje del índice actual es el USUARIO:
en ese saco entra también la memoria privada que le escribe el asistente, no sólo
las 27 filas del córtex.

El índice de aquí abajo mete las cuatro condiciones fijas del barrido en el
predicado y ordena por ``created_at`` dentro de cada owner, de modo que el
``LIMIT`` pasa a ser una parada temprana sobre el índice: sin ``Sort``, y sin
leer una fila que el barrido vaya a descartar. Sirve igual al otro barrido de la
misma tarea (``_consolidate_similar``), que añade ``created_at < cutoff`` y
``embedding IS NOT NULL`` sobre el mismo conjunto.

**Sin ``CONCURRENTLY`` a propósito**: Alembic corre dentro de una transacción y
``CREATE INDEX CONCURRENTLY`` no puede. Con 321 filas / 3,2 MB la construcción es
de milisegundos; el día que la tabla pese, el runbook de índices grandes es otro
(crear fuera de Alembic y marcar la revisión).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0142_cortex_forget_sweep_index"
down_revision: str | Sequence[str] | None = "0141_kb_embedding_canonical"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Nombre del índice. Se nombra por el CAMINO que sirve (el barrido), no por las
#: columnas: hay ya cuatro índices parciales sobre esta tabla y el que se llama
#: por su columna (``ix_memory_entries_user_id``) es justo el que confunde, porque
#: parece cubrir este acceso y sólo cubre su primera mitad.
INDEX_NAME = "ix_memory_entries_cortex_sweep"

#: Las cuatro condiciones FIJAS del barrido de olvido/consolidación
#: (``workers.cortex_maintenance``). Van en el predicado, no en las columnas: son
#: constantes en la query, así que como columnas sólo engordarían el índice.
#:
#: OJO con el nombre de la columna JSONB: el atributo del ORM es ``metadata_``
#: (con guión bajo, porque ``metadata`` está tomado por SQLAlchemy) pero **la
#: columna en Postgres se llama ``metadata``**. Escribir ``metadata_`` aquí da un
#: ``column does not exist`` en el `upgrade`.
_PREDICATE = (
    "deleted_at IS NULL"
    " AND scope = 'private'"
    " AND type = 'episodic'"
    " AND (metadata ->> 'cortex') = 'true'"
)


def upgrade() -> None:
    # (user_id, created_at): el owner es igualdad y `created_at` es el orden del
    # barrido, así que el LIMIT se sirve del índice sin Sort.
    op.create_index(
        INDEX_NAME,
        "memory_entries",
        ["user_id", "created_at"],
        postgresql_where=sa.text(_PREDICATE),
    )


def downgrade() -> None:
    # Deshace de verdad: el índice es lo ÚNICO que crea esta migración, así que
    # bajar deja `memory_entries` exactamente como estaba (ningún dato se toca).
    op.drop_index(INDEX_NAME, table_name="memory_entries")
