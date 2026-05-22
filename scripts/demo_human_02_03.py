"""human_02_03 — las salvaguardas funcionan.

Corre el agent loop con presupuestos diseñados para disparar cada
salvaguarda e imprime un checklist. Los tres primeros escenarios corren
el loop in-process (rápido, sin contenedor); el del timeout lanza un
contenedor que se cuelga y comprueba que el worker lo mata.

Uso (con el venv, desde la raíz del repo):

    .venv/Scripts/python scripts/demo_human_02_03.py

Requiere Docker (solo para el escenario de timeout).
"""

from __future__ import annotations

import sys
from typing import Any

from _demo_common import banner, check


def _run(decisions: list[dict[str, Any]], **budget_kw: Any) -> Any:
    """Corre el agent loop con un modelo scriptado y unos presupuestos."""
    from agent_runtime.graph import AgentDeps, run_agent
    from agent_runtime.model import model_from_spec
    from agent_runtime.safeguards import Budgets

    deps = AgentDeps(model=model_from_spec({"kind": "scripted", "decisions": decisions}))
    budgets = Budgets(**budget_kw) if budget_kw else None
    task = {"id": "t-h0203", "title": "Tarea de prueba de salvaguardas", "description": ""}
    return run_agent(deps, task, budgets=budgets)


def main() -> int:
    banner("human_02_03 — las salvaguardas del agent loop")
    print()
    ok = True

    # 1) max_iterations — acciones DISTINTAS, para no disparar antes el
    #    detector de loops.
    distinct = [{"kind": "act", "tool": "echo", "tool_args": {"text": str(i)}} for i in range(12)]
    res = _run(distinct, max_iterations=5)
    ok &= check(
        "max_iterations=5: la ejecución se aborta al agotar las iteraciones",
        res.status == "aborted" and res.abort_code == "max_iterations_exceeded",
        f"status={res.status} abort_code={res.abort_code} iteraciones={res.iterations}",
    )

    # 2) Detección de loops — la MISMA acción repetida una y otra vez.
    same = [{"kind": "act", "tool": "echo", "tool_args": {"text": "igual"}} for _ in range(12)]
    res = _run(same)
    ok &= check(
        "Misma acción repetida: el detector de loops aborta la ejecución",
        res.status == "aborted" and res.abort_code == "repetitive_loop_detected",
        f"abort_code={res.abort_code}",
    )

    # 3) max_cost — coste acumulado por encima del presupuesto.
    costly = [
        {"kind": "act", "tool": "echo", "tool_args": {"text": str(i)}, "cost_usd": 1.0}
        for i in range(12)
    ]
    res = _run(costly, max_cost_usd=0.5)
    ok &= check(
        "max_cost_usd=0.5: se aborta al superar el presupuesto de coste",
        res.status == "aborted" and res.abort_code == "max_cost_exceeded",
        f"abort_code={res.abort_code} coste=${res.usage.get('cost_usd')}",
    )

    # 4) Timeout de pared — un contenedor que se cuelga, matado por el worker.
    print()
    print("  Escenario timeout: lanzando un contenedor colgado (~3 s)...")
    from workers.config import Settings
    from workers.container import AgentContainerRunner, ContainerSpec

    container = AgentContainerRunner(Settings()).run(
        ContainerSpec(
            image="python:3.12-slim",
            command=["python", "-c", "import time; time.sleep(120)"],
        ),
        timeout=3,
    )
    ok &= check(
        "Contenedor que excede su presupuesto de tiempo: el worker lo mata",
        container.timed_out is True,
        f"timed_out={container.timed_out}",
    )

    print()
    print(
        "  RESULTADO:",
        "todas las salvaguardas funcionan" if ok else "REVISAR — alguna salvaguarda falló",
    )
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # - script de demo: errores legibles
        print(f"\n  ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("  ¿Está el daemon de Docker levantado?", file=sys.stderr)
        sys.exit(1)
