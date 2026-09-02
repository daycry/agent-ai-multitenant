"""ADR 0162 (decisión 1) — la raíz del proyecto llega a las bocas que deciden.

Medición sobre la instalación viva (180 ejecuciones): `stack_exec` **sin** `cwd`
sale verde el 46 % de las veces; **con** `cwd` correcto, el 77 %. Los motivos de
fallo dominantes son todos el mismo («Could not open input file: spark»,
«vendor/bin/phpunit: not found», «Composer could not find a composer.json file
in /workspace»): el proyecto vive en un subdirectorio del worktree y el comando
se lanza desde la raíz.

`_exec` ya aceptaba `cwd`, pero de las cuatro bocas que lo necesitan sólo lo
pasaba una, y no es de las que deciden: la que instala las dependencias
(`default_pre_install`) y la que ejecuta los tests (acceptance checks) corrían
siempre desde `/workspace`. Aquí se fija que ambas corren bajo `project_root`, y
que un `cwd` explícito del agente sigue ganando.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings

pytestmark = pytest.mark.integration


def _fake_client() -> tuple[MagicMock, list[Any]]:
    started: list[Any] = []

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.kwargs = kwargs
        c.exec_run = MagicMock(return_value=MagicMock(exit_code=0, output=b"ok\n"))
        started.append(c)
        return c

    client = MagicMock()
    client.containers.run.side_effect = _run
    client.networks.create.return_value = MagicMock(remove=MagicMock())
    client.networks.create.return_value.name = "test-runtime-php-phpunit-abcd"
    return client, started


def _spec(**overrides: Any) -> Any:
    from shared_test_runtimes.catalog import get
    from workers.test_runtime import AcceptanceCheck, RuntimePlan, TestRuntimeSpec

    plan = RuntimePlan(
        template=get("php-phpunit"),
        checks=(
            AcceptanceCheck(
                id="a",
                description="suite",
                runtime="php-phpunit",
                command="vendor/bin/phpunit",
            ),
        ),
    )
    base: dict[str, Any] = {
        "plan": plan,
        "worktree_host_path": "/data/projects/t1/p1/worktrees/task-1",
    }
    base.update(overrides)
    return TestRuntimeSpec(**base)


def _commands(container: Any) -> list[str]:
    """Los comandos tal y como llegan al contenedor (`timeout N sh -c '…'`)."""
    return [call.args[0][-1] for call in container.exec_run.call_args_list]


def test_pre_install_runs_under_project_root() -> None:
    """La boca que instala las dependencias: sin esto, `composer install` corre
    en `/workspace` y contesta «could not find a composer.json file»."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    TestRuntimeRunner(Settings(), client=client).launch(_spec(project_root="ci4build"))

    pre_install = _commands(started[0])[0]
    assert "cd ci4build && composer install" in pre_install


def test_acceptance_checks_run_under_project_root() -> None:
    """La boca que ejecuta los tests. Corría siempre desde la raíz del worktree,
    hiciera lo que hiciera el agente."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    TestRuntimeRunner(Settings(), client=client).launch(_spec(project_root="ci4build"))

    check = _commands(started[0])[-1]
    assert check.endswith("cd ci4build && vendor/bin/phpunit'")


def test_without_project_root_nothing_changes() -> None:
    """No-regresión: los proyectos que hoy funcionan viven en la raíz y no
    declaran nada. Ni un `cd` de más en el comando."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    TestRuntimeRunner(Settings(), client=client).launch(_spec())

    for cmd in _commands(started[0]):
        assert "cd " not in cmd
    assert _commands(started[0])[-1].endswith("sh -c 'vendor/bin/phpunit'")


def test_an_empty_project_root_is_the_worktree_root() -> None:
    """`""` significa la raíz, igual que ausente — el operador tiene que poder
    vaciar el campo desde la UI y recuperar el comportamiento de siempre."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    TestRuntimeRunner(Settings(), client=client).launch(_spec(project_root=""))

    for cmd in _commands(started[0]):
        assert "cd " not in cmd


def test_explicit_cwd_wins_over_project_root() -> None:
    """Precedencia: la petición del agente es más concreta que la configuración
    del proyecto (puede estar tocando otro subproyecto del monorepo)."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.run_command(_spec(project_root="ci4build"), "php spark migrate", cwd="apps/api")

    assert _commands(started[0]) == ["timeout -k 10 600 sh -c 'cd apps/api && php spark migrate'"]


def test_stack_exec_falls_back_to_project_root_when_the_agent_says_nothing() -> None:
    """El 46 % medido: el agente no pasa `cwd` (ni sabe que existe). Antes eso
    era la raíz del worktree; ahora es la raíz declarada del proyecto."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.run_command(_spec(project_root="ci4build"), "php spark migrate")

    assert _commands(started[0]) == ["timeout -k 10 600 sh -c 'cd ci4build && php spark migrate'"]


def test_stack_exec_without_either_is_unchanged() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.run_command(_spec(), "php spark migrate")

    assert _commands(started[0]) == ["timeout -k 10 600 sh -c 'php spark migrate'"]


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_a_blank_explicit_cwd_is_not_a_decision(blank: str | None) -> None:
    """Un `cwd` vacío no es «corre en la raíz»: es no haber dicho nada. Si
    ganase, el agente que omite el parámetro tiraría por tierra la
    configuración del proyecto — que es el defecto que esto viene a cerrar."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _fake_client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.run_command(_spec(project_root="ci4build"), "php spark", cwd=blank)

    assert "cd ci4build && php spark" in _commands(started[0])[0]
