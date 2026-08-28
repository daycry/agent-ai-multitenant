"""Real uninstall seams — tear down the stack + purge the data (prod-01 task_18).

`RealStackTeardown` and `RealDataPurger` implement the
:class:`installer_backend.uninstall.StackTeardown` / `DataPurger` Protocols with
injected seams (the shared :class:`CommandRunner` for ``docker compose down`` and
a tiny :class:`FileSystem` for ``rmtree``), so the orchestration is unit-tested
without a Docker host or touching the disk. The Uninstaller's existing double
confirmation now protects REAL destruction.

The default-wiring of these into ``build_default_uninstaller`` (replacing the
Stub seams) lands with the no-silent-stubs guard (task_19), so the existing CLI
uninstall tests are migrated in lockstep and never run a real teardown in CI.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from .command_runner import CommandRunner
from .uninstall import PurgeLeftover, PurgeReport

#: The generated env file that sits directly under the data root (0600). Named
#: here because the purge deletes it EXPLICITLY: it is the only file whose
#: survival is a security incident rather than wasted disk.
_ENV_BASENAME = ".env"

#: Human-facing categories for the purge log, grouped by the top-level data
#: sub-dirs the compose generator bind-mounts (kept in step with
#: ``config_generators._DATA_SUBDIRS``; defined here so teardown does not import
#: another module's private symbol). ``backups`` (prod-04) and ``caddy/*`` (the
#: internal CA, ADR 0061) ARE wiped on a full uninstall.
_PURGE_CATEGORIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("base de datos (PostgreSQL)", ("postgres",)),
    ("cache/cola (Redis)", ("redis",)),
    ("object store (MinIO)", ("minio",)),
    ("secretos (Vault)", ("vault",)),
    ("TLS/proxy (Caddy)", ("caddy",)),
    ("repos git + worktrees", ("projects", "worktrees", "dep-cache")),
    ("backups", ("backups",)),
    ("modelos locales (Ollama)", ("ollama",)),
    ("monitorización", ("prometheus", "alertmanager", "grafana")),
    ("antivirus (ClamAV)", ("clamav",)),
)


@runtime_checkable
class FileSystem(Protocol):
    """Minimal filesystem seam so the purge is testable without touching disk.

    Deletion RAISES ``OSError`` when it fails. That is the whole contract change
    of the 2026-08-27 audit fix: the previous binding passed
    ``ignore_errors=True``, so a busy mount point, a denied permission or a file
    still open by a surviving container all came back as success and the
    uninstall reported "Datos ELIMINADOS." over an intact ``.env``.
    """

    def exists(self, path: str) -> bool: ...

    def rmtree(self, path: str) -> None:
        """Delete the directory tree at *path*. Raises ``OSError`` if it cannot."""
        ...

    def unlink(self, path: str) -> None:
        """Delete the FILE at *path* (``rmtree`` refuses one). Raises ``OSError``."""
        ...

    def listdir(self, path: str) -> list[str]:
        """Names of *path*'s direct children (used to empty an unremovable root)."""
        ...


@dataclass
class RealFileSystem:
    """Real filesystem binding (host-only; exercised by the e2e / human tests)."""

    def exists(self, path: str) -> bool:  # pragma: no cover - host-only
        return Path(path).exists()

    def rmtree(self, path: str) -> None:  # pragma: no cover - host-only
        # `onexc` instead of `ignore_errors`: keep deleting everything that CAN
        # be deleted (one busy file must not save the rest of the tree), then
        # fail loud with the first real reason. Silence is what produced the bug.
        failures: list[str] = []

        def _record(_func: object, failed: str, exc: BaseException) -> None:
            failures.append(f"{failed}: {exc}")

        shutil.rmtree(path, onexc=_record)
        if failures:
            raise OSError(
                f"{len(failures)} ruta(s) no se pudieron eliminar bajo {path}; "
                f"la primera: {failures[0]}"
            )

    def unlink(self, path: str) -> None:  # pragma: no cover - host-only
        Path(path).unlink()

    def listdir(self, path: str) -> list[str]:  # pragma: no cover - host-only
        return [child.name for child in Path(path).iterdir()]


