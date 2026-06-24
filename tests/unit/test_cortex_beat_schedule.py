"""Córtex F4 — los 3 bucles de fondo están agendados en el beat (ADR 0078).

Regresión: el beat_schedule debe incluir curiosidad / reflexión / mantenimiento del
córtex con los nombres de tarea REGISTRADOS (si se renombra una tarea sin tocar el
schedule, el beat encolaría un nombre inexistente y el bucle nunca correría)."""

from __future__ import annotations

import pytest


def test_cortex_loops_are_scheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKERS_DATABASE_URL", "postgresql+asyncpg://x:y@localhost/z")
    monkeypatch.setenv("WORKERS_BROKER_URL", "redis://localhost:6379/1")
    monkeypatch.setenv("WORKERS_JWT_SECRET", "t")
    monkeypatch.setenv("WORKERS_AGENT_INTERNAL_API_URL", "http://x")

    from workers.beat_schedule import build_beat_schedule
    from workers.config import get_settings

    get_settings.cache_clear()
    try:
        sched = build_beat_schedule(get_settings())
    finally:
        get_settings.cache_clear()

    assert sched["cortex-curiosity"]["task"] == "workers.cortex_curiosity_loop"
    assert sched["cortex-reflection"]["task"] == "workers.cortex_reflect_scheduled"
    assert sched["cortex-maintenance"]["task"] == "workers.cortex_maintenance"

    # Los nombres deben existir como tareas registradas en la app Celery (si no, el
    # beat encolaría un nombre fantasma).
    import workers.cortex_curiosity  # (registra cortex_curiosity_loop)
    import workers.cortex_maintenance  # (registra cortex_maintenance)
    import workers.cortex_reflection  # noqa: F401  (registra cortex_reflect_scheduled)
    from workers.celery_app import app

    for name in (
        "workers.cortex_curiosity_loop",
        "workers.cortex_reflect_scheduled",
        "workers.cortex_maintenance",
    ):
        assert name in app.tasks, f"tarea {name} no registrada en Celery"
