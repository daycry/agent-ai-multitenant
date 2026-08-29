"""ADR 0162 (decisión 1) — `repository_config.project_root` se valida en el borde.

Un proyecto puede no vivir en la raíz de su worktree, sino en un subdirectorio
(el caso medido: `ci4build/`, que además creó el propio agente a mitad de plan).
Esa raíz se declara en `repository_config.project_root` como ruta **relativa** a
la raíz del worktree, y de ahí acaba concatenada dentro de un `sh -c` en el
worker (`workers.test_runtime._apply_cwd`).

Por eso se valida aquí y no sólo allí: lo que el worker rechazaría en tiempo de
run tiene que dar 422 en la puerta. Si no, el operador guarda un valor, la API
responde 200, y el fallo aparece —si aparece— dentro de un run, en un log que
nadie mira. Es el mismo modo de fallo que ya se cerró con `execution_budgets`.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _create(**kwargs: Any) -> Any:
    from api_server.schemas.projects import ProjectCreateRequest

    return ProjectCreateRequest(name="p", **kwargs)


def _update(**kwargs: Any) -> Any:
    from api_server.schemas.projects import ProjectUpdateRequest

    return ProjectUpdateRequest(**kwargs)


# Valores que el operador puede escribir legítimamente.
_ACCEPTED = ["ci4build", "packages/api", "apps/web-2", "src/main_app", "a.b/c-d"]

# Valores que el worker rechazaría (o reescribiría) en tiempo de run.
_REJECTED_TRAVERSAL = ["..", "../x", "a/../b", "ci4build/..", "a/..", "./x", "a//b", "."]
_REJECTED_ABSOLUTE = ["/ci4build", "/", "/etc/passwd", "C:/proyecto"]
_REJECTED_CHARS = ["foo; rm -rf /", "a b", "x$(id)", "a|b", "a&b", "a`b`", "a' && rm -rf / #"]


@pytest.mark.parametrize("good", _ACCEPTED)
def test_a_relative_subdirectory_is_accepted(good: str) -> None:
    assert _update(repository_config={"project_root": good}).repository_config == {
        "project_root": good
    }
    assert _create(repository_config={"project_root": good}).repository_config == {
        "project_root": good
    }


def test_absent_or_empty_project_root_is_the_worktree_root() -> None:
    """Ausente o vacío = la raíz. Es el contrato de no-regresión: los proyectos
    que hoy funcionan no declaran nada y deben seguir sin declarar nada."""
    assert _update(repository_config={}).repository_config == {}
    assert _update(repository_config={"language": "php"}).repository_config == {"language": "php"}
    assert _update(repository_config={"project_root": ""}).repository_config == {"project_root": ""}
    assert _update(repository_config={"project_root": None}).repository_config == {
        "project_root": None
    }


@pytest.mark.parametrize("bad", _REJECTED_TRAVERSAL)
def test_traversal_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="project_root"):
        _update(repository_config={"project_root": bad})


@pytest.mark.parametrize("bad", _REJECTED_ABSOLUTE)
def test_an_absolute_path_is_rejected(bad: str) -> None:
    """El worker se limita a quitarle la barra inicial (`/etc/` → `etc`). Guardar
    algo distinto de lo que el operador escribió es peor que un 422: el valor de
    la UI y el que se ejecuta dejan de ser el mismo."""
    with pytest.raises(ValueError, match="project_root"):
        _update(repository_config={"project_root": bad})


@pytest.mark.parametrize("bad", _REJECTED_CHARS)
def test_characters_outside_the_worker_charset_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="project_root"):
        _update(repository_config={"project_root": bad})


def test_a_non_string_project_root_is_rejected() -> None:
    with pytest.raises(ValueError, match="project_root"):
        _update(repository_config={"project_root": 3})


def test_the_create_endpoint_validates_it_too() -> None:
    """Un proyecto se puede crear con `repository_config` de una plantilla; si
    sólo validase el PUT, la puerta estaría abierta por el otro lado."""
    with pytest.raises(ValueError, match="project_root"):
        _create(repository_config={"project_root": "../fuera"})


def test_trailing_slash_and_whitespace_are_normalised() -> None:
    """`ci4build/` es lo que teclea cualquiera. Se guarda canónico para que el
    valor almacenado sea EXACTAMENTE el que se concatena en el `sh -c`."""
    assert _update(repository_config={"project_root": "  ci4build/  "}).repository_config == {
        "project_root": "ci4build"
    }


def test_the_edge_validator_mirrors_the_worker_validator() -> None:
    """La razón de ser de este test: dos validadores que se separan son peores
    que uno solo. Todo lo que la API acepta lo tiene que aceptar `_apply_cwd`, y
    todo lo que la API rechaza por traversal o por caracteres lo rechaza él."""
    from workers.test_runtime import InvalidCwdError, _apply_cwd

    for good in _ACCEPTED:
        assert _apply_cwd("php spark", good) == f"cd {good} && php spark"
    for bad in _REJECTED_TRAVERSAL + _REJECTED_CHARS:
        with pytest.raises(InvalidCwdError):
            _apply_cwd("php spark", bad)


def test_project_root_is_not_a_platform_key() -> None:
    """El dato es DEL OPERADOR (ADR 0162). Las claves de
    `_REPOSITORY_CONFIG_PLATFORM_KEYS` se re-inyectan cuando el payload no las
    trae, o sea que el operador no puede vaciarlas nunca; meter `project_root`
    ahí le quitaría la única forma de decir «el proyecto está en la raíz»."""
    from api_server.routers.projects import _REPOSITORY_CONFIG_PLATFORM_KEYS

    assert "project_root" not in _REPOSITORY_CONFIG_PLATFORM_KEYS
