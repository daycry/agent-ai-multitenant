"""SSO multi-provider: N configs OIDC/SAML simultáneas (backend completo).

`uq_sso_config_provider` limitaba la plataforma a UNA config por kind (un
OIDC + un SAML) — tener Google Y Microsoft a la vez era imposible, y una fila
soft-borrada bloqueaba el re-create para siempre (ocupaba el slot único). El
flujo ya era per-provider-id de punta a punta (login `/{provider_id}/…`,
state/RelayState con `provider_id`, callback resuelve del state), así que el
constraint era el único bloqueo real.

Reversible: el downgrade conserva la config más antigua VIVA de cada kind,
soft-borra el resto (no destruye secretos) y recrea el constraint. Las filas
soft-borradas previas se hard-borran en el downgrade — el constraint sin
filtro las contaría y no podría recrearse.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0115_sso_multi_provider"
down_revision: str | Sequence[str] | None = "0114_projects_slug_unique"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("uq_sso_config_provider", "sso_configurations", type_="unique")


def downgrade() -> None:
    # El constraint global (sin filtro por deleted_at) exige una sola fila por
    # kind: fuera las soft-borradas y, de las vivas, sobrevive la más antigua.
    op.execute("DELETE FROM sso_configurations WHERE deleted_at IS NOT NULL")
    op.execute(
        """
        UPDATE sso_configurations s
        SET deleted_at = now()
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY provider ORDER BY created_at, id
            ) AS rn
            FROM sso_configurations
        ) ranked
        WHERE s.id = ranked.id AND ranked.rn > 1
        """
    )
    op.execute("DELETE FROM sso_configurations WHERE deleted_at IS NOT NULL")
    op.create_unique_constraint("uq_sso_config_provider", "sso_configurations", ["provider"])
