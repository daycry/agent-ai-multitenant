"""La dep-cache se acota por tenant y deja de ser 0777 (`task_cv_24`, B-04).

Auditoría 2026-09-01. `cache_path_for` devolvía `{prefix}-{lock_hash}` sin
tenant, montado RW en el contenedor no confiable y con `chmod 0777`: dos tenants
con el mismo lockfile compartían el directorio donde maven, bundler o composer
no verifican contenido, así que uno podía envenenar la caché del otro. Ahora la
clave es `{tenant_slug}/{prefix}-{hash}`, el directorio nace 0755 (uid 1000, el
mismo del worker y del runtime) y el layout plano antiguo se purga.
"""

from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
from shared_test_runtimes import CATALOG
from shared_test_runtimes.dep_cache import DepCacheManager

pytestmark = pytest.mark.unit


def _template():  # type: ignore[no-untyped-def]
    templates = CATALOG.values() if isinstance(CATALOG, dict) else CATALOG
    return next(t for t in templates if getattr(t, "dep_cache_mount", None))


def test_the_cache_key_carries_the_tenant(tmp_path: Path) -> None:
    mgr = DepCacheManager(tmp_path)
    path = mgr.cache_path_for(_template(), "abc123", tenant_slug="acme")
    assert path.parent == tmp_path / "acme"
    assert path.name.endswith("-abc123")


def test_two_tenants_with_the_same_lockfile_get_two_directories(tmp_path: Path) -> None:
    mgr = DepCacheManager(tmp_path)
    a = mgr.ensure_entry(_template(), "samehash", tenant_slug="acme").host_path
    b = mgr.ensure_entry(_template(), "samehash", tenant_slug="globex").host_path
    assert a != b and a.is_dir() and b.is_dir()


def test_the_directory_is_not_world_writable(tmp_path: Path) -> None:
    mgr = DepCacheManager(tmp_path)
    path = mgr.ensure_entry(_template(), "h1", tenant_slug="acme").host_path
    mode = stat.S_IMODE(path.stat().st_mode)
    if os.name != "nt":  # Windows no modela el bit «otros»
        assert not (mode & stat.S_IWOTH), f"la caché sigue siendo escribible por todos: {oct(mode)}"


def test_a_tenant_slug_that_escapes_the_root_is_rejected(tmp_path: Path) -> None:
    mgr = DepCacheManager(tmp_path)
    for bad in ("../x", "a/b", "", ".", "..", "x\\y"):
        with pytest.raises(ValueError):
            mgr.cache_path_for(_template(), "h", tenant_slug=bad)


def test_purge_walks_the_tenant_folders_and_drops_the_legacy_flat_layout(tmp_path: Path) -> None:
    mgr = DepCacheManager(tmp_path)
    fresh = mgr.ensure_entry(_template(), "fresh", tenant_slug="acme").host_path
    stale = mgr.ensure_entry(_template(), "stale", tenant_slug="acme").host_path
    old = time.time() - 10 * 24 * 3600
    os.utime(stale, (old, old))
    legacy = tmp_path / f"{fresh.name.rsplit('-', 1)[0]}-0123456789abcdef"
    legacy.mkdir()

    removed = mgr.purge_expired(ttl_seconds=24 * 3600)

    assert stale in removed and legacy in removed
    assert fresh not in removed and fresh.is_dir()


def test_invalidate_is_scoped_to_one_tenant(tmp_path: Path) -> None:
    mgr = DepCacheManager(tmp_path)
    tpl = _template()
    a = mgr.ensure_entry(tpl, "h", tenant_slug="acme").host_path
    b = mgr.ensure_entry(tpl, "h", tenant_slug="globex").host_path

    removed = mgr.invalidate(tpl.id, tenant_slug="acme")

    assert a in removed and not a.exists()
    assert b not in removed and b.is_dir()
