"""El test-runtime deja de contaminar el worktree con su HOME (task_wf_20, C-01).

El contenedor de test/stack arrancaba con ``HOME=/workspace`` — el worktree
bind-montado en RW. Cualquier fichero que la toolchain escriba «en el home»
(``~/.composer/auth.json``, ``~/.npmrc``, ``~/.cache/…``) aterrizaba dentro del
repo del proyecto, y `commit_task` hace ``git add -A``: acaba comiteado.

Es el MISMO bug que ya se corrigió en el agent-runtime (donde el CLI de Claude
escribía su `.claude.json` en el worktree y luego se lo releía como contexto), y
contradice tres cosas a la vez: el comentario de tres líneas más abajo en el
propio código («never clobber HOME»), las imágenes de runtime — que declaran
`ENV HOME=/home/agent` con el directorio creado y `chown 1000:1000` — y la nota
F4 de `registry-egress-followups.md`, que da por hecho ese home escribible.

Con la raíz en solo lectura, poner `HOME=/home/agent` obliga además a montarle su
tmpfs: sin él la toolchain se comería un EROFS en vez de escribir donde toca.
"""

from __future__ import annotations

from typing import Any

import pytest
from shared_test_runtimes import catalog
from workers import test_runtime
from workers.config import Settings
from workers.test_runtime import RuntimePlan

pytestmark = pytest.mark.unit

_AGENT_HOME = "/home/agent"


def _kwargs(
    *,
    template_id: str = "php-phpunit",
    dep_cache: str | None = None,
    main_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    # Referenciados por módulo: importar `TestRuntimeRunner`/`TestRuntimeSpec` al
    # namespace del test hace que pytest intente recogerlos como clases de test
    # por su prefijo `Test` y emita un warning de colección en cada arranque.
    settings = Settings()
    runner = test_runtime.TestRuntimeRunner(settings)
    spec = test_runtime.TestRuntimeSpec(
        plan=RuntimePlan(template=catalog.get(template_id), checks=()),
        worktree_host_path="/data/worktrees/t1",
        dep_cache_host_path=dep_cache,
        main_env=main_env or {},
    )
    return runner._build_test_kwargs(spec, "bridge-test")


# ---------------------------------------------------------------------------
# El bug
# ---------------------------------------------------------------------------
def test_home_is_not_the_worktree() -> None:
    """La regresión, dicha en una línea."""
    kwargs = _kwargs()
    assert kwargs["environment"]["HOME"] != "/workspace"


def test_home_is_what_the_image_declares() -> None:
    """Las imágenes hacen `mkdir -p /home/agent && chown 1000:1000` y
    `ENV HOME=/home/agent`. El worker las estaba deshaciendo."""
    assert _kwargs()["environment"]["HOME"] == _AGENT_HOME


def test_home_has_its_own_tmpfs() -> None:
    """La raíz va en solo lectura: sin tmpfs, escribir en el home sería EROFS."""
    tmpfs = _kwargs()["tmpfs"]
    assert _AGENT_HOME in tmpfs


def test_the_home_tmpfs_is_writable_by_the_container_user() -> None:
    """El contenedor corre como 1000:1000; un tmpfs de root no le sirve."""
    options = _kwargs()["tmpfs"][_AGENT_HOME]
    assert "uid=1000" in options
    assert "gid=1000" in options
    assert "nosuid" in options


def test_the_home_tmpfs_is_not_noexec() -> None:
    """Las toolchains ejecutan binarios desde su caché de home (``~/.composer/
    vendor/bin``, npx). `noexec` ahí rompería instalaciones legítimas — mismo
    criterio que el `/workspace` del agent-runtime."""
    assert "noexec" not in _kwargs()["tmpfs"][_AGENT_HOME]


# ---------------------------------------------------------------------------
# Lo que NO puede romperse
# ---------------------------------------------------------------------------
def test_the_dep_cache_bind_still_wins_over_the_tmpfs() -> None:
    """Todas las plantillas apuntan su `dep_cache_mount` DENTRO de `/home/agent`
    (`~/.composer/cache`, `~/.npm`, `~/.m2/repository`…). El bind tiene que
    seguir montándose encima del tmpfs, o cada run reinstalaría en frío."""
    kwargs = _kwargs(dep_cache="/data/cache/composer")
    targets = [m["Target"] for m in kwargs["mounts"]]
    cache_target = catalog.get("php-phpunit").dep_cache_mount
    assert cache_target is not None
    assert cache_target.startswith(_AGENT_HOME)
    assert cache_target in targets


def test_the_cache_env_still_points_under_home() -> None:
    """`cache_env` del catálogo ya usaba rutas bajo `/home/agent`; con HOME mal
    puesto apuntaban a un sitio que no era el home del proceso."""
    env = _kwargs()["environment"]
    assert env["COMPOSER_CACHE_DIR"].startswith(_AGENT_HOME)


def test_the_project_env_still_cannot_clobber_home() -> None:
    """ADR 0129: el env del proyecto se aplica al final y gana… salvo HOME."""
    env = _kwargs(main_env={"HOME": "/workspace", "DATABASE_URL": "postgres://x"})["environment"]
    assert env["HOME"] == _AGENT_HOME
    assert env["DATABASE_URL"] == "postgres://x"


def test_the_worktree_is_still_mounted_read_write() -> None:
    kwargs = _kwargs()
    worktree = next(m for m in kwargs["mounts"] if m["Target"] == "/workspace")
    assert worktree["ReadOnly"] is False


@pytest.mark.parametrize("template_id", ["python-pytest", "node-jest", "go-test", "java-maven"])
def test_every_stack_gets_the_same_treatment(template_id: str) -> None:
    kwargs = _kwargs(template_id=template_id)
    assert kwargs["environment"]["HOME"] == _AGENT_HOME
    assert _AGENT_HOME in kwargs["tmpfs"]
