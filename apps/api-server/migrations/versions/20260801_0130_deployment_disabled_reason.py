"""marketplace v2 (ADR 0142 D7) — por qué un despliegue quedó deshabilitado.

Una columna, y existe por un modo de fallo concreto que la fase 4 introduce.

Al actualizar una instalación a una versión nueva, cada despliegue re-encaja su
`config` en el `config_schema` nuevo. Cuando ese esquema añade un campo
**requerido y sin default**, el despliegue NO se puede aplicar: aplicarlo a
medias dejaría el proyecto con una capacidad configurada a medias, que es peor
que no tenerla. Así que se queda `disabled`… y sin esta columna, `disabled` es
un estado mudo: el operador ve una capacidad apagada y ningún sitio donde leer
qué falta. La alternativa que se descartó —meter el motivo en `created_refs`—
ensucia el contrato de la retirada exacta, que es lo único que esa columna
significa.

`status` ya admitía `disabled` desde la 0128 (el CHECK no cambia); lo que
faltaba era el porqué.

RLS: **sin cambios**. `marketplace_deployments` ya lleva ENABLE + FORCE +
`tenant_isolation` desde la 0128, y añadir una columna no toca las policies.

Revision ID: 0130_deploy_disabled_reason
Revises: 0129_listing_review
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0130_deploy_disabled_reason"
down_revision: str | Sequence[str] | None = "0129_listing_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "marketplace_deployments",
        sa.Column("disabled_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    """Baja de verdad. Se pierde el motivo, que es el dato que introdujo.

    Un despliegue `disabled` sobrevive al downgrade; lo que se pierde es la
    explicación. Es la consecuencia inevitable de quitar la columna que la
    guarda, y la razón de que el downgrade se ejerza en el round-trip del test.
    """
    op.drop_column("marketplace_deployments", "disabled_reason")
