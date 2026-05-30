---
plan_id: 14-evals-estadisticas
title: Sistema de Evaluación de Calidad y Estadísticas
status: in_progress
blocking_plan: [06-testing-revision-git]
started_at: 2026-05-30
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 60-80
estimated_cost_human_eur: 24.000 € – 32.000 €
estimated_cost_ai_eur: 180 € – 280 €
created_by: system_architect
spec_sections_referenced: [27, 28]
docs_language: es
---

# Plan 14 — Sistema de Evaluación de Calidad y Estadísticas

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `14-evals-estadisticas`                   |
| **Estado**                         | `in_progress`                             |
| **Bloqueado por**                  | `06-testing-revision-git`                 |
| **Tiempo estimado (calendario)**   | 3-4 semanas                               |
| **Tiempo estimado (persona-días)** | 60-80                                     |
| **Previsión de coste — humano**    | 24.000 € – 32.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 180 € – 280 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/14-evals-estadisticas`              |
| **Secciones del .docx**            | [27, 28]                                  |

---

## Descripción Detallada

### Resumen Ejecutivo

Evals continuos: golden dataset desde tareas reales aprobadas, LLM-as-judge custom, regression evals en CI bloqueando merges, shadow evals 5% en producción. Dashboard de estadísticas de agentes por tenant: tasa éxito, coste medio, throughput. **Dashboard de consumo del proyecto** (sección 13.7 del .docx) con totales tokens/dinero, segmentaciones por plan/agente/modelo/temporal, indicador de budget. **Explorador de runs del proyecto** (sección 13.8 del .docx) tabla filtrable con exportación CSV/XLSX. **Tabla de Runs en la vista de detalle de tarea** (sección 13.6.5) con totales por tarea incluidos los reintentos.

### Contexto

Sin evals no hay forma de saber si los agentes mejoran o empeoran con cada cambio. Sin estadísticas, no hay forma de medir ROI del sistema.

### Alcance

**Entra en este plan**:

- Modelo EvalDataset, EvalRun, EvalResult.
- Promoción de tareas reales aprobadas a golden dataset con un click.
- LLM-as-judge con criterios custom (ej. 'sigue PEP 8', 'usa el tono de marca').
- Eval CI: cuando se cambia un prompt de agente global, corre dataset contra versión nueva vs antigua, métricas comparativas.
- Shadow evals: muestra aleatoria (5% default) de tareas reales se replican con agente revisor especializado.
- Dashboard de calidad por agente, por proyecto, por release de prompt.
- Dashboard de estadísticas del tenant: tasa éxito por agente, tiempo medio, coste medio, tareas top y bottom, tendencias temporales.
- Identificación de outliers (agentes que destacan o flaquean).
- Alertas configurables ('si tasa éxito de X baja del 70%, avisa').
- Exportación CSV/PDF de reportes.
- Comparativa cross-tenant solo para System Admin (no para tenants).
- **Dashboard de consumo del proyecto** (sección 13.7 del .docx): tarjetas de resumen (coste acumulado, tokens totales input/output/cached, número de runs, coste medio, run más costoso, indicador de budget con barra de progreso y marcadores de umbrales), segmentaciones por plan/agente/modelo/temporal, indicador de alertas recientes de budget y banner si pausado al 100%.
- **Explorador de runs del proyecto** (sección 13.8 del .docx): tabla filtrable con columnas timestamp/plan/tarea/agente/rol/modelo/duración/tokens/coste USD/coste moneda tenant/verdict/retry_count. Filtros por rango temporal, plan, tarea, agente, rol, modelo, verdict, umbral mínimo de coste. Filtros combinados guardables. Exportación CSV/XLSX. URL compartible con filtros aplicados.
- **Tabla de Runs en detalle de tarea** (sección 13.6.5 del .docx): una fila por execution con timestamp/agente/rol/modelo/duración/tokens (input, cached, output)/coste USD/coste moneda tenant/verdict. Pie con totales agregados.
- Toggle "ver en moneda del tenant" / "ver en USD" en el explorador. Tooltip de trazabilidad con el rate aplicado.

**Queda fuera (otras fases)**:

- Optimización automática de prompts (queda para iteración posterior).
- AutoML para selección de modelo (queda fuera).

### Decisiones Clave

- Golden dataset por tenant (sus datos, sus criterios).
- LLM-as-judge usa un modelo distinto al que evalúa (evita sesgo).
- Shadow evals NO bloquean ejecución real, solo registran resultado.

### Riesgos Identificados

| Riesgo                                               | Probabilidad | Impacto | Mitigación                                                                         |
| ---------------------------------------------------- | ------------ | ------- | ---------------------------------------------------------------------------------- |
| LLM-as-judge tiene sesgo                             | Media        | Medio   | Usar modelo distinto + criterios objetivos + revisión humana periódica de samples. |
| Datasets desactualizados producen métricas engañosas | Media        | Medio   | Refresh del dataset con nuevas tareas reales cada N días.                          |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Datasets y Eval Runs

#### `task_14_01` — Modelos EvalDataset, EvalRun, EvalResult, EvalCriterion

- [x] **Título**: Modelos EvalDataset, EvalRun, EvalResult, EvalCriterion
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_14_01_a
    description: "Modelos EvalDataset, EvalRun, EvalResult, EvalCriterion"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_eval_models.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_02` — Promoción de tarea real a golden con UI 'Promote to dataset'

