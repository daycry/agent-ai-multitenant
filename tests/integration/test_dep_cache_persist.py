"""Integration tests: DepCacheManager persists entries on disk
(Plan 06 task_06_09).

Uses tmp_path; no Docker daemon. Pins the on-host layout
``{root}/{prefix}-{hash}/`` and the mtime-touch contract.
"""

from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _python_pytest_template() -> object:
    from shared_test_runtimes.catalog import get

    return get("python-pytest")


def test_ensure_entry_creates_dir_with_prefix_and_hash(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    entry = mgr.ensure_entry(_python_pytest_template(), "abc123")

    assert entry.host_path.is_dir()
    assert entry.host_path.name == "pip-abc123"
    assert entry.cache_key == "pip-abc123"
    assert entry.container_mount == "/root/.cache/pip"


def test_ensure_entry_is_idempotent(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    e1 = mgr.ensure_entry(_python_pytest_template(), "abc")
    # Drop a file in the cache so we can verify it survives a second
    # ensure call (it's NOT a re-create).
    (e1.host_path / "marker").write_text("data")
    e2 = mgr.ensure_entry(_python_pytest_template(), "abc")
    assert e1.host_path == e2.host_path
    assert (e2.host_path / "marker").read_text() == "data"


def test_ensure_entry_makes_dir_writable_by_nonroot_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The worker creates the cache dir as root, but the test/stack runtime runs
    as a NON-root user (isolation.AGENT_UID_GID = 1000:1000). Without a world-
    writable mode the bind-mounted cache is read-only to the runtime, so
    composer/npm/pip warn 'cache directory not writable' and re-fetch every time
    (and the agent can loop trying to silence it). ensure_entry must chmod the
    dir world-writable. Asserted OS-independently by capturing the mode we pass
    to os.chmod (Windows can't reflect posix bits on the FS)."""
    from shared_test_runtimes import dep_cache

    calls: list[tuple[str, int]] = []
    real_chmod = os.chmod

    def _spy(path: object, mode: int, *a: object, **k: object) -> None:
        calls.append((str(path), mode))
        with contextlib.suppress(OSError):  # harmless / no-op on Windows
            real_chmod(path, mode)  # type: ignore[arg-type]

    monkeypatch.setattr(dep_cache.os, "chmod", _spy)
    mgr = dep_cache.DepCacheManager(tmp_path)
    entry = mgr.ensure_entry(_python_pytest_template(), "wperm")

    assert any(
        p == str(entry.host_path) and (mode & 0o777) == 0o777 for p, mode in calls
    ), f"cache dir not chmod'd world-writable; chmod calls={calls}"


def test_ensure_entry_touches_mtime(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    entry = mgr.ensure_entry(_python_pytest_template(), "xyz")

    # Backdate the directory.
    past = time.time() - 60 * 60 * 24 * 20  # 20 days ago
    os.utime(entry.host_path, (past, past))
    assert entry.host_path.stat().st_mtime < time.time() - 60 * 60 * 24

    # Re-ensure → mtime should jump forward to ~now.
    mgr.ensure_entry(_python_pytest_template(), "xyz")
    assert entry.host_path.stat().st_mtime > time.time() - 5


def test_cache_path_uses_runtime_prefix_not_template_id(tmp_path: Path) -> None:
    """node-jest and node-vitest share the same lock file and same cache
    prefix (``npm``); ensure cache_path_for reflects that."""
    from shared_test_runtimes.catalog import get
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    a = mgr.cache_path_for(get("node-jest"), "h1")
    b = mgr.cache_path_for(get("node-vitest"), "h1")
    assert a == b
    assert a.name == "npm-h1"


def test_root_is_lazy_creation(tmp_path: Path) -> None:
    """The manager doesn't create the root upfront — only on first
    ensure_entry. That keeps test runs (and ops scripts) from
    sprinkling empty dirs around."""
    from shared_test_runtimes.dep_cache import DepCacheManager

    root = tmp_path / "not-created-yet"
    mgr = DepCacheManager(root)
    assert not root.exists()
    mgr.ensure_entry(_python_pytest_template(), "abc")
    assert root.exists()
    assert (root / "pip-abc").is_dir()
