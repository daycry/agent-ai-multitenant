"""Unit test — prod-06 task_prod06_zombi_03.

The execution hard time limit MUST stay strictly below the Celery/Redis broker
visibility timeout: if a run lasts longer than the visibility window, Redis
assumes the worker died and REDELIVERS the message, duplicating a still-live run
(workers-3, the 24h hard cap vs. no broker timeout). This pins both halves of the
invariant — the broker is configured with the pinned visibility timeout, and the
operator-tunable hard-limit's ``max_value`` is capped below it (so the UI can
never raise it past the broker window).
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


def test_broker_visibility_timeout_is_pinned() -> None:
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S, build_celery_app

    app = build_celery_app()
    assert app.conf.broker_transport_options["visibility_timeout"] == EXECUTION_VISIBILITY_TIMEOUT_S


def test_hard_limit_max_stays_below_visibility_timeout() -> None:
    from api_server.platform_settings_registry import PLATFORM_KNOWN_SETTINGS
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S

    execution = PLATFORM_KNOWN_SETTINGS["ejecucion"].settings
    hard = execution["execution_hard_time_limit_s"]
    soft = execution["execution_soft_time_limit_s"]

    # The cross-check that prevents broker redelivery of a live run.
    assert hard.max_value is not None
    assert hard.max_value < EXECUTION_VISIBILITY_TIMEOUT_S
    # Soft must not exceed the hard ceiling either (hard > soft is required).
    assert soft.max_value is not None and soft.max_value <= hard.max_value


# --- prod-06 A3 (auditoría 2026-07-06): el hard time limit de Celery (SIGKILL
# del hijo) DEBE superar el mayor timeout de contenedor+gracia, o un run legítimo
# de claude_sdk (hasta 2h) se mata a los 35 min (default 2100s) y, con
# task_reject_on_worker_lost, el mensaje se reentrega → SEGUNDO contenedor +
# doble coste LLM mientras el primero sigue vivo. El backstop de Celery debe ser
# el ÚLTIMO recurso (el contenedor/loop se auto-limita antes), no el primero.
def test_default_hard_limit_exceeds_largest_container_budget() -> None:
    from api_server.db.platform_settings import (
        DEFAULT_EXECUTION_HARD_TIME_LIMIT_S,
        DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S,
    )
    from workers.config import get_settings

    settings = get_settings()
    # El mayor presupuesto de contenedor+gracia entre todos los kinds/modos.
    largest_container = max(
        settings.container_timeout_with_grace_for_kind("claude_sdk", is_review=False),
        settings.container_timeout_with_grace_for_kind("claude_sdk", is_review=True),
        settings.container_timeout_with_grace_for_kind("ollama"),
    )
    # El soft-limit no debe disparar antes de que el contenedor agote su budget.
    assert largest_container <= DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S, (
        f"soft ({DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S}) < mayor budget de "
        f"contenedor+gracia ({largest_container}) → SIGKILL prematuro + doble run"
    )
    assert DEFAULT_EXECUTION_HARD_TIME_LIMIT_S > DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S


def test_default_hard_limit_stays_below_visibility_timeout() -> None:
    from api_server.db.platform_settings import DEFAULT_EXECUTION_HARD_TIME_LIMIT_S
    from workers.celery_app import EXECUTION_VISIBILITY_TIMEOUT_S

    # El hard subido sigue por debajo de la ventana del broker (no reentrega).
    assert DEFAULT_EXECUTION_HARD_TIME_LIMIT_S < EXECUTION_VISIBILITY_TIMEOUT_S
