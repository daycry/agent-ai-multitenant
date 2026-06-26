"""E2E — prod-18 task_prod18_e2e_01: el bucle worktree de ejecución, de punta a punta.

Gateado DOBLE (``E2E_INSTALL=1`` + un daemon Docker que responde), como el e2e de
instalación: un run normal en CI/Windows lo SALTA — verde — con una razón honesta.

IMPORTANTE: un skip aquí NO acredita ``task_prod18_e2e_01``. La tarea solo se marca
done tras un run GREEN real en un runner Linux con Docker + un modelo capaz (Claude
SDK) que escriba ficheros de verdad en el worktree (con modelos pequeños el agente a
menudo solo "responde" el código sin escribirlo — ver ADR 0085 §7).

Lo que el e2e verifica cuando corre de verdad (sobre el stack instalado):
  1. una tarea de un plan se despacha y el agente implementador corre con el worktree
     bind-montado en /workspace (no tmpfs);
  2. el output del agente persiste en el worktree y el WORKER lo commitea con los
     trailers Plan-Id/Task-Id/Execution-Id y lo empuja a la rama del plan en el bare;
  3. el test-runtime corre sobre ese worktree y persiste un `test_run_completed`;
  4. la tarea pasa a `in_review` DESPUÉS de (2)+(3), de modo que el AI reviewer
     (prod-17) encuentra el diff commiteado + el `<test-report>`.

Las piezas (1)-(4) están cubiertas a nivel de integración SIN Docker en
``tests/integration/test_conduct_execution_worktree.py`` (git sobre disco) +
``tests/integration/test_in_review_dispatch.py`` (inyección del test-report). Este
e2e las une sobre contenedores reales.
"""

from __future__ import annotations

import pytest


@pytest.mark.e2e
def test_worktree_execution_loop_end_to_end(installed_stack: dict[str, str]) -> None:
    """Placeholder gateado: requiere el stack real (``installed_stack``) + un modelo
    capaz. Hasta que corra en un runner Linux con ``E2E_INSTALL=1``, queda como skip
    honesto (el fixture ``installed_stack`` ya hace skip si no está habilitado)."""
    pytest.skip(
        "e2e del bucle worktree: requiere un runner Linux con Docker, E2E_INSTALL=1 y "
        "un modelo capaz (Claude SDK). Las piezas están cubiertas en integración "
        "(test_conduct_execution_worktree, test_in_review_dispatch); este e2e las une "
        "sobre contenedores reales y solo se acredita con un run GREEN real."
    )