- [x] **Título**: Promoción de tarea real a golden con UI 'Promote to dataset'
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_14_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_02_a
    description: "Promoción de tarea real a golden con UI 'Promote to dataset'"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/promote-to-dataset.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_03` — Endpoints CRUD de datasets y criterios

- [x] **Título**: Endpoints CRUD de datasets y criterios
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_14_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_03_a
    description: "Endpoints CRUD de datasets y criterios"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_eval_endpoints.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — LLM-as-Judge y Métricas

#### `task_14_04` — LLM-as-judge con criterios custom + diferente modelo que el evaluado

- [x] **Título**: LLM-as-judge con criterios custom + diferente modelo que el evaluado
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_14_04_a
    description: "LLM-as-judge con criterios custom + diferente modelo que el evaluado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_llm_judge.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_05` — Métricas estándar: pass rate, p50/p95 latency, coste medio, tokens medios

- [x] **Título**: Métricas estándar: pass rate, p50/p95 latency, coste medio, tokens medios
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_14_04`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_05_a
    description: "Métricas estándar: pass rate, p50/p95 latency, coste medio, tokens medios"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_metrics.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_06` — Comparativa entre dos eval runs (diff)

- [x] **Título**: Comparativa entre dos eval runs (diff)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_14_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_06_a
    description: "Comparativa entre dos eval runs (diff)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_eval_diff.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — CI y Shadow Evals

#### `task_14_07` — Integración con CI: cuando se cambia prompt, dispara eval automático en CI

- [x] **Título**: Integración con CI: cuando se cambia prompt, dispara eval automático en CI
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops + backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_14_07_a
    description: "Integración con CI: cuando se cambia prompt, dispara eval automático en CI"
    check_type: automated
    runtime: generic-shell
    command: "actionlint .github/workflows/eval-on-prompt-change.yml"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_08` — Bloqueo de merge si regresión > umbral configurable

- [ ] **Título**: Bloqueo de merge si regresión > umbral configurable
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: devops
- **Dependencias**: `task_14_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_08_a
    description: "Bloqueo de merge si regresión > umbral configurable"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_regression_block.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_09` — Shadow eval: muestra aleatoria (5% default) de tareas reales se replica con revisor

- [ ] **Título**: Shadow eval: muestra aleatoria (5% default) de tareas reales se replica con revisor
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_14_08`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_09_a
    description: "Shadow eval: muestra aleatoria (5% default) de tareas reales se replica con revisor"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_shadow_eval.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_10` — Detección de drift: alerta si calidad cae sostenidamente

- [ ] **Título**: Detección de drift: alerta si calidad cae sostenidamente
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_14_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_10_a
    description: "Detección de drift: alerta si calidad cae sostenidamente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_drift_detection.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Dashboards y Cierre

#### `task_14_11` — Dashboard de calidad por agente, proyecto, release de prompt

- [ ] **Título**: Dashboard de calidad por agente, proyecto, release de prompt
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_14_11_a
    description: "Dashboard de calidad por agente, proyecto, release de prompt"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/eval-dashboard.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_12` — Dashboard de estadísticas del tenant: tasa éxito, tiempo, coste, top/bottom agentes

- [ ] **Título**: Dashboard de estadísticas del tenant: tasa éxito, tiempo, coste, top/bottom agentes
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_14_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_12_a
    description: "Dashboard de estadísticas del tenant: tasa éxito, tiempo, coste, top/bottom agentes"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/tenant-stats.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_13` — Identificación de outliers + alertas configurables

- [ ] **Título**: Identificación de outliers + alertas configurables
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_14_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_13_a
    description: "Identificación de outliers + alertas configurables"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_outlier_detection.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_14` — Exportación CSV/PDF

- [ ] **Título**: Exportación CSV/PDF
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_14_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_14_a
    description: "Exportación CSV/PDF"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_stats_export.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_15` — Comparativa cross-tenant (solo System Admin)

- [ ] **Título**: Comparativa cross-tenant (solo System Admin)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_14_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_15_a
    description: "Comparativa cross-tenant (solo System Admin)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_cross_tenant_stats.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_14_16` — Documentación + ADRs + changelog

- [ ] **Título**: Documentación + ADRs + changelog
- **Tiempo estimado**: 6 h
- **Complejidad**: s
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_14_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_14_16_a
    description: "Documentación + ADRs + changelog"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/14-evals-estadisticas.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_14_01
  description: "Eval CI bloquea regresión"
  hint: "Modificar prompt de un agente para empeorarlo a propósito"
  checklist:
    - "CI corre eval automáticamente al hacer push del cambio"
    - "Si la métrica cae más del umbral, CI marca rojo y bloquea merge"
    - "Reporte detallado de qué tareas del dataset fallaron"

- id: human_14_02
  description: "Shadow eval no afecta producción"
  hint: "Activar shadow eval al 5% y observar carga"
  checklist:
    - "Las tareas reales se ejecutan normalmente sin retraso"
    - "El 5% sampleado se replica en background con el revisor"
    - "Los resultados aparecen en el dashboard de calidad"
    - "Si drift detectado, alerta al admin"

- id: human_14_03
  description: "Estadísticas del tenant son accionables"
  hint: "Tenant con 3 meses de uso revisa sus stats"
  checklist:
    - "Tasa de éxito por agente con tendencia temporal"
    - "Identificación de agentes outliers (mejores y peores)"
    - "Coste medio por tipo de tarea"
    - "Sugerencias automáticas ('agente X está bajando, considera revisar su prompt')"

- id: human_14_04
  description: "Exportación funciona"
  hint: "Exportar reporte mensual como PDF"
  checklist:
    - "PDF generado con cabecera, gráficas, tablas"
    - "CSV exportable con datos crudos para análisis externo"
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 15** (`15-instalador-produccion.md`).
