"""córtex: el CHECK de cortex_curiosity_pursuits admite el estado 'surfaced'

Surfacing de curiosidad (ADR 0078, "abre el tema en el próximo encuentro"): un
pursuit ``digested`` que el self-context inyecta al turno pasa a ``surfaced``
(+``surfaced_at``). El CHECK original (migración 0095) no contemplaba ese estado.

Reversible de verdad: el ``downgrade()`` reconvierte las filas ``surfaced`` a
``digested`` ANTES de reponer el CHECK antiguo (sin filas inválidas).

Revision ID: 0103_cortex_pursuit_surfaced
Revises: 0102_plan_pr_url
Create Date: 2026-07-06
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0103_cortex_pursuit_surfaced"
down_revision: str | Sequence[str] | None = "0102_plan_pr_url"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE = "cortex_curiosity_pursuits"
_CHECK = "ck_cortex_pursuits_status"
_OLD_CONDITION = "status IN ('selected', 'searching', 'digested', 'skipped', 'failed')"
_NEW_CONDITION = "status IN ('selected', 'searching', 'digested', 'surfaced', 'skipped', 'failed')"


def upgrade() -> None:
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _NEW_CONDITION)


def downgrade() -> None:
    # Reconvertir ANTES de estrechar el CHECK: un pursuit ya contado vuelve a la
    # cola de pendientes (digested) — reversible sin filas inválidas.
    op.execute(f"UPDATE {_TABLE} SET status = 'digested' WHERE status = 'surfaced'")
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.create_check_constraint(_CHECK, _TABLE, _OLD_CONDITION)
