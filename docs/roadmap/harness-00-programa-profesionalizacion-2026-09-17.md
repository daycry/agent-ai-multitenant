---
plan_id: harness-00-programa-profesionalizacion-2026-09-17
title: Programa de profesionalización del agent harness — coordinación, ADR y evidencia
status: pending_approval
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 10-14 semanas (programa completo)
estimated_effort_person_days: 4 (propias) + 44-56 (planes hijos)
estimated_cost_human_eur: 1.800 € – 2.500 € (propias)
estimated_cost_ai_eur: 20 € – 40 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P0
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-00 — Programa de profesionalización del agent harness

## Cabecera

| Campo                 | Valor                                                                                                  |
| --------------------- | ------------------------------------------------------------------------------------------------------ |
| **ID del Plan**       | `harness-00-programa-profesionalizacion-2026-09-17`                                                    |
| **Prioridad**         | P0                                                                                                     |
| **Bloqueado por**     | —                                                                                                      |
| **Origen**            | [`auditoria-harness-ciclo-kb-memoria-2026-09-17.md`](auditoria-harness-ciclo-kb-memoria-2026-09-17.md) |
| **Rama git sugerida** | `plan/harness-00-programa`                                                                             |

> **Estado**: este documento coordina; no se marca `completed` hasta que los seis
> planes hijos estén `completed` o diferidos por ADR. Sustituye al borrador
> `docs/new_feature/harness-00-programa-profesionalizacion-2026-09-16.md`.

## Resumen

La plataforma ya tiene sandboxing, multitenancy, DAG, claims con identidad
(`claim_id`), supersede de ejecuciones huérfanas, cancelación cooperativa,
worktrees, reviewer y guardrails. Lo que falta es que esas defensas formen **un
sistema contractual y reproducible de extremo a extremo**: hoy el contrato
orchestrator→worker es una dataclass sin versión, el loop del agente no
sobrevive a una caída, una tool externa puede repetirse tras un redelivery, KB y
memoria confunden «vacío» con «caído», y ninguna traza cruza la frontera de Celery.

El programa cierra esos seis huecos en seis planes. **Dos correcciones respecto al
borrador externo**: (a) el ADR de garantías va **primero**, porque tres de los
cambios invierten decisiones de producto vigentes; (b) harness-05 se recorta al
**delta** sobre `memoria-agentes-2026-09-09`, que ya está aprobado y cubre seis de
sus siete tareas originales.

## Principios obligatorios

1. **Evolución incremental**: envolver y extraer contratos antes de reescribir.
2. **Fail closed donde importa**, y sólo donde el ADR 0168 lo decida.
3. **Degradación explícita**: una dependencia opcional puede fallar, pero el run lo declara.
4. **Effectively once**: idempotencia y reconciliación; no prometer exactly once.
5. **Provenance first**: modelo, prompt, tools, KB y memoria se identifican por versión/digest.
6. **Pruebas por camino productivo**: no validar un bridge llamando a su helper.
7. **Evidencia sobre casillas**: el cierre enlaza JUnit y artefactos.
8. **Compatibilidad reversible**: dual-read temporal, migraciones con downgrade.

## Secuencia de planes

```text
harness-00 (ADR 0168 + baseline + fix claim_id)
   └── harness-01 Contrato + capacidades
           ├── harness-02 Checkpoints y reanudación   (spike de medición primero)
           │       └── harness-03 Efectos, Tool Gateway, outbox
           └── harness-04 KB reproducible
                   └── harness-05 Memoria gobernada (delta sobre memoria-agentes)
harness-03 + harness-04 + harness-05
   └── harness-06 Tracing, replay, evals, caos, evidence gate
```

