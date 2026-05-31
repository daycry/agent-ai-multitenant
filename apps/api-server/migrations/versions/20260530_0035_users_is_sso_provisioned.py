"""users.is_sso_provisioned flag for hardened JIT provisioning (Plan 08 task_08_07).

Phase A/B already create a user on first SSO login (the
``_jit_provision_user`` helper in ``routers/sso.py``) with a sentinel
password hash so local login can never match. This revision makes that
state EXPLICIT and queryable with a boolean column on the global
``users`` table:

  * ``is_sso_provisioned = true``  — the row was materialised by an SSO
    (OIDC/SAML) first login; it has no usable local password. Local
    ``POST /auth/login`` rejects it with the same generic 401 as a wrong
    password (the login flow checks the flag *before* touching the
    sentinel hash, so it never trips the argon2 "invalid hash" path).
  * ``is_sso_provisioned = false`` (default) — a normal local-password
    user created via ``POST /auth/register``; login behaves EXACTLY as
    before.

A flag (rather than only relying on the sentinel hash) lets the platform
distinguish "SSO-only identity" from "local user" without parsing the
opaque password hash, and lets a future task offer "set a local
password" to convert an SSO user without changing the auth contract.

``users`` is a GLOBAL table (one user may belong to many tenants) and
carries NO row-level-security policy, so this column needs no RLS DDL —
unlike the tenant-scoped membership it does not gate cross-tenant
isolation (that lives on ``user_org_memberships``, which JIT already
writes under ``app.tenant_id``).

Reversible: ``downgrade`` simply drops the column. No data migration is
needed — existing rows default to ``false`` (local users), and the few
SSO-provisioned rows are re-flagged on their next SSO login anyway
(the helper sets it idempotently on the lookup path).

Revision ID: 0035_users_is_sso_provisioned
Revises: 0034_sso_saml_crypto
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0035_users_is_sso_provisioned"
down_revision: str | Sequence[str] | None = "0034_sso_saml_crypto"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_sso_provisioned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_sso_provisioned")
