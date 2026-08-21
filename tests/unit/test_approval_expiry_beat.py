"""prod-03 task_prod03_05 — el cableado del sweep de caducidad al beat.

`expire_stale_requests` llevaba dos meses implementada y testeada SIN un solo
llamante (el ADR 0016 lo anotaba: «falta el job periódico»). Estos tests fijan
las dos mitades del cableado, que es donde muere este tipo de trabajo:

  1. la entrada existe en el `BEAT_SCHEDULE` con su cadencia y su cola;
  2. la task está REGISTRADA en la app Celery — una entrada de beat cuyo nombre
     ningún worker registra encola un mensaje que muere con `NotRegistered`, y
     eso no se nota: beat sigue tickeando tan contento.

La guarda (2) es genérica a propósito: recorre TODAS las entradas del beat, no
solo la mía. No existía, y es exactamente el agujero por el que se cuela un job
que parece cableado.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from celery.schedules import schedule
from workers.beat_schedule import APPROVAL_EXPIRY_BEAT_ENTRY, BEAT_SCHEDULE, build_beat_schedule
from workers.config import Settings

pytestmark = pytest.mark.unit

_APPROVAL_EXPIRY_TASK = "workers.expire_stale_approvals"


def test_the_approval_expiry_entry_exists() -> None:
    entry = BEAT_SCHEDULE[APPROVAL_EXPIRY_BEAT_ENTRY]
    assert entry["task"] == _APPROVAL_EXPIRY_TASK
    options = entry["options"]
    assert isinstance(options, dict)
    # Escrituras de dominio, sin efectos de infra → la vía común.
    assert options["queue"] == "default"


def test_the_cadence_is_every_15_minutes() -> None:
    """El plan fija 15 min: bastante fino para que un timeout de 24 h no se
    alargue de forma perceptible, y bastante grueso para que la pasada en vacío
    (un SELECT) no pese."""
    entry = BEAT_SCHEDULE[APPROVAL_EXPIRY_BEAT_ENTRY]
    sched = entry["schedule"]
    assert isinstance(sched, schedule)
    assert sched.run_every.total_seconds() == 900.0


def test_the_entry_survives_build_beat_schedule() -> None:
    """`build_celery_app` no usa `BEAT_SCHEDULE` directo, sino el resultado de
    `build_beat_schedule` — que parte de una COPIA y añade las configurables. Una
    entrada estática que se perdiera ahí no llegaría nunca al beat."""
    sched = build_beat_schedule(Settings())
    assert sched[APPROVAL_EXPIRY_BEAT_ENTRY]["task"] == _APPROVAL_EXPIRY_TASK


def _registered_task_names() -> set[str]:
    """Las tasks que un worker REAL tendría registradas al arrancar.

    `imports` es perezoso: construir la app no importa nada, lo hace el worker en
    el boot vía `import_default_modules()`. Comprobar `app.tasks` sin llamarlo
    daría «no registrada» para TODAS y el test no distinguiría nada.

    Se mide en un SUBPROCESO, y no es un lujo: el registro de Celery es un
    singleton de módulo, y `@app.task` engancha la task en el import del módulo.
    Cualquier otro test de la suite que importe `workers.standup` (o quien sea)
    la deja registrada para siempre en ESTE proceso, así que medir aquí daba un
    resultado que dependía del orden de la suite: en aislamiento salía verde y
    dentro de `pytest tests/unit/` salía rojo por la aserción de «deuda ya
    arreglada». Un proceso limpio es, literalmente, la definición del docstring:
    lo que importa un worker al arrancar y nada más.
    """
    code = (
        "import json;"
        "from workers.celery_app import app;"
        "app.loader.import_default_modules();"
        "print(json.dumps(sorted(app.tasks)))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    return set(json.loads(out.stdout.strip().splitlines()[-1]))


def test_the_approval_expiry_task_is_registered_in_the_celery_app() -> None:
    """La mitad que faltaría si nadie añade el módulo a `imports`."""
    assert _APPROVAL_EXPIRY_TASK in _registered_task_names(), (
        f"{_APPROVAL_EXPIRY_TASK} no está registrada: beat la encolaría y el "
        "worker la rechazaría con NotRegistered. Falta el módulo en "
        "`celery_app.build_celery_app(imports=...)`."
    )


# --- Deuda AJENA que esta guarda descubrió (2026-07-29) ---------------------
# Seis entradas del beat nombran tasks que NINGÚN worker registra: sus módulos no
# están en `celery_app.build_celery_app(imports=...)` (los cinco de nivel
# superior) ni en el façade `workers.maintenance.__init__` (knowledge_gc). Beat
# las encola puntualmente y el worker las rechaza con `NotRegistered`, sin ruido
# visible: seis features "entregadas y desplegadas" que no se han ejecutado nunca
# (ADR 0120 standup, ADR 0122 vigía de credenciales, ADR 0124 retro de planes,
# ADR 0125 asesor de config, ADR 0126 restore-drill, G-03 GC de conocimiento).
#
# NO se arregla aquí: son ficheros de otro carril, y el arreglo ENCIENDE seis
# jobs de fondo dormidos a la vez —uno de ellos ensaya una restauración de
# backup—, así que quiere los ojos del operador, no un efecto colateral del
# carril de aprobaciones. Se deja como registro con IGUALDAD: al cablear una hay
# que quitarla de aquí, y una NUEVA rota rompe CI.
_KNOWN_UNREGISTERED_BEAT_TASKS: frozenset[str] = frozenset(
    {
        "workers.collect_knowledge_garbage",  # workers/maintenance/knowledge_gc.py
        "workers.config_advisor",  # workers/config_advisor.py
        "workers.daily_standup",  # workers/standup.py
        "workers.plan_retro",  # workers/plan_retro.py
        "workers.provider_watchdog",  # workers/provider_watchdog.py
        "workers.restore_drill",  # workers/restore_drill.py
    }
)


def test_every_beat_entry_names_a_registered_task() -> None:
    """La guarda genérica: ninguna entrada de beat apunta a una task fantasma.

    Con aserción de que la guarda ENCONTRÓ algo — un descubrimiento vacío pasaría
    vacuamente el día que `build_beat_schedule` devolviera un dict vacío.
    """
    registered = _registered_task_names()

    sched = build_beat_schedule(Settings())
    assert len(sched) >= 15, f"la guarda dejó de ver el beat schedule (vio {len(sched)})"
    assert len(registered) >= 20, f"la guarda no importó las tasks (vio {len(registered)})"

    unregistered = {str(entry["task"]) for entry in sched.values()} - registered
    new_breakage = sorted(unregistered - _KNOWN_UNREGISTERED_BEAT_TASKS)
    fixed = sorted(_KNOWN_UNREGISTERED_BEAT_TASKS - unregistered)

    assert not new_breakage, (
        f"entradas de beat cuyo nombre de task no está registrado: {new_breakage}. "
        "Beat las encolaría y el worker las rechazaría con NotRegistered "
        "(añade su módulo a `celery_app.build_celery_app(imports=...)`)."
    )
    assert not fixed, (
        f"tasks del registro de deuda que YA están cableadas: {fixed}. "
        "Quítalas de _KNOWN_UNREGISTERED_BEAT_TASKS para que la lista no mienta."
    )
