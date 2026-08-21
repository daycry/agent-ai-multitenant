"""Integration tests: ``run_*`` tools resolve their runtime from the
project stack (Plan 06.16 task_06_16_03 / 03-run-by-stack).

The four ``run_*`` builtins (``run_pytest`` / ``run_lint`` /
``run_typecheck`` / ``run_build``) are ``docker_command`` tools. Before
this task they ran in a fixed runtime (``python-pytest``); now they
resolve their :class:`RuntimeTemplate` from
``projects.default_runtime_template`` when the project pins a stack,
falling back to each tool's own ``implementation_ref`` default — and to
``python-pytest`` as the final fallback — when it does not
(backward-compatible).

Four layers are proven here:

  1. **The resolver** (``workers.test_runtime.resolve_run_runtime``):
     a project with ``php-phpunit`` resolves to the php-phpunit template;
     no project field (and no tool default) falls back to python-pytest;
     an unknown template id raises a CLEAR error, not a bare KeyError.
  2. **The tool dispatch** (``agent_runtime.tool_wiring``): a ``run_*``
     ``docker_command`` tool carrying a ``runtime_template`` resolves its
     image through the worker-injected resolver, honouring the project
     default over the tool default; an unknown id surfaces a clear error.
  3. **The spec threading** (``workers.execution._agent_spec`` +
     ``orchestrator``): the project's ``default_runtime_template`` rides
     the task spec only when the project pins a stack (NULL ⇒ no key ⇒
     per-tool default).
  4. **End to end against the real DB**: a project persisted with
     ``default_runtime_template='php-phpunit'`` reads back and resolves
     to the php-phpunit template through the same resolver.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

from tests.integration._runtime_image_refs import apunta_a

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _fresh_global_state_shield():
    """Blindaje de orden (tanda 2, 2026-07-19): este fichero fallaba SOLO en
    la suite completa (pasa aislado) — estado global heredado del fichero
    anterior (engines/caches vivos). Reset al ENTRAR en cada test: barato,
    idempotente y sin efecto cuando el estado ya está limpio."""
    from api_server.auth.deps import reset_redis_cache
    from api_server.config import get_settings
    from api_server.db.session import reset_engine_cache

    get_settings.cache_clear()
    reset_engine_cache()
    reset_redis_cache()
    yield


# ===========================================================================
# Layer 1 — the resolver: project stack → RuntimeTemplate, with fallback
# ===========================================================================
def test_project_runtime_wins() -> None:
    from workers.test_runtime import resolve_run_runtime

    template = resolve_run_runtime(
        project_default_runtime="php-phpunit",
        tool_default_runtime="python-pytest",  # run_pytest's implementation_ref
    )
    assert template.id == "php-phpunit"
    # Sin fijar la referencia literal: desde el ADR 0148 la compone el manifiesto
    # de release (registry + versión + digest tras publicar). Lo que este test
    # afirma es que resolvió la imagen de PHP y no la de Python.
    assert "agent-runtime-php-phpunit" in template.docker_image


def test_no_project_field_falls_back_to_tool_default() -> None:
    from workers.test_runtime import resolve_run_runtime

    # run_pytest carries implementation_ref='python-pytest'; with no project
    # stack it keeps running there (backward-compatible).
    template = resolve_run_runtime(
        project_default_runtime=None,
        tool_default_runtime="python-pytest",
    )
    assert template.id == "python-pytest"


def test_no_project_and_no_tool_default_falls_back_to_python_pytest() -> None:
    from workers.test_runtime import DEFAULT_RUN_RUNTIME_ID, resolve_run_runtime

    # run_lint / run_typecheck / run_build carry NO implementation_ref; with no
    # project stack they fall back to the global default (python-pytest).
    template = resolve_run_runtime(
        project_default_runtime=None,
        tool_default_runtime=None,
    )
    assert template.id == DEFAULT_RUN_RUNTIME_ID == "python-pytest"


def test_blank_project_field_is_treated_as_unset() -> None:
    from workers.test_runtime import resolve_run_runtime

    # The chips/UI never sends a tidy value; a blank string must not shadow
    # the tool default.
    template = resolve_run_runtime(
        project_default_runtime="   ",
        tool_default_runtime="node-jest",
    )
    assert template.id == "node-jest"


def test_unknown_template_raises_clear_error() -> None:
    from workers.test_runtime import RuntimeResolutionError, resolve_run_runtime

    with pytest.raises(RuntimeResolutionError) as exc:
        resolve_run_runtime(
            project_default_runtime="php-laravel-9000",
            tool_default_runtime="python-pytest",
        )
    msg = str(exc.value)
    # Clear: names the offending id AND the known set.
    assert "php-laravel-9000" in msg
    assert "php-phpunit" in msg
    assert "python-pytest" in msg
    # A subclass of ValueError so existing boot-time handlers still catch it.
    assert isinstance(exc.value, ValueError)


def test_resolve_run_runtime_image_adapter() -> None:
    from workers.test_runtime import resolve_run_runtime_image

    # The (project_default, tool_default) -> image adapter the worker injects
    # into the agent-runtime tool_wiring.WiringContext.
    # El default del PROYECTO gana al de la herramienta. Se afirma tambien la
    # negativa: sin ella, un resolutor que devolviera siempre la misma imagen
    # pasaria este test.
    del_proyecto = resolve_run_runtime_image("php-phpunit", "python-pytest")
    assert apunta_a(del_proyecto, "php-phpunit")
    assert not apunta_a(del_proyecto, "python-pytest")

    # Sin default de proyecto manda el de la herramienta; sin ninguno, el del
    # catalogo.
    assert apunta_a(resolve_run_runtime_image(None, "python-pytest"), "python-pytest")
    assert apunta_a(resolve_run_runtime_image(None, None), "python-pytest")


# ===========================================================================
# Layer 2 — the tool dispatch: docker_command run_* resolves by stack
# ===========================================================================
def _run_tool_spec(name: str, runtime_template: str | None):
    from agent_runtime.tool_wiring import ToolSpec

    config: dict = {"command_template": ["pytest", "{path}"]}
    if runtime_template is not None:
        config["runtime_template"] = runtime_template
    return ToolSpec(name=name, implementation_type="docker_command", config=config)


def test_tool_dispatch_uses_project_runtime_image() -> None:
    from agent_runtime.tool_wiring import WiringContext, register_tool_specs
    from agent_runtime.tools import ToolRegistry
    from workers.test_runtime import resolve_run_runtime_image

    registry = ToolRegistry()
    # A PHP project pins php-phpunit; run_pytest's tool default is python-pytest.
    ctx = WiringContext(
        project_default_runtime="php-phpunit",
        runtime_image_resolver=resolve_run_runtime_image,
    )
    registered = register_tool_specs(
        registry, [_run_tool_spec("run_pytest", "python-pytest")], ctx=ctx
    )
    assert registered == ["run_pytest"]
    # The registered DockerCommandTool's image is resolved from the PROJECT
    # stack, not the tool default.
    fn = registry._tools["run_pytest"]
    assert apunta_a(fn.image, "php-phpunit")  # type: ignore[attr-defined]
    assert not apunta_a(fn.image, "python-pytest")  # type: ignore[attr-defined]


def test_tool_dispatch_falls_back_to_tool_default_without_project() -> None:
    from agent_runtime.tool_wiring import WiringContext, register_tool_specs
    from agent_runtime.tools import ToolRegistry
    from workers.test_runtime import resolve_run_runtime_image

    registry = ToolRegistry()
    ctx = WiringContext(
        project_default_runtime=None,
        runtime_image_resolver=resolve_run_runtime_image,
    )
    register_tool_specs(registry, [_run_tool_spec("run_pytest", "python-pytest")], ctx=ctx)
    fn = registry._tools["run_pytest"]
    assert apunta_a(fn.image, "python-pytest")  # type: ignore[attr-defined]


def test_tool_dispatch_unknown_runtime_skips_tool_not_the_run() -> None:
    """Contrato desde 602a24b: una spec malformada de tipo VÁLIDO se salta con
    warning — no tumba el run entero. El error temprano y claro del catálogo lo
    da el WORKER en dispatch (`_resolve_tool_spec_images` sí lanza, ver el test
    dispatch-side de abajo); aquí el runtime degrada por-tool."""
    from agent_runtime.tool_wiring import WiringContext, register_tool_specs
    from agent_runtime.tools import ToolRegistry
    from workers.test_runtime import resolve_run_runtime_image

    registry = ToolRegistry()
    ctx = WiringContext(
        project_default_runtime="totally-not-a-runtime",
        runtime_image_resolver=resolve_run_runtime_image,
    )
    registered = register_tool_specs(
        registry, [_run_tool_spec("run_pytest", "python-pytest")], ctx=ctx
    )
    assert registered == []
    assert "run_pytest" not in registry._tools


def test_dispatch_side_unknown_runtime_still_raises() -> None:
    """La garantía en la que se apoya el skip del runtime: el worker resuelve las
    imágenes ANTES de lanzar el contenedor y un runtime desconocido revienta el
    dispatch con un error claro, no un boot silenciosamente cojo."""
    from workers.execution import _resolve_tool_spec_images
    from workers.test_runtime import RuntimeResolutionError

    spec = {
        "implementation_type": "docker_command",
        "config": {
            "command_template": ["pytest", "{path}"],
            "runtime_template": "totally-not-a-runtime",
        },
    }
    with pytest.raises(RuntimeResolutionError, match="totally-not-a-runtime"):
        _resolve_tool_spec_images([spec], None)


def test_explicit_image_still_works_backward_compatible() -> None:
    # A Plan 05 docker_command tool with an explicit `image` is untouched.
    from agent_runtime.tool_wiring import ToolSpec, WiringContext, register_tool_specs
    from agent_runtime.tools import ToolRegistry

    registry = ToolRegistry()
    spec = ToolSpec(
        name="hello",
        implementation_type="docker_command",
        config={"image": "alpine:3.20", "command_template": ["echo", "hi"]},
    )
    # Even with a project stack set, an explicit image wins (it's not a run_* tool).
    ctx = WiringContext(project_default_runtime="php-phpunit")
    register_tool_specs(registry, [spec], ctx=ctx)
    assert registry._tools["hello"].image == "alpine:3.20"  # type: ignore[attr-defined]


def test_docker_command_without_image_or_resolver_skips_tool() -> None:
    """Mismo contrato 602a24b: sin `image` explícita ni resolver en el contexto,
    la tool se salta (warning) en vez de tumbar el boot del run."""
    from agent_runtime.tool_wiring import register_tool_specs
    from agent_runtime.tools import ToolRegistry

    registry = ToolRegistry()
    # No explicit image, a runtime_template but NO resolver in the context.
    registered = register_tool_specs(registry, [_run_tool_spec("run_pytest", "python-pytest")])
    assert registered == []
    assert "run_pytest" not in registry._tools


# ===========================================================================
# Layer 3 — spec threading: worker forwards default_runtime_template
# ===========================================================================
def test_agent_spec_forwards_default_runtime_template_when_set() -> None:
    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        default_runtime_template="php-phpunit",
    )
    spec = _agent_spec(req, None)
    assert spec["default_runtime_template"] == "php-phpunit"
    # Round-trips through the Celery payload.
    rebuilt = ExecutionRequest.from_dict(req.as_dict())
    assert rebuilt.default_runtime_template == "php-phpunit"


def test_agent_spec_omits_default_runtime_template_when_none() -> None:
    from workers.execution import ExecutionRequest, _agent_spec

    req = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=None,
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "scripted", "decisions": []},
        default_runtime_template=None,
    )
    # No stack pinned -> no key -> per-tool default runtime (backward-compatible).
    assert "default_runtime_template" not in _agent_spec(req, None)


# ===========================================================================
# Layer 4 — end to end against the real DB: persisted stack -> resolution
# ===========================================================================
async def _seed_project(dsn: str, *, runtime: str | None) -> UUID:
    tenant = uuid4()
    project = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            tenant,
            "Tenant RunByStack",
            f"tenant-runstack-{tenant.hex[:8]}",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name, default_runtime_template)"
            " VALUES ($1, $2, $3, $4)",
            project,
            tenant,
            "PHP project",
            runtime,
        )
    finally:
        await conn.close()
    return project


async def _read_runtime(dsn: str, project_id: UUID) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            "SELECT default_runtime_template FROM projects WHERE id = $1", project_id
        )
    finally:
        await conn.close()


def test_persisted_php_project_resolves_to_php_phpunit(
    alembic_config, migrations_pg_dsn: str
) -> None:
    from workers.test_runtime import resolve_run_runtime

    command.upgrade(alembic_config, "head")

    async def _go() -> str | None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE projects, organizations CASCADE")
        finally:
            await conn.close()
        project_id = await _seed_project(migrations_pg_dsn, runtime="php-phpunit")
        return await _read_runtime(migrations_pg_dsn, project_id)

    stored_runtime = asyncio.run(_go())
    assert stored_runtime == "php-phpunit"
    # The persisted stack resolves to the php-phpunit template — run_pytest
    # for this project executes in php-phpunit, not python-pytest.
    template = resolve_run_runtime(
        project_default_runtime=stored_runtime,
        tool_default_runtime="python-pytest",
    )
    assert template.id == "php-phpunit"


def test_persisted_project_without_stack_falls_back(alembic_config, migrations_pg_dsn: str) -> None:
    from workers.test_runtime import resolve_run_runtime

    command.upgrade(alembic_config, "head")

    async def _go() -> str | None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE projects, organizations CASCADE")
        finally:
            await conn.close()
        project_id = await _seed_project(migrations_pg_dsn, runtime=None)
        return await _read_runtime(migrations_pg_dsn, project_id)

    stored_runtime = asyncio.run(_go())
    assert stored_runtime is None
    # No stack pinned -> python-pytest (the run_pytest default) is kept.
    template = resolve_run_runtime(
        project_default_runtime=stored_runtime,
        tool_default_runtime="python-pytest",
    )
    assert template.id == "python-pytest"
