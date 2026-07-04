---
title: Auditoría de plataforma 2026-07-03 — proyectos, planes, MCP/tools, git y runtime + plan de remediación
date: 2026-07-03
status: published
scope: código (HEAD 3d22337) + sistema vivo (BD/Redis/contenedores, solo lectura). Subsistemas proyectos, planes, MCP/tools, git, guardrails, runtime de ejecución. La auditoría de runs 2026-07-02 es baseline (no se duplica).
method: 3 exploradores + diagnóstico en vivo + 29 verificadores adversariales (Opus 4.8, lente «refutar», 2ª lente de impacto para P0) + lectura íntegra de los ADR acusados
docs_language: es
related_adrs:
  [
    "0008",
    "0021",
    "0035",
    "0045",
    "0048",
    "0049",
    "0052",
    "0072",
    "0081",
    "0085",
    "0089",
    "0091",
    "0094",
    "0095",
  ]
verification_run: wf_20482ae9-ee5 (29/29 veredictos)
---

# Auditoría de plataforma 2026-07-03 — informe

> **Petición del operador:** «análisis exhaustivo revisando si es correcto todo el tema de proyectos, planes,
> MCP, tools, git… y de paso mira si hay alguna mejora de funcionalidad interesante». Decisiones acordadas:
> verificación **código + sistema vivo** (solo lecturas); **runs incluidos** (la auditoría 07-02 es baseline);
> mejoras como **catálogo priorizado** (los ADR solo se redactan tras aprobación del operador). Reportado en
> vivo además: «sigue apareciendo el tema de producir output en las ejecuciones; la exploración legítima no
> funciona».

## 1. Veredicto ejecutivo

**El núcleo es sólido; el sistema se rompe en la «última milla» de tres flujos y en la fricción de
exploración del runtime.** El camino feliz —CRUD de proyectos/plantillas/fork, chat→plan→sync→dispatch→review
con claims atómicos e idempotencia, cliente MCP + catálogo de 24 templates, memoria/RAG (validada sana en la
baseline 07-02)— funciona. Los defectos se concentran en:

1. **Cadena auto-PR del cierre de plan (git)** — 🔴 rota de extremo a extremo con remoto real, pero **latente**
   (0 planes `completed` en BD; nunca se ha disparado). Se arregla antes del primer cierre real.
2. **Guardrails/validación humana en runtime** — 🔴 **P0**: el motor de guardrails nunca corre en ejecución de
   agentes (g1) y el gate de validación humana falla-abierto por desajuste de vocabulario (g6). Un agente con
   egress puede ejecutar tools sensibles sin ningún punto de control, pese a que el operador crea tenerlo.
3. **Fricción de exploración del runtime** — 🟡 el síntoma «produce output»: guardas y clasificaciones que
   penalizan lectura legítima y ciclos edit-build. En arreglo en la rama actual (`guardas-research-por-novedad`).

Ningún hallazgo es una **fuga cross-tenant explotable** (c5, el candidato a P0 de aislamiento, se degrada a
hardening: lookup por PRIMARY KEY, sin vector cross-tenant). No hay pérdida de datos en ningún defecto
confirmado.

### Tabla de salud por subsistema

