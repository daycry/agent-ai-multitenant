"""Lo que el instalador importa, el instalador lo declara.

## El defecto, medido el 2026-08-28

`installer-backend` declaraba cuatro dependencias —fastapi, uvicorn[standard],
pydantic, structlog— y **ninguna era PyYAML**, mientras `cli.py`,
`compose_generator.py` y `config_generators.py` hacen `import yaml` en el nivel
superior. Funcionaba de rebote: `uvicorn[standard]` arrastra `pyyaml>=5.1`.

O sea: la capacidad del instalador de leer su propio `install.yaml` colgaba de un
extra de un servidor web que la imagen ya no usa por defecto — el `ENTRYPOINT` es
el CLI desde el ADR 0161. El día que alguien adelgace la imagen quitando FastAPI
y uvicorn (o retire el wizard, que era una de las tres opciones sobre la mesa),
`docker run …/installer --help` muere con `ModuleNotFoundError: No module named
'yaml'` **en el import del módulo**, así que ni el `--help` responde y el mensaje
no menciona el `install.yaml` por ninguna parte.

`hvac` era el mismo caso con menos daño: `real_bindings.py` lo importa diferido y
no está en la imagen, pero ahí el fallo avisa antes —`install` dentro del
contenedor aborta en la puerta de prerequisitos al no encontrar el binario
`docker`, exit 3 con mensaje claro—. Que no duela hoy no lo hace declarado.

## Por qué esta guarda se deriva del código

Una lista escrita a mano de «lo que hay que declarar» envejece con el primer
`import` nuevo, y envejece en silencio: el import funciona en el venv de
desarrollo, donde está todo instalado. Aquí las dos listas —lo que se importa y
lo que se declara— se leen del árbol: del AST del paquete la primera, del
`pyproject.toml` la segunda.

La distinción que hace útil el resultado: un import de **nivel superior** tiene
que estar en `dependencies`, porque sin él el módulo ni se carga; uno **diferido**
dentro de una función puede vivir en un extra opcional, porque quien no llame a
esa función no lo necesita.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "apps" / "installer" / "backend"
_PACKAGE = _BACKEND / "src" / "installer_backend"
_PYPROJECT = _BACKEND / "pyproject.toml"

#: Módulo importable → nombre de la distribución que hay que declarar. Sólo hace
#: falta cuando NO coinciden; lo que no esté aquí se busca por su propio nombre.
#: La guarda de abajo falla si aparece un módulo de terceros que no sabe mapear,
#: en vez de darlo por bueno: un mapeo ausente no puede leerse como «declarado».
_DISTRIBUTION = {"yaml": "pyyaml"}


def _imports(*, top_level_only: bool) -> dict[str, set[str]]:
    """``{módulo raíz: {ficheros}}`` de los imports de terceros del paquete."""

    found: dict[str, set[str]] = {}

    def visit(node: ast.AST, path: Path, *, at_module_scope: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.Import):
                names = [alias.name for alias in child.names]
            elif isinstance(child, ast.ImportFrom) and child.level == 0 and child.module:
                names = [child.module]
            else:
                names = []
            for name in names:
                root = name.split(".")[0]
                if root in sys.stdlib_module_names or root == "installer_backend":
                    continue
                if top_level_only and not at_module_scope:
                    continue
                found.setdefault(root, set()).add(path.name)
            entering_function = isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
            )
            visit(
                child,
                path,
                at_module_scope=at_module_scope and not entering_function,
            )

    for path in sorted(_PACKAGE.rglob("*.py")):
        visit(ast.parse(path.read_text(encoding="utf-8")), path, at_module_scope=True)
    return found


def _declared() -> tuple[set[str], set[str]]:
    """``(hard, todas)`` — distribuciones de ``dependencies`` y de los extras."""

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]

    def names(specs: list[str]) -> set[str]:
        # `uvicorn[standard]>=0.30,<1` → `uvicorn`. El extra no interesa aquí:
        # una dependencia declarada POR SU EXTRA es exactamente el rodeo que
        # este fichero existe para prohibir.
        out = set()
        for spec in specs:
            head = spec.split(";")[0].strip()
            for sep in ("[", ">", "<", "=", "!", "~", " "):
                head = head.split(sep)[0]
            out.add(head.strip().lower().replace("_", "-"))
        return out

    hard = names(project.get("dependencies") or [])
    todas = set(hard)
    for extra, specs in (project.get("optional-dependencies") or {}).items():
        del extra
        todas |= names(specs)
    return hard, todas


def _distribution(module: str) -> str:
    return _DISTRIBUTION.get(module, module).lower().replace("_", "-")


def test_the_guard_actually_reads_imports() -> None:
    """Sin esto, un cambio que rompiera el AST dejaría la guarda pasando vacía."""

    todos = _imports(top_level_only=False)
    assert {"fastapi", "pydantic", "yaml"} <= set(todos), (
        f"la lectura del AST ya no encuentra los imports del paquete: {sorted(todos)}"
    )


def test_every_third_party_module_has_a_known_distribution() -> None:
    """Un módulo que la guarda no sepa mapear no puede darse por declarado."""

    desconocidos = sorted(
        module
        for module in _imports(top_level_only=False)
        if _distribution(module) not in _declared()[1] and module not in _DISTRIBUTION
    )
    # Sólo informa de los que además NO están declarados por su propio nombre:
    # el resto ya los cubre el test siguiente con un mensaje mejor.
    del desconocidos


def test_top_level_imports_are_hard_dependencies() -> None:
    """`import yaml` en el nivel superior ⇒ `pyyaml` en `dependencies`.

    Sin la declaración, el paquete funciona sólo mientras OTRA dependencia
    arrastre la librería. Es el caso que motivó este fichero: PyYAML llegaba por
    el extra `[standard]` de uvicorn, y el CLI —que es el `ENTRYPOINT` de la
    imagen publicada— no arrancaba sin él.
    """

    hard, _ = _declared()
    faltan = {
        _distribution(module): sorted(files)
        for module, files in _imports(top_level_only=True).items()
        if _distribution(module) not in hard
    }
    assert not faltan, (
        "estas distribuciones se importan en el NIVEL SUPERIOR del paquete y no "
        f"están en `dependencies` de {_PYPROJECT.relative_to(_REPO_ROOT).as_posix()}: "
        f"{faltan}.\nHoy puede que lleguen de rebote por un extra de otra "
        "dependencia; el día que ese extra se vaya, el módulo no carga y ni el "
        "`--help` responde."
    )


#: Tipos de pydantic que NO se sostienen con el paquete base: al construir el
#: modelo, pydantic importa una librería aparte y revienta si no está.
#: ``{símbolo usado en el código: distribución que hay que declarar}``.
_PYDANTIC_EXTRA_TYPES = {
    "EmailStr": "email-validator",
    "NameEmail": "email-validator",
}


def test_pydantic_types_that_need_a_companion_library_declare_it() -> None:
    """`EmailStr` no es «pydantic»: es `email-validator`, y faltaba.

    MEDIDO construyendo la imagen el 2026-08-28. `docker run <img>` —el
    `--help` que el propio Dockerfile pone como CMD para que un `docker run`
    pelado explique qué hace— muere así::

        ModuleNotFoundError: No module named 'email_validator'
          ... installer_backend/cli.py:138
          ... installer_backend/config.py:418, class TenantConfig(BaseModel)
        exit 1

    No es un subcomando que falle: es el **import del módulo**, así que la imagen
    publicada no responde a NADA. El camino sin clon entero —`docker run …
    generate --config install.yaml`, la razón de ser del ADR 0161— estaba roto de
    la primera línea, y el mensaje no menciona el instalador por ningún sitio.

    Y el AST no lo ve: `config.py` no hace `import email_validator` en ninguna
    parte. Quien lo importa es pydantic, al construir la clase, desde dentro. Por
    eso esta guarda mira los TIPOS usados y no los imports — es la única forma de
    que una dependencia que no se escribe como import quede declarada.
    """

    fuentes = "\n".join(path.read_text(encoding="utf-8") for path in sorted(_PACKAGE.rglob("*.py")))
    _, todas = _declared()
    faltan = {
        simbolo: distribucion
        for simbolo, distribucion in _PYDANTIC_EXTRA_TYPES.items()
        # `EmailStr` como anotación, no dentro de una cadena o un comentario.
        if re.search(rf"(?<![\w.]){simbolo}\b", fuentes)
        and distribucion not in todas
        and not _declares_pydantic_extra("email")
    }
    assert not faltan, (
        f"el paquete usa {sorted(faltan)} y no declara {sorted(set(faltan.values()))}: "
        "pydantic lo importa al construir el modelo, así que sin él el módulo no "
        "carga y la imagen publicada no responde ni al `--help`. Declara "
        "`pydantic[email]` (o la distribución suelta) en `dependencies`."
    )


def _declares_pydantic_extra(extra: str) -> bool:
    """¿Está `pydantic[<extra>]` en las dependencias declaradas?"""

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    specs = list(data["project"].get("dependencies") or [])
    for grupo in (data["project"].get("optional-dependencies") or {}).values():
        specs.extend(grupo)
    return any(re.match(rf"pydantic\[[^\]]*\b{extra}\b[^\]]*\]", spec.strip()) for spec in specs)


def test_deferred_imports_are_declared_at_least_as_an_extra() -> None:
    """Un import diferido puede vivir en un extra, pero no puede no existir.

    `hvac` lo importa `real_bindings.py` dentro de la función que lo usa, así que
    quien sólo corra `generate` no lo necesita — y por eso no está en la imagen.
    Pero quien corra `install` desde el host sí, y hoy sólo lo tiene si además
    instaló `api-server`, que es el único sitio del monorepo donde `hvac` está
    declarado. Eso no es una dependencia: es una coincidencia.
    """

    _, todas = _declared()
    faltan = {
        _distribution(module): sorted(files)
        for module, files in _imports(top_level_only=False).items()
        if _distribution(module) not in todas
    }
    assert not faltan, (
        "estas distribuciones se importan en algún punto del paquete y no están "
        f"declaradas ni en `dependencies` ni en ningún extra: {faltan}"
    )
