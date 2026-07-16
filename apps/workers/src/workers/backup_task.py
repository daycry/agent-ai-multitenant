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
from pathlib import Path
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.backup import BackupConfig, BackupError, run_full_backup
from workers.backup_metrics import write_backup_metrics
from workers.backup_verification import verify_bundle
from workers.celery_app import app
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.backup_task")


@app.task(name="workers.run_daily_backup")  # type: ignore[untyped-decorator]
def run_daily_backup() -> dict[str, Any]:
    """Run the daily full backup (scheduled).

    Honours the ``backup_enabled`` platform setting (a System Admin's live OFF
    switch). Best-effort: a failure is logged, never raised, so beat keeps its
    cadence. Returns a small dict summarising the run.
    """
    settings = get_settings()
    return asyncio.run(_run_daily_backup(settings))


async def _run_daily_backup(settings: Settings) -> dict[str, Any]:
    """Async wrapper: read the live schedule, then run the (sync) engine.

    The schedule (``backup_enabled`` / ``backup_cron`` / ``backup_retention_days``)
    is a PLATFORM setting a System Admin configures from the admin panel
    (task_12_04), NOT a hardcoded constant. We read all three here so:

      * a live OFF (``backup_enabled=False``) makes the run a no-op without
        restarting Celery (mirrors the price-sync enable lever);
      * the configured ``retention_days`` overrides the env default, so the
        prune window the engine applies is the one the operator set in the
        panel — no restart needed.

    The ``cron`` cadence itself is consumed by the beat PROCESS at boot
    (``workers.beat_schedule.build_beat_schedule`` reads ``Settings.backup_cron``);
    we surface it in the summary/log so an operator can see the run honoured the
    configured schedule.
    """
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.maintenance / price_sync).
    from api_server.db.platform_settings import get_backup_schedule

    cron = settings.backup_cron
    retention_days = int(settings.backup_retention_days)
    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            enabled, cron, retention_days = await get_backup_schedule(db)
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("backup.schedule_read_error", error=str(exc))
        # If we can't even read the schedule, default to running with the env
        # defaults: a missed backup is worse than a redundant one. The engine's
        # own failure handling below still protects against partial bundles.
        enabled = True
    finally:
        await engine.dispose()

    if not enabled:
        _log.info("backup.skipped", reason="disabled", cron=cron)
        return {"enabled": False, "skipped": True}

    # Apply the panel-configured retention window over the env default. A copy
    # so we never mutate the cached process Settings.
    run_settings = settings.model_copy(update={"backup_retention_days": retention_days})

    try:
        result = run_full_backup(settings=run_settings)
    except BackupError as exc:
        _log.warning("backup.failed", error=str(exc))
        _emit_backup_metric(run_settings, success=False)
        return {"enabled": True, "ok": False, "error": str(exc)}
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("backup.error", error=str(exc))
        _emit_backup_metric(run_settings, success=False)
        return {"enabled": True, "ok": False, "error": str(exc)}

    # Post-backup corruption check (task_12_03): prove the bundle is intact
    # (pg_restore --list / tar -tf / checksum match) before trusting it. A
    # failed verification marks the backup INVALID — the basis for the
    # "last backup failed" alert in Phase D. Best-effort: a verifier crash is
    # logged, never raised, but it does flip the run's `valid` to False.
    valid = _verify_after_backup(run_settings, result.bundle_dir)

    # Emit the Prometheus health metric (task_12_14): success ONLY when the
    # Offsite upload (task_prod_04_12): ship ONLY a VERIFIED bundle to the
    # configured remote destinations, so a corrupt local bundle never becomes the
    # offsite copy. Best-effort: a destination failure is captured, never raised,
    # so the daily beat still succeeds locally.
    uploaded: list[str] = []
    upload_failures: list[str] = []
    if valid:
        try:
            destinations = await _read_backup_destinations(settings)
            uploaded, upload_failures = await _upload_bundle_to_destinations(result, destinations)
        except Exception as exc:  # pragma: no cover — defensive: beat must not die
            _log.warning("backup.upload.error", error=str(exc))

    # The metric reflects the bundle both wrote AND verified. A
    # produced-but-invalid bundle is a failed backup for alerting purposes — it
    # must not advance the success clock. AUD16-19: emitted AFTER the upload so
    # the offsite count/clock of THIS run travels in the same sample.
    _emit_backup_metric(run_settings, success=valid, offsite_uploaded=len(uploaded))

    return {
        "enabled": True,
        "ok": True,
        "valid": valid,
        "cron": cron,
        "retention_days": retention_days,
        "backup_id": result.backup_id,
        "bundle_dir": str(result.bundle_dir),
        "artifacts": len(result.artifacts),
        "pruned": len(result.pruned),
        "uploaded": uploaded,
        "upload_failures": upload_failures,
    }


