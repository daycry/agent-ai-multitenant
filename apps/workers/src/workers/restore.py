"""Full restore from a backup bundle (Plan 12 task_12_10).

The destructive other half of :mod:`workers.backup`. Where the backup engine
assembles a *timestamped bundle* (a pg_dump LOGICAL directory dump + per-volume
``tar.gz`` + a checksummed ``manifest.json``, optionally collapsed into one
AES-256-GCM ``bundle.tar.enc``), this engine puts a stack back together FROM
that bundle:

  1. **LOCATE + (if encrypted) DECRYPT** the bundle. An encrypted bundle is one
     blob keyed by a Vault secret; the same :class:`workers.backup_encryption.BackupEncryptor`
     primitive decrypts it back into the plaintext bundle layout. A plaintext
     bundle is used as-is.
  2. **VERIFY** the (decrypted) bundle against its manifest — checksums + the
     structural ``pg_restore --list`` / ``tar -tf`` probes — REUSING
     :class:`workers.backup_verification.BackupVerifier`. This is the
     *verify-before-restore* gate: a bundle that fails verification ABORTS the
     restore BEFORE any destructive command runs (fail closed). Restoring a
     corrupt bundle over a live stack is the worst possible outcome.
  3. **STOP the app stack** — ``docker compose stop`` of the *app* services
     (api-server, workers, admin-panel, …) while LEAVING PostgreSQL reachable so the
     dump can be restored into it. The DB + volume-backing services are NOT
     stopped here; they are stopped/started around the volume restore separately.
  4. **pg_restore** the LOGICAL directory dump into PostgreSQL with ``--clean
     --if-exists`` so existing objects are dropped + recreated (a full restore
     replaces the database contents).
  5. **RESTORE the volume tars** (MinIO / Vault / Redis) into their host mount
     paths: stop the volume-backing services, wipe + re-extract each volume's
     ``_data`` tree from its archive, so the restored volume is exactly the
     captured one (no stale files left behind).
  6. **RESTART the stack** cleanly — ``docker compose up -d`` brings every
     service back.

Design — reuse + the subprocess seam
------------------------------------
Real ``pg_restore`` / ``docker compose`` / live volume writes cannot run inside
the unit-test environment, so EVERY external command goes through the same
injectable :class:`workers.backup.CommandRunner` the backup engine uses
(production: :class:`workers.backup.SubprocessRunner`, explicit argv, never
``shell=True``; tests inject a fake that records argv). Tests therefore assert
*command construction* (pg_restore gets ``--clean --if-exists`` + the dump dir +
the libpq URL; the stop/up argv targets the right compose project + services),
*ordering* (verify → stop → pg_restore → volumes → start), and *failure
handling* (a non-zero step surfaces a typed :class:`RestoreError`; a bad bundle
aborts before any destructive command) — never a real restore of a live stack.
A real end-to-end restore is a HUMAN test (Plan 12 Tests Humanos human_12_02).

Safety
------
Restore is DESTRUCTIVE: it drops + recreates the database and wipes + re-extracts
volume trees. Two guards:

  * **double confirmation** — :meth:`run_full_restore` requires an explicit
    ``confirm`` token equal to the bundle id; a mismatch raises
    :class:`RestoreError` before anything runs. The UI (task_12_12) supplies it
    from the operator's second confirmation; the Celery task (a background job)
    forwards it.
  * **verify-before-restore, fail closed** — if the manifest verification fails,
    the restore aborts with the verification report attached and NOTHING
    destructive runs.

Secrets (the Vault decrypt key) are resolved through the same secret seam the
backup encryption layer uses (:class:`workers.secrets.SecretsProvider`); the key
is never plaintext, never logged.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from workers.backup import (
    MANIFEST_FILENAME,
    CommandRunner,
    SubprocessRunner,
    libpq_url,
)
from workers.backup_encryption import (
    ENCRYPTED_SUFFIX,
    BackupEncryptionError,
    BackupEncryptor,
)
from workers.backup_verification import (
    BackupVerificationError,
    BackupVerifier,
    VerificationReport,
)
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.restore")

# The DB dump subtree inside a bundle (mirrors workers.backup._DB_DUMP_DIRNAME).
_DB_DUMP_DIRNAME = "postgres"

# On-disk name of the collapsed-then-encrypted bundle blob, and the plaintext
# tar it decrypts back into (mirror workers.backup's two constants).
_BUNDLE_ARCHIVE_NAME = "bundle.tar"
_ENCRYPTED_BUNDLE_NAME = _BUNDLE_ARCHIVE_NAME + ENCRYPTED_SUFFIX  # bundle.tar.enc


class RestoreError(RuntimeError):
    """Raised when a restore step fails or a precondition is not met.

    Covers a missing/locked bundle, a confirmation-token mismatch, a failed
    verification (fail closed), and any non-zero external command. The cause is
    chained where there is one; the message never echoes secret material.
    """


@dataclass(frozen=True)
class RestoreConfig:
    """Operator-tunable knobs for one full restore — no magic numbers.

    Built from :class:`workers.config.Settings` via :meth:`from_settings`. Tests
    construct it directly with ``tmp_path`` roots and a fake compose project.
    """

    backup_root: Path
    database_url: str
    volumes: tuple[str, ...]
    volumes_mount_root: Path
    # docker compose project name + the compose file path the stack control
    # commands target. Explicit so a restore never accidentally drives the wrong
    # project on a host running several compose stacks.
    compose_project: str
    compose_file: Path
    # The APP services stopped (and brought back up) around the restore. The DB
    # service is deliberately NOT here — it must stay reachable for pg_restore.
    app_services: tuple[str, ...]
    # The services that back the data volumes being restored — stopped while
    # their _data tree is wiped + re-extracted, then started again.
    volume_services: tuple[str, ...]
    # prod-04 task_prod_04_05 — where the `projects_tar` artifact (the bare repos
    # of every project) is re-extracted. Empty = do not restore them, which loses
    # the code of every project: only for a deliberate DB-only restore.
    projects_root: str = ""
    # prod-04 task_prod_04_06 — dónde se re-extrae el artefacto `redis_tar` (el
    # `appendonlydir` capturado tras un BGREWRITEAOF, más el `dump.rdb`). Vacío =
    # no restaurar Redis, que es coherente con no respaldarlo (la opción
    # «recreable» del ADR de consistencia) pero NO con haberlo respaldado: un
    # artefacto que nadie extrae es peso muerto y confianza injustificada.
    redis_dir: str = ""
    # The bind paths the operator declared for CAPTURE. A `bind_tar` artifact is
    # only restored when its recorded source is in THIS list — the manifest is
    # ours, but extracting to an arbitrary absolute path read out of a file is
    # not a power the restore engine should hold.
    bind_paths: tuple[str, ...] = ()
    # prod-04 task_prod_04_04 — FAIL-STOPPED. When a step of the destructive
    # phase fails, the stack is left DOWN by default and a RestorePartialError
    # carries the stage reached. Serving a half-restored database is worse than
    # serving nothing: both DR runbooks already order "keep it stopped", and now
    # the code obeys them. Flip to True only for a lab where availability beats
    # correctness.
    autostart_on_failure: bool = False
    # prod-04 task_prod_04_08 — the role `pg_restore` must connect as (the
    # migrations/DDL owner) and the runtime role whose GRANTs are recreated after
    # `--no-owner --no-privileges` throws them away. Empty = skip that guard.
    required_db_role: str = ""
    grant_app_role: str = ""
    # Optional at-rest encryption (mirrors BackupConfig). When the manifest says
    # the bundle is encrypted, an injected BackupEncryptor decrypts it; the Vault
    # key NAME (never the value) lives here so a default encryptor can be built.
    encryption_enabled: bool = False
    encryption_vault_key: str = "backup_encryption_key"
    # Wall-clock caps for the heavy commands. Generous; a legitimate multi-GB
    # restore must not be killed, but a hung command is a problem.
    pg_restore_timeout_s: int = 3600
    tar_timeout_s: int = 3600
    compose_timeout_s: int = 600

    @classmethod
    def from_settings(cls, settings: Settings) -> RestoreConfig:
        return cls(
            backup_root=Path(settings.backup_root),
            # Mismo saneado que el backup: pg_restore/psql hablan libpq, no
            # el dialecto de SQLAlchemy que emite el instalador (task_prod_04_09).
            database_url=libpq_url(settings.backup_database_url),
            volumes=tuple(settings.backup_volumes),
            volumes_mount_root=Path(settings.backup_volumes_mount_root),
            compose_project=str(settings.restore_compose_project),
            compose_file=Path(settings.restore_compose_file),
            app_services=tuple(settings.restore_app_services),
            volume_services=tuple(settings.restore_volume_services),
            projects_root=str(settings.backup_projects_root),
            redis_dir=str(settings.backup_redis_dir),
            bind_paths=tuple(settings.backup_bind_paths),
            autostart_on_failure=bool(settings.restore_autostart_on_failure),
            required_db_role=str(settings.restore_required_db_role),
            grant_app_role=str(settings.restore_grant_app_role),
            encryption_enabled=bool(settings.backup_encryption_enabled),
            encryption_vault_key=str(settings.backup_encryption_vault_key),
        )


@dataclass(frozen=True)
class RestoreResult:
    """What :meth:`RestoreEngine.run_full_restore` returns on success."""

    backup_id: str
    bundle_dir: str
    encrypted: bool
    restored_volumes: tuple[str, ...]
    verification: VerificationReport
    # prod-04 task_prod_04_05 — host paths re-extracted outside the docker volume
    # layout: the projects root (bare repos) and each declared bind path. Before
    # prod-04 these artifacts were captured, checksummed… and never restored.
    restored_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "bundle_dir": self.bundle_dir,
            "encrypted": self.encrypted,
            "restored_volumes": list(self.restored_volumes),
            "restored_paths": list(self.restored_paths),
            "verification": self.verification.to_dict(),
        }


class RestorePartialError(RestoreError):
    """Un paso de la fase DESTRUCTIVA falló: el stack queda PARADO (task_prod_04_04).

    Antes de prod-04 el motor tenía un ``finally: docker compose up -d`` que
    arrancaba la aplicación pase lo que pase — sobre una base de datos a medio
    restaurar. Los dos runbooks de DR ordenan justo lo contrario
    («mantén el stack parado para no servir datos parciales») y el código los
    contradecía. Ahora el arranque es opt-in explícito
    (``WORKERS_RESTORE_AUTOSTART_ON_FAILURE``) y este error lleva el ESTADO
    alcanzado y el SIGUIENTE PASO, que es lo que un operador necesita a las 4 de
    la mañana.

    ``stage`` es uno de los hitos de :data:`RESTORE_STAGES`.
    """

    def __init__(self, stage: str, cause: BaseException, *, stack_running: bool) -> None:
        self.stage = stage
        self.stack_running = stack_running
        state = RESTORE_STAGES.get(stage, stage)
        if stack_running:
            posture = (
                "el stack se ha ARRANCADO porque restore_autostart_on_failure=true "
                "(puede estar sirviendo datos parciales: párralo)"
            )
        else:
            posture = (
                "el stack sigue PARADO a propósito (solo PostgreSQL quedó "
                "alcanzable para diagnóstico); NO lo arranques"
            )
        super().__init__(
            f"el restore falló en la fase destructiva tras «{state}»: {cause}. "
            f"Estado: {posture}. Siguiente paso: diagnostica la causa y RE-EJECUTA "
            f"el restore completo desde el mismo bundle (es idempotente: repite "
            f"--clean y vuelve a vaciar los volúmenes) o desde uno anterior."
        )


#: Hitos de la fase destructiva, en orden. El ``stage`` de un
#: :class:`RestorePartialError` dice hasta dónde se llegó — la diferencia entre
#: «hay que repetirlo todo» y «solo faltaban los volúmenes».
RESTORE_STAGES: dict[str, str] = {
    "app_stack_stopped": "parar los servicios de aplicación",
    "database_restored": "restaurar la base de datos (pg_restore)",
    "grants_reapplied": "re-conceder permisos a los roles de runtime",
    "data_restored": "restaurar volúmenes, repos de proyectos y binds",
}


class RestoreVerificationError(RestoreError):
    """A :class:`RestoreError` carrying the failing verification report.

    Raised by the verify-before-restore gate so the caller (UI / task) can show
    exactly which artifact/check failed and confirm NOTHING destructive ran.
    """

    def __init__(self, report: VerificationReport) -> None:
        self.report = report
        failures = ", ".join(f"{c.check}:{c.artifact}" for c in report.failures)
        super().__init__(
            f"bundle {report.backup_id} failed verification before restore ({failures})"
        )


class RestoreEngine:
    """Orchestrates one full restore behind an injectable command runner.

    Construct with the same :class:`workers.backup.CommandRunner` seam the backup
    engine uses (production: :class:`SubprocessRunner`; tests: a fake). When the
    bundle is encrypted, an injected :class:`BackupEncryptor` decrypts it; in
    production it is built from settings in :func:`run_full_restore`.
    """

    def __init__(
        self,
        config: RestoreConfig,
        *,
        runner: CommandRunner | None = None,
        encryptor: BackupEncryptor | None = None,
        verifier: BackupVerifier | None = None,
    ) -> None:
        self._config = config
        self._runner: CommandRunner = runner or SubprocessRunner()
        self._encryptor = encryptor
        # The verifier shares the SAME runner so its pg_restore --list / tar -tf
        # probes are mocked alongside the restore commands in tests.
        self._verifier = verifier or BackupVerifier(runner=self._runner, encryptor=encryptor)

    @property
    def config(self) -> RestoreConfig:
        return self._config

    # -- public API ---------------------------------------------------------

    def run_full_restore(self, bundle: str | Path, *, confirm: str) -> RestoreResult:
        """Restore the whole stack from ``bundle`` (a bundle id or a path).

        Sequence: locate → (decrypt) → VERIFY (fail closed) → stop app stack →
        pg_restore → restore volumes → restart stack. ``confirm`` MUST equal the
        bundle id (the double-confirmation guard); a mismatch raises
        :class:`RestoreError` before anything runs.

        Raises :class:`RestoreError` (a :class:`RestoreVerificationError` when
        the bundle is corrupt) if any precondition or step fails. On a
        verification failure NOTHING destructive runs.
        """
        bundle_dir = self._locate_bundle(bundle)
        backup_id = bundle_dir.name

        # -- DOUBLE CONFIRMATION (before any work, never destructive on mismatch)
        if confirm != backup_id:
            raise RestoreError(
                f"restore confirmation token does not match the bundle id "
                f"{backup_id!r}; refusing to run a destructive restore"
            )

        _log.info("restore.start", backup_id=backup_id, bundle_dir=str(bundle_dir))

        # -- LOCATE + DECRYPT: read the manifest, decrypt an encrypted bundle in
        # place into the plaintext layout the rest of the flow + verifier expect.
        manifest = self._load_manifest(bundle_dir)
        encrypted = bool(manifest.get("encrypted", False))
        if encrypted:
            self._decrypt_bundle(bundle_dir)

        # -- VERIFY before touching anything (fail closed). The verifier re-reads
        # the (now plaintext) manifest + artifacts and runs checksum + structural
        # probes. A corrupt bundle aborts the restore here.
        report = self._verify(bundle_dir)
        if not report.valid:
            _log.warning(
                "restore.aborted.verification_failed",
                backup_id=backup_id,
                failures=[c.check + ":" + c.artifact for c in report.failures],
            )
            raise RestoreVerificationError(report)

        # -- PREFLIGHT: every service we are about to stop must be DECLARED in the
        # compose file we target. `docker compose stop <unknown>` exits ≠ 0, so a
        # phantom in the list aborted the restore in step 3 — before restoring a
        # single byte (ADR 0117 c: `web-app` did exactly that). Still cheap and
        # non-destructive here, with a message that names the culprit.
        self._assert_services_declared()

        # -- DESTRUCTIVE from here on. Stop the app stack (DB stays reachable).
        self._stop_app_stack()
        stage = "app_stack_stopped"
        try:
            self._pg_restore(bundle_dir)
            stage = "database_restored"
            self._reapply_grants()
            stage = "grants_reapplied"
            restored, restored_paths = self._restore_data_artifacts(bundle_dir, manifest)
            stage = "data_restored"
        except Exception as exc:
            # FAIL-STOPPED (task_prod_04_04): NO auto-arranque. Un stack a medio
            # restaurar sirviendo peticiones es peor que un stack parado — y es lo
            # que ordenan 04-disaster-recovery.md y dr-full-restore.md.
            stack_running = False
            if self._config.autostart_on_failure:
                _log.warning("restore.failed.autostart_opt_in", stage=stage)
                try:
                    self._start_stack()
                    stack_running = True
                except RestoreError:
                    _log.warning("restore.failed.autostart_also_failed", stage=stage)
            _log.error("restore.failed.stack_left_stopped", stage=stage, error=str(exc))
            raise RestorePartialError(stage, exc, stack_running=stack_running) from exc

        self._start_stack()

        _log.info(
            "restore.done",
            backup_id=backup_id,
            encrypted=encrypted,
            volumes=len(restored),
            paths=len(restored_paths),
        )
        return RestoreResult(
            backup_id=backup_id,
            bundle_dir=str(bundle_dir),
            encrypted=encrypted,
            restored_volumes=restored,
            restored_paths=restored_paths,
            verification=report,
        )

    # -- locate + decrypt ----------------------------------------------------

    def _locate_bundle(self, bundle: str | Path) -> Path:
        """Resolve ``bundle`` (a bundle id or a path) to an existing directory.

        A bare id (no path separator) is looked up under ``backup_root``; an
        absolute/relative path is used directly. A missing bundle raises
        :class:`RestoreError` — restore never fabricates a target.
        """
        candidate = Path(bundle)
        if candidate.is_absolute() or len(candidate.parts) > 1:
            bundle_dir = candidate
        else:
            bundle_dir = self._config.backup_root / candidate
        if not bundle_dir.is_dir():
            raise RestoreError(f"backup bundle not found: {bundle_dir}")
        return bundle_dir

    def _load_manifest(self, bundle_dir: Path) -> dict[str, Any]:
        manifest_path = bundle_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise RestoreError(f"no {MANIFEST_FILENAME} in bundle {bundle_dir}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreError(f"could not read manifest at {manifest_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RestoreError(f"manifest at {manifest_path} is not a JSON object")
        return data

    def _decrypt_bundle(self, bundle_dir: Path) -> None:
        """Decrypt ``bundle.tar.enc`` (Vault key) + un-tar it into the bundle.

        Reverses :meth:`workers.backup.BackupEngine._encrypt_bundle`: the blob is
        decrypted to ``bundle.tar`` via the injected :class:`BackupEncryptor`
        (GCM-authenticated — a tampered blob raises and aborts the restore), then
        extracted in place so the plaintext DB dump + volume tars are present for
        the verifier + the restore steps. Decryption to disk is unavoidable here
        (unlike the routine corruption check) because pg_restore + tar need the
        real files. The intermediate ``bundle.tar`` is removed afterwards.
        """
        encryptor = self._encryptor
        if encryptor is None:
            raise RestoreError(
                "bundle is encrypted but no BackupEncryptor was provided "
                "(the Vault key could not be wired)"
            )
        blob_path = bundle_dir / _ENCRYPTED_BUNDLE_NAME
        if not blob_path.is_file():
            raise RestoreError(
                f"manifest says the bundle is encrypted but {_ENCRYPTED_BUNDLE_NAME} is missing"
            )
        archive_path = bundle_dir / _BUNDLE_ARCHIVE_NAME
        try:
            encryptor.decrypt_file(blob_path, archive_path)
        except BackupEncryptionError as exc:
            # Tampered/truncated blob or the wrong key — fail closed, never echo
            # key material (BackupEncryptionError is already non-leaky).
            raise RestoreError(f"failed to decrypt backup bundle: {exc}") from exc
        # Un-tar the plaintext bundle in place (members land at the bundle root).
        args = [
            "tar",
            "--extract",
            f"--directory={bundle_dir}",
            f"--file={archive_path}",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"extracting the decrypted bundle failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        # Drop the intermediate plaintext tar — only the extracted layout is used.
        archive_path.unlink(missing_ok=True)
        _log.info("restore.decrypted", vault_key_name=self._config.encryption_vault_key)

    def _verify(self, bundle_dir: Path) -> VerificationReport:
        """Run the manifest verification (REUSED from task_12_03).

        A verifier *operational* failure (unreadable manifest, etc.) is itself a
        reason to abort: an unverifiable bundle is not trustworthy, so we map it
        to a :class:`RestoreError` rather than proceeding.
        """
        try:
            return self._verifier.verify_bundle(bundle_dir)
        except BackupVerificationError as exc:
            raise RestoreError(f"could not verify bundle before restore: {exc}") from exc

    # -- stack control -------------------------------------------------------

    def _compose_base(self) -> list[str]:
        """The ``docker compose -p <project> -f <file>`` prefix every op shares."""
        return [
            "docker",
            "compose",
            "--project-name",
            self._config.compose_project,
            "--file",
            str(self._config.compose_file),
        ]

    def _assert_services_declared(self) -> None:
        """Fail closed si algún servicio a parar NO está en el compose (task_prod_04_03).

        Se lee el YAML directamente en vez de invocar ``docker compose config
        --services``: es determinista, no necesita el daemon de Docker y no
        depende del runner (así el guard corre de verdad, no solo cuando un doble
        de test decide contestar). Si el fichero no existe o no declara un mapa
        ``services`` no-vacío, no hay nada contra lo que comparar y no inventamos
        un veredicto — se registra y se sigue: el guard estático
        (``tests/unit/test_restore_services_exist.py``) cubre el caso del
        repositorio, y `_stop_app_stack` da un mensaje accionable si compose
        rechaza un nombre.
        """
        compose_file = Path(self._config.compose_file)
        declared = _declared_compose_services(compose_file)
        if not declared:
            _log.warning(
                "restore.preflight.services_unverifiable",
                compose_file=str(compose_file),
                reason="compose file absent or declares no services",
            )
            return
        wanted = [*self._config.app_services, *self._config.volume_services]
        missing = sorted({s for s in wanted if s not in declared})
        if missing:
            raise RestoreError(
                f"estos servicios NO están declarados en {compose_file}: {missing}. "
                f"`docker compose stop` devuelve != 0 ante un servicio desconocido, "
                f"así que el restore abortaría en el primer paso destructivo. "
                f"Corrige WORKERS_RESTORE_APP_SERVICES / "
                f"WORKERS_RESTORE_VOLUME_SERVICES o apunta "
                f"WORKERS_RESTORE_COMPOSE_FILE al compose que corre de verdad "
                f"(declarados: {sorted(declared)})."
            )
        _log.info("restore.preflight.services_ok", services=len(wanted))

    def _stop_app_stack(self) -> None:
        """``docker compose stop`` the app services (DB stays reachable).

        Only the app services are stopped here — Postgres must remain up so
        pg_restore can connect. Stopping the writers first guarantees no app
        process mutates the DB mid-restore.
        """
        if not self._config.app_services:
            return
        args = [*self._compose_base(), "stop", *self._config.app_services]
        result = self._runner.run(args, timeout=self._config.compose_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"stopping the app stack failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()} "
                f"— la causa habitual es un servicio de "
                f"WORKERS_RESTORE_APP_SERVICES que no existe en "
                f"{self._config.compose_file}; compáralo con "
                f"`docker compose --file {self._config.compose_file} config --services`"
            )
        _log.info("restore.app_stack_stopped", services=list(self._config.app_services))

    def _start_stack(self) -> None:
        """``docker compose up -d`` — bring the whole stack back cleanly."""
        args = [*self._compose_base(), "up", "--detach"]
        result = self._runner.run(args, timeout=self._config.compose_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"restarting the stack failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.stack_started")

    # -- pg_restore ----------------------------------------------------------

    def _pg_restore(self, bundle_dir: Path) -> None:
        """``pg_restore`` the LOGICAL directory dump into PostgreSQL.

        ``--clean --if-exists`` drops each object before recreating it (so a full
        restore replaces existing contents without erroring on absent objects);
        ``--no-owner --no-privileges`` mirror the dump flags so ownership/ACLs are
        not required to match. ``--dbname`` carries the full libpq URL so the
        password never lands in a separate logged arg.
        """
        dump_dir = bundle_dir / _DB_DUMP_DIRNAME
        if not dump_dir.is_dir():
            raise RestoreError(f"bundle has no pg_dump directory at {dump_dir}")
        self._assert_connection_role()
        args = [
            "pg_restore",
            "--clean",
            "--if-exists",
            # prod-04 task_prod_04_04: sin --exit-on-error pg_restore sigue tras un
            # error de SQL y termina con rc=0 «con warnings». Eso enmascaraba una
            # tabla que no se restauró: el restore se daba por bueno y el hueco
            # aparecía semanas después. Con el flag, cualquier error aborta.
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            f"--dbname={self._config.database_url}",
            str(dump_dir),
        ]
        result = self._runner.run(args, timeout=self._config.pg_restore_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"pg_restore failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.pg_restored", dump_dir=str(dump_dir))

    # -- roles, ownership y GRANTs (task_prod_04_08) --------------------------

    def _assert_connection_role(self) -> None:
        """El DSN del restore tiene que conectar como el rol DDL (fail-closed).

        `pg_restore --clean` hace DROP + CREATE de todos los objetos y deja el
        ownership en el rol que conecta. Si eso pasa como `app_user` (el rol
        NOBYPASSRLS del runtime), el esquema queda propiedad del rol equivocado y
        `alembic upgrade head` como `migrations_user` empieza a fallar por
        permisos. `config.py` solo pedía «admin-grade» en la descripción, que no
        es una comprobación.
        """
        required = self._config.required_db_role
        if not required:
            return
        actual = _dsn_username(self._config.database_url)
        if actual is None:
            raise RestoreError(
                f"no se puede leer el usuario del DSN de restore; el restore exige "
                f"conectar como {required!r} (WORKERS_RESTORE_REQUIRED_DB_ROLE)"
            )
        if actual != required:
            raise RestoreError(
                f"el DSN de restore conecta como {actual!r} y debe hacerlo como "
                f"{required!r}: pg_restore --clean recrea TODOS los objetos y el "
                f"ownership queda en el rol que conecta. Con el rol equivocado el "
                f"esquema queda inservible para las migraciones. Corrige "
                f"WORKERS_BACKUP_DATABASE_URL (o "
                f"WORKERS_RESTORE_REQUIRED_DB_ROLE si el rol DDL cambió)."
            )

    def _reapply_grants(self) -> None:
        """Re-concede permisos al rol de runtime tras `pg_restore` (task_prod_04_08).

        `--no-owner --no-privileges` (en el dump Y en el restore) descarta
        ownership y ACLs a propósito, para que el restore no exija que los roles
        coincidan. El efecto colateral es que `app_user` — el rol NOBYPASSRLS del
        que depende TODO el stack con FORCE RLS — se queda sin GRANTs sobre las
        tablas recién creadas: la aplicación arranca y falla con
        «permission denied for table …» en la primera consulta.

        Idempotente por construcción (`GRANT` sobre lo ya concedido es un no-op),
        así que re-ejecutar el restore no acumula estado. Se ejecuta a través del
        mismo seam de subprocess que el resto (psql), nunca con shell.
        """
        role = self._config.grant_app_role
        if not role:
            return
        if not _is_plain_identifier(role):
            raise RestoreError(
                f"WORKERS_RESTORE_GRANT_APP_ROLE={role!r} no es un identificador SQL "
                f"simple; se rechaza antes de interpolarlo en un GRANT"
            )
        statements = [
            f"GRANT USAGE ON SCHEMA public TO {role}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
            # Para las tablas que cree una migración POSTERIOR al restore.
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
            f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {role}",
        ]
        args = [
            "psql",
            "--no-psqlrc",
            # Sin ON_ERROR_STOP psql sigue tras un error y devuelve 0: el mismo
            # enmascaramiento que --exit-on-error arregla en pg_restore.
            "--set=ON_ERROR_STOP=1",
            f"--dbname={self._config.database_url}",
        ]
        for stmt in statements:
            args += ["--command", stmt]
        result = self._runner.run(args, timeout=self._config.pg_restore_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"re-conceder permisos a {role!r} falló (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}. Sin esos GRANTs "
                f"la aplicación arranca y falla con «permission denied for table»."
            )
        _log.info("restore.grants_reapplied", role=role, statements=len(statements))

    # -- volume restore ------------------------------------------------------

    def _restore_data_artifacts(
        self, bundle_dir: Path, manifest: dict[str, Any]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """Restore every captured data artifact. Returns ``(volumes, host_paths)``.

        Cuatro clases, con semántica DISTINTA a propósito:

        * ``volume_tar`` → vaciar y re-extraer ``<mount_root>/<volume>/_data``:
          el volumen restaurado es EXACTAMENTE el capturado, sin supervivientes.
        * ``projects_tar`` → vaciar y re-extraer la raíz de proyectos (los bare
          repos). Mismo criterio: es un árbol que la plataforma posee entero.
        * ``redis_tar`` → vaciar y re-extraer el data dir de Redis. Vaciar es
          OBLIGATORIO aquí: un ``appendonlydir`` residual con una secuencia más
          alta que la capturada le gana al restaurado, porque Redis lee el
          manifest que encuentra (task_prod_04_06).
        * ``bind_tar`` → extraer **SIN vaciar**, y solo si el ``source`` está en
          los bind paths declarados. Vaciar aquí sería catastrófico: el tar del
          bind excluye deliberadamente ``backup_root``, que suele vivir DENTRO del
          bind — un ``rmtree`` borraría el bundle desde el que se está restaurando.

        prod-04 task_prod_04_05: hasta ahora este método filtraba
        ``kind == "volume_tar"``, así que ``bind_tar`` (los bare repos de los
        agentes, capturados desde julio) se respaldaba, se le calculaba el
        checksum, se verificaba… y NUNCA se restauraba. El código de los
        proyectos no volvía de un DR y nadie lo había notado.
        """
        artifacts = manifest.get("artifacts", [])
        volume_artifacts = [a for a in artifacts if a.get("kind") == "volume_tar"]
        projects_artifacts = [a for a in artifacts if a.get("kind") == "projects_tar"]
        redis_artifacts = [a for a in artifacts if a.get("kind") == "redis_tar"]
        bind_artifacts = [a for a in artifacts if a.get("kind") == "bind_tar"]
        if not (volume_artifacts or projects_artifacts or redis_artifacts or bind_artifacts):
            return (), ()

        # Los servicios dueños de los volúmenes se paran igual: los repos y los
        # binds los leen los workers, que ya están parados por `_stop_app_stack`.
        self._stop_volume_services()

        restored: list[str] = []
        for art in volume_artifacts:
            volume = str(art.get("source") or "")
            archive_name = str(art.get("path") or art.get("name") or "")
            if not volume or not archive_name:
                raise RestoreError(f"volume artifact in manifest is missing source/path: {art!r}")
            self._restore_one_volume(bundle_dir, volume=volume, archive_name=archive_name)
            restored.append(volume)

        paths: list[str] = []
        for art in projects_artifacts:
            archive_name = str(art.get("path") or art.get("name") or "")
            target = self._config.projects_root
            if not target:
                _log.warning(
                    "restore.projects.skipped",
                    reason="sin projects_root: el código de los proyectos NO se restaura",
                    captured_from=art.get("source"),
                )
                continue
            self._extract_into(bundle_dir / archive_name, Path(target), wipe=True, label="projects")
            paths.append(target)

        # prod-04 task_prod_04_06 — Redis. `wipe=True` y no por simetría estética:
        # el destino puede tener un `appendonlydir` con una secuencia MÁS ALTA que
        # la capturada, y Redis lee el manifest que encuentre. Extraer por encima
        # dejaría ficheros de dos generaciones y un manifest que apunta a la vieja:
        # el clásico restore que «funciona» y sirve datos de otro momento.
        for art in redis_artifacts:
            archive_name = str(art.get("path") or art.get("name") or "")
            target = self._config.redis_dir
            if not target:
                _log.warning(
                    "restore.redis.skipped",
                    reason="sin redis_dir: Redis NO se restaura (sesiones y colas vacías)",
                    captured_from=art.get("source"),
                )
                continue
            self._extract_into(bundle_dir / archive_name, Path(target), wipe=True, label="redis")
            paths.append(target)

        declared_binds = {str(Path(p)) for p in self._config.bind_paths}
        for art in bind_artifacts:
            archive_name = str(art.get("path") or art.get("name") or "")
            source = str(art.get("source") or "")
            if not source:
                raise RestoreError(f"bind artifact in manifest is missing source: {art!r}")
            if str(Path(source)) not in declared_binds:
                # Fail-safe, no fail-closed: un bundle viejo puede traer un bind
                # que este host ya no declara. Extraer a una ruta absoluta que
                # solo aparece en un fichero NO es una potestad del motor.
                _log.warning(
                    "restore.bind.skipped",
                    reason="source not in the configured bind paths",
                    source=source,
                    declared=sorted(declared_binds),
                )
                continue
            self._extract_into(bundle_dir / archive_name, Path(source), wipe=False, label="bind")
            paths.append(source)

        return tuple(restored), tuple(paths)

    def _stop_volume_services(self) -> None:
        """``docker compose stop`` the services backing the restored volumes."""
        if not self._config.volume_services:
            return
        args = [*self._compose_base(), "stop", *self._config.volume_services]
        result = self._runner.run(args, timeout=self._config.compose_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"stopping volume services failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.volume_services_stopped", services=list(self._config.volume_services))

    def _restore_one_volume(self, bundle_dir: Path, *, volume: str, archive_name: str) -> None:
        """Wipe + re-extract one volume's ``_data`` tree from its archive.

        Mirrors :meth:`workers.backup.BackupEngine._tar_volume` in reverse: the
        archive holds the volume's contents at its root, so we extract with
        ``--directory=<mount_root>/<volume>/_data`` after clearing that dir.
        """
        archive_path = bundle_dir / archive_name
        if not archive_path.is_file():
            raise RestoreError(f"volume archive missing in bundle: {archive_path}")
        target_dir = self._config.volumes_mount_root / volume / "_data"
        # Clear the existing volume contents so no stale file outlives the restore,
        # then recreate the empty mount point. Best-effort wipe; recreate is hard.
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "tar",
            "--extract",
            "--gzip",
            f"--directory={target_dir}",
            f"--file={archive_path}",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"restoring volume {volume!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.volume_restored", volume=volume, target=str(target_dir))

    def _extract_into(
        self, archive_path: Path, target_dir: Path, *, wipe: bool, label: str
    ) -> None:
        """Extraer un ``.tar.gz`` del bundle en un directorio del host.

        ``wipe=True`` vacía el destino antes (el árbol restaurado es exactamente
        el capturado); ``wipe=False`` extrae por encima (para un bind cuyo tar
        excluye a propósito parte del árbol — vaciarlo destruiría lo excluido,
        incluido el propio bundle).
        """
        if not archive_path.is_file():
            raise RestoreError(f"{label} archive missing in bundle: {archive_path}")
        if wipe and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "tar",
            "--extract",
            "--gzip",
            f"--directory={target_dir}",
            f"--file={archive_path}",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"restoring {label} into {target_dir} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.path_restored", kind=label, target=str(target_dir), wiped=wipe)


def _declared_compose_services(compose_file: Path) -> set[str]:
    """Los nombres de servicio declarados en un compose, o ``set()`` si no se puede saber.

    Lee el YAML (sin invocar a docker) para que el preflight de servicios no
    dependa del daemon ni del runner. Cualquier problema — fichero ausente, YAML
    inválido, ``services`` que no es un mapa — devuelve el conjunto vacío, que el
    llamante interpreta como «no verificable» y registra.
    """
    try:
        import yaml
    except ImportError:  # pragma: no cover — PyYAML es dependencia del stack
        return set()
    try:
        if not compose_file.is_file():
            return set()
        data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return set()
    if not isinstance(data, dict):
        return set()
    services = data.get("services")
    if not isinstance(services, dict):
        return set()
    return {str(name) for name in services}


def _dsn_username(dsn: str) -> str | None:
    """El usuario de un DSN libpq, o ``None`` si no lo trae.

    Deliberadamente no usa `urlsplit` sobre el DSN completo: la contraseña puede
    llevar caracteres que rompen el parseo estricto, y aquí solo hace falta el
    tramo ``usuario[:password]@``.
    """
    if "://" not in dsn:
        return None
    _, _, rest = dsn.partition("://")
    creds, sep, _ = rest.partition("@")
    if not sep or not creds:
        return None
    user = creds.partition(":")[0]
    return user or None


def _is_plain_identifier(name: str) -> bool:
    """Un identificador SQL sin comillas: letras, dígitos y ``_``, sin empezar por dígito."""
    if not name or name[0].isdigit():
        return False
    return all(ch.isalnum() or ch == "_" for ch in name)


def run_full_restore(
    bundle: str | Path,
    *,
    confirm: str,
    settings: Settings | None = None,
    runner: CommandRunner | None = None,
    encryptor: BackupEncryptor | None = None,
) -> RestoreResult:
    """Convenience entrypoint: build the engine from settings and run a restore.

    This is what the restore Celery task (a background job, task_12_12) and a
    future ``scripts/restore.sh`` call. ``runner`` / ``encryptor`` are injectable
    for tests; production leaves them ``None`` (real subprocess; and — when the
    bundle is encrypted — a default Vault/env-backed :class:`BackupEncryptor`
    built here so the engine can decrypt the blob).
    """
    cfg = RestoreConfig.from_settings(settings or get_settings())
    if encryptor is None and cfg.encryption_enabled:
        from workers.backup_encryption import EnvSecretsProvider

        encryptor = BackupEncryptor(
            provider=EnvSecretsProvider(),
            vault_key_name=cfg.encryption_vault_key,
        )
    return RestoreEngine(cfg, runner=runner, encryptor=encryptor).run_full_restore(
        bundle, confirm=confirm
    )


__all__ = [
    "RESTORE_STAGES",
    "RestoreConfig",
    "RestoreEngine",
    "RestoreError",
    "RestorePartialError",
    "RestoreResult",
    "RestoreVerificationError",
    "run_full_restore",
]
