"""prod-12 task_prod12_img_01 (sandbox-6) — el dep-cache es escribible por uid 1000.

El worker fuerza ``user=1000:1000`` en cada run de test-runtime; un
``dep_cache_mount``/``cache_env`` bajo ``/root`` (o ``/usr/local``) hacía que el
cacheo de dependencias fallara EN SILENCIO y reinstalara en cada run. Todo
apunta ahora bajo ``/home/agent`` (el HOME escribible que hornean las imágenes).
"""

from __future__ import annotations

import pytest
from shared_test_runtimes import CATALOG

pytestmark = pytest.mark.unit

_FORBIDDEN_PREFIXES = ("/root", "/usr/local")


def test_every_dep_cache_mount_is_writable_by_uid_1000() -> None:
    for template_id, template in CATALOG.items():
        mount = template.dep_cache_mount
        if mount is None:
            continue
        assert mount.startswith("/home/agent/"), (
            f"{template_id}: dep_cache_mount {mount!r} no vive bajo /home/agent — "
            "uid 1000 no puede escribirlo y el cache falla en silencio"
        )


def test_cache_env_never_points_at_root_owned_paths() -> None:
    for template_id, template in CATALOG.items():
        for name, value in template.cache_env or ():
            for prefix in _FORBIDDEN_PREFIXES:
                assert prefix not in value, (
                    f"{template_id}: cache_env {name}={value!r} apunta a una ruta "
                    f"propiedad de root ({prefix}) — inescribible con user=1000"
                )


def test_cache_env_is_consistent_with_the_mount() -> None:
    """Cada template con mount tiene al menos una env que referencia esa ruta
    (o un prefijo suyo, p.ej. CARGO_HOME → registry/)."""
    for template_id, template in CATALOG.items():
        mount = template.dep_cache_mount
        if mount is None or not template.cache_env:
            continue
        values = [value for _n, value in template.cache_env]
        assert any(mount.startswith(v) or v.startswith(mount) or mount in v for v in values), (
            f"{template_id}: ninguna cache_env referencia el mount {mount!r} — "
            "el toolchain escribiría fuera del bind-mount"
        )