| Orden | Plan                                                                                                     | Resultado principal                                                                | Esfuerzo |
| ----: | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- | -------- |
|     1 | [`harness-01-contrato-run-capacidades-2026-09-17.md`](harness-01-contrato-run-capacidades-2026-09-17.md) | `ExecutionEnvelopeV1`, contrato de dependencias, registro de capacidades, `doctor` | 8-10 d   |
|     2 | [`harness-02-checkpoints-reanudacion-2026-09-17.md`](harness-02-checkpoints-reanudacion-2026-09-17.md)   | Checkpointer LangGraph remoto, reanudación con presupuestos y detector             | 9-12 d   |
|     3 | [`harness-03-efectos-tool-gateway-2026-09-17.md`](harness-03-efectos-tool-gateway-2026-09-17.md)         | `ToolResult` tipado, Tool Gateway, Effect Ledger, outbox post-run                  | 9-11 d   |
|     4 | [`harness-04-kb-reproducible-2026-09-17.md`](harness-04-kb-reproducible-2026-09-17.md)                   | Estados de disponibilidad, snapshot de recuperación, versionado de chunk/documento | 6-8 d    |
|     5 | [`harness-05-memoria-gobernada-2026-09-17.md`](harness-05-memoria-gobernada-2026-09-17.md)               | Memory Gate, procedencia, outbox de memorización, estados de recall                | 5-7 d    |
|     6 | [`harness-06-assurance-replay-caos-2026-09-17.md`](harness-06-assurance-replay-caos-2026-09-17.md)       | Tracing extremo a extremo, replay, matriz de caos, evidence gate                   | 7-8 d    |

## Tareas propias del programa

### Fase A — Decidir antes de construir

#### `task_h00_01` — ADR 0168: garantías del harness y cambios de política

- [ ] **Título**: redactar `docs/05-architecture-decisions/0168-garantias-del-agent-harness.md`
      con las decisiones que los planes hijos necesitan y que hoy contradicen
      código o decisiones vigentes: (1) **memoria y KB pueden ser `REQUIRED`** — hoy
      «nunca rompen el run» (`__main__.py`); se decide si el default sigue siendo
      `OPTIONAL` y quién puede pedir `REQUIRED` (proyecto / tarea); (2)
      **`allowed_tools` obligatorio en V1** — hoy `None` = sin restricción (06.14); se
      decide si `['*']` reemplaza a `None` y cómo migran los agentes sin allowlist;
      (3) **guardrails fail-closed** sólo cuando hay una política configurada y no
      se puede resolver; sin política, baseline (mantiene ADR 0102 D3); (4)
      **effectively once**, no exactly once; (5) **dónde vive el checkpoint store**
      (PostgreSQL vía API interna con token por ejecución; el sandbox nunca toca la
      BD) y qué proceso escribe; (6) **execution_id vs attempt_id**. Si alguna
      decisión invalida casillas de planes existentes, se declara en `rejects:`.
      El ADR actualiza CLAUDE.md en el mismo commit si cambia un principio.
      **Test**: `tests/docs/test_adr_precedence.py` (ya existe: valida `rejects:` y
      frontmatter). **Coste**: 1 d. **Requiere aprobación humana.**

#### `task_h00_02` — El `claim_id` viaja también por el serializador canónico

- [ ] **Título**: `ExecutionRequest.as_dict()` emite `claim_id` (hoy lo omite aunque
      `from_dict` lo lea), y el orchestrator construye el payload con
      `ExecutionRequest(...).as_dict()` en vez de un dict a mano, para que no haya
      dos definiciones del contrato. Es la corrección más barata de la auditoría y
      no espera al programa. Round-trip completo `as_dict → from_dict` por cada
      campo del dataclass (`dataclasses.fields`), para que el siguiente campo
      olvidado falle en CI.
      **Test**: `tests/unit/test_execution_request_roundtrip_all_fields.py`;
      ampliar `tests/unit/test_la_reclamacion_viaja_con_identidad.py` con el caso
      `as_dict`. **Coste**: 0,5 d.

#### `task_h00_03` — Baseline ejecutable

