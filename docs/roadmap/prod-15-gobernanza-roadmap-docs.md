---
plan_id: prod-15-gobernanza-roadmap-docs
title: "Gobernanza: roadmap sincerado, CLAUDE.md real y validación humana pendiente"
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas (incluye ventanas de validación humana)
estimated_effort_person_days: 9
estimated_cost_human_eur: 4.050 € – 5.400 €
estimated_cost_ai_eur: 30 € – 60 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P2
---

# Plan prod-15 — Gobernanza: roadmap sincerado, CLAUDE.md real y validación humana pendiente

## Cabecera

| Campo                              | Valor                                           |
| ---------------------------------- | ----------------------------------------------- |
| **ID del Plan**                    | `prod-15-gobernanza-roadmap-docs`               |
| **Bloqueado por**                  | — (independiente; coordina con prod-04/prod-13) |
| **Prioridad**                      | P2                                              |
| **Tiempo estimado (calendario)**   | 2-3 semanas                                     |
| **Tiempo estimado (persona-días)** | 9                                               |
| **Rama git sugerida**              | `plan/prod-15-gobernanza-roadmap-docs`          |

> Nota: el campo "Estado" se ha eliminado deliberadamente de esta cabecera. La fuente de
> verdad del estado es el frontmatter YAML (ver hallazgo docsroadmap-6, que este plan corrige
> en todos los planes existentes).

---

## Resumen

La auditoría de producción de 2026-06-10 confirma que el código y la documentación de
contenido son de calidad alta, pero la **gobernanza documental ha divergido de la realidad**:

1. **CLAUDE.md** —el contexto que cargan los agentes IA en cada sesión— describe 6 componentes
   sin código (`apps/web-app`, `apps/memorizer`, `apps/personal-assistant`,
   `apps/webhook-dispatcher`, `packages/shared-auth`, `packages/shared-db` son `.gitkeep`
   puros), omite `apps/watchdog` y `packages/sdk-python` que sí existen, y exige mergear a
   `main` cuando la rama por defecto es `master` (docsroadmap-1).
2. **~26 fases del roadmap** están en `pending_human_validation` tras haberse empezado en
   bloque (2026-05-30/31) violando las reglas duras del propio protocolo (`blocking_plan` sin
   completar, varias `in_progress` a la vez). Todo el código 07–16 está en `master` sin
   sign-off humano (docsroadmap-2).
3. Los índices del roadmap (**EXECUTION-SEQUENCE.md**, **README.md**) describen un estado de
   2026-05-29 que contradice los frontmatter actuales, y los changelogs de 06.8/06.9 siguen
   sin crearse (docsroadmap-3).
4. **architecture-overview.md** dibuja un plano de control con servicios que el installer no
   genera (docsroadmap-4), la **matriz RBAC** no contiene los routers `/admin/platform-settings`
   y `/admin/ollama` añadidos después (docsroadmap-5), las cabeceras internas de los planes
   duplican un campo "Estado" ya desincronizado (docsroadmap-6), los **trailers de commit**
   declarados obligatorios solo aparecen en el 12% de los commits (quality-9), hay restos de
   higiene local en la raíz y demos de fase mezcladas con tooling en `scripts/` (quality-11),
   y el **router de backup importa el paquete `workers`** rompiendo la frontera de apps que el
   propio código declara (api-9).

Este plan **sincera la documentación con la realidad y restaura el valor normativo del
protocolo**: o las reglas se cumplen, o se cambian explícitamente con decisión humana. No
añade features.

## Alcance

**Entra**:

- Actualizar CLAUDE.md (estructura real del repo, rama `master`, watchdog/sdk-python).
- ADR + re-estado honesto de las ~26 fases en `pending_human_validation`, con campaña de
  validación humana priorizada para las fases que tocan producción.
- Crear changelogs faltantes (06.8, 06.9); actualizar/archivar EXECUTION-SEQUENCE.md y
  corregir README.md del roadmap.
- Alinear architecture-overview.md con la topología real del compose generado.
- Extender la matriz RBAC y su test parametrizado con los routers nuevos + check de drift.
- Decidir y aplicar la política real de trailers `Plan-Id`/`Task-Id` (hook o relajación).
- Higiene de raíz y `scripts/` (bytecode huérfano, logs, demos a `scripts/demos/`).
- Restaurar la frontera apps: `routers/backup.py` deja de importar `workers`.
- Tests automáticos de gobernanza documental (doc-lint en pytest) para que el drift no
  reaparezca en silencio.

