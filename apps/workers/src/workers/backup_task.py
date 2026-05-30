"""Scheduled daily backup beat task (Plan 12 task_12_01 / task_12_04).

A Celery beat task, ``workers.run_daily_backup``, that runs the full-backup
engine (:mod:`workers.backup`) on a CONFIGURABLE cadence. Wired to beat by
:mod:`workers.beat_schedule` (default daily at 03:00, Plan 12) and gated by a
live ``backup_enabled`` PLATFORM setting a System Admin flips from the admin
panel (task_12_04).

Best-effort, like the other beat tasks (:mod:`workers.maintenance`): a single
run failure is logged, never raised, so beat keeps firing on cadence. The
human-facing failure signal (notify the admin) is task_12_04's concern; here
we surface it in the returned summary + structured log.

Multi-tenancy / RBAC: the backup is platform-global (a full logical dump across
every tenant). A tenant CANNOT trigger or schedule it — the schedule lives in
the platform beat process and the enable flag is a platform setting only a
System Admin can write.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.backup import BackupConfig, BackupError, run_full_backup
from workers.backup_verification import verify_bundle
from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.backup_task")


@app.task(name="workers.run_daily_backup")  # type: ignore[misc]
def run_daily_backup() -> dict[str, Any]:
    """Run the daily full backup (scheduled).

    Honours the ``backup_enabled`` platform setting (a System Admin's live OFF
    switch). Best-effort: a failure is logged, never raised, so beat keeps its
    cadence. Returns a small dict summarising the run.
    """
    settings = get_settings()
    return asyncio.run(_run_daily_backup(settings))


async def _run_daily_backup(settings: Settings) -> dict[str, Any]:
    """Async wrapper: check the enable flag, then run the (sync) engine."""
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.maintenance / price_sync).
    from api_server.db.platform_settings import get_backup_enabled

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            enabled = await get_backup_enabled(db)
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("backup.enable_check_error", error=str(exc))
        # If we can't even read the flag, default to running: a missed backup
        # is worse than a redundant one. The engine's own failure handling
        # below still protects against partial bundles.
        enabled = True
    finally:
        await engine.dispose()

    if not enabled:
        _log.info("backup.skipped", reason="disabled")
        return {"enabled": False, "skipped": True}

    try:
        result = run_full_backup(settings=settings)
    except BackupError as exc:
        _log.warning("backup.failed", error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("backup.error", error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc)}

    # Post-backup corruption check (task_12_03): prove the bundle is intact
    # (pg_restore --list / tar -tf / checksum match) before trusting it. A
    # failed verification marks the backup INVALID — the basis for the
    # "last backup failed" alert in Phase D. Best-effort: a verifier crash is
    # logged, never raised, but it does flip the run's `valid` to False.
    valid = _verify_after_backup(settings, result.bundle_dir)

    return {
        "enabled": True,
        "ok": True,
        "valid": valid,
        "backup_id": result.backup_id,
        "bundle_dir": str(result.bundle_dir),
        "artifacts": len(result.artifacts),
        "pruned": len(result.pruned),
    }


def _verify_after_backup(settings: Settings, bundle_dir: Any) -> bool:
    """Verify a just-written bundle; return its overall validity.

    When at-rest encryption is enabled, build the same Vault/env-backed
    :class:`BackupEncryptor` the engine used so the verifier can additionally
    prove the encrypted blob decrypts (GCM authentication). A failure inside the
    verifier itself (unreadable manifest, etc.) is treated as INVALID — an
    unverifiable backup is not a trustworthy backup.
    """
    cfg = BackupConfig.from_settings(settings)
    encryptor = None
    if cfg.encryption_enabled:
        from workers.backup_encryption import BackupEncryptor, EnvSecretsProvider

        encryptor = BackupEncryptor(
            provider=EnvSecretsProvider(),
            vault_key_name=cfg.encryption_vault_key,
        )
    try:
        report = verify_bundle(bundle_dir, encryptor=encryptor)
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("backup.verify.error", error=str(exc))
        return False
    if not report.valid:
        _log.warning(
            "backup.invalid",
            backup_id=report.backup_id,
            failures=[c.check + ":" + c.artifact for c in report.failures],
        )
    return report.valid
