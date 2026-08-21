"""Full backup engine (Plan 12 task_12_01).

Produces a *timestamped backup bundle* on the worker host:

  1. a **pg_dump LOGICAL** dump of PostgreSQL in ``directory`` format
     (``--format=directory``). Logical — not pg_basebackup — because that is
     the only shape that lets a later task restore a *single tenant's* rows
     (Decisiones Clave, Plan 12). Directory format is parallel-friendly and
     ``pg_restore --list`` can introspect it for the verification step.
  2. a **tar + gzip** of each configured data volume (MinIO objects, the
     Redis RDB/AOF, the Vault file backend). Volume names + their host
     mount root come from config (``WORKERS_BACKUP_VOLUMES`` /
     ``WORKERS_BACKUP_VOLUMES_MOUNT_ROOT``), never hardcoded.
  3. a **manifest.json** describing what was captured: artifact paths, byte
     sizes, SHA-256 checksums, timestamps, and the overall status.

Then it prunes local bundles older than the retention window
(``WORKERS_BACKUP_RETENTION_DAYS``, default 7).

Design — the subprocess seam
----------------------------
Real ``pg_dump`` / ``tar`` / live volume access cannot run inside the unit
test environment, so every external command goes through an injectable
:class:`CommandRunner`. Production wires :class:`SubprocessRunner`; tests inject
a fake that records the argv it was asked to run and fabricates the artifact
files. Tests therefore assert *command construction* (pg_dump gets the right
URL + format, the configured volumes get tar'd), the *orchestration* (ordering,
clean failure, artifact naming), and the *verification/manifest* logic — never
a real dump of the live stack.

Clean-failure contract
----------------------
If ANY sub-step fails, the whole run fails: the partial bundle directory is
removed and :class:`BackupError` is raised. There is no partial "success" — a
half-written bundle that looked fine but is missing the DB dump is worse than
no bundle, because it gives false confidence at restore time.

Encryption (task_12_02) plugs in *after* this engine assembles the bundle. When
``BackupConfig.encryption_enabled`` is set and an :class:`BackupEncryptor` is
injected, the assembled bundle is tar'd into one archive and that archive is
wrapped into a single AES-256-GCM blob (``bundle.tar.enc``) keyed by a
Vault-resolved secret; the plaintext archive is removed and the manifest records
``encrypted: true`` plus the encrypted artifact. When disabled the behaviour is
unchanged (``encrypted: false``, the plaintext bundle is left as-is).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import structlog

from workers.backup_consistency import (
    PersistenceFlusher,
    fingerprint_diff,
    tree_fingerprint,
)
from workers.backup_encryption import ENCRYPTED_SUFFIX, BackupEncryptor
from workers.backup_quiesce import ComposeQuiescer, QuiesceRecord
from workers.backup_secrets import exclude_table_data_args
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.backup")

# Manifest schema version — bump if the on-disk shape changes so a future
# restore (task_12_10) can branch on it.
MANIFEST_VERSION = 1
MANIFEST_FILENAME = "manifest.json"

# Timestamp format for the bundle directory name. UTC, sortable, filesystem-
# safe (no colons — Windows-hostile). e.g. "20260530T031500Z".
_BUNDLE_TS_FORMAT = "%Y%m%dT%H%M%SZ"

# The DB dump lives in a directory-format subtree inside the bundle.
_DB_DUMP_DIRNAME = "postgres"

# When encryption is enabled the whole bundle is collapsed into one tar, then
# that tar is AES-256-GCM-wrapped. These are the on-disk names of the two.
_BUNDLE_ARCHIVE_NAME = "bundle.tar"
_ENCRYPTED_BUNDLE_NAME = _BUNDLE_ARCHIVE_NAME + ENCRYPTED_SUFFIX  # bundle.tar.enc

# Read chunk for checksums — bounded memory even for multi-GB tarballs.
_CHECKSUM_CHUNK = 1024 * 1024


class BackupError(RuntimeError):
    """Raised when a backup sub-step fails. The partial bundle is removed
    before this propagates, so a failed run never leaves a misleading
    half-bundle behind."""


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one external command."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """The subprocess seam. Production uses :class:`SubprocessRunner`; tests
    inject a fake that records argv and fabricates artifact files."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult: ...


@dataclass
class SubprocessRunner:
    """Real runner — shells out with explicit argv (never ``shell=True``)."""

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        import os

        full_env = {**os.environ, **(env or {})}
        completed = subprocess.run(  # — explicit argv, no shell
            list(args),
            capture_output=True,
            text=True,
            env=full_env,
            timeout=timeout,
            check=False,
        )
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


