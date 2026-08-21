"""`executions`: `last_model` / `tokens_in` / `tokens_out` denormalizados (prod-13).

Cierra la mitad de `task_prod13_18` que exigía DDL. La otra mitad —dejar de
traerse `steps_log` entero al proceso del api-server— se entregó el 2026-08-01
seleccionando columnas escalares explícitas en `runs_select`.

Qué queda por arreglar y por qué duele
--------------------------------------
Tres consultas del explorador de estadísticas siguen **expandiendo el JSONB**
con `jsonb_array_elements(steps_log)` para responder a preguntas que son de una
sola línea por run:

* `_last_model_expr()` — «¿con qué modelo terminó?». Subconsulta correlacionada
  que desenrolla el array **de cada fila del listado**, y además se usa como
  PREDICADO cuando se filtra por modelo (`?model=`), donde el planificador no
  tiene nada que indexar y acaba en un seq scan que desenrolla la tabla entera.
* `_token_split()` — la suma de tokens de entrada/salida de la ventana, que
  desenrolla todos los `steps_log` del período para sumar dos enteros.

La escala, medida el 2026-08-01 (ADR 0151): `steps_log` es el **76 %** de
`executions`, 9,5 KiB de media por run. Esa expansión la hace PostgreSQL —no
cruza la red, que era el problema de la otra mitad—, pero se paga en CPU y en
E/S del lado servidor cada vez que alguien abre el panel.

Las tres columnas son una PROYECCIÓN de `steps_log`, no una fuente nueva
---------------------------------------------------------------------
Es el mismo patrón que ya tienen `total_tokens` y `total_cost_usd`: se calculan
al cerrar el run y se guardan. La propiedad que las mantiene honestas es que
todo el que asigna `steps_log` llama acto seguido a
`db/execution_repo.py::apply_steps_rollup`. **No es «un solo escritor»**: hay
dos, el repositorio (`record_execution` / `finalize_execution` /
`create_running_execution`) y `workers.execution._mark_commit_failed`, que anexa
el paso del conflicto de rebase en su propia sesión BYPASSRLS. No hay trigger ni
columna generada que lo garantice desde la BD, así que la regla es del código y
la fija `tests/unit/test_execution_steps_rollup.py`
(`test_the_worker_marker_reprojects_the_steps_log_it_appends`, que anexa por esa
segunda vía un paso `model_call` y exige que las columnas lo describan).

Definiciones, para que el backfill y el código coincidan **exactamente**:

* `last_model` — el `model` del paso `model_call` con el `index` más alto que
  declare modelo; `NULL` si el run no llamó a ningún modelo. Es lo que
  `_last_model_expr()` devolvía, incluido el `NULL`.
* `tokens_in` / `tokens_out` — la suma sobre los pasos `model_call`. `0` cuando
  no hay ninguno, que es lo que `_token_split()` devolvía vía `coalesce`.

Coste de aplicar esto, que hay que leer ANTES de ejecutarlo
-----------------------------------------------------------
Son dos operaciones muy distintas:

1. **`ADD COLUMN`** — barato. PostgreSQL ≥ 11 no reescribe la tabla al añadir
   una columna con default constante, y en una tabla PARTICIONADA (la 0137
   convirtió `executions` a rango mensual) se propaga a todas las particiones.
   Toma `ACCESS EXCLUSIVE` un instante: hay que esperar a que no haya
   transacciones largas en curso, no a que la tabla sea pequeña.
2. **El backfill** — proporcional a la tabla. Es un `UPDATE` de todas las filas
   con pasos, o sea que reescribe cada tupla tocada y deja la anterior muerta
   hasta el siguiente `VACUUM`: **cuenta con que `executions` llegue a ocupar el
   doble en disco** durante un rato. No bloquea lecturas (`ROW EXCLUSIVE`), pero
   sí compite por E/S.

**Medido el 2026-08-12 contra la base viva** (esquema en `0138`), en solo
lectura, ejecutando la proyección sin el `UPDATE`: **180 runs, 165 con pasos,
2624 kB** sumando las seis particiones. O sea: instantáneo, y **sin ventana de
mantenimiento**. La proyección se ejecutó entera sobre esos datos reales sin un
solo error de cast, que es lo que dice que el `steps_log` de esta instalación
está limpio.

Ojo con el comando de medida en una tabla PARTICIONADA:
``pg_total_relation_size('executions')`` devuelve **0 bytes** —el padre no
almacena nada—, así que hay que sumar las particiones:

    SELECT pg_size_pretty(sum(pg_total_relation_size(c.oid)))
      FROM pg_class c
      JOIN pg_inherits i ON i.inhrelid = c.oid
      JOIN pg_class p ON p.oid = i.inhparent
     WHERE p.relname = 'executions';

El backfill va en la MIGRACIÓN y no en una task de fondo a propósito: sin él,
las tres columnas mentirían para todo el histórico (`tokens_in = 0` en un run
que gastó millones), y una columna que miente es peor que una columna que no
existe — el panel la enseñaría sin marca ninguna.

Reversibilidad
--------------
El `downgrade` borra las tres columnas. Es reversible de verdad porque no se
pierde nada: son una proyección de `steps_log`, que sigue intacta. Volver atrás
devuelve el sistema a expandir el JSONB, que es exactamente lo que hacía.

Revision ID: 0139_executions_steps_rollup
Revises: 0138_revoke_backfill_grants
Create Date: 2026-08-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0139_executions_steps_rollup"
down_revision: str | Sequence[str] | None = "0138_revoke_backfill_grants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: El backfill. Dos detalles que no son estéticos:
#:
#:  * `created_at` entra en el `WHERE` del UPDATE para que el planificador pueda
#:    podar particiones (la PK es compuesta desde la 0137); sin él, cada fila se
#:    busca en todas.
#:  * los tokens se castean vía `numeric` y `floor`, no directo a `bigint`. Un
#:    `'1.9'::bigint` es un ERROR de PostgreSQL, no un redondeo, así que un solo
#:    paso con un contador fraccionario abortaría la migración entera. La
#:    consulta que esto sustituye (`_token_split`) casteaba directo, o sea que la
#:    evidencia dice que el dato está limpio — pero el precio del error es muy
#:    distinto entre un 500 en un panel y una migración que no aplica. `floor`
#:    también empareja con el `int()` del cálculo en Python, que trunca.
_BACKFILL = """
UPDATE executions AS e
   SET last_model = s.last_model,
       tokens_in  = s.tokens_in,
       tokens_out = s.tokens_out
  FROM (
        SELECT x.id,
               x.created_at,
               (SELECT el->>'model'
                  FROM jsonb_array_elements(x.steps_log) AS el
                 WHERE el->>'kind' = 'model_call'
                   AND el->>'model' IS NOT NULL
                 ORDER BY (el->>'index')::bigint DESC
                 LIMIT 1) AS last_model,
               COALESCE((SELECT sum(floor((el->>'tokens_in')::numeric))
                           FROM jsonb_array_elements(x.steps_log) AS el
                          WHERE el->>'kind' = 'model_call'), 0)::bigint AS tokens_in,
               COALESCE((SELECT sum(floor((el->>'tokens_out')::numeric))
                           FROM jsonb_array_elements(x.steps_log) AS el
                          WHERE el->>'kind' = 'model_call'), 0)::bigint AS tokens_out
          FROM executions AS x
         WHERE jsonb_typeof(x.steps_log) = 'array'
           AND jsonb_array_length(x.steps_log) > 0
       ) AS s
 WHERE e.id = s.id
   AND e.created_at = s.created_at
"""


def upgrade() -> None:
    # `Text` y no `String(n)`: el nombre del modelo lo elige el proveedor y un
    # tope inventado convertiría un modelo nuevo de nombre largo en un fallo de
    # escritura al CERRAR el run — o sea, en un run perdido.
    op.add_column("executions", sa.Column("last_model", sa.Text(), nullable=True))
    op.add_column(
        "executions",
        sa.Column("tokens_in", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "executions",
        sa.Column("tokens_out", sa.BigInteger(), nullable=False, server_default=sa.text("0")),
    )
    op.execute(_BACKFILL)


def downgrade() -> None:
    op.drop_column("executions", "tokens_out")
    op.drop_column("executions", "tokens_in")
    op.drop_column("executions", "last_model")
