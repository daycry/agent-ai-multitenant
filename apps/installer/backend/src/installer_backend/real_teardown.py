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
    """Minimal filesystem seam so the purge is testable without touching disk."""

    def exists(self, path: str) -> bool: ...

    def rmtree(self, path: str) -> None: ...


@dataclass
class RealFileSystem:
    """Real filesystem binding (host-only; exercised by the e2e / human tests)."""

    def exists(self, path: str) -> bool:  # pragma: no cover - host-only
        return Path(path).exists()

    def rmtree(self, path: str) -> None:  # pragma: no cover - host-only
        shutil.rmtree(path, ignore_errors=True)


@dataclass
class FakeFileSystem:
    """Test filesystem: ``existing`` are the paths that exist; records ``removed``."""

    existing: set[str] = field(default_factory=set)
    removed: list[str] = field(default_factory=list)

    def exists(self, path: str) -> bool:
        return path in self.existing

    def rmtree(self, path: str) -> None:
        self.removed.append(path)
        self.existing.discard(path)


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
    """``rmtree`` the data tree under the root, logging WHAT was removed by category.

    The Uninstaller's double + purge confirmations gate this; here we just do the
    deletion (idempotent — only existing paths are removed) and produce a per-
    category log so the operator sees exactly what disappeared.
    """

    fs: FileSystem = field(default_factory=RealFileSystem)

    def purge(self, data_root: str) -> list[str]:
        lines: list[str] = []
        for category, subs in _PURGE_CATEGORIES:
            removed_any = False
            for sub in subs:
                path = f"{data_root}/{sub}"
                if self.fs.exists(path):
                    self.fs.rmtree(path)
                    removed_any = True
            if removed_any:
                lines.append(f"{category}: eliminada")
        # Finally the root itself: this also wipes the generated config + the
        # secret-bearing .env (0600) + the Caddyfile that live directly under it.
        if self.fs.exists(data_root):
            lines.append("config + secretos en disco (.env / compose / Caddyfile): eliminados")
            self.fs.rmtree(data_root)
            lines.append(f"raíz de datos eliminada: {data_root}")
        return lines
