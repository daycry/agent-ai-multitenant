"""group_role_mappings on sso_configurations (Plan 08 task_08_11).

Adds the per-tenant IdP-group → tenant-role mapping used on every SSO
login (OIDC + SAML). A ``{idp_group: tenant_role}`` JSONB object: the
user's membership role is set to the highest-privilege role any of their
asserted groups maps to. The column defaults to an empty object, so
existing rows behave EXACTLY as before the upgrade (no mapping → the JIT
default ``tenant_user`` is kept; the mapping never removes access).

Only the per-tenant roles ``tenant_admin`` / ``tenant_user`` are ever
honoured at login (see ``api_server.auth.sso.group_mapping``); a group
can never grant a platform role. The DB stores the raw object as-is —
the safe-role filtering is enforced in the application layer, not by a
constraint, so a tenant can pre-stage a mapping without the migration
needing to know the role vocabulary.

Reversible: ``downgrade`` drops the column. No data migration is needed.

Revision ID: 0039_sso_group_role_mappings
Revises: 0038_webauthn_credentials
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0039_sso_group_role_mappings"
down_revision: str | Sequence[str] | None = "0038_webauthn_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sso_configurations",
        sa.Column(
            "group_role_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sso_configurations", "group_role_mappings")
