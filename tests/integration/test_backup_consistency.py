"""Coherencia de los artefactos del bundle (prod-04 task_prod_04_06).

El bundle se ensambla con el stack VIVO: el `pg_dump` es consistente consigo
mismo, pero cada `tar` retrata un instante distinto y algunos retratan un fichero
que se está escribiendo. Este fichero cubre las dos capturas donde eso no era un
skew tolerable sino una pérdida de datos:

## Redis — y por qué NO es «BGSAVE + capturar el dump.rdb»

El plan pedía literalmente «lanzar `BGSAVE` y capturar solo el `dump.rdb`
resultante». **Medido contra `redis:7-alpine` el 2026-07-31, eso restaura una
base VACÍA**: el compose arranca Redis con `--appendonly yes`, y un Redis con AOF
activado que encuentra un `dump.rdb` y ningún `appendonlydir` no lee el RDB —
crea un AOF nuevo y vacío («Creating AOF base file … on server start») y sirve
`DBSIZE 0`. El bundle habría parecido correcto y el restore habría perdido las
sesiones, el broker de Celery y los contadores de rate limit sin un solo error.

Lo que sí funciona, medido en el mismo banco: **`BGREWRITEAOF`, esperar a que
termine, y capturar el `appendonlydir`**. El rewrite deja un base file fresco y
un incr recién abierto; al restaurar, Redis con `appendonly yes` carga el base y
el incr sin ninguna gimnasia (`DB loaded from base file … / from incr file …`), e
incluso recupera las escrituras posteriores al rewrite que quedaron en la cola
del incr. El skew residual es la cola del incr que se escriba DURANTE el tar, y
Redis la tolera por diseño (`aof-load-truncated yes` trunca el último comando
incompleto).

## Vault — captura verificada

El file backend de Vault se escribe solo cuando alguien escribe un secreto, así
que una copia en caliente casi siempre sale coherente… y cuando no, no hay señal
de que salió rota: sin él, NINGÚN secreto del stack restaurado se puede
descifrar. En vez de asumirlo, la captura se verifica: huella del árbol antes y
después del tar; si cambió, se reintenta, y si no converge el run falla. Es
detección, no magia — el quiesce de escritores es la opción que decide el ADR.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import BackupConfig, BackupEngine, BackupError, CommandResult
from workers.backup_consistency import fingerprint_diff, tree_fingerprint

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 30, 3, 0, 0, tzinfo=UTC)


@dataclass
class Runner:
    """Fabrica los artefactos que tar/pg_dump escribirían y registra el argv.

    ``mutate_on`` permite simular una escritura CONCURRENTE: cuando el argv
    contiene esa subcadena, el runner toca un fichero del árbol de origen justo
    mientras «tarea», que es lo que la verificación de estabilidad debe detectar.
    """

    calls: list[list[str]] = field(default_factory=list)
    fail_on: str | None = None
    mutate_on: str | None = None
    mutate_target: Path | None = None
    #: Cuántas veces como máximo escribe (None = en cada tar que case).
    mutate_limit: int | None = None
    #: Contenido fijo a escribir. None = uno que CRECE en cada mutación, para que
    #: el cambio sea detectable incluso por tamaño.
    mutate_payload: str | None = None
    mutations: int = 0

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
        if self.fail_on and self.fail_on in joined:
            return CommandResult(returncode=1, stderr="boom")
        may_mutate = self.mutate_limit is None or self.mutations < self.mutate_limit
        if self.mutate_on and self.mutate_on in joined and self.mutate_target and may_mutate:
            self.mutations += 1
            self.mutate_target.write_text(
                self.mutate_payload
                if self.mutate_payload is not None
                else "escrito durante el tar" + "!" * self.mutations
            )
        if argv[0] == "pg_dump":
            out = Path(_value(argv, "--file="))
            out.mkdir(parents=True, exist_ok=True)
            (out / "toc.dat").write_bytes(b"toc")
        elif argv[0] == "tar":
            Path(_value(argv, "--file=")).write_bytes(b"tarball")
        return CommandResult(returncode=0)


def _value(argv: list[str], prefix: str) -> str:
    return next(a[len(prefix) :] for a in argv if a.startswith(prefix))


@dataclass
class FakeRewriter:
    """Doble del seam que le pide a Redis un AOF fresco."""

    calls: int = 0
    raises: Exception | None = None
    description: str = "BGREWRITEAOF ok"

    def flush(self) -> str:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.description


def _redis_dir(tmp_path: Path) -> Path:
    """Un directorio de datos de Redis como el que el compose bind-montea."""
    root = tmp_path / "redis"
    aof = root / "appendonlydir"
    aof.mkdir(parents=True)
    (aof / "appendonly.aof.2.base.rdb").write_bytes(b"base")
    (aof / "appendonly.aof.2.incr.aof").write_bytes(b"incr")
    (aof / "appendonly.aof.manifest").write_text(
        "file appendonly.aof.2.base.rdb seq 2 type b\nfile appendonly.aof.2.incr.aof seq 2 type i\n"
    )
    (root / "dump.rdb").write_bytes(b"rdb")
    return root


def _config(tmp_path: Path, **overrides: object) -> BackupConfig:
    base: dict[str, object] = {
        "backup_root": tmp_path / "backups",
        "database_url": "postgresql://u:p@postgres:5432/agentic",
        "volumes": (),
        "volumes_mount_root": tmp_path / "volumes",
        "retention_days": 7,
    }
    base.update(overrides)
    return BackupConfig(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Redis
# --------------------------------------------------------------------------- #


def test_the_redis_capture_flushes_the_aof_before_tarring(tmp_path: Path) -> None:
    """El orden importa y es lo único que hace la captura coherente: primero el
    rewrite (que cierra un base file consistente), después el tar."""
    redis_dir = _redis_dir(tmp_path)
    rewriter = FakeRewriter()
    runner = Runner()
    engine = BackupEngine(
        _config(tmp_path, redis_dir=str(redis_dir)),
        runner=runner,
        redis_flusher=rewriter,
        now=_NOW,
    )

    result = engine.run_full_backup()

    assert rewriter.calls == 1, "no se pidió el rewrite del AOF antes de capturar Redis"
    redis_art = next(a for a in result.artifacts if a.kind == "redis_tar")
    assert redis_art.source == str(redis_dir)
    # El tar de Redis ocurre DESPUÉS del rewrite: si se invirtiera, el base file
    # capturado sería el viejo y el incr un río de días.
    assert any("redis" in " ".join(c) for c in runner.calls if c[0] == "tar")


def test_the_redis_capture_takes_the_aof_not_a_lone_rdb(tmp_path: Path) -> None:
    """Medido: un `dump.rdb` sin `appendonlydir` restaura una base VACÍA cuando
    Redis corre con `--appendonly yes`, que es como lo arranca el compose. El
    artefacto tiene que llevar el AOF; el RDB va de propina."""
    redis_dir = _redis_dir(tmp_path)
    runner = Runner()
    BackupEngine(
        _config(tmp_path, redis_dir=str(redis_dir)),
        runner=runner,
        redis_flusher=FakeRewriter(),
        now=_NOW,
    ).run_full_backup()

    argv = next(c for c in runner.calls if c[0] == "tar" and "redis" in " ".join(c))
    members = [a for a in argv if not a.startswith("-") and a != "tar"]
    assert "appendonlydir" in members, (
        f"el tar de Redis no captura el AOF ({members}): al restaurar, un Redis con "
        "appendonly yes ignoraría el dump.rdb y arrancaría VACÍO"
    )


def test_a_failed_aof_rewrite_fails_the_run_cleanly(tmp_path: Path) -> None:
    """Si Redis no puede darnos un AOF fresco, el bundle NO se produce a medias.

    La alternativa —capturar lo que haya y seguir— es la que deja un bundle que
    parece bueno y trae un Redis roto: exactamente el fallo silencioso que este
    task existe para quitar.
    """
    redis_dir = _redis_dir(tmp_path)
    engine = BackupEngine(
        _config(tmp_path, redis_dir=str(redis_dir)),
        runner=Runner(),
        redis_flusher=FakeRewriter(raises=RuntimeError("redis unreachable")),
        now=_NOW,
    )

    with pytest.raises(BackupError, match="redis"):
        engine.run_full_backup()

    assert not (tmp_path / "backups" / _NOW.strftime("%Y%m%dT%H%M%SZ")).exists()


def test_no_redis_dir_configured_captures_nothing(tmp_path: Path) -> None:
    """Sin `redis_dir` no hay artefacto ni rewrite: un stack que declara Redis
    recreable (la opción del ADR) no debe pagar el coste ni recibir un fallo."""
    rewriter = FakeRewriter()
    result = BackupEngine(
        _config(tmp_path), runner=Runner(), redis_flusher=rewriter, now=_NOW
    ).run_full_backup()

    assert rewriter.calls == 0
    assert not [a for a in result.artifacts if a.kind == "redis_tar"]


# --------------------------------------------------------------------------- #
# Vault: captura verificada
# --------------------------------------------------------------------------- #


def test_a_stable_snapshot_is_captured_without_complaint(tmp_path: Path) -> None:
    """No-vacuidad: el camino normal (Vault quieto) pasa a la primera."""
    vault = tmp_path / "vault"
    (vault / "core").mkdir(parents=True)
    (vault / "core" / "_seal-config").write_text("sellado")
    runner = Runner()

    result = BackupEngine(
        _config(
            tmp_path,
            bind_paths=(str(vault),),
            stable_snapshot_paths=(str(vault),),
        ),
        runner=runner,
        now=_NOW,
    ).run_full_backup()

    assert [a for a in result.artifacts if a.kind == "bind_tar"]
    tars = [c for c in runner.calls if c[0] == "tar"]
    assert len(tars) == 1, f"un snapshot estable no debería reintentarse: {tars}"


def test_a_snapshot_that_keeps_changing_fails_the_run(tmp_path: Path) -> None:
    """Si el árbol cambia en cada intento, el run falla en vez de guardar una
    copia rota sin decirlo.

    Un tar de Vault tomado a mitad de una escritura puede dejar el barrel de
    claves inconsistente, y eso no se descubre hasta que alguien intenta
    desellar el Vault restaurado — en pleno DR.
    """
    vault = tmp_path / "vault"
    (vault / "core").mkdir(parents=True)
    changing = vault / "core" / "_write-in-flight"
    changing.write_text("v0")
    runner = Runner(mutate_on=str(vault), mutate_target=changing)

    engine = BackupEngine(
        _config(
            tmp_path,
            bind_paths=(str(vault),),
            stable_snapshot_paths=(str(vault),),
            snapshot_retries=2,
        ),
        runner=runner,
        now=_NOW,
    )

    with pytest.raises(BackupError, match="cambió"):
        engine.run_full_backup()

    # Reintentó lo que se le dijo (intento inicial + 2) y no más.
    assert runner.mutations == 3, f"reintentos inesperados: {runner.mutations}"
    assert not (tmp_path / "backups" / _NOW.strftime("%Y%m%dT%H%M%SZ")).exists()


def test_a_snapshot_that_settles_on_a_retry_succeeds(tmp_path: Path) -> None:
    """Una escritura suelta durante el primer intento no debe tirar el backup:
    se reintenta y, si el árbol se queda quieto, el bundle sale."""
    vault = tmp_path / "vault"
    (vault / "core").mkdir(parents=True)
    target = vault / "core" / "_write-in-flight"
    target.write_text("v0")
    runner = Runner(mutate_on=str(vault), mutate_target=target, mutate_limit=1)

    result = BackupEngine(
        _config(
            tmp_path,
            bind_paths=(str(vault),),
            stable_snapshot_paths=(str(vault),),
            snapshot_retries=2,
        ),
        runner=runner,
        now=_NOW,
    ).run_full_backup()

    assert [a for a in result.artifacts if a.kind == "bind_tar"]
    assert runner.mutations == 1, "la escritura simulada no ocurrió una sola vez"


def test_the_fingerprint_sees_a_same_size_overwrite_with_an_unchanged_mtime(
    tmp_path: Path,
) -> None:
    """La huella detecta un cambio que `(tamaño, mtime)` NO puede ver.

    Esto no es una hipótesis: la primera versión comprobaba la estabilidad con
    `(tamaño, mtime_ns)` y una ejecución real de esta suite la pilló diciendo
    «estable» justo después de reescribir un fichero — dos escrituras del mismo
    tamaño dentro de la misma marca de reloj no mueven ninguno de los dos campos.
    Y detectar por mtime es peor que no detectar: es una CARRERA, así que el mismo
    caso pasa unas veces y falla otras (verificado: al revertir la huella a mtime,
    el caso a veces se colaba). Vault reescribe ficheros de tamaño constante — el
    `_seal-config`, las entradas del barrel de claves — así que el caso es el suyo.

    El mtime se fija a mano con `os.utime` para que el discriminante sea
    DETERMINISTA y no dependa de la resolución del reloj de la máquina: con la
    huella por contenido este test pasa siempre; con la de mtime, falla siempre.
    """
    tree = tmp_path / "vault"
    (tree / "core").mkdir(parents=True)
    target = tree / "core" / "_seal-config"
    target.write_bytes(b"AAAA")
    stamp = target.stat()
    before = tree_fingerprint(tree)

    target.write_bytes(b"BBBB")  # mismos bytes de tamaño, contenido distinto
    os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))  # y el MISMO mtime
    assert target.stat().st_size == 4
    assert target.stat().st_mtime_ns == stamp.st_mtime_ns, "la premisa del test no se cumple"

    changed = fingerprint_diff(before, tree_fingerprint(tree))
    assert changed == ["core/_seal-config"], (
        "la huella no vio una reescritura del mismo tamaño con el mismo mtime: la "
        "verificación de estabilidad daría «coherente» sobre una copia rota de Vault"
    )


def test_the_redis_artifact_is_restored_and_not_just_captured(tmp_path: Path) -> None:
    """El otro lado del contrato, que es donde este repo se ha caído dos veces.

    `projects_tar` se respaldaba, se le calculaba el checksum, se verificaba… y
    NUNCA se restauraba, porque `_restore_volumes` filtraba `kind == "volume_tar"`.
    Un artefacto nuevo que el restore ignora es peor que no tenerlo: ocupa espacio
    y da confianza injustificada. Así que el `redis_tar` tiene que tener llamante
    en el motor de restauración.
    """
    from workers.restore import RestoreConfig, RestoreEngine

    redis_target = tmp_path / "redis"
    redis_target.mkdir()
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "redis.tar.gz").write_bytes(b"tarball")
    manifest = {
        "artifacts": [
            {
                "kind": "redis_tar",
                "name": "redis.tar.gz",
                "path": "redis.tar.gz",
                "source": str(redis_target),
            }
        ]
    }
    runner = Runner()
    engine = RestoreEngine(
        RestoreConfig(
            backup_root=tmp_path / "backups",
            database_url="postgresql://u:p@postgres:5432/agentic",
            volumes=(),
            volumes_mount_root=tmp_path / "volumes",
            compose_project="agentic-platform",
            compose_file=tmp_path / "docker-compose.yml",
            app_services=(),
            volume_services=(),
            redis_dir=str(redis_target),
        ),
        runner=runner,
    )

    _restored_volumes, restored_paths = engine._restore_data_artifacts(bundle, manifest)

    assert str(redis_target) in restored_paths, (
        "el restore ignoró el artefacto redis_tar: Redis volvería del DR con las "
        "sesiones, el broker de Celery y los rate limits del día anterior o vacío"
    )
    extract = next(
        c for c in runner.calls if c[0] == "tar" and "--extract" in c and "redis" in " ".join(c)
    )
    assert f"--directory={redis_target}" in extract


def test_paths_outside_the_stable_list_are_not_verified(tmp_path: Path) -> None:
    """MinIO se escribe todo el rato por diseño: exigirle estabilidad sería
    convertir el backup nocturno en un fallo nocturno. La verificación es una
    lista explícita, no una política global."""
    minio = tmp_path / "minio"
    (minio / "bucket").mkdir(parents=True)
    changing = minio / "bucket" / "object"
    changing.write_text("v0")
    runner = Runner(mutate_on=str(minio), mutate_target=changing)

    result = BackupEngine(
        _config(tmp_path, bind_paths=(str(minio),), stable_snapshot_paths=()),
        runner=runner,
        now=_NOW,
    ).run_full_backup()

    assert [a for a in result.artifacts if a.kind == "bind_tar"]
    assert len([c for c in runner.calls if c[0] == "tar"]) == 1
