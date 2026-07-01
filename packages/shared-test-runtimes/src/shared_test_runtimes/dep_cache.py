"""Dependency cache for test-runtime containers (Plan 06 Fase C).

A *dep-cache* is a per-runtime shared directory the worker bind-mounts
into the test container so ``npm ci`` / ``pip install -r ...`` /
``composer install`` only does real work the first time it sees a
given lock file. The cache layout on the host:

    /data/agent-platform/dep-cache/
        pip-<sha256-of-requirements.txt>/        ← python-pytest mount
        npm-<sha256-of-package-lock.json>/       ← node-* mount
        composer-<sha256-of-composer.lock>/      ← php-* mount
        …

The runtime template (``shared_test_runtimes.types.RuntimeTemplate``)
declares ``dep_cache_mount`` (where the cache lives inside the
container) and the worker picks the right lock file from the
worktree to hash. When the hash hasn't changed since the previous
run, the bound dir is already populated; the ``pre_install`` step is
a near-no-op.

The five tasks of Fase C all live here:

  * :func:`compute_lock_hash` (06_08) — hash the runtime's lock file
    inside a worktree to a stable, content-addressable id.
  * :class:`DepCacheManager` (06_09 + 06_10 + 06_11) — own the
    on-disk layout, mount paths, atime touching, TTL purge.
  * :meth:`DepCacheManager.invalidate` (06_12) — the API endpoint /
    UI button "Invalidar caché" calls this to nuke an entry.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .types import RuntimeTemplate

# How long a cache entry can sit untouched before the TTL purge sweeps
# it. Matches Plan 06 task_06_11 ("TTL de 14 días sin uso → purga
# automática").
DEFAULT_TTL_SECONDS = 14 * 24 * 60 * 60

# Map runtime id → (lock file name, cache key prefix). Used by both
# compute_lock_hash and DepCacheManager.cache_path_for. The prefix is
# what shows up in the directory name, so operators can guess what a
# cache is from a quick ``ls /data/.../dep-cache``.
RUNTIME_LOCK_FILES: Mapping[str, tuple[str, str]] = {
    "python-pytest": ("requirements.txt", "pip"),
    "node-jest": ("package-lock.json", "npm"),
    "node-vitest": ("package-lock.json", "npm"),
    "node-playwright": ("package-lock.json", "npm"),
    "php-phpunit": ("composer.lock", "composer"),
    "php-pest": ("composer.lock", "composer"),
    "go-test": ("go.sum", "go"),
    "java-maven": ("pom.xml", "maven"),
    "java-gradle": ("gradle.lockfile", "gradle"),
    "ruby-rspec": ("Gemfile.lock", "gem"),
    "rust-cargo": ("Cargo.lock", "cargo"),
    "dotnet-test": ("packages.lock.json", "nuget"),
    "generic-shell": ("", ""),
    "generic-http": ("", ""),
}


# ---------------------------------------------------------------------------
# task_06_08 — Lock hashing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockHashResult:
    """Outcome of hashing a worktree's lock file.

    ``hash`` is ``None`` when the runtime doesn't declare a lock file
    (generic-shell, generic-http) or when the lock file doesn't exist
    in the worktree (the project hasn't pinned versions yet). In both
    cases the worker skips the cache mount entirely.
    """

    runtime_id: str
    lock_path: Path | None
    hash: str | None
    prefix: str


def compute_lock_hash(
    worktree_path: Path | str,
    runtime_id: str,
) -> LockHashResult:
    """Hash the lock file the runtime cares about, sha256 hex.

    Returns a :class:`LockHashResult` with ``hash=None`` when there's
    no lock file to hash. Callers must treat ``hash=None`` as
    "cache disabled for this run" — the worker just doesn't bind the
    dep-cache mount.

    The hash is over the lock file's *bytes*, not a parsed tree. That
    way an inconsequential whitespace change still busts the cache
    (safer than risking a stale cache from a missed semantic edit).
    """
    workdir = Path(worktree_path)
    entry = RUNTIME_LOCK_FILES.get(runtime_id)
    if entry is None or not entry[0]:
        return LockHashResult(runtime_id=runtime_id, lock_path=None, hash=None, prefix="")

    lock_name, prefix = entry
    lock_path = workdir / lock_name
    if not lock_path.is_file():
        return LockHashResult(runtime_id=runtime_id, lock_path=lock_path, hash=None, prefix=prefix)

    h = hashlib.sha256()
    with lock_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(64 * 1024), b""):
            h.update(chunk)
    return LockHashResult(
        runtime_id=runtime_id,
        lock_path=lock_path,
        hash=h.hexdigest(),
        prefix=prefix,
    )


# ---------------------------------------------------------------------------
# task_06_09, 06_10, 06_11, 06_12 — DepCacheManager
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheEntry:
    """One entry in the dep-cache, materialised on disk."""

    template_id: str
    lock_hash: str
    host_path: Path
    container_mount: str

    @property
    def cache_key(self) -> str:
        """Directory name as it appears on disk."""
        return self.host_path.name


class DepCacheManager:
    """Owner of the on-host dep-cache directory tree.

    The manager keeps the cache root simple: one flat directory of
    ``{prefix}-{hash}`` sub-folders. Each sub-folder is owned by the
    worker process (uid 1000) so the bind-mounted test-runtime can
    read+write without uid jugglery.
    """

    def __init__(self, cache_root: Path | str) -> None:
        self._root = Path(cache_root)

    @property
    def root(self) -> Path:
        return self._root

    # --- path helpers ---------------------------------------------------

    def cache_path_for(
        self,
        template: RuntimeTemplate,
        lock_hash: str,
    ) -> Path:
        """Return the on-host directory for a (template, lock_hash) pair.

        Naming uses the runtime's prefix (``pip``, ``npm``, …) rather
        than the template id, so two templates that share a stack
        (e.g. ``node-jest`` and ``node-vitest``) reuse the same cache
        when their lock file is identical.
        """
        prefix = RUNTIME_LOCK_FILES.get(template.id, (None, template.id))[1]
        return self._root / f"{prefix}-{lock_hash}"

    # --- task_06_09 — persistence --------------------------------------

    def ensure_entry(
        self,
        template: RuntimeTemplate,
        lock_hash: str,
    ) -> CacheEntry:
        """Create the cache dir if missing, touch its mtime, return entry.

        The worker calls this BEFORE launching the test-runtime. The
        directory may be empty (cold cache) — the test container's
        ``pre_install`` populates it on first use. The mtime touch is
        what the TTL purge uses to decide "still in use".
        """
        host_path = self.cache_path_for(template, lock_hash)
        host_path.mkdir(parents=True, exist_ok=True)
        # The worker creates this dir as root, but the runtime container that
        # writes into the bind-mounted cache runs as a NON-root user
        # (workers.isolation.AGENT_UID_GID = 1000:1000). Without a world-writable
        # mode the tool (composer/npm/pip) can't populate the cache and warns
        # "cache directory ... not writable" on EVERY command — noise that can
        # send the agent into a retry loop (repetitive_loop_detected). Re-chmod
        # on every ensure so a dir created cold (0755) before this fix is healed.
        with contextlib.suppress(OSError):
            os.chmod(host_path, 0o777)
        # Touch BOTH atime and mtime — different filesystems honor
        # one or the other.
        now = time.time()
        host_path.touch(exist_ok=True)
        # touch() only updates the file itself, not the directory's
        # mtime when noatime is mounted; do it explicitly.
        os.utime(host_path, (now, now))
        return CacheEntry(
            template_id=template.id,
            lock_hash=lock_hash,
            host_path=host_path,
            container_mount=template.dep_cache_mount or "",
        )

    # --- task_06_10 — mount decision -----------------------------------

    def mount_for(
        self,
        template: RuntimeTemplate,
        lock_hash: str | None,
    ) -> CacheEntry | None:
        """Resolve to a :class:`CacheEntry` ready to bind into the
        test-runtime, or ``None`` when caching should be skipped.

        Skip conditions:
          * The template declares no ``dep_cache_mount``
            (generic-shell, generic-http).
          * ``lock_hash`` is None — the worktree has no lock file
            yet (a freshly-cloned project, or a stack we don't
            track lock files for).

        Otherwise ensures the dir exists, touches its mtime so the
        TTL purge doesn't catch it mid-flight, and returns the entry.
        """
        if template.dep_cache_mount is None or lock_hash is None:
            return None
        return self.ensure_entry(template, lock_hash)

    # --- task_06_11 — TTL purge ----------------------------------------

    def purge_expired(
        self,
        *,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        now: float | None = None,
    ) -> list[Path]:
        """Remove cache dirs whose mtime is older than ``ttl_seconds``.

        Returns the list of paths actually removed — the caller logs
        them at INFO level so operators see what was reclaimed.
        ``now`` is overridable for tests.
        """
        threshold = (now if now is not None else time.time()) - ttl_seconds
        removed: list[Path] = []
        if not self._root.exists():
            return removed
        for entry in self._root.iterdir():
            if not entry.is_dir():
                continue
            try:
                mtime = entry.stat().st_mtime
            except FileNotFoundError:
                continue
            if mtime < threshold:
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry)
        return removed

    # --- task_06_12 — invalidate ---------------------------------------

    def invalidate(
        self,
        template_id: str,
        lock_hash: str | None = None,
    ) -> list[Path]:
        """Delete one (or all) cache entries for a runtime.

        Called by the "Invalidar caché" UI button (task_06_12). When
        ``lock_hash`` is given, only that single entry is removed;
        when ``None``, every entry whose prefix matches the runtime is
        wiped. Returns the list of removed paths.
        """
        prefix = RUNTIME_LOCK_FILES.get(template_id, (None, template_id))[1]
        if not prefix or not self._root.exists():
            return []

        removed: list[Path] = []
        if lock_hash is not None:
            target = self._root / f"{prefix}-{lock_hash}"
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)
                removed.append(target)
            return removed

        for entry in self._root.iterdir():
            if not entry.is_dir() or not entry.name.startswith(f"{prefix}-"):
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed.append(entry)
        return removed

    def invalidate_all(self) -> list[Path]:
        """Nuke every cache entry. Useful for ops scripts ("free disk now")."""
        if not self._root.exists():
            return []
        removed: list[Path] = []
        for entry in self._root.iterdir():
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
                removed.append(entry)
        return removed


__all__ = [
    "CacheEntry",
    "DEFAULT_TTL_SECONDS",
    "DepCacheManager",
    "LockHashResult",
    "RUNTIME_LOCK_FILES",
    "compute_lock_hash",
]
