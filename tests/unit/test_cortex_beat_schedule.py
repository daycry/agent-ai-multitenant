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


# ---------------------------------------------------------------------------
# La cadencia sale de los settings, no de una constante.
# ---------------------------------------------------------------------------
#
# Auditoría del córtex 2026-07-27 (F3.9 / F4.2 / F4.10): `cortex_curiosity_cron`,
# `cortex_reflection_cron` y `cortex_maintenance_cron` estaban declarados en
# `workers.config` como «operator-tunable», documentados con un default concreto…
# y eran CÓDIGO MUERTO: el beat hardcodeaba `run_every=900.0` y dos `crontab()`
# literales. El operador podía exportar la variable, reiniciar el beat y no pasaba
# nada — y los defaults documentados MENTÍAN (`*/30` vs 15 min, `42 4` vs 04:45).
#
# Un setting que no hace nada es peor que no tenerlo: consume una decisión del
# operador y le devuelve silencio. Estos tests fijan que la cadencia venga del
# settings y que un cron a medida se respete.
_ENV = {
    "WORKERS_DATABASE_URL": "postgresql+asyncpg://x:y@localhost/z",
    "WORKERS_BROKER_URL": "redis://localhost:6379/1",
    "WORKERS_JWT_SECRET": "t",
    "WORKERS_AGENT_INTERNAL_API_URL": "http://x",
}


def _sched(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> dict[str, dict[str, object]]:
    for key, value in {**_ENV, **overrides}.items():
        monkeypatch.setenv(key, value)

    from workers.beat_schedule import build_beat_schedule
    from workers.config import get_settings

    get_settings.cache_clear()
    try:
        return build_beat_schedule(get_settings())
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize(
    ("entry", "env_var", "expr", "expected"),
    [
        (
            "cortex-curiosity",
            "WORKERS_CORTEX_CURIOSITY_CRON",
            "*/7 * * * *",
            {"minute": {0, 7, 14, 21, 28, 35, 42, 49, 56}},
        ),
        (
            "cortex-reflection",
            "WORKERS_CORTEX_REFLECTION_CRON",
            "5 2 * * *",
            {"minute": {5}, "hour": {2}},
        ),
        (
            "cortex-maintenance",
            "WORKERS_CORTEX_MAINTENANCE_CRON",
            "9 3 * * *",
            {"minute": {9}, "hour": {3}},
        ),
    ],
)
def test_cortex_cadence_comes_from_settings(
    monkeypatch: pytest.MonkeyPatch,
    entry: str,
    env_var: str,
    expr: str,
    expected: dict[str, set[int]],
) -> None:
    sched = _sched(monkeypatch, **{env_var: expr})
    cron = sched[entry]["schedule"]
    for field, values in expected.items():
        assert getattr(cron, field) == values, (entry, field, getattr(cron, field, None))


def test_cortex_defaults_match_what_the_settings_document(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin override, la cadencia real ES la que documenta el Field.

    Es la mitad que faltaba: cablear el setting pero dejar el default divergente
    seguiría mintiendo en la documentación que el operador lee.
    """
    from workers.beat_schedule import _try_crontab
    from workers.config import Settings

    sched = _sched(monkeypatch)
    for entry, field in (
        ("cortex-curiosity", "cortex_curiosity_cron"),
        ("cortex-reflection", "cortex_reflection_cron"),
        ("cortex-maintenance", "cortex_maintenance_cron"),
    ):
        documented = _try_crontab(str(Settings.model_fields[field].default))
        assert documented is not None, field
        actual = sched[entry]["schedule"]
        assert (actual.minute, actual.hour) == (documented.minute, documented.hour), entry
