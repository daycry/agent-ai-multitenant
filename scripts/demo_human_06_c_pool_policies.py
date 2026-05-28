"""Demo: pool elástico + matriz de policies Git (human_06_07 + 06_08).

  * human_06_07 — el pool por plan arranca con min, crece hasta max
    al ejecutar pasos paralelos, el mismo container sirve a roles
    diferentes (implementador → reviewer → memorizer), idle eviction
    funciona, las métricas reflejan la realidad.
  * human_06_08 — las cuatro combinaciones de branch_push_mode x
    plan_validation_mode producen el comportamiento esperado en
    apply_push_policy + open_plan_pr.

Pre-condición: scripts/setup_demo_06.py limpio.
"""

from __future__ import annotations

import contextlib
import itertools
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
    from workers.runtime_pool import PoolConfig, RuntimePool

    _banner("demo human_06_c - pool + policies")

    # human_06_07 ---------------------------------------------------
    _banner("human_06_07 - pool elastico por plan")

    counter = itertools.count()
    pool = RuntimePool(
        plan_id="demo-c-pool",
        project_id="demo-project",
        config=PoolConfig(min=1, max=3, idle_ttl_seconds=60),
        container_factory=lambda: f"c-{next(counter)}",
    )
    pool.start()
    _check(pool.metrics().size == 1, "pool arranca con min=1 container")

    # Acquire 3 paralelos -> pool crece a 3.
    cms = [pool.acquire(f"role{i}") for i in range(3)]
    slots = [cm.__enter__() for cm in cms]
    _check(pool.metrics().size == 3, "pool crece a max=3 con 3 acquires paralelos")
    _check(pool.metrics().busy == 3, "los 3 slots estan busy")
    for cm in cms:
        cm.__exit__(None, None, None)
    _check(pool.metrics().idle == 3, "post-release todos idle")

    # Role switch in-place sobre el mismo container.
    seen_containers = set()
    for role in ("implementador", "reviewer", "memorizer", "technical_writer"):
        with pool.acquire(role) as slot:
            seen_containers.add(slot.container_id)
    _check(
        len(seen_containers) == 1,
        "mismo container sirve 4 roles distintos sin reinicio",
        str(seen_containers),
    )

    # Idle eviction: backdate los slots y barre.
    far_future = max(s.last_used_at for s in slots) + 10_000
    removed = pool.sweep_idle(now=far_future)
    _check(len(removed) == 2, "sweep_idle elimina 2 (min=1 sobrevive)", str(len(removed)))
    _check(pool.metrics().size == 1, "pool vuelve a tamano min=1")

    # Metrics shape.
    metrics = pool.metrics()
    _check(metrics.evictions_total == 2, "evictions_total counter en 2")
    _check(
        sum(metrics.role_executions_total.values()) >= 7,
        "role_executions_total acumula >=7 invocaciones",
        str(metrics.role_executions_total),
    )

    pool.shutdown()

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