**Queda fuera**:

- Ejecutar las validaciones humanas de las 26 fases (las hace un humano; este plan organiza
  la cola, prioriza y deja el estado honesto).
- Construcción de imágenes/Dockerfiles (prod-01), CI en verde (prod-02), cobertura con gate
  (quality-6, en prod-02).
- El bloqueo del event loop por I/O síncrono en backup (api-3, en prod-13) — aunque la tarea
  `task_gov_app_boundary_11` lo resuelve de facto para estos dos endpoints; se anota la
  coordinación para no duplicar trabajo.
- Renombrar la rama `master` → `main` (se documenta `master` como la real; renombrar exigiría
  tocar CI, protecciones y hábitos — decisión aparte si alguien la quiere).

## Decisiones clave

Las decisiones de producto/proceso van como **ADR propuesto** — las cierra un humano, no este plan:

- **D1 — Destino de las ~26 fases en `pending_human_validation`** (ADR nuevo, task 03):
  - _Opción A_: campaña de validación humana completa, fase a fase, antes de tocar estados.
    Coste humano alto (~26 sesiones), pero el protocolo queda intacto.
  - _Opción B_: re-estado honesto — añadir un campo `gate_override: {by, date, reason}` al
    frontmatter de cada fase empezada con gate saltado, actualizar el protocolo de CLAUDE.md
    para reconocer el override humano explícito, y mantener `pending_human_validation` como
    cola real de trabajo.
  - _Recomendación_: **híbrida** — Opción B como base documental inmediata + campaña
    priorizada (Opción A) para las 4 fases que tocan producción directamente:
    `12-backup-restore`, `15-instalador-produccion`, `08-sso-empresarial`, `09-marketplace`.
- **D2 — Trailers `Plan-Id`/`Task-Id`** (task 09): _Opción A_: hook `commit-msg` en
  pre-commit que los exige siempre. _Opción B_: relajar `conventions.md` — obligatorios solo
  en commits de tareas de plan (ramas `plan/*`), opcionales en mantenimiento.
  _Recomendación_: **B** + hook que valida el trailer únicamente cuando la rama actual es
  `plan/*` (la regla escrita pasa a ser la real sin perder trazabilidad donde importa).
- **D3 — EXECUTION-SEQUENCE.md**: actualizar al estado real vs archivar como histórico.
  _Recomendación_: **archivar** con banner "histórico (estado a 2026-05-29); la fuente de
  verdad es el frontmatter de cada fase" — el documento ya incumplió su propia promesa de
  actualización por ola, y duplicar estado es la causa raíz de docsroadmap-3 y docsroadmap-6.
- **D4 — Componentes vacíos en CLAUDE.md**: eliminarlos del árbol vs anotarlos.
  _Recomendación_: **anotarlos** como "previsto; hoy integrado en api-server (ADR 0033)" —
  conserva la intención de diseño sin mentir sobre el presente.
- **D5 — Frontera apps en backup (api-9)**: extraer un paquete compartido de destinos vs
  mover conectividad/listado a tareas Celery encoladas por nombre.
  _Recomendación_: **Celery por nombre** (patrón ya existente para restore en el mismo
  fichero); resuelve además el bloqueo del event loop (api-3, prod-13) sin paquete nuevo.

## Tareas

### Fase A — Contexto que cargan los agentes (CLAUDE.md y arquitectura)

#### `task_gov_claude_md_01` — CLAUDE.md refleja el repo real

