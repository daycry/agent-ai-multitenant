"""Tighten agents RLS and expose global_builtin rows to all tenants.

Two changes:

1. Replace `agents_tenant_isolation` with a USING + WITH CHECK pair so a
   tenant session can't INSERT/UPDATE a row carrying another tenant's id
   (the original migration 0002 had USING only, which let a malicious
   write slip through and RLS just hid the result on subsequent SELECT).

2. Add `agents_global_builtin_read`, a SELECT-only policy that lets any
   authenticated session read agents with `scope='global_builtin'`,
   regardless of tenant_id. Per spec §5.7.5 globals must be visible to
   all tenants. INSERT/UPDATE/DELETE on those rows is unaffected -- the
   tenant-isolation policy's WITH CHECK rejects writes that don't match
   the current tenant_id, so only BYPASSRLS roles (System Admin) can
   create/modify built-ins.

Revision ID: 0004_agents_builtin_visibility
Revises: 0003_agent_scope
Create Date: 2026-05-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0004_agents_builtin_visibility"
down_revision: str | Sequence[str] | None = "0003_agent_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agents_tenant_isolation ON agents")
    op.execute(
        "CREATE POLICY agents_tenant_isolation ON agents FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
        " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
    op.execute(
        "CREATE POLICY agents_global_builtin_read ON agents FOR SELECT"
        " USING (scope = 'global_builtin')"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS agents_global_builtin_read ON agents")
    op.execute("DROP POLICY IF EXISTS agents_tenant_isolation ON agents")
    # Restore the original USING-only policy from migration 0002.
    op.execute(
        "CREATE POLICY agents_tenant_isolation ON agents FOR ALL"
        " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    )