| Subsistema                                     | Salud | Resumen                                                                                                                                                                                                 |
| ---------------------------------------------- | :---: | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Proyectos (CRUD, plantillas, fork, git-config) |  🟢   | Sano. Canonicalización de slugs correcta.                                                                                                                                                               |
| Planes — máquina de estados                    |  🟡   | `PUT /tasks` evita la state machine (c1, real); `submit_verdict` muta en crudo (c2, higiene); `PlanStatus` divergente (c10, higiene).                                                                   |
| Planes — cierre / PR / git                     |  🔴   | Rama del PR ≠ rama de commits (P1), bare divergente (P2), sin push al remoto en el modo default (P3), PR no persistido (P6), sin visor de diffs ni flujo de conflictos (P7). **Latente** (0 completed). |
| Tareas / DAG / orquestación                    |  🟢   | Correcto. `_revert_to_ready` sin `tenant_id` (c5) = hardening, no fuga. Turno de planning no durable (c9).                                                                                              |
| MCP / catálogo de tools                        |  🟡   | `import-para-ver` es diseño (g2, ADR 0052); marketplace no materializa (g3, deuda ADR 0081); tools sin executor asignables (g4); docling-mcp no arranca (g5).                                           |
| Guardrails / validación humana (runtime)       |  🔴   | **P0**: motor nunca cableado en ejecución (g1); gate humano fail-open por vocabulario (g6). Principios 10 y 11 incumplidos de facto.                                                                    |
| Runtime de ejecución (guardas de novedad)      |  🟡   | Fricción de exploración legítima y falsos positivos de convergencia (r2/r4/r5a/r7). En arreglo (rama actual).                                                                                           |
| Memoria / RAG / memorizer                      |  🟢   | Sano (baseline 07-02). Único pendiente: purga del ruido de 63 memorias destiladas con `llama3.2:1b`.                                                                                                    |
| Cierre documental del plan (Technical Writer)  |  🔴   | El changelog automático (`docs/07-changelog/`) nunca se genera en runtime (c4). Criterio de cierre 4 de CLAUDE.md incumplido.                                                                           |

## 2. Metodología

- **Exploración**: 3 agentes Explore en paralelo sobre proyectos/planes, MCP/tools y git + diagnóstico en vivo
  del síntoma «produce output» sobre BD/Redis/logs con la imagen `agent-runtime` del día.
- **Verificación adversarial**: 29 verificadores independientes (**Opus 4.8**, esfuerzo xhigh), cada uno con la
  consigna de **refutar** su hallazgo (buscar callers no vistos, scope de ADR malinterpretado, código que sí
  hace lo que se dice que falta). Cada hallazgo que acusa a un ADR `accepted` exigió leer el ADR completo.
  Severidad P0 → 2ª lente de impacto real. Los **refutados/matizados se publican igual** (sección 7).
- **Clases de evidencia**: E1 (evidencia en vivo suficiente), E2 (leído por 1 explorador → 2º lector
  independiente cita file:line en HEAD), E3 (afirmaciones universales «nunca/ningún» → grep exhaustivo de
  callers + query read-only en BD viva).
- **Solo lecturas** sobre BD/contenedores (orden vigente: nada de desbloquear/relanzar hasta verificación del
  operador). Todo en la rama `plan/runs-visor-trabajo`, sin push.

## 3. Arrastre de la baseline 2026-07-02 (no se re-narra)

La auditoría de runs `auditoria-runs-2026-07-02.md` sigue siendo la fuente para ejecuciones/memoria/workers.
Estado hoy de sus pendientes:

| Ítem de la baseline 07-02                            | Estado hoy (2026-07-03)                                                                          | Dónde continúa                                      |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| Convergencia núcleo (ADR 0087/0089/0090/0091/0092)   | 🟢 sostenido: `max_iterations_exceeded` = 0 post-guardas; escaladas tempranas (11 iter vs 30-50) | `guardas-research-por-novedad.md` (F3, métricas)    |
| Durabilidad de `/data` (incidente root:root)         | 🟢 resuelto (named volume durable) según memoria de proyecto                                     | —                                                   |
| Guardas de research por novedad (d034122)            | 🟡 desplegado; F2 (e2e «Tests de feature») pendiente                                             | `guardas-research-por-novedad.md` (Fase F ampliada) |
| Purga de memorias destiladas con `llama3.2:1b`       | ⏳ pendiente del operador: **63** entradas 1b + 73 `ollama` (52 vivas de 137)                    | acción del operador                                 |
| Re-ejecución limpia del plan CI4                     | 🟡 en curso: plan `019f1397` `in_progress`, del proyecto Api CI                                  | observación pasiva (orden: no relanzar)             |
| registry-proxy / egress runtime-templates (ADR 0094) | 🟢 desplegado; F2/F4 de su plan pendientes                                                       | `registry-egress` (memoria de proyecto)             |

## 4. Hallazgos por causa raíz

