"""cortex_curiosity_pursuits: la columna `approved` del owner-approval gate (F4).

El paso 7 del bucle de curiosidad (ADR 0078, parte del MVP y no un fast-follow)
dice: si `cortex.curiosity_approval_gate` está ON, el córtex elige el tema, deja el
pursuit propuesto y **NO sale a Internet** hasta que el owner lo aprueba. Esa
salvaguarda no existía porque no había dónde anotar la aprobación: sin esta columna
la primera búsqueda autónoma salía sola (auditoría 2026-07-27, causa raíz del hueco
del gate tanto en el bucle como en el router).

## Por qué NULLABLE y no `NOT NULL DEFAULT false`

Porque la columna es **tri-estado** y los tres significan cosas distintas:

  * `NULL`  → propuesto, esperando al owner. El bucle no busca y espera.
  * `true`  → aprobado. La siguiente pasada lo investiga.
  * `false` → rechazado. No se vuelve a intentar ese pursuit.

Un `NOT NULL DEFAULT false` fundiría «pendiente» con «rechazado», y el bucle no
podría distinguir «espera» de «descarta»: o esperaría eternamente algo ya
rechazado, o investigaría algo que el owner dijo que no. La condición del gate es
literalmente `approved IS NULL`, así que la nulabilidad es el contrato, no una
concesión.

Las filas históricas quedan en `NULL` a propósito: son persecuciones de antes del
gate, ya terminadas (`digested`/`failed`/`skipped`), y su estado de aprobación es
genuinamente desconocido. Inventarles un `true` retroactivo sería afirmar que el
owner aprobó algo que nunca vio; el gate solo mira pursuits en `selected`, así que
el `NULL` histórico es inerte.

Sin índice: el gate consulta por `id` (el pursuit que acaba de insertar o el que
llega por el endpoint `/approve`) y la cola de pendientes ya está cubierta por
`ix_cortex_pursuits_owner_status` (owner + status). Un índice por `approved` sería
un índice de dos valores sobre una tabla pequeña: coste de escritura sin lector.

Tabla tenant-less (ADR 0074): sin RLS y sin `tenant_id`; el aislamiento es el
filtro `owner_user_id` explícito de todo su SQL.

Revision ID: 0123_cortex_pursuit_approved
Revises: 0122_retire_run_tools
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0123_cortex_pursuit_approved"
down_revision: str | Sequence[str] | None = "0122_retire_run_tools"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "cortex_curiosity_pursuits",
        sa.Column("approved", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    # Reversible sin pérdida de datos ajenos: se retira la columna y las filas de
    # persecuciones quedan intactas (el veredicto de aprobación es el único dato
    # que se pierde, y es el que esta migración introdujo).
    op.drop_column("cortex_curiosity_pursuits", "approved")