@dataclass
class FakeFileSystem:
    """Test filesystem: ``existing`` are the paths that exist; records ``removed``.

    Two failure modes are scriptable, because both happen on a real host and
    both used to be invisible:

    * ``fail_on`` maps a path → the ``OSError`` message its deletion raises
      (``Device or resource busy`` on a mount point, ``Permission denied``);
    * ``undeletable`` are paths whose deletion reports NO error yet leaves them
      on disk — the silent case ``ignore_errors=True`` manufactured, and the
      reason the purger verifies with :meth:`exists` after every removal.

    ``children`` scripts :meth:`listdir` for the "empty a root you cannot
    remove" path.
    """

    existing: set[str] = field(default_factory=set)
    removed: list[str] = field(default_factory=list)
    fail_on: dict[str, str] = field(default_factory=dict)
    undeletable: set[str] = field(default_factory=set)
    children: dict[str, list[str]] = field(default_factory=dict)

    def exists(self, path: str) -> bool:
        return path in self.existing

    def _delete(self, path: str) -> None:
        if path in self.fail_on:
            raise OSError(self.fail_on[path])
        self.removed.append(path)
        if path not in self.undeletable:
            self.existing.discard(path)

    def rmtree(self, path: str) -> None:
        self._delete(path)

    def unlink(self, path: str) -> None:
        self._delete(path)

    def listdir(self, path: str) -> list[str]:
        return list(self.children.get(path, []))


@dataclass
class RealStackTeardown:
    """``docker compose down`` (no ``-v`` by default — data lives in the bind mount).

    Teardown is tolerant: a non-zero ``down`` is logged but does NOT raise (the
    stack may be partially up; the operator still wants the rest removed). If the
    generated ``docker-compose.yml`` is missing (an install aborted at
    GENERATE_CONFIG), it falls back to ``-p <project> down`` (Compose can destroy
    a project by name via its labels) instead of failing on a missing ``-f``.
    """

    compose_dir: str
    runner: CommandRunner
    fs: FileSystem = field(default_factory=RealFileSystem)

    @property
    def _compose_file(self) -> str:
        return f"{self.compose_dir}/docker-compose.yml"

    def down(self, project_name: str, *, remove_volumes: bool) -> list[str]:
        args: list[str] = ["docker", "compose", "-p", project_name]
        if self.fs.exists(self._compose_file):
            args += ["-f", self._compose_file]
        args.append("down")
        if remove_volumes:
            args.append("-v")
        lines: list[str] = []
        result = self.runner.run(args, cwd=self.compose_dir, on_line=lines.append)
        if result.returncode != 0:
            lines.append(f"aviso: 'docker compose down' devolvió rc={result.returncode}")
        return lines


