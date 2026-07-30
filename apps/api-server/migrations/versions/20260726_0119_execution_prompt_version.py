"""executions: etiqueta del conjunto de prompts que produjo el run (`task_wf_52`).

`EvalRun.subject_prompt_version` existe desde el Plan 14 y **nadie lo poblaba**:
el dashboard de calidad por release agrupaba todas las corridas bajo «(sin
versión)». Se podía medir la calidad y no atribuirla a un cambio de prompt, que
es justo lo que hace falta para saber si un retoque mejora o empeora.

El productor de esa etiqueta es el agent-runtime, que la calcula al cerrar el
run a partir del texto de sus propios prompts (`agent_runtime.prompt_version`).
Viaja en el envelope de `execution.finished` y aterriza aquí; de aquí la leerá
el muestreo de shadow-evals para poblar `eval_runs.subject_prompt_version`.

Nullable a propósito: los runs ya persistidos —y los de cualquier imagen
anterior al versionado— no la tienen, y rellenarlos con un valor inventado sería
peor que la ausencia (agruparía runs de prompts distintos bajo una etiqueta
falsa).

Revision ID: 0119_execution_prompt_version
Revises: 0118_review_session_preview
Create Date: 2026-07-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0119_execution_prompt_version"
down_revision: str | Sequence[str] | None = "0118_review_session_preview"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "executions",
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
    )
    # El dashboard filtra y agrupa por esta etiqueta sobre el histórico de un
    # tenant; sin índice es un scan completo de `executions`, que es la tabla
    # que más crece del sistema.
    op.create_index(
        "ix_executions_prompt_version",
        "executions",
        ["tenant_id", "prompt_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_executions_prompt_version", table_name="executions")
    op.drop_column("executions", "prompt_version")
