"""Integration tests: dep-cache TTL purge (Plan 06 task_06_11).

Cache entries older than ``ttl_seconds`` (default 14 days) get
swept. The worker schedules ``purge_expired`` periodically — these
tests pin the contract: only expired dirs go, not-yet-expired ones
stay, never-touched non-dir files are ignored.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _seed(root: Path, name: str, age_seconds: float) -> Path:
    """Create a fake cache dir and set its mtime back ``age_seconds``."""
    # `task_cv_24`: la caché vive por tenant (`{tenant}/{prefix}-{hash}`); una
    # entrada plana en la raíz es layout antiguo y la purga la trata aparte.
    path = root / "acme" / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "marker").write_text("cache content")
    past = time.time() - age_seconds
    os.utime(path, (past, past))
    return path


def test_purge_removes_only_expired(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    old = _seed(tmp_path, "pip-abc", 60 * 60 * 24 * 20)  # 20 days
    fresh = _seed(tmp_path, "pip-def", 60 * 60 * 24 * 3)  # 3 days

    removed = mgr.purge_expired()
    assert old in removed
    assert fresh not in removed
    assert not old.exists()
    assert fresh.exists()


def test_purge_respects_custom_ttl(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    seven_days = _seed(tmp_path, "pip-x", 60 * 60 * 24 * 7)

    # ttl=14 days → keep
    assert mgr.purge_expired(ttl_seconds=60 * 60 * 24 * 14) == []
    assert seven_days.exists()

    # ttl=5 days → purge
    removed = mgr.purge_expired(ttl_seconds=60 * 60 * 24 * 5)
    assert seven_days in removed
    assert not seven_days.exists()


def test_purge_ignores_non_directory_entries(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    # A stray file at cache root (e.g. an admin's note) shouldn't crash
    # nor be deleted.
    stray = tmp_path / "README.md"
    stray.write_text("don't delete me")

    _seed(tmp_path, "pip-old", 60 * 60 * 24 * 30)
    removed = mgr.purge_expired()

    assert len(removed) == 1
    assert stray.exists()


def test_purge_handles_missing_root(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path / "never-existed")
    # No exception, returns empty list.
    assert mgr.purge_expired() == []


def test_purge_uses_now_override_for_deterministic_tests(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    cache = _seed(tmp_path, "pip-deterministic", 60 * 60 * 24 * 100)
    cache_mtime = cache.stat().st_mtime

    # Fake a "now" that's 200 days after the cache mtime — TTL 50 days.
    fake_now = cache_mtime + 60 * 60 * 24 * 200
    removed = mgr.purge_expired(ttl_seconds=60 * 60 * 24 * 50, now=fake_now)
    assert cache in removed
