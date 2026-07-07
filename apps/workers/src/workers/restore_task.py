"""Restore Celery tasks — the background jobs the restore UI enqueues (Plan 12 task_12_12).

A restore is LONG (a full pg_restore + volume re-extract, or a per-tenant staged
filtered copy) and DESTRUCTIVE, so it must NOT run inline on the api-server
request thread. The restore UI (task_12_12) instead ENQUEUES one of these tasks
and then polls its status; the heavy lifting lives in the Phase C engines
(:mod:`workers.restore` full restore, :mod:`workers.restore_per_tenant`).

Two tasks, both ``bind=True`` so they can report progress through Celery's
``update_state`` (persisted in the result backend Redis DB the api-server reads
via ``AsyncResult``):

  * ``workers.run_restore``            — full-stack restore (task_12_10).
  * ``workers.run_restore_per_tenant`` — selective per-tenant restore (task_12_11).

Safety carried through from the engines (NOT re-implemented here):

  * **double confirmation** — each task forwards the ``confirm`` token the UI
    collected to the engine, which refuses unless it matches (the full restore
    wants the bundle id; the per-tenant restore wants ``<tenant_id>@<bundle_id>``).
    A bad token raises inside the engine BEFORE anything destructive runs.
  * **verify-before-restore, fail closed** — the engines verify the bundle
    against its manifest checksums + structural probes BEFORE any destructive
    command; a corrupt bundle aborts with nothing written.
  * **per-tenant isolation** — the per-tenant engine only ever touches the target
    tenant's rows (tenant_id-scoped on both sides) + that tenant's object-storage
    slice; another tenant is never in scope.
  * **secrets via the secret seam** — the engines resolve the Vault decrypt key
    through ``workers.secrets``; nothing secret is logged here. We log only the
    backup id / tenant id and the phase.

Unlike the backup beat task this is NOT best-effort: a restore the operator
explicitly triggered must surface its failure. A failed restore re-raises so the
task lands in the FAILURE state (with the error message in the result backend)
the UI renders — never a silent success.
"""

from __future__ import annotations

from typing import Any

import structlog

from workers.celery_app import app
from workers.restore import RestoreError, run_full_restore
from workers.restore_per_tenant import PerTenantRestoreError, run_per_tenant_restore

_log = structlog.get_logger("workers.restore_task")

# Celery custom states the UI's progress view keys on. ``PROGRESS`` is the
# in-flight state carrying a {phase, message} meta; the terminal SUCCESS / FAILURE
# states are Celery's own. Kept as constants so the api-server status reader and
# this producer agree on the wire shape.
STATE_PROGRESS = "PROGRESS"


@app.task(bind=True, name="workers.run_restore")  # type: ignore[untyped-decorator]
def run_restore(self: Any, backup_id: str, *, confirm: str) -> dict[str, Any]:
    """Run a FULL restore from ``backup_id`` (a background job).

    ``confirm`` MUST equal the bundle id (the double-confirmation guard the UI
    collected) — the engine refuses otherwise BEFORE anything destructive runs.
    Reports a single PROGRESS state (the engine itself is one long sequence:
    verify → stop → pg_restore → volumes → restart) then returns the engine's
    JSON-safe result on success. A failure re-raises so the job lands in FAILURE
    with the (non-leaky) error message the UI shows.
    """
    _log.info("restore_task.full.start", backup_id=backup_id)
    self.update_state(
        state=STATE_PROGRESS,
        meta={"phase": "restoring", "message": f"restoring full bundle {backup_id}"},
    )
    try:
        result = run_full_restore(backup_id, confirm=confirm)
    except RestoreError as exc:
        # A typed engine failure (bad token, failed verification, a non-zero
        # command). Re-raise so the job is FAILURE; the message is already
        # non-leaky (the engine never echoes secret material).
        _log.warning("restore_task.full.failed", backup_id=backup_id, error=str(exc))
        raise
    _log.info("restore_task.full.done", backup_id=backup_id)
    return result.to_dict()


@app.task(bind=True, name="workers.run_restore_per_tenant")  # type: ignore[untyped-decorator]
def run_restore_per_tenant(
    self: Any,
    backup_id: str,
    *,
    tenant_id: str,
    confirm: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run a SELECTIVE per-tenant restore (a background job).

    Restores ONLY ``tenant_id``'s data from ``backup_id`` — never another
    tenant's rows or object-storage slice. ``confirm`` MUST equal
    ``<tenant_id>@<backup_id>`` (the double-confirmation guard); the engine
    refuses otherwise before any work. ``dry_run`` computes the preview without
    writing — the UI uses the read-only preview endpoint for that, but the flag
    is forwarded for completeness.

    Reports a single PROGRESS state then returns the engine's JSON-safe result.
    A failure re-raises so the job lands in FAILURE with the error message.
    """
    _log.info(
        "restore_task.per_tenant.start",
        backup_id=backup_id,
        tenant_id=tenant_id,
        dry_run=dry_run,
    )
    self.update_state(
        state=STATE_PROGRESS,
        meta={
            "phase": "restoring",
            "message": f"restoring tenant {tenant_id} from bundle {backup_id}",
        },
    )
    try:
        result = run_per_tenant_restore(
            backup_id,
            tenant_id=tenant_id,
            confirm=confirm,
            dry_run=dry_run,
        )
    except PerTenantRestoreError as exc:
        _log.warning(
            "restore_task.per_tenant.failed",
            backup_id=backup_id,
            tenant_id=tenant_id,
            error=str(exc),
        )
        raise
    _log.info("restore_task.per_tenant.done", backup_id=backup_id, tenant_id=tenant_id)
    return result.to_dict()


__all__ = ["STATE_PROGRESS", "run_restore", "run_restore_per_tenant"]