Cada hallazgo lleva: **severidad** · **veredicto** (confirmado/matizado/refutado) · **evidencia clave** ·
**remediación** (→ fichero de plan). El detalle completo de la verificación adversarial está en la sección 7.

### Causa A — Identidad git sin fuente única → `cadena-pr-plan.md`

La rama (`plan/{id8}-{slug}`) y el bare repo de un plan se derivan en **tres sitios con reglas distintas**. Sin
fuente única, ejecución y auto-PR operan sobre ramas/bares diferentes.

- **P1** · P1 (latente) · **confirmado** — La rama del auto-PR **nunca** coincide con la de los commits: el
  enqueue antepone `"Plan: "` (`review.py:484`) y el worker deriva de `_slugify(title)` (`plan_pr.py:65`);
  la ejecución usa `plan.slug` (sin prefijo, ascii-fold, cap 60). Triple divergencia calculada en vivo para el
  plan `019f1397`. _Contradice_ ADR 0072 (que declara la invariante «rama consistente con el push»).
- **P2** · P2 · **confirmado** — El bare de ejecución (`{project.slug}.git`, sin `origin`) ≠ el de clone/PR
  (`{basename(url)}.git`, con `origin`). Dos bares físicos distintos para todo proyecto con remoto.
- **P8** · nit · **matizado (inerte)** — `PlanGitPolicies()` hardcoded en `execution.py:1005`, pero
  `push_review_to_bare` **no lee** políticas → sin impacto funcional. Higiene.

### Causa B — Mutación de estado fuera de la máquina de estados → `ciclo-vida-planes-fixes.md`

- **c1** · media · **confirmado** — `PUT /tasks` no consulta `transition_task_status`: solo valida DAG y setea
  `status` crudo. `backlog→done` pasa sin validar; el trigger `trg_compute_task_ready` amplifica (promueve
  dependientes del falso `done`). Ya flagueado en `auditoria-zonas-2026-06.md:85`.
- **c2** · higiene · **matizado** — `submit_verdict` muta `plan.status` en crudo, pero completar **antes** de
  encolar el PR es el diseño **aceptado** de ADR 0072 fase 2 (no un bug); no hay `approved_at`/`completed_at`
  que sellar. Residuo: encaminar por la state machine por uniformidad.
- **c10** · higiene · **matizado** — Dos definiciones de `PlanStatus` (StrEnum de dominio vs `Literal`), pero
  sin bug runtime (los `Literal` no se validan; columna `String(32)` libre; no-op seguro). Unificar por higiene.

### Causa C — Tenancy y durabilidad del orquestador → `ciclo-vida-planes-fixes.md`

