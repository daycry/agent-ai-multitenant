"""Integration tests: dep-cache mounts into test-runtime when warm
(Plan 06 task_06_10).

We're testing the *decision* — should the worker bind-mount the cache,
and at what path inside the container — not the docker side (covered
by test_test_runtime_launch.py).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def _template(template_id: str) -> object:
    from shared_test_runtimes.catalog import get

    return get(template_id)


def test_mount_for_returns_entry_when_lock_hash_present(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    entry = mgr.mount_for(_template("python-pytest"), "abc123")
    assert entry is not None
    assert entry.container_mount == "/home/agent/.cache/pip"
    assert entry.host_path.is_dir()
    assert entry.host_path.name == "pip-abc123"


def test_mount_for_returns_none_when_lock_hash_missing(tmp_path: Path) -> None:
    """Worktree has no requirements.txt → no cache mount."""
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    entry = mgr.mount_for(_template("python-pytest"), None)
    assert entry is None
    # No directory created on disk either.
    assert not any(tmp_path.iterdir())


def test_mount_for_returns_none_when_template_has_no_dep_cache(
    tmp_path: Path,
) -> None:
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    # generic-shell has dep_cache_mount=None.
    entry = mgr.mount_for(_template("generic-shell"), "abc123")
    assert entry is None


def test_mount_path_matches_template_per_runtime(tmp_path: Path) -> None:
    """Each template advertises a runtime-correct mount path:
    npm cache at /home/agent/.npm, composer at /home/agent/.composer/cache, etc."""
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    cases: list[tuple[str, str]] = [
        ("python-pytest", "/home/agent/.cache/pip"),
        ("node-jest", "/home/agent/.npm"),
        ("php-phpunit", "/home/agent/.composer/cache"),
        ("go-test", "/home/agent/go/pkg/mod"),
        ("java-maven", "/home/agent/.m2/repository"),
        ("ruby-rspec", "/home/agent/.bundle"),
        ("rust-cargo", "/home/agent/.cargo/registry"),
        ("dotnet-test", "/home/agent/.nuget/packages"),
    ]
    for template_id, expected_mount in cases:
        entry = mgr.mount_for(_template(template_id), "h")
        assert entry is not None
        assert (
            entry.container_mount == expected_mount
        ), f"{template_id}: expected {expected_mount}, got {entry.container_mount}"


def test_warm_cache_skips_pre_install_intent(tmp_path: Path) -> None:
    """When a cache dir already has content, mount_for still returns
    the entry — the worker is the one that decides ``pre_install`` is
    a no-op once it sees the cache has the expected files. This test
    pins that mount_for never destructively recreates a warm cache."""
    from shared_test_runtimes.dep_cache import DepCacheManager

    mgr = DepCacheManager(tmp_path)
    e1 = mgr.mount_for(_template("python-pytest"), "abc")
    assert e1 is not None
    (e1.host_path / "pytest-8.2-py3-none-any.whl").write_text("(fake wheel)")

    e2 = mgr.mount_for(_template("python-pytest"), "abc")
    assert e2 is not None
    assert (e2.host_path / "pytest-8.2-py3-none-any.whl").exists()
