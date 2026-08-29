"""ADR 0162: el `project_root` llega al spec que construye la FASE DE TESTS.

Por qué este fichero existe aparte de `tests/integration/test_project_root_reaches_exec.py`:
aquéllos construyen el `TestRuntimeSpec` a mano y demuestran que el *runner*
honra `project_root` — que es verdad y hace falta. Lo que NO demuestran es que
alguien se lo ponga.

Y no se lo ponía. Hay exactamente dos `TestRuntimeSpec(...)` en producción:
`stack_exec_task.py` (la vía del agente, la única de las cuatro bocas que ya
funcionaba) y `test_runtime_task.py` (la fase de tests, que es la que decide si
una tarea se da por buena). El cableado llegó al primero y no al segundo, así que
`default_pre_install` y los acceptance checks seguían corriendo desde la raíz del
worktree — exactamente el defecto que el ADR 0162 mide.

Este test se ancla en el sitio donde el spec NACE, no donde se consume: es la
única posición desde la que un test puede caerse si alguien vuelve a olvidar el
parámetro.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.unit]


def _request(repository_config: Any) -> dict[str, Any]:
    """Una request de fase de tests con UN criterio ejecutable.

    El criterio tiene que llevar `runtime` y `command` o no se construye ningún
    plan y el bucle no llega nunca al `TestRuntimeSpec` — que es justo lo que el
    ADR 0162 documenta como causa de que la fase no se dispare.
    """
    return {
        "task_id": "00000000-0000-0000-0000-000000000001",
        "tenant_id": "00000000-0000-0000-0000-000000000002",
        "worktree_host_path": "/data/agent-platform/worktrees/t1",
        "repository_config": repository_config,
        "acceptance_criteria": [
            {
                "id": "c1",
                "description": "los tests pasan",
                "runtime": "php-phpunit",
                "command": "vendor/bin/phpunit",
            }
        ],
    }


def _capture_spec(request: dict[str, Any]) -> Any:
    """Corre la fase de tests con el runner mockeado y devuelve el spec construido."""
    import asyncio

    import workers.test_runtime as test_runtime_mod
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    runner = MagicMock()
    runner.launch.return_value = MagicMock(
        outcomes=[], status="passed", logs="", to_dict=lambda: {}
    )

    # `TestRuntimeRunner` se importa DENTRO de la función (import diferido), así
    # que parchear el módulo de la tarea no serviría: hay que parchearlo en su
    # módulo de origen, que es de donde el import diferido lo resuelve.
    with patch.object(test_runtime_mod, "TestRuntimeRunner", return_value=runner):
        asyncio.run(test_runtime_task._launch_test_runtime_plans(request, Settings()))

    assert runner.launch.call_args is not None, (
        "el runner no llegó a lanzarse: el criterio de prueba dejó de construir un "
        "plan, así que este test ya no mide lo que dice medir"
    )
    return runner.launch.call_args.args[0]


def test_the_test_phase_spec_carries_the_project_root() -> None:
    """El caso que estaba roto: proyecto anidado, fase de tests desde la raíz."""
    spec = _capture_spec(_request({"project_root": "ci4build"}))

    assert spec.project_root == "ci4build", (
        "la fase de tests construyó el spec SIN `project_root`: `default_pre_install` "
        "y los acceptance checks volverán a correr desde la raíz del worktree"
    )


def test_a_project_without_root_keeps_todays_behaviour() -> None:
    """No-regresión, y es el test que más importa: la inmensa mayoría de los
    proyectos no declara `project_root` y tiene que seguir comportándose igual."""
    for config in ({}, None, {"language": "php"}):
        spec = _capture_spec(_request(config))
        assert spec.project_root is None, (
            f"con repository_config={config!r} el spec debería quedarse sin "
            f"`project_root`, y se quedó con {spec.project_root!r}"
        )


# NOTA (2026-08-29), y va aquí porque es donde alguien la buscará: la lectura de
# `project_root` es defensiva (`isinstance(..., dict)`), pero NO existe test de
# `repository_config` no-dict porque hoy no se llega tan lejos: unas líneas antes,
# `build_project_runtime_services` hace `repository_config.get("services")` sin
# comprobar el tipo y revienta con `AttributeError: 'str' object has no attribute
# 'get'` (`runtime_services.py:304`).
#
# Es un defecto PREEXISTENTE y ajeno al ADR 0162, así que no se arregla aquí ni se
# escribe un test que lo dé por bueno: fijar el comportamiento roto lo volvería
# permanente. Queda anotado para quien toque `runtime_services`.
