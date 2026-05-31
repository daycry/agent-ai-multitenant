"""marketplace_audit_entries — enforce append-only at the DB level (Plan 09 task_09_08).

Revocation (task_09_08) makes the marketplace audit trail mandatory: every
revoke / uninstall / consent action MUST leave an immutable record. Phase A
(migration 0041) created ``marketplace_audit_entries`` with a single
``FOR ALL`` tenant-isolation RLS policy. ``FOR ALL`` permits SELECT / INSERT
/ UPDATE / DELETE for the current tenant — so the app role could, in
principle, mutate or erase an audit row, which would defeat the "append-only,
no update/delete" guarantee the plan requires.

This migration hardens the table into a true append-only log **at the
database level** (defence in depth, not just an application convention):

  - it DROPS the ``FOR ALL`` policy, and
  - adds two narrower policies — ``FOR SELECT`` (read your tenant's rows)
    and ``FOR INSERT`` (append a row for your tenant, ``WITH CHECK`` on the
    current tenant).

Under ``FORCE ROW LEVEL SECURITY`` (already set in 0041) a command with no
matching permissive policy affects zero rows for a non-BYPASSRLS role. With
no UPDATE and no DELETE policy, an ``UPDATE`` / ``DELETE`` issued by the
application's ``app_user`` (NOBYPASSRLS) therefore touches **no rows** — the
audit entry is immutable. Cross-tenant isolation is preserved by the
``USING`` / ``WITH CHECK`` clauses, identical in shape to the policy they
replace. The BYPASSRLS ``migrations_user`` (used only by migrations / the
System-Admin maintenance path) is intentionally not constrained.

Fully reversible: ``downgrade`` restores the original single ``FOR ALL``
tenant-isolation policy created in 0041, leaving the table exactly as it was.

Revision ID: 0043_mkt_audit_append_only
Revises: 0042_marketplace_consent
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0043_mkt_audit_append_only"
down_revision: str | Sequence[str] | None = "0042_marketplace_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ---------------------------------------------------------------------------
# Append-only RLS: replace the FOR ALL policy with SELECT + INSERT only.
# The NULLIF(..., '') guard turns the empty string returned by
# current_setting(..., true) on an unset GUC into NULL before the ::uuid
# cast, so an unset session deterministically matches zero rows (copied
# verbatim from 0041 so the tenant-isolation semantics are unchanged).
# ---------------------------------------------------------------------------
_UP: tuple[str, ...] = (
    # Drop the permissive FOR ALL policy created in 0041.
    "DROP POLICY IF EXISTS marketplace_audit_entries_tenant_isolation"
    " ON marketplace_audit_entries",
    # Read your own tenant's audit rows.
    "CREATE POLICY marketplace_audit_entries_select ON marketplace_audit_entries"
    " FOR SELECT"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
    # Append a row for your own tenant. No UPDATE / DELETE policy exists, so
    # under FORCE ROW LEVEL SECURITY those commands affect zero rows for the
    # NOBYPASSRLS app role — the log is append-only.
    "CREATE POLICY marketplace_audit_entries_insert ON marketplace_audit_entries"
    " FOR INSERT"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)

_DOWN: tuple[str, ...] = (
    "DROP POLICY IF EXISTS marketplace_audit_entries_insert ON marketplace_audit_entries",
    "DROP POLICY IF EXISTS marketplace_audit_entries_select ON marketplace_audit_entries",
    # Restore the original FOR ALL tenant-isolation policy from 0041.
    "CREATE POLICY marketplace_audit_entries_tenant_isolation ON marketplace_audit_entries"
    " FOR ALL"
    " USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
    " WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)",
)


def upgrade() -> None:
    for stmt in _UP:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWN:
        op.execute(stmt)
