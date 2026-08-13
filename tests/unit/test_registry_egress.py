"""Egress de runtime-templates a registries vía registry-proxy (ADR 0094).

Cubre el cableado (settings + spec) y el mecanismo de red (attach/detach del
proxy, inyección de env) del puente que da a los runtime-templates egress
allowlisted para resolver sus dependencias.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit


def _result_capture(captured: dict) -> type:
    from workers.test_runtime import TestRuntimeResult

    class _FakeRunner:
        def __init__(self, settings: object) -> None: ...

        def launch(self, spec: object) -> object:
            captured["spec"] = spec
            return TestRuntimeResult(
                runtime=spec.plan.template.id,  # type: ignore[attr-defined]
                exit_codes=(0,),
                logs="",
                container_id="c",
                timed_out=False,
                network_name="n",
            )

        def run_command(
            self, spec: object, command: str, *, timeout_s: int, cwd: str | None = None
        ) -> tuple[int, str]:
            captured["spec"] = spec
            return 0, "ok"

    return _FakeRunner


# --- settings + spec defaults (cableado) -----------------------------------


def test_settings_expose_registry_proxy_defaults() -> None:
    from workers.config import Settings

    s = Settings()
    assert s.registry_proxy_url == "http://registry-proxy:8888"
    assert s.registry_proxy_container == "agentic-registry-proxy"
    assert s.registry_proxy_alias == "registry-proxy"
    # el host del alias debe coincidir con el host de la URL (lo usa el runtime
    # para encontrar el proxy en su bridge).
    assert s.registry_proxy_alias in s.registry_proxy_url


def test_spec_dep_egress_defaults_false() -> None:
    from shared_test_runtimes.types import RuntimeTemplate
    from workers.test_runtime import RuntimePlan, TestRuntimeSpec

    plan = RuntimePlan(
        template=RuntimeTemplate(id="php-phpunit", docker_image="img:v1"),
        checks=(),
    )
    spec = TestRuntimeSpec(plan=plan, worktree_host_path="/data/wt")
    assert spec.dep_egress is False


# --- catálogo: alineación cache_env ↔ dep_cache_mount (ADR 0094) -----------


def test_every_dep_installing_template_has_cache_env() -> None:
    """Cada plantilla que instala deps (tiene dep_cache_mount + pre_install) debe
    alinear su caché para que el cache caliente reduzca egress (ADR 0094)."""
    from shared_test_runtimes.catalog import CATALOG

    for t in CATALOG.values():
        if t.dep_cache_mount and t.default_pre_install:
            assert t.cache_env, f"{t.id} tiene dep_cache_mount pero no cache_env"


def test_cache_env_values_target_the_dep_cache_mount() -> None:
    """Cada cache_env apunta al dep_cache_mount: el mount está EN/BAJO algún valor
    (igualdad, prefijo —home que contiene el cache— o substring —flag con la ruta)."""
    from shared_test_runtimes.catalog import CATALOG

    for t in CATALOG.values():
        if not t.cache_env:
            continue
        assert t.dep_cache_mount, f"{t.id}: cache_env sin dep_cache_mount"
        mount = t.dep_cache_mount
        values = [v for _k, v in t.cache_env]
        assert any(mount == v or mount.startswith(v) or mount in v for v in values), (
            f"{t.id}: ningún cache_env apunta a dep_cache_mount {mount!r}: {values}"
        )


# --- call sites: dep_egress=True en aceptación + stack_exec (ADR 0094) ------


@pytest.mark.asyncio
async def test_acceptance_launch_requests_dep_egress(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.config import Settings
    from workers.tasks import test_runtime_task as tasks  # lookup site del split

    captured: dict = {}
    monkeypatch.setattr("workers.test_runtime.TestRuntimeRunner", _result_capture(captured))
    monkeypatch.setattr("docker.from_env", MagicMock)

    request = {
        "worktree_host_path": "/wt",
        "acceptance_criteria": [
            {
                "check_type": "automated",
                "runtime": "python-pytest",
                "command": "pytest",
                "id": "a",
                "description": "x",
            }
        ],
    }
    out = await tasks._launch_test_runtime_plans(request, Settings())
    assert out, "expected at least one plan outcome"
    assert captured["spec"].dep_egress is True


@pytest.mark.asyncio
async def test_stack_exec_requests_dep_egress(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    from workers.config import Settings
    from workers.tasks import stack_exec_task as tasks  # lookup site del split

    tenant_id, task_id = uuid4(), uuid4()
    project = SimpleNamespace(
        id=uuid4(),
        slug="api-ci",
        allowed_commands=["composer"],
        default_runtime_template="php-phpunit",
        # ADR 0129: `_run_stack_command` lee `project.repository_config` para
        # levantar los servicios auxiliares del proyecto. NULL = sin servicios
        # declarados, que es el caso de este test (el código hace `dict(... or {})`).
        repository_config=None,
    )
    task = SimpleNamespace(id=task_id, project_id=project.id)
    org = SimpleNamespace(slug="demo")

    class _Res:
        def __init__(self, obj: object) -> None:
            self._obj = obj

        def scalar_one_or_none(self) -> object:
            return self._obj

    class _Session:
        def __init__(self) -> None:
            self._seq = [task, project]
            self._i = 0

        async def __aenter__(self) -> _Session:
            return self

        async def __aexit__(self, *a: object) -> bool:
            return False

        async def execute(self, _stmt: object) -> _Res:
            obj = self._seq[self._i]
            self._i += 1
            return _Res(obj)

        async def get(self, _model: object, _pk: object) -> object:
            return org

    class _Engine:
        async def dispose(self) -> None: ...

    # `worker_engine` y no `create_async_engine`: ver la nota en
    # tests/unit/test_stack_exec_errors.py::_wire_db — el engine compartido cambió
    # la costura, no lo que se prueba.
    monkeypatch.setattr(tasks, "worker_engine", lambda _settings: _Engine())
    monkeypatch.setattr(tasks, "async_sessionmaker", lambda _engine, **_k: _Session)
    monkeypatch.setattr("docker.from_env", MagicMock)
    monkeypatch.setattr(
        "workers.test_runtime.resolve_run_runtime",
        lambda **_k: __import__("shared_test_runtimes.catalog", fromlist=["get"]).get(
            "php-phpunit"
        ),
    )
    # F0.3: _run_stack_command ahora exige que el worktree exista en el host
    # antes de containers/create — un tmp real en vez de una ruta ficticia.
    monkeypatch.setattr(
        "workers.git_repos.BareRepoLayout",
        lambda **_k: SimpleNamespace(worktree_path=lambda _t: str(tmp_path)),
    )
    captured: dict = {}
    monkeypatch.setattr("workers.test_runtime.TestRuntimeRunner", _result_capture(captured))

    result = await tasks._run_stack_command(
        {"tenant_id": str(tenant_id), "task_id": str(task_id), "command": "composer install"},
        Settings(),
    )
    assert result["exit_code"] == 0
    assert captured["spec"].dep_egress is True
