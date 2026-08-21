"""Quiesce de escritores durante la captura del bundle (ADR 0149, opción A).

El ADR 0149 se firmó el 2026-08-01 con la **opción A** —parar los escritores
mientras se captura— y con un matiz que no estaba en ninguna de las tres
opciones evaluadas y que es el corazón de este fichero:

    Se pide la parada y se espera un máximo (`WORKERS_BACKUP_QUIESCE_TIMEOUT_SECONDS`,
    180 s por defecto). Si vencen los plazos, **el backup sigue adelante** con los
    escritores que queden en pie y el acta registra `quiesce: partial` con quién no
    paró. Los servicios rearrancan SIEMPRE, en un `finally`.

Por qué eso no es un detalle: un quiesce que se cuelga convierte el backup
nocturno en una caída, y a las 03:00 no hay nadie mirando. Un backup con skew
registrado es mucho mejor que un backup que no existe.

Por qué estos tests viven en `tests/unit/` y no junto a `test_backup_full.py`
-----------------------------------------------------------------------------
No tocan Postgres, Redis ni Docker: el seam de subprocesos está doblado igual
que en el resto del motor. Y `tests/integration/` **no lo corre CI** (ver
CONTINUE_HERE §«Verificación local»): 517 ficheros que nadie ejecuta enteros son
donde se esconden los rojos. Un test de una degradación que solo ocurre de
madrugada tiene que correrlo alguien.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import BackupConfig, BackupEngine, BackupError, CommandResult
from workers.backup_quiesce import (
    QUIESCE_DISABLED,
    QUIESCE_FULL,
    QUIESCE_PARTIAL,
    ComposeQuiescer,
    QuiesceRecord,
)

_NOW = datetime(2026, 8, 1, 3, 0, 0, tzinfo=UTC)


@dataclass
class FakeRunner:
    """Doble del seam de subprocesos: registra argv y fabrica los artefactos.

    ``running`` es el conjunto que ``docker compose ps --services --status=running``
    devolverá: así un test describe «este servicio NO paró» sin simular Docker.
    ``fail_on`` marca un argv (subcadena) que devuelve rc≠0, y ``raise_on`` uno que
    revienta como lo haría un ``subprocess.TimeoutExpired``.
    """

    running: tuple[str, ...] = ()
    fail_on: str | None = None
    raise_on: str | None = None
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
        if self.raise_on is not None and self.raise_on in joined:
            raise TimeoutError(f"simulated hang running {joined!r}")
        if self.fail_on is not None and self.fail_on in joined:
            return CommandResult(returncode=1, stderr="boom: simulated failure")
        if "ps" in argv and "--services" in argv:
            return CommandResult(returncode=0, stdout="\n".join(self.running) + "\n")
        if argv[0] == "pg_dump":
            out_dir = _arg_value(argv, "--file=")
            assert out_dir is not None
            target = Path(out_dir)
            target.mkdir(parents=True, exist_ok=True)
            (target / "toc.dat").write_bytes(b"fake-toc")
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


def _compose_file(tmp_path: Path) -> Path:
    """Un compose que EXISTE: el quiescer se niega a pilotar uno que no está."""
    path = tmp_path / "docker-compose.yml"
    path.write_text("services: {}\n", encoding="utf-8")
    return path


def _quiescer(runner: FakeRunner, tmp_path: Path, **overrides: object) -> ComposeQuiescer:
    kwargs: dict[str, object] = {
        "runner": runner,
        "project": "agentic-platform",
        "compose_file": _compose_file(tmp_path),
        "services": ("api-server", "orchestrator", "workers"),
        "timeout_s": 180,
        "never_stop": ("workers-privileged",),
    }
    kwargs.update(overrides)
    return ComposeQuiescer(**kwargs)  # type: ignore[arg-type]


def _stop_argv(runner: FakeRunner) -> list[str] | None:
    for argv in runner.calls:
        if "stop" in argv:
            return argv
    return None


# --------------------------------------------------------------------------
# El camino feliz y el que el ADR añadió al firmar
# --------------------------------------------------------------------------


def test_quiesce_stops_the_configured_services_and_records_full(tmp_path: Path) -> None:
    runner = FakeRunner(running=())  # nadie sigue en pie tras el stop

    record = _quiescer(runner, tmp_path).quiesce()

    argv = _stop_argv(runner)
    assert argv is not None
    assert argv[:2] == ["docker", "compose"]
    assert "--project-name" in argv and "agentic-platform" in argv
    # `--timeout=` es el plazo del punto 1 del ADR, y va antes de los servicios.
    assert f"--timeout={180}" in argv
    assert argv[argv.index("stop") + 2 :] == ["api-server", "orchestrator", "workers"]
    assert record.mode == QUIESCE_FULL
    assert record.still_running == ()
    assert record.requested == ("api-server", "orchestrator", "workers")


def test_a_service_that_does_not_stop_degrades_to_partial_and_is_named(tmp_path: Path) -> None:
    # `workers` sigue corriendo tras el stop: un run largo que no atiende la señal.
    runner = FakeRunner(running=("workers", "postgres", "redis"))

    record = _quiescer(runner, tmp_path).quiesce()

    assert record.mode == QUIESCE_PARTIAL
    # Solo se reportan los que se PIDIÓ parar: postgres y redis siguen en pie a
    # propósito (son los que se leen).
    assert record.still_running == ("workers",)


def test_a_stop_that_hangs_degrades_instead_of_tumbar_el_backup(tmp_path: Path) -> None:
    # El caso que motiva el matiz del ADR: `docker compose stop` no vuelve.
    runner = FakeRunner(raise_on="stop")

    record = _quiescer(runner, tmp_path).quiesce()

    assert record.mode == QUIESCE_PARTIAL
    assert record.error is not None and "stop" in record.error


def test_a_stop_that_fails_degrades_instead_of_raising(tmp_path: Path) -> None:
    runner = FakeRunner(fail_on="stop")

    record = _quiescer(runner, tmp_path).quiesce()

    assert record.mode == QUIESCE_PARTIAL
    assert record.error is not None


def test_an_unverifiable_quiesce_is_partial_not_full(tmp_path: Path) -> None:
    """«No comprobado» y «coherente» no son lo mismo, y confundirlos es el verde vacío."""
    runner = FakeRunner(fail_on="ps")

    record = _quiescer(runner, tmp_path).quiesce()

    assert record.mode == QUIESCE_PARTIAL
    assert record.error is not None


def test_a_compose_file_that_is_not_there_says_so_instead_of_failing_three_commands(
    tmp_path: Path,
) -> None:
    """El instalador NO emite `WORKERS_RESTORE_COMPOSE_FILE`, y el default asume
    `data_root=/data/agent-platform`. Con otro data root el quiesce apuntaría a un
    fichero que no existe y degradaría **cada noche** con un `rc=1` de docker que
    no dice qué variable arreglar. Mejor decirlo antes de gastar tres subprocesos.
    """
    runner = FakeRunner()

    record = _quiescer(runner, tmp_path, compose_file=tmp_path / "no-existe.yml").quiesce()

    assert record.mode == QUIESCE_PARTIAL
    assert record.error is not None
    assert "RESTORE_COMPOSE_FILE" in record.error
    assert runner.calls == []


def test_without_configured_services_nothing_is_stopped(tmp_path: Path) -> None:
    runner = FakeRunner()

    record = _quiescer(runner, tmp_path, services=()).quiesce()

    assert record.mode == QUIESCE_DISABLED
    assert runner.calls == []


# --------------------------------------------------------------------------
# La guarda que evita que el backup se mate a sí mismo
# --------------------------------------------------------------------------


def test_the_lane_that_runs_the_backup_is_never_stopped(tmp_path: Path) -> None:
    """`workers-privileged` drena la cola `privileged`: ahí corre ESTE proceso.

    Pararlo mata el backup a mitad de la captura y deja el resto del stack
    parado hasta que alguien lo note. La lista de servicios es del operador, así
    que la guarda no puede ser «no lo pongas».
    """
    runner = FakeRunner()

    record = _quiescer(
        runner,
        tmp_path,
        services=("api-server", "workers-privileged", "workers"),
    ).quiesce()

    argv = _stop_argv(runner)
    assert argv is not None
    assert "workers-privileged" not in argv
    assert record.requested == ("api-server", "workers")


# --------------------------------------------------------------------------
# El rearranque, que es incondicional
# --------------------------------------------------------------------------


def test_resume_starts_every_service_that_was_asked_to_stop(tmp_path: Path) -> None:
    runner = FakeRunner()
    quiescer = _quiescer(runner, tmp_path)
    record = quiescer.quiesce()

    resumed = quiescer.resume(record)

    start = [argv for argv in runner.calls if "start" in argv]
    assert len(start) == 1
    assert start[0][start[0].index("start") + 1 :] == ["api-server", "orchestrator", "workers"]
    assert resumed.resumed is True
    assert resumed.resume_error is None


def test_resume_falls_back_to_up_detached_when_start_fails(tmp_path: Path) -> None:
    """Si el contenedor ya no existe, `start` no puede: `up -d` lo recrea.

    Dejar el stack parado porque el rearranque elegante falló sería el peor
    resultado posible de un backup.
    """
    runner = FakeRunner(fail_on="start")
    quiescer = _quiescer(runner, tmp_path)
    record = quiescer.quiesce()

    resumed = quiescer.resume(record)

    assert any("up" in argv and "--detach" in argv for argv in runner.calls)
    assert resumed.resumed is True


def test_resume_records_the_failure_when_neither_start_nor_up_work(tmp_path: Path) -> None:
    runner = FakeRunner(fail_on="compose")
    quiescer = _quiescer(runner, tmp_path)
    record = quiescer.quiesce()

    resumed = quiescer.resume(record)

    assert resumed.resumed is False
    assert resumed.resume_error is not None


def test_resume_without_a_quiesce_does_nothing(tmp_path: Path) -> None:
    runner = FakeRunner()
    quiescer = _quiescer(runner, tmp_path, services=())
    record = quiescer.quiesce()

    resumed = quiescer.resume(record)

    assert runner.calls == []
    assert resumed.mode == QUIESCE_DISABLED


# --------------------------------------------------------------------------
# El motor: el quiesce envuelve la captura y el acta lo registra
# --------------------------------------------------------------------------


def _config(tmp_path: Path, **overrides: object) -> BackupConfig:
    kwargs: dict[str, object] = {
        "backup_root": tmp_path / "backups",
        "database_url": "postgresql://migrations_user:s3cr3t@postgres:5432/agentic_platform",
        "volumes": ("minio_data",),
        "volumes_mount_root": tmp_path / "volumes",
        "retention_days": 7,
        "quiesce_services": ("api-server", "workers"),
        "compose_project": "agentic-platform",
        "compose_file": str(_compose_file(tmp_path)),
    }
    kwargs.update(overrides)
    return BackupConfig(**kwargs)  # type: ignore[arg-type]


def _manifest(bundle_dir: Path) -> dict[str, object]:
    payload = json.loads((bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_engine_stops_before_pg_dump_and_starts_after_the_last_tar(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    engine.run_full_backup()

    kinds = [
        "stop" if "stop" in argv else "start" if "start" in argv else argv[0]
        for argv in runner.calls
    ]
    assert kinds.index("stop") < kinds.index("pg_dump")
    assert kinds.index("start") > max(i for i, k in enumerate(kinds) if k == "tar")


def test_engine_records_a_full_quiesce_in_the_manifest(tmp_path: Path) -> None:
    runner = FakeRunner(running=())
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    result = engine.run_full_backup()

    quiesce = _manifest(result.bundle_dir)["quiesce"]
    assert isinstance(quiesce, dict)
    assert quiesce["mode"] == QUIESCE_FULL
    assert quiesce["still_running"] == []
    assert result.quiesce.mode == QUIESCE_FULL


def test_engine_records_a_partial_quiesce_with_who_did_not_stop(tmp_path: Path) -> None:
    runner = FakeRunner(running=("workers",))
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    result = engine.run_full_backup()

    quiesce = _manifest(result.bundle_dir)["quiesce"]
    assert isinstance(quiesce, dict)
    assert quiesce["mode"] == QUIESCE_PARTIAL
    assert quiesce["still_running"] == ["workers"]


def test_engine_restarts_the_services_even_when_the_capture_fails(tmp_path: Path) -> None:
    """El `finally` del punto 4 del ADR: el stack vuelve aunque el backup muera."""
    runner = FakeRunner(fail_on="pg_dump")
    engine = BackupEngine(_config(tmp_path), runner=runner, now=_NOW)

    with pytest.raises(BackupError):
        engine.run_full_backup()

    assert any("start" in argv for argv in runner.calls)


def test_engine_without_quiesce_services_behaves_exactly_as_before(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = BackupEngine(_config(tmp_path, quiesce_services=()), runner=runner, now=_NOW)

    result = engine.run_full_backup()

    assert not any("docker" in argv[0] for argv in runner.calls)
    assert result.quiesce.mode == QUIESCE_DISABLED
    assert _manifest(result.bundle_dir)["quiesce"] == QuiesceRecord.disabled().to_dict()