- [ ] **Título**: Actualizar estructura, rama y componentes de CLAUDE.md
- **Descripción**: En `CLAUDE.md`: (1) en el árbol "Estructura del Repositorio" (líneas
  46-56), anotar `web-app`, `memorizer`, `personal-assistant`, `webhook-dispatcher`,
  `shared-auth`, `shared-db` y `shared-domain` como "previsto — hoy integrado en api-server
  (`api_server/assistant/`, `api_server/memorizer/`, `api_server/webhooks/`,
  `api_server/auth/`, `api_server/db/`), ver ADR 0033" según D4; (2) añadir `apps/watchdog`
  y `packages/sdk-python`, que existen y no figuran; (3) sustituir `main` por `master` en el
  protocolo de cierre (línea 163) y en "Cosas que NO Hacer" (línea 218).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_01_a
    runtime: python-pytest
    command: "pytest tests/unit/test_docs_governance.py::test_claude_md_tree_matches_repo tests/unit/test_docs_governance.py::test_claude_md_no_main_branch -v"
  ```
  (test nuevo: cada ruta del árbol de CLAUDE.md existe y, si está marcada como activa,
  contiene código; la palabra `main` no aparece como rama destino del protocolo)

#### `task_gov_arch_overview_02` — architecture-overview alineado con la topología real

- [ ] **Título**: Plano de control = los 5 servicios que el installer genera
- **Descripción**: En `docs/context/architecture-overview.md` (líneas 33, 104-105, 433):
  sustituir la lista del plano de control (que incluye personal-assistant,
  webhook-dispatcher, memorizer y web-app) por los servicios reales de
  `compose_generator.py:97-110` (api-server, orchestrator, workers,
  notification-dispatcher, admin-panel + infraestructura), anotando que
  asistente/memorizer/webhooks son módulos internos de api-server con enlace al ADR 0033.
  Corregir el diagrama Mermaid. Misma narrativa que CLAUDE.md (task 01).
- **Tiempo**: 3 h · **Complejidad**: s · **Depende de**: task_gov_claude_md_01
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_02_a
    runtime: python-pytest
    command: "pytest tests/unit/test_docs_governance.py::test_arch_overview_control_plane_matches_compose_generator -v"
  ```
  (el test importa `CORE_SERVICES`/lista de servicios de `installer_backend.compose_generator`
  y verifica que el plano de control documentado no lista servicios inexistentes)

### Fase B — Roadmap sincerado

#### `task_gov_adr_gates_03` — ADR: destino de las fases con gate saltado

- [ ] **Título**: ADR (proposed) con las opciones de D1 para decisión humana
- **Descripción**: Redactar `docs/05-architecture-decisions/00XX-gates-validacion-humana-roadmap.md`
  con las opciones A/B/híbrida de D1, el inventario exacto de fases afectadas (las ~26 en
  `pending_human_validation`, con `started_at` y `blocking_plan` incumplido de cada una) y la
  lista priorizada de campaña (12-backup, 15-instalador, 08-sso, 09-marketplace primero).
  Status `proposed`: NO se aplica nada hasta que un humano lo apruebe.
- **Tiempo**: 4 h · **Complejidad**: m
- **Tests automáticos**: no aplica (documento de decisión); el lint de docs de CI valida
  frontmatter del ADR.

#### `task_gov_reestado_04` — Aplicar la decisión: re-estado honesto + cola de validación

- [ ] **Título**: Frontmatter de las ~26 fases coherente con la decisión del ADR
- **Descripción**: Tras aprobación humana del ADR (task 03): añadir `gate_override` (o el
  mecanismo decidido) al frontmatter de cada fase empezada con gate saltado; actualizar la
  sección "Reglas Duras del Protocolo" de CLAUDE.md para reconocer el override explícito;
  publicar la cola priorizada de validación humana (orden, responsable, ventana) en
  `docs/roadmap/README.md`. Ninguna fase pasa a `completed` aquí: eso solo lo produce la
  validación humana real de cada una.