@dataclass(frozen=True)
class BackupConfig:
    """Operator-tunable knobs for one backup run — no magic numbers.

    Built from :class:`workers.config.Settings` via :meth:`from_settings`, which
    is the only place the env defaults are read. Tests construct it directly
    with ``tmp_path`` roots.
    """

    backup_root: Path
    database_url: str
    volumes: tuple[str, ...]
    volumes_mount_root: Path
    retention_days: int
    # Bind mounts (rutas absolutas, NO named volumes) que también entran en el
    # bundle. Auditoría 2026-07-02 (F0.4): /data/agent-platform (bare repos +
    # worktrees de los agentes) no lo cubría ningún backup y un engine-restart
    # de Docker Desktop lo arrasó perdiendo el trabajo comiteado de 8 tareas.
    bind_paths: tuple[str, ...] = ()
    # prod-04 task_prod_04_05 — the bare repos of every project (the platform's
    # PRODUCT: principios rectores 4 y 5). A dedicated, verified, RESTORED
    # artifact instead of riding along inside the data-root bind tar. Empty
    # string = do not capture them (a deliberate operator choice).
    projects_root: str = ""
    # Directory NAMES excluded from the projects tar + the bind tars because they
    # are regenerable (`worktrees`, `dep-cache`). Also avoids tar's rc≠0 "file
    # changed as we read it" over a worktree an agent is writing.
    transient_excludes: tuple[str, ...] = ()
    # prod-04 task_prod_04_06 — el directorio de datos de Redis (host path). Se
    # captura como artefacto PROPIO y precedido de un BGREWRITEAOF, en vez de
    # entrar de rebote en un bind tar sobre un AOF en escritura activa. Vacío = no
    # capturar Redis (la opción «recreable» del ADR de consistencia).
    redis_dir: str = ""
    # URL con la que hablarle a Redis para pedirle el rewrite. Vacío = no se puede
    # consolidar la persistencia; el motor lo trata como error si `redis_dir` está
    # configurado, porque capturar el AOF sin rewrite es la captura ingenua.
    redis_url: str = ""
    # prod-04 task_prod_04_06 — bind paths cuya captura se VERIFICA estable
    # (huella del árbol antes y después del tar). Para el file backend de Vault,
    # que se escribe rara vez pero cuya copia rota no da ninguna señal hasta que
    # alguien intenta desellar el Vault restaurado. Deliberadamente NO se aplica a
    # MinIO: se escribe todo el rato por diseño y exigirle estabilidad convertiría
    # el backup nocturno en un fallo nocturno.
    stable_snapshot_paths: tuple[str, ...] = ()
    # Reintentos de la captura verificada antes de darla por imposible.
    snapshot_retries: int = 2
    # Optional at-rest encryption (task_12_02). When True the engine expects an
    # injected BackupEncryptor; the Vault key NAME (not value) is here so the
    # engine can build a default encryptor from settings.
    encryption_enabled: bool = False
    encryption_vault_key: str = "backup_encryption_key"
    # prod-04 task_prod_04_07 — offsite custody of the AES key. The fingerprint of
    # the key an operator DECLARES to have deposited offsite; the engine refuses
    # to produce an encrypted bundle whose key does not match it.
    key_custody_fingerprint: str = ""
    # Whether an ABSENT custody fingerprint is fatal. True outside dev: an
    # encrypted bundle whose key is not in custody is unrecoverable if the host
    # dies, which is the exact scenario a backup exists for.
    require_key_custody: bool = False
    # prod-04 task_prod_04_09 (hallazgo deploy-4). Si el `.env` no emite
    # `WORKERS_BACKUP_DATABASE_URL`, el motor hereda el default de DEV
    # (`…changeme-migrations-dev-only@localhost:15432`) y `pg_dump` sale a buscar
    # un postgres que dentro del contenedor de un worker no existe. True fuera de
    # dev: mejor abortar con un mensaje que diga qué variable falta que producir un
    # fallo de conexión cada noche a las 03:00.
    require_production_dsn: bool = False
    # ----- Quiesce de escritores (ADR 0149, opción A) -----
    # Los servicios de aplicación que se paran MIENTRAS dura la captura, para que
    # ningún artefacto retrate un fichero a medio escribir. Vacía = no parar nada
    # (el comportamiento anterior al ADR). Ver `workers.backup_quiesce` para el
    # contrato completo, incluida la degradación cuando alguno no para a tiempo.
    quiesce_services: tuple[str, ...] = ()
    # Nunca se paran, aunque el operador los liste: la lane que corre ESTE backup
    # se mataría a sí misma a mitad de la captura.
    quiesce_never_stop: tuple[str, ...] = ()
    # El plazo del punto 1 del ADR. Vencido, el backup SIGUE con skew registrado.
    quiesce_timeout_s: int = 180
    # A qué stack de compose se le pide la parada. Se toman de la configuración
    # del RESTORE a propósito (`WORKERS_RESTORE_COMPOSE_*`): es el mismo stack, y
    # un segundo par de variables sería un segundo sitio que mantener en sincronía
    # cuyo modo de fallo —parar el proyecto equivocado— es peor que el acoplamiento.
    compose_project: str = ""
    compose_file: str = ""
    # ----- Salvaguarda de secretos de columna (ADR 0146) -----
    # Tablas cuyos DATOS se dejan fuera del dump porque llevan secretos que un
    # tenant configura para terceros, cifrados con Fernet + una variable de
    # entorno. Sin esto, quien tenga el bundle y esa variable tiene los secretos.
    # Vacía = viajan (la palanca del operador para volver atrás; el default del
    # `Settings` es el seguro). Ver `workers.backup_secrets`.
    column_secret_tables: tuple[str, ...] = ()
    # Wall-clock caps for the two heavy commands. Generous; a hung pg_dump or
    # tar is a problem, but a legitimate multi-GB dump must not be killed.
    pg_dump_timeout_s: int = 3600
    tar_timeout_s: int = 3600

    @classmethod
    def from_settings(cls, settings: Settings) -> BackupConfig:
        return cls(
            backup_root=Path(settings.backup_root),
            # Normalizado a libpq: el instalador emite la URL de SQLAlchemy
            # (`postgresql+asyncpg://`) y pg_dump no la entiende (task_prod_04_09).
            database_url=libpq_url(settings.backup_database_url),
            volumes=tuple(settings.backup_volumes),
            volumes_mount_root=Path(settings.backup_volumes_mount_root),
            retention_days=int(settings.backup_retention_days),
            bind_paths=tuple(settings.backup_bind_paths),
            projects_root=str(settings.backup_projects_root),
            transient_excludes=tuple(settings.backup_transient_excludes),
            redis_dir=str(settings.backup_redis_dir),
            # Con qué conexión pedirle el rewrite: la propia del backup si se
            # configuró, y si no la del broker de Celery, que el worker ya tiene y
            # apunta al MISMO servidor (BGREWRITEAOF es global, no por-db).
            redis_url=str(settings.backup_redis_url or settings.broker_url),
            stable_snapshot_paths=tuple(settings.backup_stable_snapshot_paths),
            snapshot_retries=int(settings.backup_snapshot_retries),
            quiesce_services=tuple(settings.backup_quiesce_services),
            quiesce_never_stop=tuple(settings.backup_quiesce_never_stop),
            quiesce_timeout_s=int(settings.backup_quiesce_timeout_seconds),
            compose_project=str(settings.restore_compose_project),
            compose_file=str(settings.restore_compose_file),
            column_secret_tables=tuple(settings.backup_column_secret_tables),
            encryption_enabled=bool(settings.backup_encryption_enabled),
            encryption_vault_key=str(settings.backup_encryption_vault_key),
            key_custody_fingerprint=str(settings.backup_key_custody_fingerprint).strip().lower(),
            # Mismo criterio que el guard de secretos-dev de este Settings: en dev
            # se avisa, fuera de dev se falla. Un bundle cifrado sin clave en
            # custodia es irrecuperable, y enterarse en el DR es demasiado tarde.
            require_key_custody=settings.environment != "dev",
            # Fail-CLOSED («todo lo que no es dev»), no una lista de entornos que
            # haya que acordarse de ampliar el día que aparezca un cuarto.
            require_production_dsn=settings.environment != "dev",
        )


