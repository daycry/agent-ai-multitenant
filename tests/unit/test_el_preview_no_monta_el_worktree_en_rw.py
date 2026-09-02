"""El preview de review no monta el worktree del plan en RW (`task_cv_26`, B-06).

Auditoría 2026-09-01. `review_runtime_task` lanza la aplicación del tenant
durante 48 horas con el worktree del plan bind-mounteado en lectura/escritura:
cualquier cosa que la app escriba (caché, uploads, logs) o cualquier
vulnerabilidad de la propia app modifica el código que el humano va a validar.
Por defecto `/workspace` va en sólo lectura; las rutas que la app necesite
escribir se declaran (`preview.writable_paths`) y se montan como tmpfs; el RW
completo es un opt-in explícito (`preview.workspace_rw`).
"""

from __future__ import annotations

import pytest
from workers.config import Settings
from workers.runtime_services import (
    RuntimeServicesConfigError,
    build_project_runtime_services,
)
from workers.tasks.review_runtime_task import _preview_run_kwargs

pytestmark = pytest.mark.unit


def _workspace_mount(kwargs: dict) -> dict:
    mounts = [m for m in kwargs.get("mounts", []) if m["Target"] == "/workspace"]
    assert len(mounts) == 1, "el preview tiene que montar /workspace exactamente una vez"
    return mounts[0]


def test_by_default_the_worktree_is_mounted_read_only() -> None:
    kwargs = _preview_run_kwargs(
        Settings(), worktree_host_path="/data/wt", services=build_project_runtime_services(None)
    )
    assert _workspace_mount(kwargs)["ReadOnly"] is True


def test_declared_writable_paths_become_tmpfs_under_workspace() -> None:
    services = build_project_runtime_services(
        {"preview": {"writable_paths": ["writable", "storage/logs"]}}
    )
    kwargs = _preview_run_kwargs(Settings(), worktree_host_path="/data/wt", services=services)
    assert _workspace_mount(kwargs)["ReadOnly"] is True
    assert "/workspace/writable" in kwargs["tmpfs"]
    assert "/workspace/storage/logs" in kwargs["tmpfs"]
    assert "uid=1000" in kwargs["tmpfs"]["/workspace/writable"]


def test_rw_is_an_explicit_opt_in() -> None:
    services = build_project_runtime_services({"preview": {"workspace_rw": True}})
    kwargs = _preview_run_kwargs(Settings(), worktree_host_path="/data/wt", services=services)
    assert _workspace_mount(kwargs)["ReadOnly"] is False


@pytest.mark.parametrize("bad", ["../etc", "/abs", "", "a/../../b", "x\x00y"])
def test_a_writable_path_that_escapes_the_workspace_is_rejected(bad: str) -> None:
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services({"preview": {"writable_paths": [bad]}})


def test_preview_must_be_a_mapping() -> None:
    with pytest.raises(RuntimeServicesConfigError):
        build_project_runtime_services({"preview": ["writable"]})
