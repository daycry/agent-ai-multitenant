"""Integration tests: DepCacheManager persists entries on disk
(Plan 06 task_06_09).

Uses tmp_path; no Docker daemon. Pins the on-host layout
``{root}/{prefix}-{hash}/`` and the mtime-touch contract.
"""

from __future__ import annotations

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
    entry = mgr.ensure_entry(_python_pytest_template(), "abc123", tenant_slug="acme")

    assert entry.host_path.is_dir()
    assert entry.host_path.name == "pip-abc123"
    assert entry.cache_key == "pip-abc123"
    assert entry.container_mount == "/home/agent/.cache/pip"


def test_ensure_entry_is_idempotent(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    e1 = mgr.ensure_entry(_python_pytest_template(), "abc", tenant_slug="acme")
    # Drop a file in the cache so we can verify it survives a second
    # ensure call (it's NOT a re-create).
    (e1.host_path / "marker").write_text("data")
    e2 = mgr.ensure_entry(_python_pytest_template(), "abc", tenant_slug="acme")
    assert e1.host_path == e2.host_path
    assert (e2.host_path / "marker").read_text() == "data"


def test_ensure_entry_owns_the_dir_for_the_nonroot_runtime_without_0777(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`task_cv_24` (auditoría 2026-09-01, B-04): la caché se monta RW en un
    contenedor no confiable y antes nacía `chmod 0777` — escribible por todos los
    uid del host. El runtime corre como uid 1000 (isolation.AGENT_UID_GID), así
    que basta con que el directorio sea SUYO (chown cuando el worker es root) y
    0755. Se afirma capturando el modo que se pasa a os.chmod, independiente del
    SO, y el chown cuando el proceso es root."""
    from shared_test_runtimes import dep_cache

    chmods: list[tuple[str, int]] = []
    chowns: list[tuple[str, int, int]] = []
    monkeypatch.setattr(dep_cache.os, "chmod", lambda path, mode: chmods.append((str(path), mode)))
    monkeypatch.setattr(dep_cache.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(
        dep_cache.os,
        "chown",
        lambda path, uid, gid: chowns.append((str(path), uid, gid)),
        raising=False,
    )
    mgr = dep_cache.DepCacheManager(tmp_path)

    entry = mgr.ensure_entry(_python_pytest_template(), "abc123", tenant_slug="acme")

    assert chmods and chmods[-1] == (str(entry.host_path), 0o755)
    assert all(mode & 0o002 == 0 for _path, mode in chmods), "sigue naciendo escribible por todos"
    assert chowns == [(str(entry.host_path), 1000, 1000)]


def test_ensure_entry_touches_mtime(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    entry = mgr.ensure_entry(_python_pytest_template(), "xyz", tenant_slug="acme")

    # Backdate the directory.
    past = time.time() - 60 * 60 * 24 * 20  # 20 days ago
    os.utime(entry.host_path, (past, past))
    assert entry.host_path.stat().st_mtime < time.time() - 60 * 60 * 24

    # Re-ensure → mtime should jump forward to ~now.
    mgr.ensure_entry(_python_pytest_template(), "xyz", tenant_slug="acme")
    assert entry.host_path.stat().st_mtime > time.time() - 5


def test_cache_path_uses_runtime_prefix_not_template_id(tmp_path: Path) -> None:
    """node-jest and node-vitest share the same lock file and same cache
    prefix (``npm``); ensure cache_path_for reflects that."""
    from shared_test_runtimes.catalog import get
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    a = mgr.cache_path_for(get("node-jest"), "h1", tenant_slug="acme")
    b = mgr.cache_path_for(get("node-vitest"), "h1", tenant_slug="acme")
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
    mgr.ensure_entry(_python_pytest_template(), "abc", tenant_slug="acme")
    assert root.exists()
    assert (root / "acme" / "pip-abc").is_dir()  # task_cv_24: por tenant
