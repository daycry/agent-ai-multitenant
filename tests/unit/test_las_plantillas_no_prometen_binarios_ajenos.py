"""Una plantilla de proyecto no promete comandos que su runtime no trae
(`task_cv_36`, auditoría 2026-09-01 F-08).

`devops-bootstrap` prometía `docker/terraform/ansible` sobre `python-pytest`;
`webapp` prometía `node/npm/npx` y `legacy-migration` prometía `php/composer/
phpunit` sobre el mismo runtime de Python. Cada uno de esos comandos entra en la
allowlist, el agente lo invoca por `stack_exec`, recibe «not found» del sistema
operativo y quema iteraciones buscándolo. Ahora cada runtime del catálogo
declara las toolchains que su imagen trae (`RuntimeTemplate.toolchains`) y este
test cruza las dos listas; las plantillas built-in quedan corregidas.
"""

from __future__ import annotations

import pytest
from shared_test_runtimes import CATALOG, get
from shared_test_runtimes.types import TOOLCHAIN_COMMANDS, foreign_commands

pytestmark = pytest.mark.unit


def test_foreign_commands_is_the_pure_rule() -> None:
    assert foreign_commands(("python", "pip", "docker", "terraform"), frozenset({"python"})) == [
        "docker",
        "terraform",
    ]
    assert foreign_commands(("php", "composer", "phpunit", "spark"), frozenset({"php"})) == []
    # lo que no pertenece a ninguna toolchain conocida (utilidades genéricas) no es ajeno
    assert foreign_commands(("ls", "grep", "make"), frozenset()) == []


def test_every_toolchain_command_belongs_to_exactly_one_toolchain() -> None:
    seen: dict[str, str] = {}
    for toolchain, commands in TOOLCHAIN_COMMANDS.items():
        for command in commands:
            assert command not in seen, f"{command!r} está en {seen[command]} y en {toolchain}"
            seen[command] = toolchain


def test_every_non_generic_runtime_declares_its_toolchains() -> None:
    templates = CATALOG.values() if isinstance(CATALOG, dict) else CATALOG
    for template in templates:
        if template.id.startswith("generic-"):
            assert template.toolchains == frozenset(), template.id
            continue
        assert template.toolchains, f"{template.id} no declara qué toolchain trae"
        assert template.toolchains <= set(TOOLCHAIN_COMMANDS), template.id


def _builtin_project_templates() -> list:  # type: ignore[type-arg]
    from api_server.seeds.builtin_project_templates import BUILTIN_PROJECT_TEMPLATES
    from api_server.seeds.ci4_team import CI4_PROJECT_TEMPLATE

    return [*BUILTIN_PROJECT_TEMPLATES, CI4_PROJECT_TEMPLATE]


def test_no_builtin_project_template_promises_another_stacks_toolchain() -> None:
    offenders: list[tuple[str, list[str]]] = []
    for tpl in _builtin_project_templates():
        if not tpl.default_runtime_template:
            continue
        runtime = get(tpl.default_runtime_template)
        foreign = foreign_commands(tpl.allowed_commands, runtime.toolchains)
        if foreign:
            offenders.append((tpl.slug, foreign))
    assert not offenders, (
        "plantillas que prometen comandos que su runtime no trae "
        f"(el agente recibirá «not found» y quemará iteraciones): {offenders}"
    )