@dataclass(frozen=True)
class ArtifactRecord:
    """One captured artifact in the manifest."""

    name: str
    # "pg_dump" | "volume_tar" | "projects_tar" | "bind_tar" | "redis_tar"
    # | "encrypted_bundle"
    kind: str
    path: str  # relative to the bundle directory
    size_bytes: int
    sha256: str
    # For a volume/bind tar, which docker volume or host path it came from.
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class BackupManifest:
    """The manifest written at the root of a completed bundle."""

    version: int
    backup_id: str
    created_at: str  # ISO-8601 UTC
    status: str  # "completed"
    database_url_sanitized: str
    encrypted: bool
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    # prod-04 task_prod_04_07 — huella SHA-256 (con separación de dominio) de la
    # clave con la que se cifró este bundle. NO es la clave ni permite derivarla:
    # sirve para que quien vaya a restaurar pueda comprobar, ANTES de intentarlo,
    # que la clave que ha sacado de la custodia offsite es la correcta. `None` en
    # un bundle en claro.
    key_fingerprint: str | None = None
    # ADR 0149 — el acta del quiesce. Es lo que permite juzgar, meses después,
    # si las divergencias que reporte `restore_reconcile` sobre ESTE bundle son
    # el comportamiento acordado (`partial`: se capturó con escritores en pie) o
    # una incidencia. Un skew que no consta en ningún sitio no se puede juzgar.
    quiesce: QuiesceRecord = field(default_factory=QuiesceRecord.disabled)
    # ADR 0146 — qué NO viaja en este bundle a propósito. Un backup al que le
    # falta algo por diseño tiene que decirlo en el acta: es lo único que separa
    # una decisión de arquitectura de una pérdida de datos silenciosa para quien
    # abra el bundle dentro de seis meses.
    excluded_secret_tables: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "status": self.status,
            "database": {"url": self.database_url_sanitized},
            "encrypted": self.encrypted,
            "key_fingerprint": self.key_fingerprint,
            "quiesce": self.quiesce.to_dict(),
            # Datos, no prosa: la explicación vive en el runbook y en
            # `workers.backup_secrets`. Una nota larga aquí, además de duplicar
            # documentación, hizo saltar la guarda que comprueba que ninguna
            # credencial se filtra al manifest (la palabra estaba dentro).
            "column_secrets": {
                "excluded_tables": list(self.excluded_secret_tables),
                "adr": "0146",
                "runbook": "06-runbooks/04-disaster-recovery.md",
            },
            "artifacts": [a.to_dict() for a in self.artifacts],
            "total_size_bytes": sum(a.size_bytes for a in self.artifacts),
        }


@dataclass(frozen=True)
class BackupResult:
    """What :meth:`BackupEngine.run_full_backup` returns."""

    backup_id: str
    bundle_dir: Path
    manifest_path: Path
    artifacts: tuple[ArtifactRecord, ...]
    pruned: tuple[str, ...]
    # ADR 0149 — cómo se capturó: con los escritores parados (`full`), con
    # alguno en pie (`partial`) o sin intentarlo (`disabled`).
    quiesce: QuiesceRecord = field(default_factory=QuiesceRecord.disabled)


def _sanitize_db_url(url: str) -> str:
    """Strip the password from a libpq URL so it is safe to log / manifest.

    ``postgresql://user:secret@host/db`` → ``postgresql://user:***@host/db``.
    A best-effort masker — never leak the credential into the manifest.
    """
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, _, hostpart = rest.partition("@")
    if ":" in creds:
        user, _, _ = creds.partition(":")
        creds = f"{user}:***"
    return f"{scheme}://{creds}@{hostpart}"


def libpq_url(url: str) -> str:
    """Normalizar un DSN al dialecto que hablan `pg_dump` / `pg_restore` / `psql`.

    prod-04 (task_prod_04_09, hallazgo deploy-4). El instalador emite
    ``WORKERS_BACKUP_DATABASE_URL`` copiando ``WORKERS_DATABASE_URL``, que es una
    URL **de SQLAlchemy** (``postgresql+asyncpg://...``). libpq no entiende el
    sufijo ``+driver``: el backup diario de una instalación de producción moría en
    el primer `pg_dump` con un error de URI que no dice nada. El docstring de
    `Settings.backup_database_url` ya avisaba («NOT the SQLAlchemy +asyncpg
    form»), pero un aviso en una descripción no es una comprobación.

    Aquí se corrige en el motor, que es donde importa, en vez de confiar en que
    todos los generadores de `.env` presentes y futuros se acuerden:

        postgresql+asyncpg://u:p@h/db  →  postgresql://u:p@h/db
        postgres+psycopg://u:p@h/db    →  postgres://u:p@h/db
        postgresql://u:p@h/db          →  (sin cambios)

    Un DSN sin esquema reconocible se devuelve tal cual: puede ser una conninfo
    de libpq (``host=... dbname=...``), que es igualmente válida.
    """
    scheme, sep, rest = url.partition("://")
    if not sep or "+" not in scheme:
        return url
    base = scheme.split("+", 1)[0]
    if base not in ("postgresql", "postgres"):
        return url
    return f"{base}://{rest}"


