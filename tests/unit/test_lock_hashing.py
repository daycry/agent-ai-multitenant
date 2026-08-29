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


# ---------------------------------------------------------------------------
# ADR 0162 (decisión 1) — la cuarta boca: el lockfile de un proyecto anidado
# ---------------------------------------------------------------------------


class _RecordingLogger:
    """Doble del logger del módulo. `caplog` es frágil aquí (gotcha
    `caplog-y-orden-de-tests`: otras suites llaman a `logging.disable`)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def warning(self, msg: str, *args: object, **_kw: object) -> None:
        self.calls.append((msg, args))


def test_lock_hash_finds_the_lockfile_under_the_project_root(tmp_path: Path) -> None:
    """El proyecto vive en `ci4build/`, y su `composer.lock` con él. Mirando
    sólo la raíz, `compute_lock_hash` devolvía `hash=None` y DESACTIVABA la
    caché de dependencias — el mismo defecto de las otras tres bocas, pagando
    un precio distinto (cada run reinstala desde cero)."""
    from shared_test_runtimes.dep_cache import compute_lock_hash

    nested = tmp_path / "ci4build"
    nested.mkdir()
    (nested / "composer.lock").write_bytes(b'{"hash":"abc"}\n')

    found = compute_lock_hash(tmp_path, "php-phpunit", project_root="ci4build")
    assert found.hash is not None
    assert found.lock_path == nested / "composer.lock"
    # Y el contenido es lo que se hashea: mismo fichero en la raíz, mismo hash.
    (tmp_path / "composer.lock").write_bytes(b'{"hash":"abc"}\n')
    assert compute_lock_hash(tmp_path, "php-phpunit").hash == found.hash


def test_a_root_project_hash_does_not_change(tmp_path: Path) -> None:
    """No-regresión dura: el hash es la clave del directorio de caché en disco.
    Si cambiara para los proyectos de la raíz, TODAS las cachés calientes
    quedarían huérfanas de golpe."""
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "requirements.txt").write_bytes(b"hello\n")
    pinned = "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"

    assert compute_lock_hash(tmp_path, "python-pytest").hash == pinned
    assert compute_lock_hash(tmp_path, "python-pytest", project_root=None).hash == pinned
    assert compute_lock_hash(tmp_path, "python-pytest", project_root="").hash == pinned
    assert compute_lock_hash(tmp_path, "python-pytest", project_root="   ").hash == pinned


def test_a_missing_lockfile_is_logged_instead_of_silently_disabling_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lo que denuncia el ADR 0162: devolvía `hash=None` y se apagaba la caché
    **sin un solo log**. El comportamiento no cambia (sigue `None`); lo que
    cambia es que ahora se puede saber, y dónde miró."""
    from shared_test_runtimes import dep_cache

    fake = _RecordingLogger()
    monkeypatch.setattr(dep_cache, "_log", fake)

    result = dep_cache.compute_lock_hash(tmp_path, "php-phpunit", project_root="ci4build")

    assert result.hash is None
    rendered = [msg % args for msg, args in fake.calls]
    assert any("composer.lock" in line and "ci4build" in line for line in rendered)


def test_a_traversing_project_root_never_escapes_the_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Aquí la ruta no se concatena en un `sh -c` sino en un `Path`, y
    `Path('/wt') / '/etc'` da `/etc` — el operando absoluto se come al izquierdo.
    Un `..` cae a la raíz del worktree (y se dice), nunca fuera."""
    from shared_test_runtimes import dep_cache

    (tmp_path / "requirements.txt").write_bytes(b"hello\n")
    fake = _RecordingLogger()
    monkeypatch.setattr(dep_cache, "_log", fake)

    for bad in ("..", "../..", "a/../..", "."):
        result = dep_cache.compute_lock_hash(tmp_path, "python-pytest", project_root=bad)
        assert result.lock_path == tmp_path / "requirements.txt"
    assert fake.calls  # el descarte del valor inválido no es mudo


def test_an_absolute_project_root_is_read_as_relative_like_the_worker_does(
    tmp_path: Path,
) -> None:
    """`_apply_cwd` (worker) le quita la barra inicial y lo trata como relativo.
    Aquí se hace lo MISMO: si los dos no coinciden, la caché busca el lockfile en
    un sitio y el comando corre en otro, que es peor que no cachear."""
    from shared_test_runtimes.dep_cache import compute_lock_hash

    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "requirements.txt").write_bytes(b"hello\n")

    result = compute_lock_hash(tmp_path, "python-pytest", project_root="/etc")
    assert result.lock_path == tmp_path / "etc" / "requirements.txt"
    assert result.hash is not None
