"""executions: digest de la imagen del runtime que produjo el run (`task_wf_62`).

Las imágenes de runtime se referencian por etiqueta flotante
(``agent-runtime-php-phpunit:v1``). Reconstruir esa etiqueta cambia **en
silencio** lo que ejecuta toda tarea PHP del sistema: no hay forma de saber qué
build produjo un resultado, ni de volver a la anterior cuando una regresión
aparece. Junto a ``prompt_version`` (migración 0119) cierra la trazabilidad de
un run: qué prompts y qué imagen lo produjeron.

El valor lo lee el worker del ``inspect`` del contenedor —el campo ``Image``,
que el daemon rellena con el id ya resuelto—, no preguntando por la etiqueta
después: entre el lanzamiento y la consulta la etiqueta puede haberse reasignado
a otra build, y se registraría la imagen equivocada justo en el caso que esta
columna existe para detectar.

Nullable: los runs ya persistidos no lo tienen, y un daemon que no lo reporte no
puede impedir un run. 80 caracteres cubren ``sha256:`` + 64 hex con holgura.

Revision ID: 0120_execution_runtime_digest
Revises: 0119_execution_prompt_version
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0120_execution_runtime_digest"
down_revision: str | Sequence[str] | None = "0119_execution_prompt_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("runtime_image_digest", sa.String(length=80), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("executions", "runtime_image_digest")
