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

- [x] **Título**: Actualizar estructura, rama y componentes de CLAUDE.md
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

- [x] **Título**: Plano de control = los 5 servicios que el installer genera
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

- [x] **Título**: ADR (proposed) con las opciones de D1 para decisión humana
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
  - ⏳ **La anotación de 2026-07-31 está OBSOLETA; medido de nuevo el 2026-08-01:** el ADR 0138 está **`accepted`** (opción C) y sus tres primeras consecuencias están **aplicadas**: las **6** fases en deuda (`06.10-kb-categories`, `06.17-capacitacion-agentes`, `11.1-budgets-fx`, `15-instalador-produccion`, `16-human-agents`, `prod-17-bucle-ai-reviewer`) llevan `gate_override` con `reason` escrito, `CLAUDE.md` tiene la sección «La excepción al gate», el `xfail` ya no está y `auto_gov_04_a` (`pytest tests/unit/test_roadmap_frontmatter.py`) da **11 passed** — incluidos `test_started_phase_declares_its_gate` y `test_gate_override_carries_a_written_justification`.
  - **Lo corregido hoy**: el `README.md` del roadmap seguía diciendo que el ADR estaba `proposed` y «pendiente de decisión humana». Ya no.
  - **Por qué la casilla NO se marca**: falta el tercer bullet de la tarea — la cola de validación tiene el **orden** publicado, pero no **responsable ni ventana**. Y eso no es trabajo pendiente, es una decisión que un agente no puede tomar: el propio ADR 0138 la deja fuera de su alcance por escrito («`prod-15` exige nombrarlos; este ADR no los puede inventar») porque compromete el calendario de una persona. **Firma humana pendiente**, sin nada que implementar antes.
  - ✅ **Verificado el 2026-08-01 — los tres guardas del ADR 0138 no son cargo cult, y había un cuarto agujero.** Lo que pedía comprobar esta casilla era que el mecanismo se cumple HOY y que los tests lo vigilan de verdad. Se hizo por mutación, rompiendo cada invariante a propósito y mirando el rojo antes de restaurar:

    | Guarda                                                                            | Mutación aplicada                                             | Resultado                                          |
    | --------------------------------------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------- |
    | `test_gate_override_carries_a_written_justification`                              | `reason` de `11.1-budgets-fx` reducido a 9 caracteres         | **rojo**, nombrando plan y longitud                |
    | `test_gate_override_only_where_the_gate_is_actually_unmet`                        | `11-guardrails-precios` → `completed`                         | **rojo**: «la excepción caducó: `11.1-budgets-fx`» |
    | `test_gate_debt_inventory_has_not_grown` + `test_started_phase_declares_its_gate` | `prod-11` → `pending_human_validation` con su gate incumplido | **rojo los dos**                                   |

    Los 6 `gate_override` vivos tienen los cuatro campos y una `reason` por encima del mínimo. Solo hay una fase `in_progress` (`marketplace-v2-despliegue`).

  - 🔧 **Agujero encontrado y cerrado (1): un `gate_override` sobre un plan sin `blocking_plan` pasaba los once guardas en silencio.** `test_gate_override_only_where_the_gate_is_actually_unmet` solo mira planes con dependencias (`if deps and not sin_cerrar`), y `unmet_gates()` descarta los planes sin bloqueantes **antes** de mirar el override. Comprobado inyectando uno en `prod-14` (`blocking_plan: null`): 11/11 en verde. No es hipotético — `blocking_plan` es una lista YAML multilínea en varios planes y basta vaciarla para que el gate deje de declararse y el override sobreviva como afirmación de una excepción que ya no se refiere a nada. Cerrado con `test_gate_override_names_a_gate_that_actually_exists` (rojo con la inyección, verde al retirarla).
  - 🔧 **Agujero encontrado y cerrado (2): 17 ficheros del roadmap llevan un `status:` del enum de fases y NO llevan `plan_id`, así que ningún guarda de gate los ve.** `_plans()` exige los dos campos; sin `plan_id`, un fichero se salta `test_at_most_one_phase_in_progress`, el gate, el `gate_override` y el changelog. Ocho de los diecisiete son las fases del córtex, **con casillas `- [ ]` y `blocking_plan` propio**: planes en todo menos en el campo que los haría auditables. Se destapó por un recuento que daba 46 por fichero y 35 por plan. Cerrado con `test_no_new_roadmap_file_escapes_the_guards_by_omitting_plan_id`, con la deuda acotada e inventariada al estilo de `_GATE_DEBT_2026_07_29`: **no arregla los 17** —darles `plan_id` los somete de golpe al guarda de changelog, que es trabajo de otro carril— pero impide que aparezca el número dieciocho. Mutación verificada: un `.md` nuevo con `status: in_progress` y sin `plan_id` lo pone rojo.
  - ➕ **De paso, la tercera guarda que faltaba**: el recuento de la cola de validación del `README.md` («35 planes están en `pending_human_validation`») no lo vigilaba nadie, a diferencia del recuento hermano de planes de construcción. Ahora sí (`test_readme_declares_the_real_size_of_the_validation_queue`, mutado a 34 → rojo). Y conviene dejar dicho, porque casi me lleva a «corregir» un número correcto: **35 son los planes; los 46 que cuenta `CONTINUE_HERE.md` incluyen 11 ficheros sin `plan_id`**. Son las dos poblaciones del agujero (2).
  - Suite tras los tres tests nuevos: `tests/unit/test_roadmap_frontmatter.py` **14 passed**.
  - ⏳ **2026-08-10 — la parte de frontmatter sigue coherente, medida y no
    supuesta; lo que falta sigue siendo una firma de calendario.** - Ejecutado: `pytest tests/unit/test_roadmap_frontmatter.py
tests/unit/test_docs_governance.py` → **25 passed**. - **Una sola fase `in_progress`** (`marketplace-v2-despliegue`), que es lo que
    exige la regla dura del protocolo — comprobado además a mano con
    `grep -l '^status: in_progress' docs/roadmap/*.md`, porque el test y el grep
    pueden discrepar y el que manda es el fichero. - Recuento por estado hoy: 46 `pending_human_validation`, 25 `completed`,
    14 `pending_approval`, 1 `in_progress`, 1 `blocked`, más los ficheros del
    roadmap que llevan `status:` de otro enum (`published`, `informe`, `open`,
    `delivered`, `archived`, `approved`, `remediation_implemented`) y que son
    justamente la población del **agujero (2)** de arriba. - **Por qué sigue sin marcarse, sin cambios**: falta el tercer bullet de la
    tarea — la cola de validación tiene **orden** publicado pero no
    **responsable ni ventana**. Eso compromete el calendario de una persona, el
    ADR 0138 lo deja fuera de su alcance por escrito, y no hay nada que
    implementar antes. Es una firma, no trabajo.
  - 🔧 **2026-08-12 — la incoherencia se movió de sitio, y esta vez es NUEVA.** Los gates
    y los `gate_override` siguen coherentes (`pytest tests/unit/test_roadmap_frontmatter.py`
    → **16 passed**, y sólo `marketplace-v2-despliegue` está `in_progress`). Pero la
    medición de hoy encontró otra cosa que este mismo casillero cubre —«frontmatter
    coherente»— y que las olas de estas dos semanas han creado:
    **los CATORCE planes en `pending_approval` tienen casillas marcadas.** El enum de
    CLAUDE.md define ese estado como «plan definido pero **no empezado**», así que hoy no
    describe a ninguno de los catorce. De ellos, **seis no tienen NADA abierto**:
    `cadena-pr-plan`, `prod-03`, `prod-04` (se unió hoy, al cerrarse su última casilla),
    `prod-05`, `prod-07` y `prod-09`.
    - **Por qué NO les cambio el estado**: pasar a `pending_human_validation` exige la
      entrada en `docs/07-changelog/` —lo pide `test_every_started_phase_has_changelog`, y
      sólo `prod-07` la tiene—, y escribirla es auditar entre 9 y 18 tareas por plan. Y
      hay un motivo de fondo mejor: cambiarles el estado afirmaría una aprobación que
      **nadie ha dado**. Que se hayan implementado sin aprobar es precisamente el problema
      de gobernanza que este plan existe para no tapar.
    - **Entregado**: el guarda que impide que crezca a espaldas de nadie, con el patrón de
      inventario congelado de este mismo fichero
      (`_DELIVERED_BUT_UNSTARTED_2026_08_12` + `test_no_new_plan_is_delivered_while_still_labelled_unstarted`
      y su hermano de entradas muertas). **Rojo verificado en las dos direcciones**:
      marcando la única casilla abierta de `prod-14` → rojo nombrándolo; y poniendo
      `prod-07` en `pending_human_validation` → rojo por entrada caduca. Restaurado: 16 passed.
    - **Lo que sigue faltando es lo de siempre y sigue siendo humano**: la cola de
      validación tiene orden publicado pero **no responsable ni ventana**. El ADR 0138 lo
      deja fuera de su alcance por escrito. **Firma humana pendiente, sin nada que
      implementar antes.**
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

