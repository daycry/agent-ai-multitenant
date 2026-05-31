"""incoming_webhook_configs.action_mappings — webhook -> system action map.

Plan 13 Fase C task_13_10. Adds ONE column to the per-project incoming-webhook
config: ``action_mappings`` (JSONB, default ``[]``). It declares — PER project,
PER normalised event type — which SYSTEM action a verified incoming event
triggers (create a task, comment on a task, or escalate), plus the title/body
TEMPLATES rendered from the event. Example:

    [
      {"event_type": "github.pull_request_review",
       "action": "create_task",
       "title_template": "Review: {title}",
       "body_template": "{body}\n\nfrom {actor}"},
      {"event_type": "sentry.error",
       "action": "escalate",
       "target_task_id": "..."}
    ]

The mapping is interpreted IN the config's own tenant/project (RLS-scoped) so an
event for project A can never act on tenant B — the column lives on the
already-tenant+project-scoped ``incoming_webhook_configs`` row, so no new RLS is
needed (the table's ``tenant_isolation`` policy from 0055 covers it).

Single head before this migration is ``0055_incoming_webhooks``; this is
``0056_webhook_action_mappings``. Fully reversible: ``downgrade`` drops the one
column. Proven by an up / down to ``0040_sso_email_domains`` / up cycle.

Revision ID: 0056_webhook_action_mappings
Revises: 0055_incoming_webhooks
Create Date: 2026-05-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0056_webhook_action_mappings"
down_revision: str | Sequence[str] | None = "0055_incoming_webhooks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "incoming_webhook_configs",
        sa.Column(
            "action_mappings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("incoming_webhook_configs", "action_mappings")
