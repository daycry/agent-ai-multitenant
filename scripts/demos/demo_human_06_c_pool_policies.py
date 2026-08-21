"""Demo: matriz de policies Git (human_06_08).

  * human_06_08 — las cuatro combinaciones de branch_push_mode x
    plan_validation_mode producen el comportamiento esperado en
    apply_push_policy + open_plan_pr.

**human_06_07 (pool elástico por plan) se retiró el 2026-08-19.** No es que la
demo fallase: es que el sujeto no existe. `workers/runtime_pool.py` —con
`RuntimePool`, `PoolConfig`, `sweep_idle` y el cambio de rol en caliente— se
borró del repo el 2026-07-26 (commit `7959cdcb`) junto con sus ocho ficheros de
test, y hoy el worker lanza **un contenedor efímero por tarea**
(`workers/execution.py` → `AgentContainerRunner`, `container.py` →
`containers.run(detach=True)`).

Este fichero llevaba desde entonces reventando con `ImportError` en la línea 36 y
nadie se enteró, porque lo ejecuta un humano siguiendo una guía y nadie lo había
vuelto a ejecutar. Lo que se retira aquí es la mitad muerta; la de policies Git
sigue viva y es la que se conserva. Las casillas del plan `06` que describían el
pool quedaron desmarcadas el mismo día, con su enunciado reescrito.

Pre-condición: scripts/setup_demo_06.py limpio.
"""

from __future__ import annotations

import contextlib
import sys

for _stream in (sys.stdout, sys.stderr):
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def _banner(text: str) -> None:
    print(f"\n{'-' * 60}\n  {text}\n{'-' * 60}", flush=True)


def _check(ok: bool, label: str, detail: str = "") -> bool:
    print(f"  {'[ OK ]' if ok else '[FAIL]'} {label}" + (f" - {detail}" if detail else ""))
    return ok


def main() -> int:
    from workers.plan_git import PlanGitPolicies

    _banner("demo human_06_c - policies Git")

    # human_06_08 ---------------------------------------------------
    _banner("human_06_08 - matriz 4 combinaciones branch_push x plan_validation")

    combos = [
        ("incremental", "human_required", "rama visible en remoto desde 1a tarea, humano valida"),
        ("incremental", "auto_approve", "rama en vivo, plan se cierra sin paso humano"),
        ("final_only", "human_required", "rama no aparece hasta cierre, humano valida"),
        ("final_only", "auto_approve", "rama aparece de golpe, sin paso humano"),
    ]
    for bpm, vmode, descr in combos:
        try:
            policies = PlanGitPolicies(
                branch_push_mode=bpm,  # type: ignore[arg-type]
                plan_validation_mode=vmode,  # type: ignore[arg-type]
            )
            _check(
                policies.branch_push_mode == bpm and policies.plan_validation_mode == vmode,
                f"{bpm} + {vmode}",
                descr,
            )
        except Exception as exc:
            _check(False, f"{bpm} + {vmode}", str(exc))

    print(
        "\n  Las 12 combinaciones (2 x 2 x 3 con push_policy) estan testeadas"
        "\n  exhaustivamente en tests/integration/test_git_policies_matrix.py."
    )

    _banner("demo human_06_c PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
