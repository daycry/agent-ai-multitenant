"""llm_providers — platform-global LLM provider catalog (Plan 11.2 task_11_2_01).

Creates the ``llm_providers`` table whose ORM shape (columns, enum, the
kind CHECK) was defined in ``api_server.db.llm_providers``. The table
records the non-secret runtime configuration of each LLM provider the
platform talks to — the four ADR-0021 paths (claude_sdk / copilot /
azure_foundry / ollama). The runtime selection factory (task_11_2_04)
reads an active row's ``base_url`` + non-secret ``config`` plus the
credential resolved from Vault, falling back to the installer/env config
when no active row exists.

Tenancy decision (ADR 0028 + CLAUDE.md principle 9 / 1):
**platform-global, gestionado solo por ``system_admin``.** A provider is
configured once for the whole platform, NOT per tenant, so the table
carries **no ``tenant_id``**.

RLS decision — **NO RLS policy at all** (ADR 0028). Unlike the read-open
catalogs ``model_prices`` (migration 0049) and ``exchange_rates``
(migration 0062), a provider row exposes *operational* configuration
(endpoints, the Vault path that holds its credential) that is not open to
every tenant. ADR 0028 keeps the table platform-global with no RLS and
gates access entirely at the application layer: the ``system_admin``
endpoints run on the BYPASSRLS admin engine (``get_admin_session``), and
no tenant/``app_user`` path ever queries this table. So this migration
intentionally does NOT ``ENABLE ROW LEVEL SECURITY`` and creates no
policy — the migration test asserts both (``relrowsecurity`` is false and
``pg_policies`` has no row for the table).

Secret handling (CLAUDE.md: no plaintext secrets in the DB). The
credential VALUE is never stored here — there is deliberately no column
that could hold one. ``secret_vault_path`` is only a *pointer* to the
Vault location (``platform/llm/<provider_id>``) where the secret lives
(written in task_11_2_02); ``base_url`` + ``config`` are non-secret.

The DB enforces the closed ``LLMProviderKind`` value set via the
``ck_llm_providers_kind`` CHECK (same shape as ``ck_agents_agent_type``).

Single head before this migration is ``0069_human_task_assignments``;
this is ``0070_llm_providers``. Fully reversible: ``downgrade`` drops the
table, restoring 0069 exactly (no policy / RLS to unwind, since none was
created). Proven by ``tests/migrations/test_llm_providers_table.py``
(up / down / up).

Revision ID: 0070_llm_providers
Revises: 0069_human_task_assignments
Create Date: 2026-06-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0070_llm_providers"
down_revision: str | Sequence[str] | None = "0069_human_task_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # One of the four ADR-0021 provider paths (an LLMProviderKind value).
        sa.Column("kind", sa.String(length=32), nullable=False),
        # Operator-facing label.
        sa.Column("display_name", sa.String(length=255), nullable=False),
        # APIM gateway / Ollama endpoint. NULL for claude_sdk (no base URL).
        sa.Column("base_url", sa.Text(), nullable=True),
        # Vault pointer (platform/llm/<id>) — NEVER the credential value.
        sa.Column("secret_vault_path", sa.Text(), nullable=True),
        # Non-secret config (enabled flags, default model ids, ...).
        sa.Column(
            "config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_providers"),
        # The DB enforces the closed LLMProviderKind value set.
        sa.CheckConstraint(
            "kind IN ('claude_sdk', 'copilot', 'azure_foundry', 'ollama')",
            name="ck_llm_providers_kind",
        ),
    )
    # Browse / "active providers of a kind" lookup path used by the runtime
    # selection factory (task_11_2_04) — partial on the active rows it cares
    # about. No tenant_id index: the table is platform-global.
    op.create_index(
        "ix_llm_providers_kind_active",
        "llm_providers",
        ["kind"],
        postgresql_where=sa.text("is_active = true"),
    )
    # NO RLS + NO policy (ADR 0028): platform-global, access only through
    # the system_admin endpoints on the BYPASSRLS admin engine.


def downgrade() -> None:
    op.drop_index("ix_llm_providers_kind_active", table_name="llm_providers")
    op.drop_table("llm_providers")
