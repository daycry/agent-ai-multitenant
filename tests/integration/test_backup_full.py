"""Integration tests for the full backup engine (Plan 12 task_12_01).

The real pg_dump / tar / live volume access cannot run in the test
environment, so the external-command seam (:class:`workers.backup.CommandRunner`)
is MOCKED. A fake runner records the argv it is handed and fabricates the
artifact files pg_dump / tar would have written. The tests therefore assert:

  * COMMAND CONSTRUCTION — pg_dump gets the configured libpq URL + LOGICAL
    directory format; each configured volume is tar'd from its host _data tree.
  * ORCHESTRATION — DB dump first, then the volumes in order; a failing
    sub-step fails the WHOLE run cleanly (no partial bundle survives).
  * MANIFEST + CHECKSUMS — manifest.json lists every artifact with its size +
    SHA-256; the password never leaks into the manifest.
  * RETENTION — bundles older than the window are pruned; recent ones + the
    just-written bundle survive.

No real backup of the live stack happens here.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import (
    BackupConfig,
    BackupEngine,
    BackupError,
    CommandResult,
    run_full_backup,
)

pytestmark = pytest.mark.integration


@dataclass
class FakeRunner:
    """Records argv + fabricates the artifacts the real command would write.

    ``fail_on`` is an argv-substring; when a command's joined argv contains it,
    the runner returns a non-zero result (and writes nothing) to simulate a
    failing sub-step.
    """

    fail_on: str | None = None
    calls: list[list[str]] = field(default_factory=list)

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)
        joined = " ".join(argv)
        if self.fail_on is not None and self.fail_on in joined:
            return CommandResult(returncode=1, stderr="boom: simulated failure")

        if argv[0] == "pg_dump":
            # Fabricate a directory-format dump: pg_dump --file=<dir>.
            out_dir = _arg_value(argv, "--file=")
            assert out_dir is not None
            target = Path(out_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "toc.dat").write_bytes(b"fake-toc")
            (target / "3434.dat.gz").write_bytes(b"fake-table-data")
        elif argv[0] == "tar":
            archive = _arg_value(argv, "--file=")
            assert archive is not None
            Path(archive).write_bytes(b"fake-tar-gz-bytes")
        return CommandResult(returncode=0)


def _arg_value(argv: list[str], prefix: str) -> str | None:
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _config(tmp_path: Path, *, retention_days: int = 7) -> BackupConfig:
    return BackupConfig(
        backup_root=tmp_path / "backups",
        database_url="postgresql://migrations_user:s3cr3t@db:5432/agentic_platform",
        volumes=("minio_data", "redis_data", "vault_data"),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=retention_days,
    )


_NOW = datetime(2026, 5, 30, 3, 0, 0, tzinfo=UTC)


def test_pg_dump_invoked_logical_directory_with_db_url(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    engine.run_full_backup()

    pg_calls = [c for c in runner.calls if c[0] == "pg_dump"]
    assert len(pg_calls) == 1
    argv = pg_calls[0]
    joined = " ".join(argv)
    # LOGICAL directory format — the format the per-tenant restore needs.
    assert "--format=directory" in argv
    assert "--format=custom" not in joined
    # pg_basebackup is the binary path we explicitly DO NOT take.
    assert "pg_basebackup" not in joined
    # The configured libpq URL (with password) is passed via --dbname.
    assert "--dbname=postgresql://migrations_user:s3cr3t@db:5432/agentic_platform" in argv


def test_configured_volumes_are_tarred_from_their_data_trees(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path)
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    engine.run_full_backup()

    tar_calls = [c for c in runner.calls if c[0] == "tar"]
    # One tar per configured volume, in order.
    sources = [_arg_value(c, "--directory=") for c in tar_calls]
    assert sources == [
        str(cfg.volumes_mount_root / "minio_data" / "_data"),
        str(cfg.volumes_mount_root / "redis_data" / "_data"),
        str(cfg.volumes_mount_root / "vault_data" / "_data"),
    ]
    # Each tar is CREATE (GNU tar exige el modo — «You must specify one of the
    # '-Acdtrux' options»; faltó desde Plan 12 hasta el primer backup real
    # 2026-07-03) + gzip + writes a <volume>.tar.gz into the bundle.
    for call in tar_calls:
        assert "--create" in call
        assert "--gzip" in call
        archive = _arg_value(call, "--file=")
        assert archive is not None and archive.endswith(".tar.gz")


def test_db_dump_runs_before_volume_tars(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    engine.run_full_backup()

    verbs = [c[0] for c in runner.calls]
    # pg_dump first, then all tars.
    assert verbs[0] == "pg_dump"
    assert set(verbs[1:]) == {"tar"}


def test_manifest_and_checksums_written(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path)
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    result = engine.run_full_backup()

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["backup_id"] == "20260530T030000Z"
    assert manifest["encrypted"] is False
    # One pg_dump artifact + one per volume.
    kinds = [a["kind"] for a in manifest["artifacts"]]
    assert kinds == ["pg_dump", "volume_tar", "volume_tar", "volume_tar"]
    # Every artifact carries a real (64-hex) SHA-256 + a positive size.
    for art in manifest["artifacts"]:
        assert len(art["sha256"]) == 64
        assert int(art["sha256"], 16) >= 0  # valid hex
        assert art["size_bytes"] > 0
    # total_size_bytes is the sum.
    assert manifest["total_size_bytes"] == sum(a["size_bytes"] for a in manifest["artifacts"])


def test_configured_bind_paths_are_tarred_and_manifested(tmp_path: Path) -> None:
    """Auditoría 2026-07-02 (F0.4): /data/agent-platform (bind, NO named volume)
    entra en el bundle — los bare repos + worktrees no los cubría ningún backup
    y el wipe del bind en un engine-restart perdió el trabajo de 8 tareas."""
    runner = FakeRunner()
    bind = tmp_path / "data" / "agent-platform"
    cfg = BackupConfig(
        backup_root=tmp_path / "backups",
        database_url="postgresql://migrations_user:s3cr3t@db:5432/agentic_platform",
        volumes=("minio_data",),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=7,
        bind_paths=(str(bind),),
    )
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    result = engine.run_full_backup()

    tar_sources = [_arg_value(c, "--directory=") for c in runner.calls if c[0] == "tar"]
    assert str(bind) in tar_sources
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    bind_artifacts = [a for a in manifest["artifacts"] if a["kind"] == "bind_tar"]
    assert len(bind_artifacts) == 1
    assert bind_artifacts[0]["source"] == str(bind)
    assert bind_artifacts[0]["name"].endswith(".tar.gz")


def test_bind_tar_excludes_nested_backup_root(tmp_path: Path) -> None:
    """prod-04 A7 (auditoría 2026-07-06): el bind tar NO debe auto-incluir el
    backup_root cuando este vive DENTRO del bind_path.

    Config por defecto: bind_paths=[/data/agent-platform], backup_root=
    /data/agent-platform/backups → sin --exclude, cada backup diario embebía
    todos los bundles previos + los artefactos del run en curso (crecimiento
    cuadrático; y tar puede devolver rc≠0 por 'file changed as we read it')."""
    runner = FakeRunner()
    bind = tmp_path / "data" / "agent-platform"
    backups = bind / "backups"  # backup_root ANIDADO dentro del bind
    cfg = BackupConfig(
        backup_root=backups,
        database_url="postgresql://migrations_user:s3cr3t@db:5432/agentic_platform",
        volumes=(),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=7,
        bind_paths=(str(bind),),
    )
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    engine.run_full_backup()

    bind_tar = next(
        c for c in runner.calls if c[0] == "tar" and _arg_value(c, "--directory=") == str(bind)
    )
    # El argv debe llevar un --exclude que cubra el backup_root anidado (rel al
    # directorio archivado, p.ej. './backups' o 'backups').
    excludes = [t.split("=", 1)[1] for t in bind_tar if t.startswith("--exclude=")]
    assert any(
        "backups" in e for e in excludes
    ), f"el bind tar no excluye el backup_root anidado; argv={bind_tar}"


def test_bind_tar_no_exclude_when_backup_root_outside(tmp_path: Path) -> None:
    """Si el backup_root NO está bajo el bind_path, no se añade exclusión espuria."""
    runner = FakeRunner()
    bind = tmp_path / "data" / "agent-platform"
    cfg = BackupConfig(
        backup_root=tmp_path / "backups",  # FUERA del bind
        database_url="postgresql://migrations_user:s3cr3t@db:5432/agentic_platform",
        volumes=(),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=7,
        bind_paths=(str(bind),),
    )
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    engine.run_full_backup()

    bind_tar = next(
        c for c in runner.calls if c[0] == "tar" and _arg_value(c, "--directory=") == str(bind)
    )
    excludes = [t for t in bind_tar if t.startswith("--exclude=")]
    assert excludes == []


def test_manifest_does_not_leak_db_password(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    result = engine.run_full_backup()

    raw = result.manifest_path.read_text(encoding="utf-8")
    assert "s3cr3t" not in raw
    manifest = json.loads(raw)
    assert (
        manifest["database"]["url"] == "postgresql://migrations_user:***@db:5432/agentic_platform"
    )


def test_failing_pg_dump_fails_cleanly_no_partial_bundle(tmp_path: Path) -> None:
    runner = FakeRunner(fail_on="pg_dump")
    cfg = _config(tmp_path)
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    with pytest.raises(BackupError, match="pg_dump failed"):
        engine.run_full_backup()

    # No bundle directory survives a failed run — no false "success".
    bundles = list(cfg.backup_root.iterdir()) if cfg.backup_root.exists() else []
    assert bundles == []


def test_failing_volume_tar_fails_cleanly_no_partial_bundle(tmp_path: Path) -> None:
    # Fail on the SECOND volume — the DB dump + first tar already "succeeded".
    runner = FakeRunner(fail_on="redis_data")
    cfg = _config(tmp_path)
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    with pytest.raises(BackupError, match="tar of volume 'redis_data' failed"):
        engine.run_full_backup()

    bundles = list(cfg.backup_root.iterdir()) if cfg.backup_root.exists() else []
    assert bundles == []


def test_retention_prunes_old_bundles_keeps_recent(tmp_path: Path) -> None:
    cfg = _config(tmp_path, retention_days=7)
    cfg.backup_root.mkdir(parents=True)
    # Pre-seed three bundles: one 30 days old (prune), one 2 days old (keep),
    # one with a non-bundle name (leave alone).
    old = cfg.backup_root / "20260430T030000Z"
    recent = cfg.backup_root / "20260528T030000Z"
    foreign = cfg.backup_root / "not-a-bundle"
    for d in (old, recent, foreign):
        d.mkdir()
        (d / "marker").write_text("x", encoding="utf-8")

    runner = FakeRunner()
    engine = BackupEngine(cfg, runner=runner, now=_NOW)
    result = engine.run_full_backup()

    assert "20260430T030000Z" in result.pruned
    assert not old.exists()
    # Within the window — survives.
    assert recent.exists()
    # Not one of ours — never touched.
    assert foreign.exists()
    # The just-written bundle is present and never pruned.
    assert (cfg.backup_root / result.backup_id).exists()
    assert result.backup_id not in result.pruned


def test_retention_zero_keeps_only_the_new_bundle(tmp_path: Path) -> None:
    cfg = _config(tmp_path, retention_days=0)
    cfg.backup_root.mkdir(parents=True)
    yesterday = cfg.backup_root / "20260529T030000Z"
    yesterday.mkdir()

    runner = FakeRunner()
    engine = BackupEngine(cfg, runner=runner, now=_NOW)
    result = engine.run_full_backup()

    # retention_days=0 → cutoff is now; the just-written bundle is exempt.
    assert not yesterday.exists()
    assert (cfg.backup_root / result.backup_id).exists()


def test_run_full_backup_entrypoint_builds_engine_from_settings(tmp_path: Path) -> None:
    from workers.config import Settings

    settings = Settings(
        backup_root=str(tmp_path / "backups"),
        backup_database_url="postgresql://migrations_user:pw@db:5432/agentic_platform",
        backup_volumes=["minio_data"],
        backup_volumes_mount_root=str(tmp_path / "volumes"),
        backup_bind_paths=[str(tmp_path / "data" / "agent-platform")],
        backup_retention_days=7,
    )
    runner = FakeRunner()

    result = run_full_backup(settings=settings, runner=runner, now=_NOW)

    assert result.bundle_dir.exists()
    assert (result.bundle_dir / "manifest.json").exists()
    # Honoured the single configured volume + the configured bind path (F0.4).
    tar_calls = [c for c in runner.calls if c[0] == "tar"]
    assert len(tar_calls) == 2