- [x] **Título**: Crear `docs/07-changelog/06.8-rbac-enforcement.md` y `06.9-agent-scoped-kbs.md`
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

- [x] **Título**: Archivar EXECUTION-SEQUENCE como histórico y corregir el README
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

- [x] **Título**: Eliminar el campo "Estado" duplicado de las cabeceras de los planes
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

- [x] **Título**: Añadir `/admin/platform-settings` y `/admin/ollama` a rbac.md y al test parametrizado
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

- [x] **Título**: Conventions.md y práctica de trailers convergen (D2)
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
  - ⏳ **Sigue pendiente, y el 2026-08-01 se midió su radio de explosión antes de tocar nada:** confirmado que no hay `scripts/demos/`, que quedan **14 `demo_human_*` + 7 `setup_demo_*`** (de 26 `.py` en la raíz de `scripts/`; el plan decía «~27» contando el resto de tooling), que `scripts/__pycache__/check_commit_trailers.cpython-313.pyc` sigue ahí —bytecode huérfano, aunque no el `setup_webscorpo` que citaba la tarea— y que `tests/unit/test_scripts_layout.py` no existe.
  - **Dos correcciones a la descripción de la tarea, que la subestima:** (1) dice que «nada de esto está trackeado en git» — **es falso**, los 21 demos y `_demo_common.py` están todos en `git ls-files`, así que esto no es higiene local sino un rename de ficheros versionados; (2) `_demo_common.py` **tiene que moverse con ellos**: **11 scripts** hacen `from _demo_common import …`, que solo resuelve porque el directorio del script está en `sys.path` — si se quedara en `scripts/` los once dejarían de arrancar.
  - **Blast radius medido: 74 ficheros** mencionan `demo_human_*` / `setup_demo_*` / `_demo_common` / `.demo_state`, repartidos en `docs/03-guides/human-tests/` (24), `docs/03-guides/gotchas/`, `docs/05-architecture-decisions/`, `docs/07-changelog/`, `scripts/dev/*.ps1`, `.gitignore`, `pyproject.toml`, `apps/api-server/.../seeds/builtin_kbs.py` y `tests/docs/test_human_test_guides.py` (una guarda que se pondría roja a mitad del movimiento).
  - **NO ejecutado a propósito**: es un rename mecánico de 74 ficheros, casi todos fuera del carril de este agente, en una sesión con otros 4 agentes escribiendo en paralelo sobre el mismo árbol. Es exactamente el cambio que hay que hacer **solo**, en una pasada dedicada y sin nadie más tocando el repo. Bajo riesgo técnico, alto riesgo de conflicto.
  - ⏳ **Re-medido el 2026-08-01: idéntico.** 26 `.py` en la raíz de `scripts/`, de los cuales **14 `demo_human_*` + 7 `setup_demo_*`**; `scripts/demos/` no existe; `tests/unit/test_scripts_layout.py` no existe. Nada ha cambiado, y la razón para no hacerlo tampoco: esta pasada vuelve a ser concurrente con otros cuatro carriles. **Sigue siendo la casilla más barata del plan y la que más caro sale hacer mal**: no necesita decisión humana, necesita el repo para ella sola.
  - ✅ **2026-08-10 — el sub-punto (1) se cierra en NEGATIVO: no queda nada que
    borrar.** Verificado fichero a fichero, y la descripción de la tarea está
    desfasada en los tres ítems:
    · `scripts/__pycache__/setup_webscorpo.cpython-313.pyc` **no existe** (el
    script que lo generaba se eliminó hace tiempo). Lo que hay son
    `check_commit_trailers.cpython-31{2,3}.pyc`, bytecode de un script **vivo** y
    en un directorio **no trackeado** (`git ls-files scripts/__pycache__` → vacío):
    borrarlo no es higiene, es ruido que Python regenera al siguiente import.
    · `admin-panel-dev.log` y `.e2e-api-server.log` **no existen** en la raíz.
    · `.gitignore` ya cubre los seis patrones de `.demo_state*` — sub-punto (3),
    verificado.
    Lo único que queda de esta casilla es el sub-punto (2), el movimiento.
  - ⏳ **2026-08-10 — el movimiento: medido por CUARTA vez y NO ejecutado, con la
    razón concreta y una receta para que la próxima no vuelva a medir.** El dato
    que faltaba en las tres anotaciones anteriores es _cuántos ficheros ajenos_
    hay que editar, y es el que decide: **48**, de los cuales 31 son guías de
    `docs/03-guides/human-tests/`. El detalle:

    | Qué                                                                 | Cuánto | Quién lo toca |
    | ------------------------------------------------------------------- | -----: | ------------- |
    | Scripts a mover (`demo_human_*`, `setup_demo_*`, `_demo_common.py`) |     22 | el rename     |
    | Guías de tests humanos que citan rutas                              |     31 | otro carril   |
    | `pyproject.toml` (carve-outs de ruff, 2 líneas)                     |      1 | otro carril   |
    | `.gitignore` (6 patrones `scripts/.demo_state*`)                    |      1 | otro carril   |
    | `apps/api-server/.../seeds/builtin_kbs.py`                          |      1 | otro carril   |
    | `tests/docs/test_human_test_guides.py`                              |      1 | otro carril   |
    | Gotchas / ADR / changelogs (relato histórico)                       |     11 | —             |

    **Y una trampa que ninguna anotación anterior nombró**:
    `tests/docs/test_human_test_guides.py` resuelve la existencia de cada script
    recomendado contra `_SCRIPTS = repo/scripts`. En cuanto se mueve el primer
    fichero y antes de tocar esa constante, **la guarda se pone roja** — y su
    mensaje dirá «la guía recomienda un script que no existe», que es
    indistinguible de un error de documentación real. El movimiento no tiene
    estado intermedio verde.
    **Receta para la pasada dedicada** (con el repo para ella sola), en este orden
    y en un solo commit:
    1. `git mv scripts/{demo_human_*,setup_demo_*,_demo_common}.py scripts/demos/`
       — `_demo_common.py` va con ellos **obligatoriamente**: 11 scripts hacen
       `from _demo_common import …`, que solo resuelve porque el directorio del
       script entra en `sys.path`.
    2. `pyproject.toml`: `scripts/demo_*.py` → `scripts/demos/demo_*.py` y
       `scripts/setup_demo_*.py` → `scripts/demos/setup_demo_*.py`.
    3. `tests/docs/test_human_test_guides.py`: `_SCRIPTS` pasa a `scripts/demos`.
    4. `sed` sobre las 31 guías + `builtin_kbs.py`: `scripts/demo_human_` →
       `scripts/demos/demo_human_`, `scripts/setup_demo_` →
       `scripts/demos/setup_demo_`.
    5. `.gitignore`: los seis `scripts/.demo_state*` → `scripts/demos/.demo_state*`
       (los scripts escriben el estado **junto a sí mismos**).
    6. Escribir `tests/unit/test_scripts_layout.py` (`auto_gov_10_a`, que **no
       existe**): la raíz de `scripts/` solo contiene tooling, los demos compilan
       con `py_compile`, y ninguna guía cita la ruta vieja.
       **Por qué sigo sin hacerlo**: 48 ficheros, 35 de ellos fuera de la propiedad
       de este carril, en una pasada con otros cuatro agentes escribiendo el mismo
       árbol, y sin estado intermedio verde. El riesgo técnico es bajo; el de
       conflicto, alto; y el coste de hacerlo mal —guías de test humano apuntando a
       scripts que no arrancan— lo paga un humano en mitad de una validación.

  - ⏳ **2026-08-12 — sigue sin ejecutarse, y el motivo ya NO es la medición.** Re-verificado
    en 30 segundos y sin cambios: **14 `demo_human_*` + 7 `setup_demo_*` + `_demo_common.py`**
    en la raíz de `scripts/`, `scripts/demos/` no existe, `tests/unit/test_scripts_layout.py`
    tampoco. La receta de arriba sigue siendo correcta al pie de la letra; no hace falta
    medir una sexta vez.
    **El bloqueo real, dicho sin rodeos**: de los 48 ficheros que toca, **35 no son de este
    carril** (`docs/03-guides/human-tests/` ×31, `pyproject.toml`, `.gitignore`,
    `apps/api-server/.../seeds/builtin_kbs.py`, `tests/docs/test_human_test_guides.py`), y
    el movimiento **no tiene estado intermedio verde**: en cuanto se mueve el primer
    fichero, `test_human_test_guides.py` se pone rojo diciendo «la guía recomienda un
    script que no existe». Hacer sólo la parte propia deja el repo roto para todos los
    demás. **Esto no necesita una decisión ni más análisis: necesita una pasada con el
    repo entero asignado a un solo agente, y son ~30 minutos.**

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
  - ⏳ **Pendiente. Re-medido el 2026-08-01 con el inventario exacto**, que es el dato que faltaba para dimensionarla: `from workers` aparece en **6 ficheros** de `api_server`, no en uno —
    `routers/backup.py:234,235,369,370` (`backup_destinations`, `backup_encryption`),
    `backup_restore.py:164` (`restore_per_tenant`),
    `code_diff.py:91,92` (`git_repos`, `plan_git`),
    `docs_structure/kb_sync.py:736` (`git_repos`),
    `docs_viewer/service.py:655` (`git_repos`) y
    `routers/review.py:44` (`review_runtime`, y este es un import **de módulo**, no diferido dentro de la función).
  - **Por qué eso cambia el alcance**: `auto_gov_11_a` es
    `test_app_boundaries.py::test_api_server_never_imports_workers`, una guarda sobre **todo** `api_server`. Arreglar solo `backup.py` la deja igual de roja, así que la casilla **no puede cerrarse** con el alcance que la tarea describe. O se amplía la tarea a los 6, o la guarda nace con una allowlist declarada de excepciones — y esa es una decisión de diseño, no de implementación.
  - **NO abordado en esta pasada**: los cinco ficheros restantes son operaciones de git/datos que el memorándum del proyecto manda ejecutar **en el worker**, así que moverlas es un rediseño, no un rename; y crear las dos tareas Celery de `backup.test_destination` / `backup.list_remote` toca `apps/workers/**` y cambia el contrato de dos endpoints que consume el `admin-panel`, ambos fuera del carril de este agente. Sigue vigente la coordinación con prod-04/prod-13 que la propia tarea declara.
  - ⏳ **Re-medido el 2026-08-01: el inventario no se ha movido.** Siguen siendo **6 ficheros y 10 imports** (`backup.py` ×4, `backup_restore.py`, `code_diff.py` ×2, `docs_structure/kb_sync.py`, `docs_viewer/service.py`, `routers/review.py`). La decisión de diseño que la casilla necesita —ampliar la tarea a los 6, o que `auto_gov_11_a` nazca con una allowlist de excepciones declarada— **sigue sin tomarse**, y es lo único que la bloquea. Apunte para quien la tome: los 5 ficheros que no son `backup.py` importan **funciones puras** (`_run_git`, `worktree_coordinates`, `sign_review_url`, `DEFAULT_TENANT_SCOPED_TABLES`), no ejecutan I/O de worker por el hecho de importarlas; el que de verdad rompe la frontera en espíritu —boto3/paramiko dentro del event loop del api-server— es `backup.py`. Una allowlist que distinga «importa un helper» de «ejecuta trabajo de worker» sería honesta; una que liste los 6 sin distinguir, no.
  - 🔧 **2026-08-10 — la guarda ya existe (era el hueco más caro), y la casilla
    sigue abierta a propósito.** Lo que se descubrió al ir a ejecutar el test
    declarado: **`tests/unit/test_app_boundaries.py` no existía**. O sea que
    `auto_gov_11_a` —«la guardia permanente» según la propia tarea— llevaba
    tres pasadas nombrando un fichero inexistente: tal cual, falla en la
    recolección, y ese rojo no distingue «la frontera está rota» de «el arnés
    apunta a la nada». Nadie vigilaba la frontera **en absoluto**.
    - **Entregado**: `tests/unit/test_app_boundaries.py` (6 tests, verde).
      Descubrimiento por **AST**, no por grep — este repositorio está lleno de
      comentarios que explican por qué NO se importa `workers`, y un grep los
      cuenta como violaciones. Congela el inventario de los 6 módulos con la
      clasificación que la anotación de arriba pedía: cinco `helper` (función
      pura; la salida es moverla a `packages/`) y **uno solo `worker-work`**,
      `routers/backup.py`, que es el hallazgo api-9 de verdad y el que cierra la
      decisión D5. Hay un test dedicado a que ese conjunto siga teniendo un solo
      elemento: si aparece un segundo, el problema dejó de ser un caso aislado
      con decisión pendiente y pasó a ser un patrón.
    - **Rojo verificado en las dos direcciones** (no solo «falla si crece»):
      añadido un `from workers.git_repos import _run_git` a `routers/_guards.py`
      → rojo nombrando el módulo; renombrada una entrada del inventario a un
      fichero inexistente → rojo por entrada muerta, que es lo que impide que la
      allowlist envejezca afirmando una deuda ya pagada. Restaurado: 6 passed.
    - **Nota de rumbo que la tarea no tenía**: `backup.py` **ya no bloquea el
      bucle de eventos** — sus dos llamadas van por `to_thread`, así que la
      coordinación con api-3/prod-13 que la tarea declara está resuelta por otro
      camino. Lo que queda es estrictamente el acoplamiento de import.
    - **Por qué NO se marca**: el enunciado es «`routers/backup.py` deja de hacer
      `from workers…`», y sigue haciéndolo. Cerrarlo exige crear
      `backup.test_destination` y `backup.list_remote` en `apps/workers/**` y
      cambiar el contrato de dos endpoints que consume el `admin-panel`: dos
      superficies fuera de este carril. La guarda es el suelo que impide que la
      deuda crezca mientras esa decisión espera, no el arreglo.
    - **Arnés corregido**: `auto_gov_11_a` ya apunta a un fichero que existe.
      `auto_gov_11_b` (`tests/integration/test_backup_destination_endpoints.py`)
      **sigue sin existir** y no puede existir todavía: cubre «el nuevo flujo
      encolado», que es justo lo que falta por diseñar.
  - 🔎 **2026-08-12 — esto ha dejado de ser una deuda estética: el endpoint NO PUEDE
    funcionar donde está.** Lo que faltaba para decidir D5 era saber si mover la sonda al
    worker aporta algo más que limpieza. Aporta corrección, y se verifica en un grep:
    - Los adaptadores resuelven sus credenciales por el _seam_ de secretos, y el que se les
      pasa desde el router es `EnvSecretsProvider`, que lee **`os.environ` del proceso que
      lo ejecuta** (`backup_encryption.py:157-164`: `backup_s3_access_key_id` →
      `WORKERS_BACKUP_S3_ACCESS_KEY_ID`).
    - El proceso que lo ejecuta es el **api-server**, y en el compose desplegado el servicio
      `api-server` **no declara ni una sola variable `WORKERS_*`** (verificado sobre
      `docker/docker-compose.manuals.yml`, servicio `api-server`: 0 coincidencias). Las
      `WORKERS_BACKUP_*` viven en la lane `workers-backup`, que es donde
      `04-reference/backup-restore.md` manda ponerlas.
    - Traducción operativa: en cuanto alguien configure un destino remoto con credencial
      (S3/B2/SFTP/rclone) siguiendo la documentación, **el botón «probar conectividad» del
      panel dirá FAIL** con un «faltan credenciales» perfectamente correcto y perfectamente
      inútil, y el listado remoto devolverá vacío en silencio (es best-effort). Hoy nadie
      lo ha visto porque **no hay ningún destino configurado** en este stack.
    - **Lo que esto cambia para D5**: la salida no es «encolar por elegancia», es que la
      sonda tiene que correr **donde están los secretos**. Y añade una restricción que
      ninguna anotación anterior tenía: la lane `privileged` es `--concurrency=1` y es la
      que corre el backup nocturno y los restores, así que encolar ahí una sonda la deja
      esperando detrás de un backup de media hora. O va a `default` con las
      `WORKERS_BACKUP_*` replicadas en esa lane, o hace falta una lane propia. **Eso es
      diseño de despliegue, y sigue fuera de este carril.**
    - **NO ejecutado**, y la razón de hoy pesa más que la de agosto: tocar el contrato de
      dos endpoints que consume el `admin-panel` la misma tarde en que se redespliega el
      stack cambia un fallo latente que nadie ha visto por un fallo nuevo en el camino
      caliente del despliegue. La guarda de `test_app_boundaries.py` impide que la deuda
      crezca mientras tanto.
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
    command: "pytest tests/unit/test_app_boundaries.py -v"
  - id: auto_gov_11_b
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_destination_endpoints.py -v"
  ```
  (el primero es la guardia permanente: AST de imports `workers` en todo `api_server`;
  el segundo cubre el nuevo flujo encolado de test/list con worker simulado)

> **`auto_gov_11_a` cambió de selector el 2026-08-10, a propósito.** Nombraba
> `::test_api_server_never_imports_workers`, un test que **no puede estar en verde
> hoy**: el api-server sí importa `workers` en seis módulos. Un comando que solo
> puede salir rojo no es un gate, es ruido que se aprende a ignorar — y encima el
> fichero entero no existía, así que fallaba en la recolección. El fichero sí es
> ejecutable y verde, y contiene el invariante que hoy se puede exigir de verdad:
> la deuda está inventariada, clasificada y **no crece**. El día que se implemente
> la decisión D5, el inventario se queda vacío solo y el test literal es trivial.

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
