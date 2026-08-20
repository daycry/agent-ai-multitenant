"""Siete `created_at`/`updated_at` que se quedaron nullables por un olvido copiado.

`TimestampMixin` las declara obligatorias y todo el código las lee asumiendo que
tienen valor, pero las migraciones **0108** (`assistant_conversations`,
`assistant_turns`), **0109** (`llm_usage_events`) y **0112** (`browse_sessions`)
las crearon así::

    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"))

o sea con el default puesto y **sin `nullable=False`**. Es el mismo descuido
copiado tres veces, no una decisión: en el esquema hay 163 columnas
`created_at`/`updated_at` NOT NULL repartidas en 95 tablas, y sólo estas siete
nullables.

Hasta el 2026-08-20 la discrepancia no la veía nadie, porque `alembic check` no
podía dar veredicto (la metadata cargaba 34 tablas de 84 y el comando moría con
`NoReferencedTableError`; ver
`docs/03-guides/gotchas/alembic-metadata-a-medias-propone-borrar-lo-que-no-ve.md`).
Al arreglarse, éstos fueron los últimos 7 items de deriva de los 162 iniciales, y
los únicos que **no** se podían cerrar en el modelo: aquí el modelo acierta y es
la base de datos la que hay que mover.

## Por qué hay un UPDATE si no puede haber nulos

Se comprobó en el stack de referencia: **cero filas con NULL** en las cuatro
tablas, y el `server_default=now()` cubre cualquier INSERT en SQL crudo que omita
la columna, igual que el mixin cubre los del ORM. Aun así el `UPDATE` va delante,
porque un `SET NOT NULL` con una sola fila nula **aborta la migración entera**, y
el coste de la salvaguarda es cero: en una instalación sin nulos el `UPDATE` no
toca ninguna fila.

El valor de relleno es `now()`, el mismo que el `server_default` habría puesto si
la columna se hubiera omitido. No se inventa una fecha pasada: una fila que llegó
sin `created_at` no tiene forma de decir cuándo se creó, y poner el instante del
arreglo es la única lectura que no miente sobre el dato que falta. En
`updated_at` se usa `created_at` cuando existe —una fila nunca se actualizó antes
de crearse— y `now()` sólo si también falta.

## Por qué el SQL va escrito y no interpolado

Las cuatro tablas se nombran literalmente en :data:`_RELLENOS` en vez de
construirse con un f-string sobre una lista. Interpolar el nombre de tabla en un
`UPDATE` es el patrón que ruff marca como `S608` aunque la fuente sea una
constante del módulo, y silenciarlo con un `noqa` en una migración —el sitio
donde un error se aplica a producción una sola vez y sin vuelta— es exactamente
donde no toca. Escritas, se leen igual y no hay nada que suprimir.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0144_timestamps_not_null"
down_revision: str | Sequence[str] | None = "0143_agent_prompt_versions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: El relleno previo al `SET NOT NULL`, escrito y no interpolado (ver docstring).
#:
#: `updated_at` cae a `COALESCE(created_at, now())` donde la tabla tiene las dos
#: columnas: una fila no se actualizó antes de crearse. `llm_usage_events` sólo
#: tiene nullable la `updated_at`, así que su `created_at` ya es fiable y sirve
#: igual de fuente.
_RELLENOS: tuple[str, ...] = (
    "UPDATE assistant_conversations SET created_at = now() WHERE created_at IS NULL",
    "UPDATE assistant_conversations SET updated_at = COALESCE(created_at, now())"
    " WHERE updated_at IS NULL",
    "UPDATE assistant_turns SET created_at = now() WHERE created_at IS NULL",
    "UPDATE assistant_turns SET updated_at = COALESCE(created_at, now()) WHERE updated_at IS NULL",
    "UPDATE browse_sessions SET created_at = now() WHERE created_at IS NULL",
    "UPDATE browse_sessions SET updated_at = COALESCE(created_at, now()) WHERE updated_at IS NULL",
    "UPDATE llm_usage_events SET updated_at = COALESCE(created_at, now()) WHERE updated_at IS NULL",
)

#: `(tabla, columna)` de las siete, en orden de tabla.
_COLUMNAS: tuple[tuple[str, str], ...] = (
    ("assistant_conversations", "created_at"),
    ("assistant_conversations", "updated_at"),
    ("assistant_turns", "created_at"),
    ("assistant_turns", "updated_at"),
    ("browse_sessions", "created_at"),
    ("browse_sessions", "updated_at"),
    ("llm_usage_events", "updated_at"),
)


def upgrade() -> None:
    for sentencia in _RELLENOS:
        op.execute(sa.text(sentencia))

    for tabla, columna in _COLUMNAS:
        op.alter_column(
            tabla,
            columna,
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=False,
        )


def downgrade() -> None:
    # No se reponen NULLs: los valores que el relleno haya escrito son los que el
    # `server_default` habría puesto igual, y borrarlos sería la única operación
    # de este fichero que sí perdería información.
    for tabla, columna in reversed(_COLUMNAS):
        op.alter_column(
            tabla,
            columna,
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=True,
        )
