"""Un reintento no reinstala las dependencias desde cero (task_wf_24, C-06).

`sync_to_head` hacía `git clean -fdx` antes de cada run. El `-x` incluye los
ficheros IGNORADOS, así que se llevaba por delante `vendor/`, `node_modules/`,
`.venv/`… El `-x` está ahí por una razón buena — que el agente arranque de un
estado determinista, sin artefactos de una ejecución anterior —, pero el precio
es que **cada reintento reinstala en frío**: minutos de reloj, egress por el
proxy allowlisted y, si el registro está caído, un fallo que no tiene nada que
ver con la tarea.

Las dos cosas son compatibles: los directorios de dependencias se preservan y
todo lo demás sigue barriéndose. Los nombres NO son una convención global
inventada aquí: los declara cada plantilla de runtime, que es quien sabe cuáles
son los suyos.
"""

from __future__ import annotations

import pytest
from shared_test_runtimes import catalog

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Los nombres los declara la plantilla
# ---------------------------------------------------------------------------
def test_each_stack_declares_its_own_dependency_dirs() -> None:
    assert "vendor" in catalog.get("php-phpunit").dependency_dirs
    assert "node_modules" in catalog.get("node-jest").dependency_dirs


def test_a_build_output_is_not_a_dependency_dir() -> None:
    """`target/` (maven) o `dist/` son ARTEFACTOS: preservarlos devolvería el
    problema que el `-x` resolvía, que es lo que hay que evitar."""
    for template_id in catalog.list_ids():
        dirs = catalog.get(template_id).dependency_dirs
        assert "target" not in dirs
        assert "dist" not in dirs
        assert "build" not in dirs


def test_the_union_is_what_a_monorepo_needs() -> None:
    """Un worktree puede tener varios stacks a la vez (backend PHP + frontend
    node es el caso común). Preservar solo los de UNA plantilla seguiría
    arrasando los de la otra."""
    union = catalog.dependency_dirs()
    assert {"vendor", "node_modules"} <= set(union)


def test_the_union_is_deduplicated_and_deterministic() -> None:
    assert list(catalog.dependency_dirs()) == sorted(set(catalog.dependency_dirs()))


# ---------------------------------------------------------------------------
# El comando que se ejecuta
# ---------------------------------------------------------------------------
def test_clean_excludes_the_preserved_dirs() -> None:
    from workers.git_repos import clean_args

    args = clean_args(["vendor", "node_modules"])
    assert args[:2] == ("clean", "-fdx")
    assert "-e" in args
    assert "vendor" in args
    assert "node_modules" in args


def test_without_preservation_the_command_is_the_old_one() -> None:
    """Regresión: sin nada que preservar el comportamiento no cambia."""
    from workers.git_repos import clean_args

    assert clean_args([]) == ("clean", "-fdx")


def test_the_x_flag_survives() -> None:
    """Quitar el `-x` entero habría sido el arreglo fácil y el equivocado: los
    artefactos ignorados de un run anterior volverían a contaminar el siguiente."""
    from workers.git_repos import clean_args

    assert "-fdx" in clean_args(["vendor"])


def test_a_preserved_name_cannot_smuggle_an_option() -> None:
    """Los nombres vienen del catálogo, pero pasarlos sin validar a la línea de
    comandos de git es la clase de hueco que no se deja abierta."""
    from workers.git_repos import clean_args

    with pytest.raises(ValueError):
        clean_args(["--force"])


@pytest.mark.parametrize("bad", ["", "   ", "../escape", "/absolute"])
def test_a_nonsense_name_is_rejected(bad: str) -> None:
    from workers.git_repos import clean_args

    with pytest.raises(ValueError):
        clean_args([bad])
