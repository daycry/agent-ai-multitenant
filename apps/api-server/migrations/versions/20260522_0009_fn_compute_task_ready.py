"""fn_compute_task_ready: auto-promote tasks to 'ready' (task_02_04).

A task in the Kanban can only start once every task it depends on is
`done`. Rather than make the orchestrator poll, the DB does it: an
AFTER UPDATE trigger on `tasks` fires whenever a task reaches `done`
and promotes any dependent that has no remaining unfinished
dependency from `backlog` to `ready`.

  fn_compute_task_ready()   PL/pgSQL trigger function.
  trg_compute_task_ready    AFTER UPDATE ON tasks.

The trigger's own UPDATE moves tasks backlog -> ready, never -> done,
so the `WHEN` guard (status became 'done') stops it recursing.

Tasks with zero dependencies are unaffected here — they are already
startable; nothing gates them. The orchestrator decides when to pull
a no-dependency task.

Revision ID: 0009_fn_compute_task_ready
Revises: 0008_approval_policy_templates
Create Date: 2026-05-22
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0009_fn_compute_task_ready"
down_revision: str | Sequence[str] | None = "0008_approval_policy_templates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CREATE_FUNCTION = """
CREATE OR REPLACE FUNCTION fn_compute_task_ready()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    -- For every task that depends on the one that just completed,
    -- flip it to 'ready' iff it is still in 'backlog' and none of
    -- its dependencies remain unfinished.
    UPDATE tasks t
       SET status = 'ready',
           updated_at = now()
     WHERE t.status = 'backlog'
       AND t.id IN (
           SELECT task_id
             FROM task_dependencies
            WHERE depends_on_task_id = NEW.id
       )
       AND NOT EXISTS (
           SELECT 1
             FROM task_dependencies d
             JOIN tasks dep ON dep.id = d.depends_on_task_id
            WHERE d.task_id = t.id
              AND dep.status <> 'done'
       );
    RETURN NULL;
END;
$$;
"""

_CREATE_TRIGGER = """
CREATE TRIGGER trg_compute_task_ready
    AFTER UPDATE OF status ON tasks
    FOR EACH ROW
    WHEN (NEW.status = 'done' AND OLD.status IS DISTINCT FROM 'done')
    EXECUTE FUNCTION fn_compute_task_ready();
"""


def upgrade() -> None:
    op.execute(_CREATE_FUNCTION)
    op.execute(_CREATE_TRIGGER)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_compute_task_ready ON tasks")
    op.execute("DROP FUNCTION IF EXISTS fn_compute_task_ready()")
