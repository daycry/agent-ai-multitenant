"""PROJ-01/P1-05 (auditoría proyecto 2026-07-17): las plantillas builtin traen
allowlist de comandos + runtime template coherentes con su stack.

Sin esto, adoptar una plantilla producía proyectos con `allowed_commands=[]`
(stack_exec deny-all: el agente no puede correr ni su test runner) y sin
`default_runtime_template`.
"""

from __future__ import annotations

import pytest
from api_server.seeds.builtin_project_templates import BUILTIN_PROJECT_TEMPLATES
from api_server.seeds.ci4_team import CI4_PROJECT_TEMPLATE

pytestmark = pytest.mark.unit

_BY_SLUG = {tpl.slug: tpl for tpl in BUILTIN_PROJECT_TEMPLATES}


def test_ci4_template_carries_php_toolchain() -> None:
    tpl = CI4_PROJECT_TEMPLATE
    assert "composer" in tpl.allowed_commands
    assert "php" in tpl.allowed_commands
    assert "phpunit" in tpl.allowed_commands
    assert tpl.default_runtime_template == "php-phpunit"


def test_api_rest_template_carries_python_toolchain() -> None:
    tpl = _BY_SLUG["api-rest"]
    assert "pytest" in tpl.allowed_commands
    assert "pip" in tpl.allowed_commands
    assert tpl.default_runtime_template == "python-pytest"


def test_webapp_template_carries_both_stacks() -> None:
    tpl = _BY_SLUG["webapp"]
    assert "pytest" in tpl.allowed_commands
    assert "npm" in tpl.allowed_commands


def test_e2e_template_carries_node_toolchain() -> None:
    tpl = _BY_SLUG["e2e-test-suite"]
    assert "npm" in tpl.allowed_commands
    assert "npx" in tpl.allowed_commands
    assert tpl.default_runtime_template == "node-playwright"


def test_templates_seed_only_worker_config_keys_with_readers() -> None:
    """P1-04: `worker_config.{min,max}_workers/cpu/ram` no tiene NINGÚN lector
    (solo assignment_policy y git_policies se leen) — sembrarlo vendía una
    configuración que no hace nada. Las plantillas solo siembran claves vivas."""
    read_keys = {"assignment_policy", "git_policies"}
    for tpl in (*BUILTIN_PROJECT_TEMPLATES, CI4_PROJECT_TEMPLATE):
        dead = set(tpl.worker_config) - read_keys
        assert not dead, f"{tpl.slug} siembra claves muertas de worker_config: {sorted(dead)}"


def test_every_code_template_has_a_runtime() -> None:
    """Toda plantilla con repositorio de código declara runtime + comandos.
    (research-spec y doc-modernization no ejecutan stacks: quedan exentas.)"""
    exempt = {"research-spec", "doc-modernization"}
    for tpl in BUILTIN_PROJECT_TEMPLATES:
        if tpl.slug in exempt:
            continue
        assert tpl.allowed_commands, f"{tpl.slug} sin allowed_commands"
        assert tpl.default_runtime_template, f"{tpl.slug} sin default_runtime_template"
