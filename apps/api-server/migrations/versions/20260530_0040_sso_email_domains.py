"""email_domains on sso_configurations (Plan 08 task_08_12).

Login discovery: the public ``GET /auth/discover?email=<addr>`` endpoint
maps an email DOMAIN to the tenant whose enabled SSO config claims it,
so the login UI can route the user straight to their IdP instead of
guessing. This adds the ``email_domains`` JSONB array column that holds
the operator-attested domains for a config (e.g. ``["acme.com",
"acme.io"]``).

Matching is **case-insensitive**: the discovery query lower-cases both
the queried domain and the stored values, and the application layer
normalises domains to lower-case before persisting them, so the array is
expected to hold lower-case entries.

Multi-tenant-domain behaviour: a config MAY claim several domains (the
column is an array), and there is deliberately NO global-uniqueness
constraint across tenants — domains are operator-attested, not verified,
so two tenants could in principle both list the same domain. Discovery
resolves such a collision deterministically by returning the
oldest-created matching config (``ORDER BY created_at``); it never leaks
that more than one tenant matched. The empty array (the default) means
"no domain claimed", so existing rows behave EXACTLY as before the
upgrade — discovery falls back to the generic local-login response.

Reversible: ``downgrade`` drops the column. No data migration is needed.

Revision ID: 0040_sso_email_domains
Revises: 0039_sso_group_role_mappings
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0040_sso_email_domains"
down_revision: str | Sequence[str] | None = "0039_sso_group_role_mappings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sso_configurations",
        sa.Column(
            "email_domains",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("sso_configurations", "email_domains")