- **Tiempo**: 2 d · **Complejidad**: m · **Depende de**: task_gov_adr_gates_03 (aprobado)
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_04_a
    runtime: python-pytest
    command: "pytest tests/unit/test_roadmap_frontmatter.py -v"
  ```
  (test nuevo que parsea todos los frontmatter de `docs/roadmap/*.md` y verifica: como mucho
  una fase `in_progress`; toda fase empezada tiene `blocking_plan` completado O
  `gate_override` documentado; estados dentro del enum válido)

#### `task_gov_changelogs_05` — Changelogs faltantes de 06.8 y 06.9

- [ ] **Título**: Crear `docs/07-changelog/06.8-rbac-enforcement.md` y `06.9-agent-scoped-kbs.md`
- **Descripción**: Son los dos únicos planes con código mergeado sin entrada de changelog
  (el resto de fases pendientes sí la tienen). Generarlas con el formato de las existentes
  (p.ej. `06.10-kb-categories.md`), resumiendo lo entregado y los tests que lo cubren.
- **Tiempo**: 3 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_roadmap_frontmatter.py::test_every_started_phase_has_changelog -v"
  ```

#### `task_gov_indices_06` — EXECUTION-SEQUENCE.md y README.md del roadmap al día

- [ ] **Título**: Archivar EXECUTION-SEQUENCE como histórico y corregir el README
- **Descripción**: Según D3: añadir banner de histórico a `EXECUTION-SEQUENCE.md` (estado
  congelado a 2026-05-29: dice "10 completados" cuando hay 16, lista como violación viva
  3 fases ya `completed`, clasifica 07–16 como "nuevos") y retirar su pretensión de
  actualización por ola. En `docs/roadmap/README.md`: corregir "17 planes" (línea 10) al
  recuento real (35 numerados + descriptivos + serie prod-01…prod-16), añadir la serie
  correctiva de producción a la tabla y la cola de validación de task 04.
- **Tiempo**: 3 h · **Complejidad**: s · **Depende de**: task_gov_reestado_04
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_docs_governance.py::test_roadmap_readme_count_matches_files -v"
  ```

#### `task_gov_cabeceras_07` — Una sola fuente de estado por plan + huecos canónicos

- [ ] **Título**: Eliminar el campo "Estado" duplicado de las cabeceras de los planes
- **Descripción**: Según docsroadmap-6: retirar la fila `| **Estado** | ... |` de la tabla de
  cabecera de todos los planes en `docs/roadmap/*.md` (ya está desincronizada: 06.8 dice
  `pending_approval` con frontmatter `pending_human_validation`; 15 dice `in_progress`),
  dejando el frontmatter como única fuente. Además: documentar o renumerar el hueco
  `02-getting-started/01→03` (falta `02-*`) y decidir el destino de `docs/provider-example/`
  (mover bajo la carpeta canónica que corresponda o documentarla en el índice).
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_07_a
    runtime: python-pytest
    command: "pytest tests/unit/test_roadmap_frontmatter.py::test_no_estado_field_in_plan_headers -v"
  ```

### Fase C — Contratos documentales vivos

#### `task_gov_rbac_matrix_08` — Matriz RBAC al día + guardia anti-drift

- [ ] **Título**: Añadir `/admin/platform-settings` y `/admin/ollama` a rbac.md y al test parametrizado
- **Descripción**: Según docsroadmap-5: añadir a `docs/04-reference/rbac.md` las secciones de
  `platform_settings.py` (4 endpoints, prefix `/admin/platform-settings`) y `ollama.py`
  (3 endpoints, prefix `/admin/ollama`), ambos `require_system_admin`; añadir sus filas al
  test parametrizado `tests/integration/test_rbac_resources.py`. Añadir un check (test o paso
  de CI) que compare la salida de `scripts/audit_rbac.py` contra la matriz documentada y
  falle si aparece un endpoint no documentado — es la guardia que evita el siguiente drift.
- **Tiempo**: 1 d · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_08_a
    runtime: python-pytest
    command: "pytest tests/integration/test_rbac_resources.py -k 'platform_settings or ollama' -v"
  - id: auto_gov_08_b
    runtime: python-pytest
    command: "pytest tests/unit/test_rbac_matrix_drift.py -v"
  ```

#### `task_gov_trailers_09` — Política real de trailers de commit

- [ ] **Título**: Conventions.md y práctica de trailers convergen (D2)
- **Descripción**: Según quality-9 (solo 80/662 commits no-merge llevan `Plan-Id`; ninguno de
  los últimos ~30): aplicar la opción decidida de D2. Con la recomendación B: editar
  `docs/context/conventions.md:106-123` para que los trailers sean obligatorios solo en
  commits de tareas de plan (ramas `plan/*`) y opcionales en mantenimiento; añadir hook
  `commit-msg` a `.pre-commit-config.yaml` que exige `Plan-Id`/`Task-Id` cuando la rama
  actual empieza por `plan/`. Si el humano elige la opción A, el hook se aplica siempre y
  conventions.md no cambia.
- **Tiempo**: 4 h · **Complejidad**: s · **Depende de**: decisión D2 (puede resolverse junto al ADR de task 03)
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_09_a
    runtime: python-pytest
    command: "pytest tests/unit/test_commit_msg_hook.py -v"
  ```
  (test del script del hook: acepta mensaje con trailers en rama plan/\*, lo rechaza sin
  ellos, y deja pasar mantenimiento en master)

### Fase D — Higiene y fronteras

#### `task_gov_higiene_10` — Higiene de raíz y reordenación de scripts/

- [ ] **Título**: Borrar restos locales y mover demos de fase a `scripts/demos/`
- **Descripción**: Según quality-11 (nada de esto está trackeado en git; es higiene local +
  reorganización): (1) borrar `scripts/__pycache__/setup_webscorpo.cpython-313.pyc` (bytecode
  huérfano de un script eliminado) y los logs locales de la raíz (`admin-panel-dev.log`,
  `.e2e-api-server.log`); (2) mover los ~27 `demo_human_*` / `setup_demo_*` / `.demo_state*`
  a `scripts/demos/`, actualizando los carve-outs de lint en `pyproject.toml:92-93`, los
  imports de `_demo_common.py` y TODAS las referencias en guías de tests humanos
  (`docs/`, grep previo obligatorio); (3) verificar que `.gitignore` sigue cubriendo los
  patrones tras el movimiento.
- **Tiempo**: 4 h · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_scripts_layout.py -v"
  ```
  (test nuevo: `scripts/` raíz solo contiene tooling de plataforma; los demos viven bajo
  `scripts/demos/` y compilan con `py_compile`; ninguna guía de docs referencia la ruta vieja)

#### `task_gov_app_boundary_11` — Restaurar la frontera apps: backup sin importar workers

- [ ] **Título**: `routers/backup.py` deja de hacer `from workers...` (api-9, D5)
- **Descripción**: `celery_client.py:6` declara "we never import the workers package", pero
  `routers/backup.py:222` y `:351` importan `workers.backup_destinations` y
  `workers.backup_encryption` para test de conectividad y listado remoto, ejecutando
  boto3/paramiko dentro del api-server. Con la recomendación D5: crear dos tareas Celery
  cortas en workers (`backup.test_destination`, `backup.list_remote`) encoladas por nombre
  desde el router (patrón ya usado por restore en el mismo fichero), con el endpoint
  devolviendo el resultado vía polling corto o respuesta diferida. **Coordinación**: esto
  resuelve también el bloqueo de event loop de esos dos endpoints (api-3, asignado a
  prod-13) y toca el router que prod-04 (backup/DR) modifica — sincronizar ramas para no
  pisarse; si prod-04 se ejecuta antes, esta tarea se rebasa sobre su resultado.
- **Tiempo**: 1,5 d · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_gov_11_a
    runtime: python-pytest
    command: "pytest tests/unit/test_app_boundaries.py::test_api_server_never_imports_workers -v"
  - id: auto_gov_11_b
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_destination_endpoints.py -v"
  ```
  (el primero es la guardia permanente: AST/grep de imports `workers` en todo `api_server`;
  el segundo cubre el nuevo flujo encolado de test/list con worker simulado)

## Hallazgos de auditoría cubiertos

| fid           | Severidad | Tarea(s) que lo cierran                                   |
| ------------- | --------- | --------------------------------------------------------- |
| docsroadmap-1 | high      | task_gov_claude_md_01                                     |
| docsroadmap-2 | high      | task_gov_adr_gates_03, task_gov_reestado_04               |
| docsroadmap-3 | medium    | task_gov_changelogs_05, task_gov_indices_06               |
| docsroadmap-4 | medium    | task_gov_arch_overview_02                                 |
| docsroadmap-5 | medium    | task_gov_rbac_matrix_08                                   |
| docsroadmap-6 | low       | task_gov_cabeceras_07                                     |
| quality-9     | low       | task_gov_trailers_09                                      |
| quality-11    | low       | task_gov_higiene_10                                       |
| api-9         | low       | task_gov_app_boundary_11 (coordinado con prod-04/prod-13) |

## Riesgos

1. **La campaña de validación humana no avanza**: el plan deja la cola priorizada, pero las
   ~26 validaciones dependen de disponibilidad humana; si no hay compromiso de agenda, el
   re-estado honesto (Opción B) es lo único que evita que el roadmap vuelva a mentir.
   Mitigación: el ADR exige nombrar responsable y ventana por fase.
2. **Conflicto de ramas con prod-04 y prod-13**: `task_gov_app_boundary_11` toca
   `routers/backup.py`, que ambos planes también modifican. Mitigación: declarada la
   coordinación en la tarea; quien llegue segundo rebasa.
3. **Mover demos rompe guías de tests humanos**: las guías de `docs/` y los `.demo_state*`
   referencian rutas en `scripts/`. Mitigación: grep exhaustivo previo + test
   `test_scripts_layout.py` que falla si queda una referencia vieja.
4. **Edición masiva de frontmatter (26 ficheros) introduce errores de YAML**: un typo en un
   frontmatter rompe el parseo del estado de fase. Mitigación: `test_roadmap_frontmatter.py`
   parsea todos los frontmatter en CI desde esta misma tarea.
5. **Los doc-lint tests generan fricción**: tests que comparan docs contra código pueden dar
   falsos positivos ante refactors legítimos. Mitigación: mantenerlos quirúrgicos (lista de
   servicios, recuentos, imports prohibidos), no semánticos.
6. **El hook de trailers molesta en mantenimiento**: si la decisión D2 acaba siendo la
   opción A (hook siempre), los ~12 PRs/semana de mantenimiento sufrirán rechazos.
   Mitigación: la recomendación B limita el hook a ramas `plan/*`.

## Tests humanos del Plan

```yaml
- id: human_gov_01
  description: "CLAUDE.md y architecture-overview cuentan la arquitectura real"
  hint: "Leer ambos documentos con el repo al lado"
  checklist:
    - "El árbol de CLAUDE.md no lista ningún componente vacío sin anotación 'previsto/integrado en api-server'"
    - "apps/watchdog y packages/sdk-python aparecen en el árbol"
    - "Ninguna instrucción del protocolo menciona la rama 'main'"
    - "El plano de control de architecture-overview lista exactamente los servicios que genera compose_generator.py"

- id: human_gov_02
  description: "El roadmap permite saber qué está validado y qué no"
  hint: "Abrir docs/roadmap/README.md y 3 fases al azar"
  checklist:
    - "README.md indica el recuento real de planes y la cola priorizada de validación humana"
    - "Toda fase empezada con gate saltado tiene gate_override (o el mecanismo decidido en el ADR) en su frontmatter"
    - "Ninguna tabla de cabecera de plan muestra un 'Estado' distinto del frontmatter (el campo ya no existe)"
    - "EXECUTION-SEQUENCE.md tiene banner de histórico o estado actualizado, según la decisión D3"
    - "Existen docs/07-changelog/06.8-*.md y 06.9-*.md"

- id: human_gov_03
  description: "ADR de gates revisado y decidido por un humano"
  hint: "Leer el ADR y firmar una de las opciones"
  checklist:
    - "El ADR lista todas las fases afectadas con su started_at y blocking_plan incumplido"
    - "Se ha elegido opción (A/B/híbrida) y el ADR pasa a accepted con el nombre del decisor"
    - "La sección de reglas duras de CLAUDE.md refleja la decisión"

- id: human_gov_04
  description: "Matriz RBAC y fronteras de código verificables"
  hint: "Ejecutar los tests de guardia y revisar la matriz"
  checklist:
    - "rbac.md contiene /admin/platform-settings y /admin/ollama con su rol mínimo"
    - "pytest tests/unit/test_rbac_matrix_drift.py pasa, y falla si se comenta un endpoint de la matriz (probar y revertir)"
    - "grep 'from workers' en apps/api-server/src no devuelve nada"
    - "Probar 'Test conectividad' de un destino de backup caído en el panel: la UI no congela el resto de la plataforma"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. ADR de gates (task 03) decidido por un humano (status `accepted`), no solo redactado.
3. Los 4 tests humanos del plan validados y firmados.
4. Suites de gobernanza (`test_docs_governance.py`, `test_roadmap_frontmatter.py`,
   `test_app_boundaries.py`, `test_rbac_matrix_drift.py`) integradas en CI.
5. Entrada de changelog en `docs/07-changelog/prod-15-gobernanza-roadmap-docs.md`.
6. PR del plan mergeado a `master`.

## Próximo Plan

Siguiente de la serie correctiva por prioridad: **prod-16-frontend-i18n-calidad** (P2 —
i18n ES+EN real y partición de componentes del frontend). Dentro del mismo nivel P2 también
queda **prod-14-tenancy-defensa-profundidad**; ambos son independientes de este plan y pueden
ejecutarse en paralelo. La cola de validación humana publicada por `task_gov_reestado_04`
sigue su propio calendario al margen de la serie prod-\*.