- [ ] **Título**: correr las suites de lifecycle, runtime, KB y memoria y guardar
      JUnit + commit + head de migración + digests de imagen en
      `.artifacts/harness-baseline/` (ignorado por git; se adjunta al PR). Un skip
      en un test marcado obligatorio falla el baseline. No se corrige nada aquí:
      cada rojo se abre en el plan hijo que corresponda.
      **Test**: `pytest tests/unit tests/integration -q --junitxml=.artifacts/harness-baseline/junit.xml`
      y `pytest docker/agent-runtimes/agent-runtime/tests -q`. **Coste**: 0,5 d.

### Fase B — Gobierno de implementación

#### `task_h00_04` — Registro de evidencia por tarea

- [ ] **Título**: cada plan hijo cierra sus casillas con la nota de cierre que ya usa
      el repo («Entregada el …», test visto en rojo) **y** un enlace al JUnit del
      commit. Se añade a `tests/unit/test_declared_tests_exist.py` la comprobación
      de que los `command:` de las casillas `[x]` de los planes `harness-*` existen
      (ya lo hace para todo el roadmap; aquí sólo se confirma que los nuevos
      ficheros entran en su barrido). No se inventa un formato nuevo de evidencia.
      **Test**: `tests/unit/test_declared_tests_exist.py`. **Coste**: 0,5 d.

### Fase C — Cierre del programa

#### `task_h00_05` — Escenario E2E de aceptación

- [ ] **Título**: instalación limpia (el test humano de
      `remediacion-instalador-runs-de-serie-2026-09-09`) → tenant, proyecto, agente,
      KB y memoria inicial → una tarea que lee KB, recupera memoria, llama una tool
      con efecto y produce commit → kill del runtime tras el efecto → reanudación
      sin duplicarlo → corte temporal del backend de KB con la política declarada →
      replay con snapshot y comparación de la traza.
      **Test**: `tests/e2e/test_harness_acceptance.py` (runner Docker; no corre en
      CI de PR, se adjunta su JUnit). **Coste**: 1,5 d. **Depende de**: todos los hijos.

## Criterios de cierre

- [ ] ADR 0168 `accepted` y CLAUDE.md actualizado si procede.
- [ ] Los seis hijos `pending_human_validation`, `completed` o diferidos por ADR.
- [ ] Ningún test obligatorio terminó en skip.
- [ ] El E2E demuestra reanudación y ausencia de doble efecto.
- [ ] El replay muestra modelo, prompt, tools, efectos y checkpoints.
- [ ] Un operador valida instalación, recuperación y diagnóstico.

## Riesgos

1. **Gran refactor accidental**: contratos primero, feature flags, commits por tarea.
2. **Doble sistema de memoria/KB**: harness-05 nace como delta; harness-04 abre con gap analysis.
3. **Cola de validación humana**: la rama ya lleva tres planes `pending_human_validation`; este programa no cierra ninguno sin el humano.
4. **Sobreestimar la necesidad de checkpoints**: harness-02 arranca con un spike que mide cuántos runs mueren a mitad; si son pocos, se recorta.
5. **CI lenta**: las matrices de caos viven en `tests/e2e` (runner Docker), no en el PR.

## Pruebas humanas

```yaml
- id: human_h00_01
  title: Revisión del ADR 0168 y del programa
  steps:
    - Leer el ADR 0168 y confirmar las seis decisiones.
    - Confirmar que ningún plan hijo da acceso directo del sandbox a PostgreSQL o al socket Docker.
    - Confirmar que harness-05 no duplica memoria-agentes-2026-09-09.
  expected: Aprobación explícita o lista de cambios antes de arrancar harness-01.

- id: human_h00_02
  title: Validación E2E del harness profesionalizado
  steps:
    - Lanzar el escenario de task_h00_05.
    - Inspeccionar traza, checkpoints, snapshots y ledger de efectos.
    - Verificar que la UI/API distingue dependencia vacía de dependencia caída.
  expected: El operador explica y recupera el run sin correlacionar logs a mano.
```