def _checksum_file(path: Path) -> str:
    """SHA-256 of a file (streamed)."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHECKSUM_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _checksum_tree(root: Path) -> str:
    """SHA-256 over a directory tree (directory-format pg_dump).

    Folds each file's relative path + content into one digest so the
    checksum is stable regardless of filesystem iteration order.
    """
    h = hashlib.sha256()
    for child in sorted(root.rglob("*")):
        if not child.is_file():
            continue
        rel = child.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        with child.open("rb") as fh:
            for chunk in iter(lambda: fh.read(_CHECKSUM_CHUNK), b""):
                h.update(chunk)
    return h.hexdigest()


def _dir_size(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def _bind_tar_excludes(bind_path: str, nested: Path) -> list[str]:
    """``--exclude`` args para sacar ``nested`` del tar de ``bind_path`` (prod-04 A7).

    Si ``nested`` está DENTRO de ``bind_path``, devuelve un exclude anclado a la
    raíz archivada (``./<rel>``). Fuera del bind → lista vacía (sin exclusión
    espuria). Determinista y puro; tolerante a rutas no relativas.

    Dos usos, la misma forma: el ``backup_root`` (para que el bundle no se
    auto-incluya recursivamente) y el ``projects_root`` (que ya viaja como su
    propio artefacto `projects_tar` y no debe duplicarse)."""
    try:
        rel = Path(nested).resolve().relative_to(Path(bind_path).resolve())
    except (ValueError, OSError):
        return []
    rel_posix = rel.as_posix()
    if not rel_posix or rel_posix == ".":
        return []
    return [f"--exclude=./{rel_posix}"]


def _transient_excludes(names: Sequence[str]) -> list[str]:
    """``--exclude`` args para los directorios regenerables (prod-04 task_prod_04_05).

    Sin ``--anchored`` (el default de GNU tar) el patrón casa cualquier sufijo de
    componente, así que ``--exclude=worktrees`` corta el directorio a cualquier
    profundidad — que es exactamente lo que hace falta con el layout
    ``projects/<tenant>/<project>/worktrees/<task_id>/``. Verificado con tar 1.35.
    """
    return [f"--exclude={name}" for name in names if name]


class BackupEngine:
    """Orchestrates one full-backup run behind an injectable command runner."""

    def __init__(
        self,
        config: BackupConfig,
        *,
        runner: CommandRunner | None = None,
        encryptor: BackupEncryptor | None = None,
        redis_flusher: PersistenceFlusher | None = None,
        quiescer: ComposeQuiescer | None = None,
        now: datetime | None = None,
    ) -> None:
        self._config = config
        self._runner: CommandRunner = runner or SubprocessRunner()
        # El que para los escritores durante la captura (ADR 0149). Se construye
        # aquí y no en la factoría porque necesita el MISMO runner que el motor:
        # así el doble de los tests ve también los argv de compose.
        self._quiescer = quiescer or self._build_quiescer()
        # El seam que le pide a Redis un AOF fresco antes de tarearlo
        # (task_prod_04_06). No cabe en el CommandRunner: es una conversación por
        # red, no un subproceso. Producción lo construye en run_full_backup().
        self._redis_flusher = redis_flusher
        # The Vault-keyed AES-256 encryptor — only used when encryption is
        # enabled. Tests inject one backed by a StaticSecretsProvider; in
        # production it is built from settings in run_full_backup().
        self._encryptor = encryptor
        # Injectable clock so tests get deterministic bundle ids + retention.
        self._now = now or datetime.now(UTC)

    @property
    def config(self) -> BackupConfig:
        return self._config

    def _build_quiescer(self) -> ComposeQuiescer | None:
        """El quiescer de producción, o ``None`` si no hay nada que parar."""
        cfg = self._config
        if not cfg.quiesce_services:
            return None
        return ComposeQuiescer(
            runner=self._runner,
            project=cfg.compose_project,
            compose_file=Path(cfg.compose_file),
            services=cfg.quiesce_services,
            timeout_s=cfg.quiesce_timeout_s,
            never_stop=cfg.quiesce_never_stop,
        )

    # -- public API ---------------------------------------------------------

    def run_full_backup(self) -> BackupResult:
        """Run the full backup: DB dump → volume tars → manifest → prune.

        Raises :class:`BackupError` (after removing the partial bundle) if any
        sub-step fails. On success the bundle directory contains the DB dump,
        one ``<volume>.tar.gz`` per configured volume, and ``manifest.json``.
        """
        backup_id = self._now.strftime(_BUNDLE_TS_FORMAT)
        bundle_dir = self._config.backup_root / backup_id
        if bundle_dir.exists():
            raise BackupError(f"backup bundle {bundle_dir} already exists")

        # Los dos gates ANTES de gastar una hora en pg_dump + tar: un bundle
        # cifrado con una clave que nadie puede recuperar no merece la pena
        # producirlo (task_prod_04_07), y un DSN de dev no va a producir ninguno
        # (task_prod_04_09).
        self._assert_production_dsn()
        key_fingerprint = self._assert_key_custody()

        bundle_dir.mkdir(parents=True, exist_ok=False)
        _log.info("backup.start", backup_id=backup_id, bundle_dir=str(bundle_dir))

        quiesce = QuiesceRecord.disabled()
        try:
            # ADR 0149: los escritores paran ALREDEDOR de la captura, y vuelven
            # SIEMPRE — el `finally` de abajo, no una rama feliz. Si no paran a
            # tiempo el backup sigue igualmente con el skew registrado: un stack
            # detenido a las 03:00 esperando a un worker que no responde es peor
            # que un bundle con constancia de su propia incoherencia.
            try:
                if self._quiescer is not None:
                    quiesce = self._quiescer.quiesce()
                artifacts = self._capture(bundle_dir)
            finally:
                if self._quiescer is not None:
                    quiesce = self._quiescer.resume(quiesce)
            encrypted = False
            if self._config.encryption_enabled:
                artifacts = self._encrypt_bundle(bundle_dir, artifacts)
                encrypted = True
            manifest = self._write_manifest(
                bundle_dir,
                backup_id,
                artifacts,
                encrypted=encrypted,
                key_fingerprint=key_fingerprint,
                quiesce=quiesce,
            )
        except BackupError:
            # Remove the partial bundle so a failed run leaves nothing
            # that could be mistaken for a good backup.
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise
        except Exception as exc:  # any unexpected error → clean failure
            shutil.rmtree(bundle_dir, ignore_errors=True)
            raise BackupError(f"backup {backup_id} failed: {exc}") from exc

        pruned = self._prune_old_backups(keep=backup_id)

        _log.info(
            "backup.done",
            backup_id=backup_id,
            artifacts=len(artifacts),
            pruned=len(pruned),
        )
        return BackupResult(
            backup_id=backup_id,
            bundle_dir=bundle_dir,
            manifest_path=manifest,
            artifacts=tuple(artifacts),
            pruned=tuple(pruned),
            quiesce=quiesce,
        )

    # -- steps --------------------------------------------------------------

    def _capture(self, bundle_dir: Path) -> list[ArtifactRecord]:
        """Las capturas propiamente dichas — lo que el quiesce envuelve.

        Extraído de :meth:`run_full_backup` para que la ventana de parada tenga
        un principio y un final visibles: todo lo que está aquí dentro se ejecuta
        con los escritores detenidos, y el cifrado (que solo toca ficheros ya
        escritos) queda fuera para no alargarla sin motivo.
        """
        artifacts: list[ArtifactRecord] = [self._dump_database(bundle_dir)]
        for volume in self._config.volumes:
            artifacts.append(self._tar_volume(bundle_dir, volume))
        projects = self._tar_projects(bundle_dir)
        if projects is not None:
            artifacts.append(projects)
        redis_art = self._tar_redis(bundle_dir)
        if redis_art is not None:
            artifacts.append(redis_art)
        for bind_path in self._config.bind_paths:
            artifacts.append(self._tar_bind_path(bundle_dir, bind_path))
        return artifacts

    def _assert_production_dsn(self) -> None:
        """Rechazar el DSN de DEV del backup fuera de dev (task_prod_04_09).

        `Settings.backup_database_url` es una SEGUNDA credencial con su propio
        default de desarrollo, y el guard anti-defaults del `Settings` solo mira
        `database_url`. Un `.env` de producción que no emita
        `WORKERS_BACKUP_DATABASE_URL` deja al `pg_dump` diario apuntando a
        `localhost:15432` con `changeme-migrations-dev-only`: dentro del contenedor
        de un worker no hay ningún postgres ahí, así que el backup fallaba todas
        las noches con un error de conexión que nadie relaciona con la variable que
        falta. Y un backup que falla en silencio no se descubre hasta el desastre.

        No se comprueba al arrancar el worker a propósito: negarle el boot a la
        flota entera por una variable del backup es un radio de explosión mayor que
        el problema. Aquí falla el run —el único que necesita el DSN— con un mensaje
        accionable y antes de gastar una hora en el dump.
        """
        if not self._config.require_production_dsn:
            return
        dsn = self._config.database_url.lower()
        markers = [m for m in ("changeme", "dev-only") if m in dsn]
        if not markers:
            return
        raise BackupError(
            "WORKERS_BACKUP_DATABASE_URL sigue con la credencial de DESARROLLO "
            f"(marcadores: {', '.join(markers)}). El pg_dump saldría a buscar "
            f"{_sanitize_db_url(self._config.database_url)}, que dentro del "
            "contenedor del worker no existe: el backup fallaría cada noche. Emite "
            "el DSN libpq del postgres del stack "
            "(postgresql://migrations_user:<password>@postgres:5432/agentic_platform)."
        )

    def _assert_key_custody(self) -> str | None:
        """Comprobar la custodia offsite de la clave de cifrado (task_prod_04_07).

        Devuelve la huella de la clave activa (para el manifest) o ``None`` si el
        bundle va en claro. Eleva :class:`BackupError` con un mensaje accionable
        cuando la clave activa no es la declarada en custodia.

        Por qué esto no es burocracia: la clave que descifra el bundle vive en el
        entorno de la máquina respaldada y el Vault viaja DENTRO del blob cifrado.
        Ante pérdida total del host, sin la clave en custodia el backup es
        matemáticamente irrecuperable — y las unseal keys NO descifran AES-GCM.
        El único momento en que un control automático puede evitarlo es ANTES de
        producir el bundle.
        """
        if not self._config.encryption_enabled:
            return None
        encryptor = self._encryptor
        if encryptor is None:
            raise BackupError(
                "encryption is enabled but no BackupEncryptor was provided "
                "(the Vault key could not be wired)"
            )
        try:
            active = encryptor.key_fingerprint()
        except Exception as exc:  # clave ausente/vacía → mensaje limpio, sin valor
            raise BackupError(f"no se pudo resolver la clave de cifrado del backup: {exc}") from exc

        declared = self._config.key_custody_fingerprint
        if not declared:
            message = (
                "la clave de cifrado del backup NO está declarada en custodia offsite. "
                "Sin custodia, ante la pérdida del host el bundle es irrecuperable: la "
                "clave vive en el entorno de ESTA máquina y el Vault viaja dentro del "
                "propio blob cifrado (las unseal keys no descifran AES-GCM). "
                f"Deposita el VALOR de la clave en el gestor corporativo / sobre sellado "
                f"y registra su huella en WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT: {active}"
            )
            if self._config.require_key_custody:
                raise BackupError(message)
            _log.warning("backup.key_custody.undeclared", fingerprint=active, hint=message)
            return active

        if declared != active:
            raise BackupError(
                "la clave de cifrado ACTIVA no es la que está declarada en custodia "
                f"offsite (custodia: {declared[:16]}…, activa: {active[:16]}…). Alguien "
                "rotó WORKERS_BACKUP_ENCRYPTION_KEY sin actualizar la custodia: los "
                "bundles que se produjeran ahora no los podría abrir nadie. Deposita la "
                "clave nueva y actualiza WORKERS_BACKUP_KEY_CUSTODY_FINGERPRINT, o "
                "restaura la clave anterior."
            )
        _log.info("backup.key_custody.verified", fingerprint=active)
        return active

    def _dump_database(self, bundle_dir: Path) -> ArtifactRecord:
        """Run pg_dump in LOGICAL directory format into the bundle.

        ``--format=directory`` (``-Fd``) is mandatory: it is the only format
        ``pg_restore`` can list + filter by table/schema, which the later
        per-tenant restore (task_12_11) needs. ``--dbname`` carries the full
        libpq URL so the password never lands in a separate logged arg.
        """
        out_dir = bundle_dir / _DB_DUMP_DIRNAME
        args = [
            "pg_dump",
            "--format=directory",
            "--no-owner",
            "--no-privileges",
            # ADR 0146: los DATOS de las tablas con secretos de tenant→tercero se
            # quedan fuera (la DEFINICIÓN sí viaja, o el restore dejaría la base
            # sin esas tablas). Un dump robado deja de llevar el ciphertext.
            *exclude_table_data_args(self._config.column_secret_tables),
            f"--file={out_dir}",
            f"--dbname={self._config.database_url}",
        ]
        result = self._runner.run(args, timeout=self._config.pg_dump_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"pg_dump failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not out_dir.exists():
            raise BackupError(f"pg_dump reported success but produced no output at {out_dir}")
        return ArtifactRecord(
            name=_DB_DUMP_DIRNAME,
            kind="pg_dump",
            path=_DB_DUMP_DIRNAME,
            size_bytes=_dir_size(out_dir),
            sha256=_checksum_tree(out_dir),
        )

    def _tar_volume(self, bundle_dir: Path, volume: str) -> ArtifactRecord:
        """tar + gzip one docker named volume's ``_data`` tree.

        The volume materialises at ``<mount_root>/<volume>/_data`` (docker's
        local driver layout). We tar from inside that directory (``-C``) so the
        archive holds the volume's contents at its root, not the host path.
        """
        archive_name = f"{volume}.tar.gz"
        archive_path = bundle_dir / archive_name
        source_dir = self._config.volumes_mount_root / volume / "_data"
        # `--create` es OBLIGATORIO en GNU tar («You must specify one of the
        # '-Acdtrux' options»); faltaba desde Plan 12 y el runner fake de los
        # tests nunca lo detectó — el primer backup real (2026-07-03) reventó aquí.
        args = [
            "tar",
            "--create",
            "--gzip",
            f"--directory={source_dir}",
            f"--file={archive_path}",
            ".",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"tar of volume {volume!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not archive_path.exists():
            raise BackupError(f"tar of volume {volume!r} reported success but produced no archive")
        return ArtifactRecord(
            name=archive_name,
            kind="volume_tar",
            path=archive_name,
            size_bytes=archive_path.stat().st_size,
            sha256=_checksum_file(archive_path),
            source=volume,
        )

    def _tar_projects(self, bundle_dir: Path) -> ArtifactRecord | None:
        """tar + gzip los bare repos de todos los proyectos (task_prod_04_05).

        `{data_root}/projects/<tenant>/<project>/repos/<repo>.git` es EL PRODUCTO
        de la plataforma: cada plan materializa su rama `plan/<id>-<slug>` ahí
        (principios rectores 4 y 5). Antes de prod-04 solo entraba de rebote en el
        tar del bind del data-root — con los worktrees dentro y sin que el restore
        lo extrajese jamás. Ahora es un artefacto propio (`projects_tar`),
        verificado por `backup_verification` y restaurado por `restore.py`.

        Devuelve ``None`` (con log) cuando no hay raíz configurada o todavía no
        existe en disco: una instalación recién parida no tiene proyectos, y
        fallar el backup entero por eso convertiría el primer backup en un fallo.
        Si la raíz existe y tar falla, el run entero falla (contrato clean-failure).
        """
        root = self._config.projects_root
        if not root:
            _log.info("backup.projects.skipped", reason="no projects_root configured")
            return None
        source_dir = Path(root)
        if not source_dir.is_dir():
            _log.warning("backup.projects.skipped", reason="not on disk", path=str(source_dir))
            return None

        archive_name = "projects.tar.gz"
        archive_path = bundle_dir / archive_name
        args = [
            "tar",
            "--create",
            "--gzip",
            f"--directory={source_dir}",
            f"--file={archive_path}",
            # worktrees + dep-cache son regenerables desde el bare repo; además
            # están en escritura activa y harían fallar a tar con rc≠0.
            *_transient_excludes(self._config.transient_excludes),
            # El backup_root NUNCA debería estar bajo projects/, pero si un
            # operador lo configurase así el bundle se auto-incluiría.
            *_bind_tar_excludes(str(source_dir), self._config.backup_root),
            ".",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"tar of projects root {root!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not archive_path.exists():
            raise BackupError(
                f"tar of projects root {root!r} reported success but produced no archive"
            )
        return ArtifactRecord(
            name=archive_name,
            kind="projects_tar",
            path=archive_name,
            size_bytes=archive_path.stat().st_size,
            sha256=_checksum_file(archive_path),
            source=str(source_dir),
        )

    def _tar_redis(self, bundle_dir: Path) -> ArtifactRecord | None:
        """Capturar Redis con un AOF FRESCO, no en caliente (task_prod_04_06).

        Redis alojaba su estado en el bundle de rebote, dentro del tar del bind del
        data-root: un ``appendonlydir`` en escritura activa, acumulado durante días,
        copiado mientras el servidor le escribía. Ahora es un artefacto propio y va
        precedido de un ``BGREWRITEAOF`` completado, que deja un base file recién
        cerrado y un incr recién abierto.

        Lo que NO se hace, y el plan pedía: «BGSAVE y capturar solo el dump.rdb».
        **Medido contra redis:7-alpine el 2026-07-31, eso restaura una base
        vacía**: con ``--appendonly yes`` (como lo arranca el compose) un Redis que
        encuentra un ``dump.rdb`` y ningún ``appendonlydir`` NO lee el RDB — crea un
        AOF nuevo y vacío y sirve ``DBSIZE 0``. Habría sido un bundle que pasa toda
        verificación y pierde las sesiones, el broker y los rate limits en silencio.
        Así que se captura el directorio entero (AOF + el RDB si está), que es lo
        que el restore puede volver a poner sin gimnasia de configuración.

        Devuelve ``None`` cuando no hay ``redis_dir`` configurado (Redis declarado
        recreable) o cuando todavía no existe en disco.
        """
        root = self._config.redis_dir
        if not root:
            _log.info("backup.redis.skipped", reason="no redis_dir configured")
            return None
        source_dir = Path(root)
        if not source_dir.is_dir():
            _log.warning("backup.redis.skipped", reason="not on disk", path=str(source_dir))
            return None

        flusher = self._redis_flusher
        if flusher is None:
            raise BackupError(
                "redis_dir está configurado pero no hay forma de hablar con redis "
                "(sin `redis_url` ni flusher inyectado): capturar el appendonlydir sin "
                "un BGREWRITEAOF previo es la captura en caliente que este paso quita"
            )
        try:
            operation = flusher.flush()
        except Exception as exc:
            raise BackupError(f"no se pudo consolidar la persistencia de redis: {exc}") from exc

        # Miembros explícitos en vez de '.': si mañana alguien mete un socket o un
        # fichero temporal en el data dir, no entra en el bundle por accidente.
        members = [name for name in ("appendonlydir", "dump.rdb") if (source_dir / name).exists()]
        if not members:
            raise BackupError(
                f"el directorio de datos de redis {root!r} no contiene ni appendonlydir "
                f"ni dump.rdb tras el {operation}: no hay nada restaurable que capturar"
            )

        archive_name = "redis.tar.gz"
        archive_path = bundle_dir / archive_name
        args = [
            "tar",
            "--create",
            "--gzip",
            f"--directory={source_dir}",
            f"--file={archive_path}",
            *members,
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"tar of redis data dir {root!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not archive_path.exists():
            raise BackupError(f"tar of redis data dir {root!r} produced no archive")
        _log.info("backup.redis.captured", operation=operation, members=members)
        return ArtifactRecord(
            name=archive_name,
            kind="redis_tar",
            path=archive_name,
            size_bytes=archive_path.stat().st_size,
            sha256=_checksum_file(archive_path),
            source=str(source_dir),
        )

    def _run_tar_verified(self, args: list[str], source_dir: Path, *, label: str) -> CommandResult:
        """Ejecutar un ``tar`` comprobando que el árbol de origen no cambió.

        Para el file backend de Vault (task_prod_04_06). Una copia tomada a mitad
        de una escritura puede dejar el barrel de claves inconsistente, y eso no da
        ninguna señal: se descubre cuando alguien intenta desellar el Vault
        restaurado, en pleno DR. Aquí se detecta en el momento.

        Se reintenta porque una escritura suelta no debe tirar el backup nocturno;
        si el árbol NO se queda quieto, el run falla, que es la única respuesta
        honesta (la alternativa es guardar una copia rota sin decirlo). El quiesce
        corto de escritores es la opción que decide el ADR de consistencia.
        """
        attempts = max(1, self._config.snapshot_retries + 1)
        changed: list[str] = []
        for attempt in range(1, attempts + 1):
            before = tree_fingerprint(source_dir)
            result = self._runner.run(args, timeout=self._config.tar_timeout_s)
            if result.returncode != 0:
                return result  # el llamante ya traduce el rc a BackupError
            changed = fingerprint_diff(before, tree_fingerprint(source_dir))
            if not changed:
                if attempt > 1:
                    _log.info("backup.snapshot.stable_on_retry", label=label, attempt=attempt)
                return result
            _log.warning(
                "backup.snapshot.unstable",
                label=label,
                attempt=attempt,
                of=attempts,
                changed=changed[:10],
            )
        raise BackupError(
            f"el árbol de {label} ({source_dir}) cambió durante la captura en los "
            f"{attempts} intentos: la copia no sería coherente. Ficheros que se "
            f"movieron: {changed[:10]}. Si esto es habitual, la vía es el quiesce "
            "corto de escritores en la ventana del backup (ADR de consistencia del "
            "bundle)."
        )

    def _tar_bind_path(self, bundle_dir: Path, bind_path: str) -> ArtifactRecord:
        """tar + gzip un bind mount (ruta absoluta del host) dentro del bundle.

        Auditoría 2026-07-02 (F0.4): los bare repos + worktrees de los agentes
        viven en el bind /data/agent-platform (no un named volume) y quedaban
        fuera del backup. El nombre del archivo es un slug de la ruta para que
        dos binds distintos no colisionen. Mismo contrato clean-failure que los
        volúmenes: si tar falla, el run entero falla.
        """
        slug = "-".join(part for part in Path(bind_path).parts if part not in ("/", "\\")) or "root"
        slug = slug.replace(":", "").replace("\\", "-").replace("/", "-")
        archive_name = f"bind-{slug}.tar.gz"
        archive_path = bundle_dir / archive_name
        args = [
            "tar",
            "--create",
            "--gzip",
            f"--directory={bind_path}",
            f"--file={archive_path}",
            # prod-04 A7: excluye el backup_root cuando vive DENTRO del bind_path
            # (config por defecto: bind /data/agent-platform ⊇ backups). Sin esto
            # cada backup se auto-incluía recursivamente (todos los bundles previos
            # + los artefactos del run) → crecimiento cuadrático y/o rc≠0 de tar
            # ("file changed as we read it"). Anclado con '.' relativo al directorio
            # archivado. GNU tar ya excluye su propio --file por inode, pero NO el
            # resto del árbol de backups.
            *_bind_tar_excludes(bind_path, self._config.backup_root),
            # …y el árbol de proyectos si ya viaja como `projects_tar` (que es el
            # caso por defecto: projects_root = {data_root}/projects ⊂ el bind).
            # Sin esto los bare repos entrarían DOS veces en cada bundle y el
            # restore los extraería dos veces, la segunda por encima de la
            # primera. Mismo contenido, el doble de tamaño.
            *(
                _bind_tar_excludes(bind_path, Path(self._config.projects_root))
                if self._config.projects_root
                else []
            ),
            # prod-04 task_prod_04_05: worktrees + dep-cache fuera también del bind.
            # Son regenerables (el worktree se recrea del bare, la cache se
            # re-descarga) y están en escritura activa mientras corren agentes —
            # el «file changed as we read it» de tar daba rc≠0 y tiraba el backup.
            *_transient_excludes(self._config.transient_excludes),
            ".",
        ]
        # task_prod_04_06: los binds declarados «estables» (el file backend de
        # Vault) se capturan comprobando que el árbol no se movió mientras tar
        # leía. El resto (MinIO, que se escribe por diseño) va directo.
        if str(Path(bind_path)) in {str(Path(p)) for p in self._config.stable_snapshot_paths}:
            result = self._run_tar_verified(args, Path(bind_path), label=f"bind {bind_path}")
        else:
            result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"tar of bind path {bind_path!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not archive_path.exists():
            raise BackupError(
                f"tar of bind path {bind_path!r} reported success but produced no archive"
            )
        return ArtifactRecord(
            name=archive_name,
            kind="bind_tar",
            path=archive_name,
            size_bytes=archive_path.stat().st_size,
            sha256=_checksum_file(archive_path),
            source=bind_path,
        )

    def _encrypt_bundle(
        self, bundle_dir: Path, artifacts: list[ArtifactRecord]
    ) -> list[ArtifactRecord]:
        """Collapse the assembled bundle into one tar, then AES-256-GCM wrap it.

        Runs only when ``encryption_enabled``. The plaintext artifacts (DB dump
        + volume tars) are tar'd into a single ``bundle.tar`` via the command
        runner, that archive is encrypted with the Vault-keyed
        :class:`BackupEncryptor` into ``bundle.tar.enc``, and both the plaintext
        artifacts and the intermediate tar are removed — only the encrypted blob
        survives in the bundle directory. Returns the new (single-artifact)
        manifest list.
        """
        encryptor = self._encryptor
        if encryptor is None:
            raise BackupError(
                "encryption is enabled but no BackupEncryptor was provided "
                "(the Vault key could not be wired)"
            )

        archive_path = bundle_dir / _BUNDLE_ARCHIVE_NAME
        # tar the plaintext artifacts (relative names) from inside the bundle so
        # the archive holds them at its root. Encryption happens off-disk after.
        member_names = [a.path for a in artifacts]
        args = [
            "tar",
            "--create",
            f"--directory={bundle_dir}",
            f"--file={archive_path}",
            *member_names,
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise BackupError(
                f"tar of bundle for encryption failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        if not archive_path.exists():
            raise BackupError("bundle tar for encryption produced no archive")

        enc_path = bundle_dir / _ENCRYPTED_BUNDLE_NAME
        try:
            enc_size = encryptor.encrypt_file(archive_path, enc_path)
        except Exception as exc:
            raise BackupError(f"backup encryption failed: {exc}") from exc

        # Remove every plaintext artifact + the intermediate tar — only the
        # encrypted blob may remain so nothing readable is left at rest.
        for art in artifacts:
            target = bundle_dir / art.path
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
        archive_path.unlink(missing_ok=True)

        _log.info(
            "backup.encrypted",
            vault_key_name=self._config.encryption_vault_key,
            blob=_ENCRYPTED_BUNDLE_NAME,
        )
        return [
            ArtifactRecord(
                name=_ENCRYPTED_BUNDLE_NAME,
                kind="encrypted_bundle",
                path=_ENCRYPTED_BUNDLE_NAME,
                size_bytes=enc_size,
                sha256=_checksum_file(enc_path),
            )
        ]

    def _write_manifest(
        self,
        bundle_dir: Path,
        backup_id: str,
        artifacts: list[ArtifactRecord],
        *,
        encrypted: bool,
        key_fingerprint: str | None = None,
        quiesce: QuiesceRecord | None = None,
    ) -> Path:
        manifest = BackupManifest(
            version=MANIFEST_VERSION,
            backup_id=backup_id,
            created_at=self._now.isoformat(),
            status="completed",
            database_url_sanitized=_sanitize_db_url(self._config.database_url),
            encrypted=encrypted,
            artifacts=artifacts,
            key_fingerprint=key_fingerprint,
            quiesce=quiesce or QuiesceRecord.disabled(),
            excluded_secret_tables=self._config.column_secret_tables,
        )
        manifest_path = bundle_dir / MANIFEST_FILENAME
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return manifest_path

    def _prune_old_backups(self, *, keep: str) -> list[str]:
        """Remove bundle directories older than the retention window.

        A bundle's age comes from its directory name (the UTC timestamp), not
        its mtime — robust against a touched/copied directory. ``keep`` is the
        just-written bundle, never pruned even if retention is 0.
        """
        root = self._config.backup_root
        if not root.exists():
            return []
        cutoff = self._now - timedelta(days=self._config.retention_days)
        pruned: list[str] = []
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry.name == keep:
                continue
            ts = _parse_bundle_ts(entry.name)
            if ts is None:
                # Not one of ours — leave it alone.
                continue
            if ts < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                pruned.append(entry.name)
        if pruned:
            _log.info("backup.pruned", count=len(pruned), names=pruned)
        return pruned


def _parse_bundle_ts(name: str) -> datetime | None:
    """Parse a bundle directory name back to a UTC datetime, or None."""
    try:
        return datetime.strptime(name, _BUNDLE_TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def run_full_backup(
    *,
    settings: Settings | None = None,
    runner: CommandRunner | None = None,
    encryptor: BackupEncryptor | None = None,
    redis_flusher: PersistenceFlusher | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Convenience entrypoint: build the engine from settings and run it.

    This is what ``scripts/backup.sh`` and the beat task call. ``runner`` /
    ``encryptor`` / ``redis_flusher`` / ``now`` are injectable for tests;
    production leaves them ``None`` (real subprocess, real clock, and — when
    enabled — a default Vault/env-backed :class:`BackupEncryptor` plus a
    :class:`RedisAofRewriter` talking to the configured Redis).
    """
    cfg = BackupConfig.from_settings(settings or get_settings())
    if redis_flusher is None and cfg.redis_dir and cfg.redis_url:
        # task_prod_04_06: sin esto, `_tar_redis` falla a propósito — capturar el
        # appendonlydir sin pedir antes un AOF fresco es la captura en caliente
        # ingenua que este paso existe para quitar.
        from workers.backup_consistency import RedisAofRewriter

        redis_flusher = RedisAofRewriter(url=cfg.redis_url)
    if encryptor is None and cfg.encryption_enabled:
        # Build the default Vault/env-backed encryptor. The provider resolves
        # the AES-256 key from the platform's secret mechanism (Vault → env);
        # the key never appears in code or the manifest.
        from workers.backup_encryption import EnvSecretsProvider

        encryptor = BackupEncryptor(
            provider=EnvSecretsProvider(),
            vault_key_name=cfg.encryption_vault_key,
        )
    return BackupEngine(
        cfg,
        runner=runner,
        encryptor=encryptor,
        redis_flusher=redis_flusher,
        now=now,
    ).run_full_backup()