async def _read_backup_destinations(settings: Settings) -> list[dict[str, Any]]:
    """The operator's configured remote destinations (NON-secret config only).

    Platform-global (the backup is a full logical dump across every tenant); read
    from the same ``backup_destinations`` platform setting the admin panel writes."""
    from api_server.db.platform_settings import get_backup_destinations

    engine = create_async_engine(settings.database_url)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            dests: list[dict[str, Any]] = list(await get_backup_destinations(db))
            return dests
    finally:
        await engine.dispose()


async def _upload_bundle_to_destinations(
    result: Any, destinations: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """Pack the verified bundle into a single ``<backup_id>.tar`` and upload it to
    every ENABLED destination. Best-effort per destination (task_prod_04_12).

    Returns ``(uploaded_names, failed_names)``. A destination that raises is logged
    (never the credential or the blob) and recorded in ``failed_names`` — the run
    does not fail. The single-file ``.tar`` name matches ``_strip_bundle_suffix`` so
    the restore listing dedupes by backup_id. Credentials resolve lazily through the
    workers' ``EnvSecretsProvider`` (Vault/env seam); each adapter has its own
    ``timeout_s``, so a hung upload can't block the beat forever."""
    enabled = [d for d in destinations if d.get("enabled", True)]
    if not enabled:
        return [], []

    import tempfile

    from workers.backup import SubprocessRunner
    from workers.backup_destinations import build_destination
    from workers.backup_encryption import EnvSecretsProvider

    bundle_dir = Path(result.bundle_dir)
    backup_root = bundle_dir.parent
    runner = SubprocessRunner()
    secrets = EnvSecretsProvider()
    uploaded: list[str] = []
    failed: list[str] = []

    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / f"{result.backup_id}.tar"
        # Pack the bundle dir (relative to backup_root, so the archive holds
        # <backup_id>/...) — explicit argv, no shell.
        runner.run(
            [
                "tar",
                "--create",
                f"--directory={backup_root}",
                f"--file={tar_path}",
                str(result.backup_id),
            ]
        )
        for dest in enabled:
            name = str(dest.get("name") or dest.get("type") or "?")
            try:
                adapter = build_destination(
                    {"type": dest.get("type"), "name": name, **(dest.get("config") or {})},
                    secrets=secrets,
                    runner=runner,
                )
                adapter.upload(tar_path)
                uploaded.append(name)
                _log.info("backup.dest.uploaded", destination=name)
            except Exception as exc:
                failed.append(name)
                _log.warning("backup.dest.upload_failed", destination=name, error=str(exc))
    return uploaded, failed


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


def _emit_backup_metric(settings: Settings, *, success: bool, offsite_uploaded: int = 0) -> None:
    """Write the node-exporter textfile metric for this run (task_12_14).

    Best-effort wrapper around :func:`workers.backup_metrics.write_backup_metrics`
    — the metric feeds the BackupLastRunFailed / BackupTooOld / BackupOffsiteStale
    alert rules but a failure to write it (collector dir absent because the
    monitoring overlay is not up, permission error, ...) must never affect the
    backup outcome.
    """
    try:
        write_backup_metrics(
            settings.backup_metrics_textfile_path,
            success=success,
            offsite_uploaded=offsite_uploaded,
        )
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("backup.metrics.error", error=str(exc))