- **c5** · hardening · **matizado** — `_revert_to_ready` consulta por `Task.id` **sin `tenant_id`** en sesión
  BYPASSRLS (viola la regla dura #1), pero `Task.id` es PRIMARY KEY → sin vector cross-tenant; el `task_id`
  viene del mismo evento interno. **No es P0** (candidato inicial refutado como fuga). Fix por consistencia +
  guard-test estático.
- **c9** · media · **confirmado** — El turno de respuesta del equipo corre como `asyncio.create_task` detached
  en el api-server (`responder.py:854`), sin Celery/cola durable/reintento/recuperación. Un reinicio a mitad
  de turno pierde la respuesta (el mensaje del usuario sí es durable).

### Causa D — Guardrails / gate humano sin enforcement en runtime → `tools-y-cierre-plan-fixes.md` (coord. prod-03)

- **g1** · **P0 seguridad** · **confirmado** — El motor `GuardrailPipeline` solo se instancia en `planning.py`
  y solo cablea pre*llm/post_llm; `apps/workers` tiene **0 refs** a guardrail; el agent-runtime lo reconoce en
  docstrings («lands in Plan 11»). Los hooks `pre_tool`/`post_tool` **nunca corren** en ejecución de agentes.
  Con egress web/registry (ADR 0094/0067) las salidas MCP/HTTP/RAG **reentran crudas** → inyección indirecta
  sin defensa. `security_level` tampoco se enforcea (`tool_wiring.py:23`). \_Contradice* el principio 10;
  ADR 0035 diseña el motor puro y el host nunca lo cabló.
- **g6** · **P0 seguridad** · **confirmado** — El gate de validación humana emite 4 categorías
  (`code_execution`/`file_write`/`network_access`/`agent_delegation`) que **no intersectan** las 13 canónicas
  de las plantillas → intersección vacía → `requires_human()` siempre `auto` → **fail-open**. Ni el preset
  «Cliente Externo» (13/13 human*required) detiene una tool sensible; las tools MCP nunca se gatean. Los tests
  solo pinean claves (nombres), no valores (categorías), así que CI no lo detecta. \_Contradice* el principio 11.

### Causa E — El catálogo declara lo que el runtime no tiene → `tools-y-cierre-plan-fixes.md`

- **g4** · P1 · **confirmado (peor que el claim)** — `apply_patch`/`search_code`/`summarize_text` son
  asignables pero **sin executor**. La UI no los filtra ni marca. Peor: los **seeds de rol/equipo CI4 los
  asignan por SQL crudo** esquivando la guarda 422 del PUT → **51 agentes tienen `search_code` hoy** (+4
  `summarize_text`). Evidencia viva: `search_code` murió como «unknown tool» en el run `019f27ff` (idx 36).
  _Contradice_ el principio de ADR 0049 (que ya retiró la familia git por esto mismo).
- **g5** · media · **confirmado** — `DOCLING_MCP` se oferta en el picker pero requiere un binario `docling-mcp`
  que upstream no publica; servicio comentado en compose. Única vía Docling operativa: `docling-serve` HTTP.
- **g2** · nit · **matizado** — Las tools MCP declaradas-no-importadas son invisibles al LLM, pero es el
  **diseño de ADR 0052** (import manual; auto-import rechazado). Residuo: UX (badge «requiere import») +
  al importar no se persiste el `input_schema` (la tool se anuncia con params vacíos).
- **g3** · deuda aceptada · **confirmado** — El marketplace no materializa (`Tool/Skill` no se crean; sin
  columna de provenance; gates no corren en install fresco), pero es **diferimiento aceptado y documentado**
  (ADR 0081, H4+M1); el docstring de `ENABLED` desmiente la usabilidad. Sin acción urgente.

### Causa F — Las guardas de novedad castigan exploración legítima (síntoma «produce output») → `guardas-research-por-novedad.md` (Fase F ampliada)

Diagnóstico en vivo con la imagen del día (runs `019f27ff`/`019f292d`, «Tests de feature»). El run `019f27ff`
murió por **429 de cuota claude_sdk**, no por las guardas.

- **r2** · media · **matizado** — `_read_target` ignora offset/limit (paginar = releer el mismo target) y
  `read_counts` es acumulativo sin decay/reset; pero el escenario «paginar fichero grande» **no es alcanzable**
  (el lector builtin no pagina, erra a >1 MB) y el backstop duro está gated. Residuo real: el contador acumulado
  durante la exploración puede disparar la escalada en la primera research call tras producir.
- **r4** · media · **confirmado** — `has_produced` se latchea con producing tools **fallidas** (`graph.py:950`,
  sin comprobar `observation.ok`): un `shell_exec` denegado cuenta como haber producido → desvía cada trip de
  `STATUS_ABORTED` a `needs_human_review` (contamina la cola humana) y cambia el nudge a «FINISH».
- **r5a** · baja · **matizado** — `search_code` no está en `_RESEARCH_TOOLS` y como verbo no clasificado cuenta
  como MUTADOR; pero la sub-afirmación «ni resetea la racha estéril» está **invertida** (sí la resetea) y
  `search_code` no tiene executor (toda llamada es `ok=False`); solo se ofrece al reviewer (donde escala, no
  aborta).
- **r7** · media · **matizado** (candidato P0 **refutado**) — Un comando idéntico repetido tripa
  `repetitive_loop` al 4.º; pero en las **5 ejecuciones reales** siempre **escaló a `needs_human_review`**
  (trabajo preservado), no hard-abort, y **4 de 5 eran comandos idempotentes exitosos** (`composer audit/validate`)
  re-ejecutados, no denegados. Gobernado por ADR 0089 `accepted`. Defecto de fondo real: el `LoopDetector`
  fingerprintea `(tool,args)` **sin reset** → falso positivo de convergencia en ciclos edit-build legítimos.
- **Síntoma en vivo (E1)**: `sed`/`awk` ausentes del allowlist (`command not allowed: sed` 2× en vivo, **r1**);
  el visor hardcodea «stop researching, produce output» para toda variante de nudge (**r3**); los 2
  `needs_human_review` de reviews que aprobaron «files not present» corrieron con imagen vieja (**r6**, pendiente
  e2e). El inventario completo de restricciones y su recalibración (F1-F13) va en el plan de guardas.

### Causa G — Cierre y materialización incompletos del ciclo de plan → `cadena-pr-plan.md` + `tools-y-cierre-plan-fixes.md` + `ciclo-vida-planes-fixes.md`

- **P3** · P2 · **confirmado** — En modo `incremental` (**default**) la rama nunca se empuja al remoto.
  _Contradice_ ADR 0085 decisión 5. → `cadena-pr-plan.md` T3.
- **P4** · media · **confirmado** — `apply_push_policy` es código muerto; «Merge directo a la rama base» se
  comporta idéntico a «Abrir PR». → `cadena-pr-plan.md` T5.
- **P5** · media · **matizado** — Sin re-sync automático del remoto (beat/webhook prometidos en docstring no
  existen); re-guardar la config sí re-sincroniza. → `cadena-pr-plan.md` T6.
- **P6** · media · **confirmado** — La URL/rama del PR no se persiste ni se muestra (sin columna, task
  fire-and-forget). → `cadena-pr-plan.md` T4.
- **P7** · media · **confirmado** — Sin visor de diffs de código ni flujo de conflictos; un rebase conflictivo
  acaba en `commit_failed` genérico excluido del panel de escaladas. → `cadena-pr-plan.md` T8 (+ ADR 0099).
- **c3** · media · **confirmado** — Una tarea `blocked` estanca el plan en `in_progress` sin salida automática
  (`_OPEN_TASK_STATUSES` incluye `blocked`, `plan_progress.py:55`). → `ciclo-vida-planes-fixes.md` T7. _(Ver
  observación od1.)_
- **c4** · alta · **confirmado** — El Technical Writer al cierre es aspiracional: `generate_plan_docs`/
  `render_changelog` solo se invocan desde tests; `_on_task_done` no genera changelog. Criterio de cierre 4 de
  CLAUDE.md incumplido. → `tools-y-cierre-plan-fixes.md` T8.
- **c6** · baja · **confirmado** — Los planes de chat nacen sin `phases[]` (degradación benigna). →
  `ciclo-vida-planes-fixes.md` T8.
- **c7** · baja · **matizado** — Rol desconocido → `assigned=None` silencioso, pero es diseño ADR 0091 D1;
  residuo: falta un warning. → `ciclo-vida-planes-fixes.md` T9.
- **c11** · baja · **confirmado** — `complexity` de tareas de chat siempre `'m'` → el desglose de coste pondera
  igual. → `ciclo-vida-planes-fixes.md` T10.
- **c8** · baja · **matizado** — El board gerencial pinta proyectos como planes; ADR 0008 autorizó el
  placeholder pero la tabla `plans` real ya existe → spec-drift obsoleto. → `ciclo-vida-planes-fixes.md` T11.

## 5. Observaciones (no-código)

- **gov1 — Gobernanza del roadmap:** hay **7 fases en `status: in_progress` simultáneas**
  (`guardas-research-por-novedad`, `prod-18-worktree-en-ejecucion`, `prod-17-bucle-ai-reviewer`,
  `prod-06-ciclo-vida-ejecucion`, `plan-unificacion-provider-id`, `mejoras-2026-06-chat-coste-cortex`,
  `cortex-fases`). El protocolo de CLAUDE.md exige **solo una**. No es un bug de código; se registra para que el
  operador decida cerrar/cancelar/re-etiquetar. _(No se tocan frontmatters en esta auditoría.)_
- **od1 — Run `done` con tarea `blocked` y sin review:** se observó una ejecución `done/success` cuya tarea
  quedó `blocked` sin disparar review — evidencia viva del mecanismo de c3 (una tarea `blocked` no propaga al
  plan). Observación pasiva; ninguna acción de desbloqueo (orden vigente).

## 6. Catálogo de mejoras

### Quick wins (sin ADR)

| Mejora                                                                   | Valor                                                          | Esfuerzo | Recogida en         |
| ------------------------------------------------------------------------ | -------------------------------------------------------------- | -------- | ------------------- |
| `pr_url` + rama en la ficha de plan                                      | trazabilidad del entregable                                    | S        | cadena-pr-plan T4   |
| Panel de `safeguard_stats` en el visor de runs                           | ver falsos positivos de guardas (instrumentación B1 ya existe) | S        | guardas Fase F      |
| Warning de rol desconocido en el planner                                 | evita perder presets por un typo                               | S        | ciclo-vida T9       |
| Badge «requiere import» / «no cableado» en asignación de tools           | deja de ofrecer lo que no se puede usar                        | S        | tools-y-cierre T6   |
| Ampliar allowlist con `sed/awk/sort/uniq/cut/tr/echo` + error accionable | quita fricción de lectura del runtime                          | S        | guardas Fase F (F6) |
| Board gerencial por `plan_id` (c8)                                       | cumple ADR 0008 (no necesita ADR nuevo)                        | M        | ciclo-vida T11      |

### ADR candidatos (SOLO listados — se redactan tras aprobación del operador)

| ADR  | Tema                                                                                          | Disparado por       |
| ---- | --------------------------------------------------------------------------------------------- | ------------------- |
| 0098 | Política de push/PR/re-sync del ciclo de plan (timing, merge directo real, webhook con firma) | P3/P4/P5            |
| 0099 | Visor de diffs de código + flujo de resolución de conflictos                                  | P7                  |
| 0100 | Materialización del marketplace (puente install→catálogo, provenance, gates)                  | g3 (adelantar 0081) |
| 0101 | Discovery MCP en runtime (auto-visibilidad de tools descubiertas)                             | g2                  |
| 0102 | Guardrails persistidos por proyecto + motor completo (4 hooks) en runtime                     | g1/g6               |

## 7. Resultados de la verificación adversarial (29/29, Opus 4.8)

Veredicto por hallazgo. Los **matizados/refutados se publican igual**. `wf_20482ae9-ee5`.

| Id  | Veredicto  | Severidad  | Síntesis del veredicto                                                                                             |
| --- | ---------- | ---------- | ------------------------------------------------------------------------------------------------------------------ |
| P1  | confirmado | P1 latente | Rama del PR ≠ rama de commits (prefijo `plan-` + ascii-fold + cap 60). 0 completed en BD → cero impacto observado. |
| P2  | confirmado | P2         | Bare de ejecución (`{project.slug}.git`, sin origin) ≠ bare de PR (`{basename(url)}.git`).                         |
| P3  | confirmado | P2         | Sin push al remoto en modo `incremental` (default). Contradice ADR 0085 dec.5.                                     |
| P4  | confirmado | media      | `apply_push_policy` código muerto; «merge directo» = «abrir PR».                                                   |
| P5  | matizado   | media      | Sin re-sync automático; re-guardar config sí re-sincroniza. Docstring miente (beat/webhook inexistentes).          |
| P6  | confirmado | media      | URL/rama del PR no persistida ni mostrada (task fire-and-forget).                                                  |
| P7  | confirmado | media      | Sin visor de diffs de código ni flujo de conflictos; `commit_failed` genérico.                                     |
| P8  | matizado   | nit        | `PlanGitPolicies()` hardcoded pero **inerte** (`push_review_to_bare` no lee políticas).                            |
| c1  | confirmado | media      | `PUT /tasks` evita la state machine; `backlog→done` posible; trigger amplifica.                                    |
| c2  | matizado   | higiene    | Mutación cruda de `plan.status`, pero el orden completar→PR es diseño ADR 0072. Sin `approved_at` que sellar.      |
| c3  | confirmado | media      | Tarea `blocked` estanca el plan en `in_progress` sin salida automática (`plan_progress.py:55`).                    |
| c4  | confirmado | alta       | Changelog/docs al cierre nunca se generan en runtime. Criterio de cierre 4 incumplido.                             |
| c5  | matizado   | hardening  | Query BYPASSRLS sin `tenant_id`, pero lookup por PK → **no** fuga cross-tenant (P0 refutado).                      |
| c6  | confirmado | baja       | Planes de chat sin `phases[]` (degradación benigna).                                                               |
| c7  | matizado   | baja       | Rol desconocido → NULL silencioso, pero es diseño ADR 0091 D1; falta warning.                                      |
| c8  | matizado   | baja       | Board pinta proyectos; ADR 0008 lo autorizó pero la tabla `plans` real ya existe (spec-drift).                     |
| c9  | confirmado | media      | Turno de planning no durable (`asyncio.create_task` detached).                                                     |
| c10 | matizado   | higiene    | `PlanStatus` divergente, pero sin bug runtime (`Literal` no valida; columna libre).                                |
| c11 | confirmado | baja       | `complexity` de tareas de chat siempre `'m'`.                                                                      |
| g1  | confirmado | **P0**     | Guardrails `pre_tool`/`post_tool` nunca corren en ejecución; salidas MCP/HTTP/RAG crudas. Principio 10.            |
| g2  | matizado   | nit        | `import-para-ver` es diseño ADR 0052; residuo: `input_schema` no persistido al importar.                           |
| g3  | confirmado | deuda ok   | Marketplace no materializa, pero es diferimiento aceptado ADR 0081; copy honesto.                                  |
| g4  | confirmado | P1         | Tools sin executor asignables; **seeds asignan `search_code` a 51 agentes** esquivando el guard 422.               |
| g5  | confirmado | media      | `DOCLING_MCP` no arranca (sin imagen upstream); solo `docling-serve` HTTP funciona.                                |
| g6  | confirmado | **P0**     | Gate humano **fail-open** por desajuste de vocabulario de categorías; ni «Cliente Externo» detiene nada.           |
| r2  | matizado   | media      | Contadores per-target sin reset, pero «paginar fichero grande» no alcanzable; backstop gated.                      |
| r4  | confirmado | media      | `has_produced` se latchea con producing tools **fallidas** → contamina la cola de review humana.                   |
| r5a | matizado   | baja       | `search_code` sin clasificar/sin executor; sub-claim «no resetea racha» **invertido** (sí la resetea).             |
| r7  | matizado   | media      | Loop-detector tripa comandos idempotentes exitosos re-ejecutados; siempre **escala** (no aborta). P0 refutado.     |

**Recuento:** 18 confirmados · 11 matizados · 0 refutados de plano (2 candidatos a P0 —c5, r7— degradados por
la 2ª lente de impacto). Dos P0 confirmados y sostenidos: **g1** y **g6** (guardrails/gate de runtime).

## 8. Plan de remediación

Cuatro ficheros de plan nacen `pending_approval` (ningún fix implementado, ningún ADR redactado). Orden de
aprobación sugerido:

1. **`guardas-research-por-novedad.md`** (Fase F ampliada) — causa F, **rama ya en curso**; recalibración de las
   restricciones del runtime (síntoma «produce output»).
2. **`tools-y-cierre-plan-fixes.md`** — causas D+E+G; contiene los **dos P0** (g1, g6) → prioridad de seguridad.
3. **`cadena-pr-plan.md`** — causa A+G; la cadena auto-PR (latente pero garantizada al primer cierre real).
4. **`ciclo-vida-planes-fixes.md`** — causas B+C; máquina de estados, tenancy, durabilidad.

> **Nota de prioridad:** aunque la cadena git es la más visible, los **P0 de seguridad (g1, g6)** son el riesgo
> más alto en términos absolutos: un agente autónomo con egress ejecuta tools sensibles sin ningún punto de
> control humano ni check de inyección. Recomiendo abordarlos primero, en coordinación con `prod-03`.
