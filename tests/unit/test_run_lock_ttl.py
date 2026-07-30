"""El run-lock debe sobrevivir al run más largo posible (C-05).

El lock por tarea existe para que una re-entrega de Celery (`acks_late`) no
arranque un SEGUNDO run de la misma tarea mientras el primero sigue vivo: su
`sync_to_head` hace `reset --hard` + `clean -fdx` sobre el worktree y se llevaría
por delante el trabajo en vuelo.

Su TTL se calculaba desde el presupuesto de CONTENEDOR (7200 + 120 de gracia +
300 = 7620 s), por debajo del `execution_hard_time_limit_s` (7800 s por defecto,
y hasta 6 h si el operador lo sube). Un run que llegara al límite duro perdía el
lock ANTES de morir, y en esa ventana la re-entrega podía adquirirlo — justo el
escenario que el lock existe para impedir.

El ancla correcta no es el presupuesto del contenedor sino la **ventana de
visibilidad del broker**: es el instante en que Redis re-entrega el mensaje, o
sea el primer momento en que puede existir un competidor. Mientras el lock viva
exactamente eso, no hay hueco.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def _hard_limit_ceiling() -> int:
    """El techo del `execution_hard_time_limit_s` que el operador puede fijar."""
    from api_server.platform_settings_registry import _find_def

    definition = _find_def("execution_hard_time_limit_s")
    assert definition.max_value is not None, "el hard limit debe tener techo declarado"
    return int(definition.max_value)


def test_lock_ttl_outlives_the_longest_possible_run() -> None:
    """Ningún valor legal del hard limit puede dejar al run sin lock."""
    from workers.tasks.run_cycle import run_lock_ttl_s

    assert run_lock_ttl_s() >= _hard_limit_ceiling(), (
        "el run-lock caducaría antes de que el run muera: una re-entrega podría "
        "adquirirlo y arrasar el worktree en vuelo"
    )


def test_lock_ttl_matches_the_broker_visibility_window() -> None:
    """El TTL es exactamente la ventana de visibilidad: ni menos (habría hueco)
    ni más (retendría la tarea pasado el momento en que la re-entrega es
    legítima, porque el run anterior ya está muerto)."""
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S
    from workers.tasks.run_cycle import run_lock_ttl_s

    assert run_lock_ttl_s() == EXECUTION_VISIBILITY_TIMEOUT_S


def test_lock_ttl_beats_every_per_kind_container_budget() -> None:
    """Regresión del cálculo antiguo: el TTL supera el presupuesto de contenedor
    (más gracia) de CUALQUIER proveedor, no solo el del más corto."""
    from workers.config import Settings
    from workers.tasks.run_cycle import run_lock_ttl_s

    settings = Settings()
    for kind in ("claude_sdk", "azure_foundry", "copilot", "ollama"):
        for is_review in (False, True):
            budget = settings.container_timeout_with_grace_for_kind(kind, is_review=is_review)
            assert run_lock_ttl_s() > budget, f"{kind} (review={is_review})"
