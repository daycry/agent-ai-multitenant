---
name: refactorizacion-por-partes
description: Refactor por partes del pipeline de runs COMPLETADO (P1-P7 + hallazgos H1-H6; P8 no-abordar deliberado); plan en docs/roadmap/refactorizacion-por-partes-2026-07-07.md; SIN desplegar.
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

Petición del operador (2026-07-07): refactor **por partes** (sin big-bang) + revisar
implementación y prompts de sistema. Plan y hallazgos en
`docs/roadmap/refactorizacion-por-partes-2026-07-07.md`; rama `plan/runs-visor-trabajo`, SIN desplegar.

**Hechas:** P1 = `workers/maintenance.py` → paquete de 9 submódulos + façade + test de
caracterización (9acd794). P2 = `execution.py` → extraídos `run_contract.py`/`run_result.py`/
`run_spec.py` puros que ENTRAN al gate mypy; execution 1768→1273 líneas, re-exporta todo (270a108).
De paso: 2 tests rojos PREEXISTENTES de `test_run_tools_by_stack.py` re-afirmados al contrato
skip-malformed de 602a24b (37f43bd).

**Hallazgos CORREGIDOS (2026-07-08, orden «revisa y corrige los hallazgos»):** H2 reviewer→
`_resolve_model_spec` (cb96ad0, quick-win de P4), H3 contrato `<verdict>` a fuente única
`agent_runtime/review_contract.py` + test de contrato cruzado runtime↔worker (3595b9a), H1
**fencing anti-injection** `<<<UNTRUSTED_DATA…>>>` en review_context/feedback/comments con
neutralización de marcadores (4f4fe80 — CAMBIA prompts: QA e2e de un ciclo review al desplegar),
H5+H6 menores (e436a9a), H4 decide/review consolidados con flags de clase = **P6 hecha** (78ca74d).

**Completadas 2026-07-08 (orden «continua»):** P5 graph.py 1639→1242 (tool_classification/
nudges/review_harvest, bf68379), P4 builder común `_assemble_run_request` en dispatch
1458→1390 (944997f), P3 `conduct_execution` 518 líneas → orquestador de 5 fases nombradas en
el mismo módulo (\_prepare_run/\_provision_workspace/\_launch_and_stream/\_finalize_and_transition/
\_implementer_post_process, 1535ab5). **PLAN COMPLETO.** P8 domain-vs-models = NO abordar
(170+103 importadores). Deuda anotada: dataclass de estado (H6-real), fusión de los dos
canales de veredicto (decisión de producto). Deploy pendiente de ventana del operador
(api-server+workers+agent-runtime WITH_CLAUDE=1; QA e2e de un ciclo review por el fencing H1).

**"Adelante" + "autorizo el reinicio" 2026-07-08:** (1) **mypy-total** (db1c5d0): hook local
`scripts/mypy_gate.py` con el entorno del proyecto, excludes por path RETIRADOS, 95 errores
corregidos, 567 ficheros verdes — mypy cazó en el acto un sombreado PlanStatus StrEnum/Literal
que habría roto el reconciler en runtime. (2) **DESPLEGADO a dev**: migración 0104 aplicada,
7 servicios recreados con imágenes nuevas; gotcha nuevo aprendido: la imagen del ORCHESTRATOR
también se construye sobre base api-server — hay que pasarle `--build-arg
BASE_IMAGE=agentic-platform/api-server:manuals` o hereda el api_server viejo de `:ci`
(crashloop ImportError transition_to_blocked; corregido con rebuild). (3) **tasks.py troceado**
(1b5b440): paquete workers/tasks/ (run_cycle/test_runtime_task/stack_exec_task/
review_runtime_task) + façade + `workers/docker_client.get_docker_client()` dedup (3 sitios;
stack_exec fuera a propósito). El clasificador de permisos VETA desbloquear tareas vía SQL —
para QA e2e el operador debe pulsar Desbloquear en el panel (monitor armado esperándolo).
Pendiente de la lista aprobada: A4/cobertura, dataclass estado runtime, ronda frontend.

**QA e2e 2026-07-07 (ciclo review, tarea 'Tests de feature'):** el implementador convergió
(done/success, stack_exec phpunit exit 1 = warning «no coverage driver», suite verde 6/63 —
mejora opcional: añadir pcov al template php-phpunit) y el QA CAZÓ una regresión real: el skip
A5 de self-review dejaba los runs de review con status=running → el worker (ADR 0096) degradaba
TODO approve a blocked. Fix TDD en el skip (fija done si RUNNING, preserva terminales) =
**faf2c78** + rebuild agent-runtime. El sweeper M1 selló solo la fila zombi
(stale_after_worker_loss). Stack dev entero a HEAD y healthy. **Re-QA VALIDADO**: 2º ciclo
implementador done/success → review re-anunciada por el reconciler pasada (b) tras una carrera
de 8ms con el lock A6 (mejora fina pendiente: publicar el evento diferido tras soltar el lock o
retry con countdown del concurrent_run_locked de reviews) → reviewer done + approve APLICADO →
tarea done y el DAG promocionó a las dependientes (plan CI4 corriendo autónomo sus 3 tareas
finales). **Plan CI4 COMPLETO 14/14 → pending_human_validation** con review-runtime auto-arrancado
(sesión 019f3d15, expira 2026-07-09 14:57) — validación humana del operador pendiente; tras su
approve, cierre de plan + auto-PR. Mejoras destapadas en el QA (cola): carrera lock↔evento
diferido (~6 min de latencia evitable), auto-revert de plan blocked cuando su causa desaparece,
botón «Desbloquear plan» también en detalle/board (hoy solo en /escalated), pcov en php-phpunit.
El ciclo completo del pipeline quedó verificado en vivo post-refactor.

**Reglas aprendidas del split:** monkeypatch = lookup site (parchear el submódulo, no la façade);
nombres Celery por string + módulo en `celery_app.imports`; módulos nuevos con api_server → añadir
al exclude mypy de `.pre-commit-config.yaml` en el mismo commit; los hooks reformatean → re-add y
re-commit (si queda `MM` el commit NO entró). Ver [[remediacion-auditoria-prod-implementados]].
