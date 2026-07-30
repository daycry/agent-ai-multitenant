"""Restore-drill mensual (ADR 0126): un backup no probado no existe.

El beat ``workers.restore_drill`` (día 2 de cada mes, 04:30 UTC — tras el
backup diario) toma el último bundle, ejecuta la verificación estructural
EXISTENTE (:mod:`workers.backup_verification`: pg_restore --list, tar -tf,
checksums) y, solo si es válida, lo restaura DE VERDAD a una base de datos
efímera (``drill_<fecha>``) contando filas de tablas clave; la base se
elimina siempre al acabar. El resultado se notifica SIEMPRE
(``restore_drill_result``, señal de plataforma): un drill fallido en
silencio sería peor que no tener drill. Restaurar cero filas cuenta como
fallo — un dump vacío estructuralmente válido no es un backup.
"""

from __future__ import annotations

import asyncio
import enum
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import structlog

from workers.celery_app import app
from workers.config import get_settings

_log = structlog.get_logger(__name__)

DRILL_EVENT = "restore_drill_result"

#: Tablas cuyo conteo demuestra que el restore trajo datos reales.
DRILL_TABLES = ("organizations", "plans", "executions")


class DrillOutcome(enum.StrEnum):
    OK = "ok"
    FAILED = "failed"


class DrillNotifier(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


Verifier = Callable[[str], Awaitable[tuple[bool, str]]]
Restorer = Callable[[str], Awaitable[dict[str, int]]]


def _event(*, ok: bool, detail: str) -> dict[str, Any]:
    return {
        "event_type": DRILL_EVENT,
        "tenant_id": None,  # señal de plataforma (System Admin)
        "context": {"ok": ok, "detail": detail},
    }


async def _run_drill(
    *,
    bundle: str,
    verifier: Verifier,
    restorer: Restorer,
    notifier: DrillNotifier,
) -> DrillOutcome:
    """Un drill: verificar → restaurar → contar → notificar SIEMPRE."""
    try:
        valid, verify_detail = await verifier(bundle)
        if not valid:
            notifier.publish(_event(ok=False, detail=f"verificación fallida: {verify_detail}"))
            return DrillOutcome.FAILED
        counts = await restorer(bundle)
        if not counts or all(v == 0 for v in counts.values()):
            notifier.publish(
                _event(ok=False, detail=f"restore vacío (conteos: {counts}) — bundle sospechoso")
            )
            return DrillOutcome.FAILED
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        notifier.publish(_event(ok=True, detail=f"restore verificado — {summary}"))
        return DrillOutcome.OK
    except Exception as exc:
        notifier.publish(_event(ok=False, detail=f"{type(exc).__name__}: {exc}"))
        return DrillOutcome.FAILED


# ---------------------------------------------------------------------------
# Cableado real — integración/operación.
# ---------------------------------------------------------------------------
def _latest_bundle(backup_root: Path) -> Path | None:
    """El bundle más reciente (subdirectorio con manifest.json)."""
    candidates = sorted(
        (p for p in backup_root.iterdir() if (p / "manifest.json").is_file()),
        key=lambda p: p.name,
        reverse=True,
    )
    return candidates[0] if candidates else None


async def _real_verifier(bundle: str) -> tuple[bool, str]:
    from workers.backup_verification import BackupVerifier

    report = BackupVerifier().verify_bundle(Path(bundle))
    if report.valid:
        return True, "estructura íntegra"
    failures = "; ".join(f"{c.check}: {c.detail}" for c in report.failures)
    return False, failures


async def _real_restorer(bundle: str) -> dict[str, int]:
    """Restaura el dump a una DB efímera y cuenta filas clave. Siempre limpia."""
    import asyncpg

    settings = get_settings()
    # DSN admin (BYPASSRLS) del worker → conexión a `postgres` para el DDL.
    admin_dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    base_dsn, _, _ = admin_dsn.rpartition("/")
    drill_db = f"drill_{datetime.now(tz=UTC).strftime('%Y%m%d%H%M%S')}"
    conn = await asyncpg.connect(f"{base_dsn}/postgres")
    try:
        await conn.execute(f'CREATE DATABASE "{drill_db}"')
    finally:
        await conn.close()
    try:
        dump_dir = Path(bundle) / "db"
        proc = await asyncio.create_subprocess_exec(
            "pg_restore",
            "--no-owner",
            "--no-privileges",
            "--dbname",
            f"{base_dsn}/{drill_db}",
            str(dump_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"pg_restore exit {proc.returncode}: {stderr.decode()[:300]}")
        check = await asyncpg.connect(f"{base_dsn}/{drill_db}")
        try:
            counts: dict[str, int] = {}
            for table in DRILL_TABLES:
                counts[table] = int(await check.fetchval(f'SELECT count(*) FROM "{table}"'))
            return counts
        finally:
            await check.close()
    finally:
        cleanup = await asyncpg.connect(f"{base_dsn}/postgres")
        try:
            await cleanup.execute(f'DROP DATABASE IF EXISTS "{drill_db}" WITH (FORCE)')
        finally:
            await cleanup.close()


@app.task(name="workers.restore_drill")  # type: ignore[untyped-decorator]
def restore_drill_task() -> str:
    """Drill mensual del último backup. Notifica el resultado siempre."""
    settings = get_settings()

    async def _main() -> str:
        from workers.standup import CeleryStandupNotifier

        notifier = CeleryStandupNotifier(broker_url=settings.broker_url)
        backup_root = Path(settings.backup_root)
        bundle = _latest_bundle(backup_root) if backup_root.is_dir() else None
        if bundle is None:
            notifier.publish(_event(ok=False, detail=f"sin bundles de backup en {backup_root}"))
            return DrillOutcome.FAILED.value
        outcome = await _run_drill(
            bundle=str(bundle),
            verifier=_real_verifier,
            restorer=_real_restorer,
            notifier=notifier,
        )
        return outcome.value

    try:
        return asyncio.run(_main())
    except Exception:
        _log.exception("restore_drill.run_failed")
        return DrillOutcome.FAILED.value
