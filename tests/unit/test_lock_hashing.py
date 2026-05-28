"""Unit tests for lock-file hashing (Plan 06 task_06_08).

In-process, no I/O beyond tmp_path. We pin: deterministic per-content
hashes, sensitivity to byte-level changes, the right lock file picked
per runtime, missing-lock returns ``hash=None``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_hash_is_deterministic(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "requirements.txt").write_text("pytest==8.2.0\nstructlog==24.1\n")
    a = compute_lock_hash(tmp_path, "python-pytest")
    b = compute_lock_hash(tmp_path, "python-pytest")
    assert a.hash == b.hash
    assert a.hash is not None
    assert len(a.hash) == 64  # sha256 hex


def test_hash_changes_with_content(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "requirements.txt").write_text("pytest==8.2.0\n")
    h1 = compute_lock_hash(tmp_path, "python-pytest").hash
    (tmp_path / "requirements.txt").write_text("pytest==8.3.0\n")
    h2 = compute_lock_hash(tmp_path, "python-pytest").hash
    assert h1 != h2


def test_hash_picks_right_lock_per_runtime(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "requirements.txt").write_text("pytest")
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "composer.lock").write_text('{"hash":"php"}')

    py = compute_lock_hash(tmp_path, "python-pytest")
    node = compute_lock_hash(tmp_path, "node-jest")
    php = compute_lock_hash(tmp_path, "php-phpunit")

    assert py.prefix == "pip"
    assert node.prefix == "npm"
    assert php.prefix == "composer"
    assert py.hash != node.hash != php.hash


def test_missing_lock_returns_none_hash(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import compute_lock_hash

    # No requirements.txt in tmp_path.
    result = compute_lock_hash(tmp_path, "python-pytest")
    assert result.hash is None
    assert result.prefix == "pip"


def test_generic_runtimes_have_no_lock_file() -> None:
    from shared_test_runtimes.dep_cache import compute_lock_hash

    result = compute_lock_hash("/nonexistent", "generic-shell")
    assert result.hash is None
    assert result.prefix == ""
    assert result.lock_path is None


def test_node_runtimes_share_lock_file(tmp_path: Path) -> None:
    """node-jest, node-vitest, node-playwright all key off package-lock.json
    — so their hashes are equal for the same lockfile content."""
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "package-lock.json").write_text('{"name":"x","version":"1.0.0"}')
    jest = compute_lock_hash(tmp_path, "node-jest").hash
    vitest = compute_lock_hash(tmp_path, "node-vitest").hash
    pw = compute_lock_hash(tmp_path, "node-playwright").hash
    assert jest is not None
    assert jest == vitest == pw


def test_php_runtimes_share_lock_file(tmp_path: Path) -> None:
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "composer.lock").write_text('{"hash":"abc"}')
    a = compute_lock_hash(tmp_path, "php-phpunit").hash
    b = compute_lock_hash(tmp_path, "php-pest").hash
    assert a is not None
    assert a == b


def test_known_hash_pin(tmp_path: Path) -> None:
    """sha256 of literal bytes 'hello\\n' is well-known — pin so any
    future change to the hashing algorithm (e.g. switching to blake3)
    requires an explicit refactor of this test."""
    from shared_test_runtimes.dep_cache import compute_lock_hash

    # write_bytes — write_text on Windows defaults to CRLF newlines,
    # which would change the hash; we want byte-exact reproducibility.
    (tmp_path / "requirements.txt").write_bytes(b"hello\n")
    result = compute_lock_hash(tmp_path, "python-pytest")
    assert result.hash == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"