@dataclass
class RealDataPurger:
    """Delete the data tree under the root, VERIFY it, and report what survived.

    The Uninstaller's double + purge confirmations gate this; here we do the
    deletion (idempotent — only existing paths are touched), check each path
    afterwards and produce a per-category log plus the list of leftovers, so the
    operator sees what disappeared *and* what did not.

    Three deliberate behaviours, each fixing a way the previous version lied:

    * **a failure does not abort the rest** — one busy mount point must not save
      the other nine categories from deletion, so every path is attempted and
      the failures are accumulated;
    * **the ``.env`` is deleted explicitly and first**, before the root: it is
      the one file whose survival is a SECURITY event and not a disk-space one
      (Postgres password, MinIO keys, JWT secret, the three Fernet keys), so its
      removal must not depend on the root's;
    * **a root that cannot be removed is EMPTIED instead**, and said so. A data
      root on a dedicated disk — exactly what the disk prereq's remediation
      recommends — is a mount point: ``rmtree`` on it fails with ``Device or
      resource busy`` however clean the deletion of its contents was. Emptying
      it is equivalent for the data; claiming "raíz de datos eliminada" is not.
    """

    fs: FileSystem = field(default_factory=RealFileSystem)

    def _remove(self, path: str, *, is_file: bool = False) -> str | None:
        """Delete *path*; return ``None`` if it is gone, else WHY it survived.

        The post-check with :meth:`FileSystem.exists` is not belt-and-braces: it
        is what catches the silent failure — a deletion that reports no error and
        leaves the path in place — which is precisely what the old
        ``ignore_errors=True`` produced on every failure.
        """

        try:
            if is_file:
                self.fs.unlink(path)
            else:
                self.fs.rmtree(path)
        except OSError as exc:
            return str(exc)
        if self.fs.exists(path):
            return "sigue en disco tras un borrado que no dio error"
        return None

    def _empty(self, data_root: str) -> list[PurgeLeftover]:
        """Delete *data_root*'s remaining children one by one; return the survivors."""

        try:
            names = self.fs.listdir(data_root)
        except OSError as exc:
            return [PurgeLeftover(path=data_root, reason=f"no se pudo listar su contenido: {exc}")]
        survivors: list[PurgeLeftover] = []
        for name in names:
            child = f"{data_root}/{name}"
            reason = self._remove(child)
            if reason is not None:
                survivors.append(PurgeLeftover(path=child, reason=reason))
        return survivors

    def _purge_categories(self, data_root: str, leftovers: list[PurgeLeftover]) -> list[str]:
        """Delete the per-category sub-trees; return the log lines for what went."""

        lines: list[str] = []
        for category, subs in _PURGE_CATEGORIES:
            removed_any = False
            for sub in subs:
                path = f"{data_root}/{sub}"
                if not self.fs.exists(path):
                    continue
                reason = self._remove(path)
                if reason is None:
                    removed_any = True
                else:
                    leftovers.append(PurgeLeftover(path=path, reason=reason))
            if removed_any:
                lines.append(f"{category}: eliminada")
        return lines

    def _purge_env_file(self, data_root: str, leftovers: list[PurgeLeftover]) -> list[str]:
        """Delete the ``.env`` explicitly, BEFORE the root (see the class docstring)."""

        env_path = f"{data_root}/{_ENV_BASENAME}"
        if not self.fs.exists(env_path):
            return []
        reason = self._remove(env_path, is_file=True)
        if reason is None:
            return [f"secretos en disco ({_ENV_BASENAME}): eliminados"]
        leftovers.append(PurgeLeftover(path=env_path, reason=reason))
        return [
            f"AVISO DE SEGURIDAD: {_ENV_BASENAME} NO se ha podido eliminar "
            f"({reason}). Sigue en disco con la contraseña de Postgres, las "
            "claves de MinIO, el secreto JWT y las tres claves Fernet."
        ]

    def _purge_root(self, data_root: str, leftovers: list[PurgeLeftover]) -> list[str]:
        """Delete the root — or, when it cannot be deleted, EMPTY it and say so."""

        if not self.fs.exists(data_root):
            return []
        reason = self._remove(data_root)
        if reason is None:
            # This also takes the generated compose + the Caddyfile that live
            # directly under it.
            return [f"raíz de datos eliminada: {data_root} (config y compose incluidos)"]
        survivors = self._empty(data_root)
        leftovers.extend(survivors)
        if survivors:
            return [f"raíz de datos NO vaciada del todo: {data_root} ({reason})"]
        return [
            f"raíz de datos vaciada pero NO eliminada: {data_root} "
            f"— es lo esperable si es un punto de montaje ({reason}). "
            "No queda ningún dato dentro."
        ]

    def purge(self, data_root: str) -> PurgeReport:
        # El ORDEN es parte del contrato, no una casualidad: el `.env` se borra
        # DESPUÉS de las categorías y ANTES de la raíz, para que su borrado no
        # dependa de que la raíz se pueda eliminar (no se puede, si es un punto
        # de montaje). Por eso son tres sentencias y no una lista.
        leftovers: list[PurgeLeftover] = []
        lines = self._purge_categories(data_root, leftovers)
        lines += self._purge_env_file(data_root, leftovers)
        lines += self._purge_root(data_root, leftovers)
        return PurgeReport(lines=lines, leftovers=tuple(leftovers))
