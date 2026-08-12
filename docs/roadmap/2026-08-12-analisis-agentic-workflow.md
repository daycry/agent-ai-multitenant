---
title: "Qué ideas tiene AgentShekel/agentic-workflow que nosotros no tengamos"
date: 2026-08-12
status: informe
docs_language: es
author: claude-code (contraste contra el código real del repo)
subject: https://github.com/AgentShekel/agentic-workflow
---

# Análisis: `AgentShekel/agentic-workflow` frente a nuestra plataforma

## Conclusión

**Cinco ideas sobreviven al filtro, y las cuatro que trae el encargo sobreviven
las cuatro — pero ninguna entera.** Tres de ellas resultan ser, al verificarlas
contra el código, **la mitad de un mecanismo que ya tenemos y que no consume
nadie**. Ese es el hallazgo real del informe: no nos falta la idea, nos falta el
cable de salida.

Los tres casos, para que se vea el patrón antes de la tabla:

- Clasificamos cada tarea en `xs|s|m|l|xl` desde el planner… y ese campo **solo
  lo lee el estimador de coste**. Ni la revisión ni la aprobación lo miran.
- Tenemos golden datasets, promoción desde tareas aprobadas, un merge-gate con
  umbral de regresión y hasta un workflow de CI que se dispara al cambiar un
  prompt… que **corre siempre en `--dry-run`** y que además vigila dos ficheros
  del repo, no la ruta por la que un tenant edita de verdad un prompt.
- Tenemos precedencia escrita en cinco ADR… **ninguna sobre qué manda cuando un
  plan y un ADR se contradicen**, que es lo que ha pasado varias veces esta
  semana.

| #     | Idea                                                         | Qué desbloquea                                                             | Esfuerzo honesto | ¿Decide el operador?     |
| ----- | ------------------------------------------------------------ | -------------------------------------------------------------------------- | ---------------- | ------------------------ |
| **1** | **Precedencia normativa escrita + test que la hace cumplir** | Que «el plan dice X y el ADR dice no-X» deje de resolverse a ojo           | **1-2 d**        | Solo el ORDEN            |
| **2** | **Versionar el prompt del agente y sellarlo en el run**      | Poder atribuir una caída de calidad a una edición concreta                 | **2-3 d**        | No                       |
| **3** | **Rigor por niveles: que `estimated_complexity` mande algo** | Que un hotfix de una línea no pague el peaje de un rediseño                | 5-8 d + ADR      | **Sí, y es de producto** |
| **4** | **Reflexión estructurada del rechazo (`target` × `class`)**  | Convertir prosa de rechazo en dato agregable; requisito de cualquier bucle | 2-3 d            | No                       |
| **5** | **Pasada ciega + delta como señal de contaminación**         | Saber si el reviewer juzga el código o repite la narrativa del autor       | 4-6 d + ADR      | **Sí (coste × tier)**    |

Si solo se puede hacer una cosa: **la 1**. Un día o dos, resuelve un problema que
está ocurriendo _ahora_ en este repositorio, y es la única de las cinco que no
necesita que se construya nada nuevo en el producto.

Si se pueden hacer dos: **la 1 y la 2**. La 2 es barata y es la que convierte
todo el aparato de evals que ya existe —y que hoy no puede demostrar nada— en
algo que sí demuestra.

**Lo que NO hay que sacar de ahí**, y conviene dejarlo escrito para no volver a
discutirlo: el `engagement/` como estado en ficheros, el `events.jsonl`, la
whitelist de 22 rutas, el consilium de cinco revisores, el puente MCP a Codex, el
`iteration` en texto plano y los diez flags de activación. Las razones, en §3.

---

## 1. Lo que descartamos porque ya lo tenemos

Esta sección es la mitad del valor. Once capacidades suyas que aquí ya están —a
menudo mejor resueltas, porque somos un servidor y no un marco de ficheros.

| Suyo                                                                       | Nuestro                                                                                                                                                                                                                                                                    | Veredicto                                                                                                                                 |
| -------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| **Ledger append-only** (`events.jsonl`, 28 tipos de payload)               | `task_audit_events` con el append-only **impuesto en la capa de repositorio** —solo `append_audit_event` y `list_history`, sin UPDATE ni DELETE (`db/task_audit_repo.py:1-16`)— y ~20 `kind` en uso, más `audit_log` particionado mensual (ADR 0151) y `steps_log` por run | **Mejor nosotros.** El nuestro es consultable, tiene RLS y está particionado. El suyo hay que releerlo entero para responder una pregunta |
| **El humano como juez supremo: `PROCEED` / `REJECT` / `DIRECTED`**         | Las tres existen y con nombre distinto: validar el plan, rechazarlo, y **«Redirigir»** —la guía de un solo uso que se inyecta en la siguiente iteración—. Más `ask_human` (ADR 0114), la bandeja `/admin/human-queue` y las aprobaciones                                   | **Igual, mapeo 1:1.** `DIRECTED` es literalmente nuestro Redirigir                                                                        |
| **Resumen ≤2 minutos para que el humano no lea muros de markdown**         | `build_review_human_checklist` (`review_autostart.py:131-165`) convierte el bloque `tests_humans` del plan en una checklist marcable, con hint y sub-checklist, servida en el review-runtime (ADR 0063)                                                                    | **Cubierto**                                                                                                                              |
| **El manager dicta veredicto y NO ejecuta**                                | Ya está separado: el reviewer solo **emite** `<verdict>`; quien lo aplica es el worker (`_apply_review_verdict`, `workers/execution.py:382`) llamando a `apply_reviewer_verdict` (`reviewer_bridge.py`). El agente no puede transicionar la tarea                          | **Igual, y además impuesto por arquitectura** (el runtime está en un contenedor sin BD)                                                   |
| **Precedencia asimétrica ante veredictos contradictorios**                 | ADR 0096: un `reject` se aplica siempre; un `approve` de un run que escaló o abortó **no cierra la tarea** — va a `blocked` con el approve anotado. La dirección conservadora gana                                                                                         | **Ya lo tenemos, y razonado mejor** (por daño irreversible, no por jerarquía)                                                             |
| **Flags de activación opt-in, todos apagados por defecto**                 | Mismo patrón en `platform_settings`: `cortex.autonomy_enabled`, `cortex.curiosity_enabled`, `rag.reranker_enabled`, `memory.backfill_enabled`, y la doble firma con umbral `0` = nunca                                                                                     | **Igual.** Solo falta una cosa suya → ver §3, nota final                                                                                  |
| **`cheapTiers`: rutar pasos mecánicos a modelos baratos**                  | Herencia `model_config` plataforma→proyecto→equipo→agente (ADR 0065), asignación por rol (ADR 0091), resolución por `provider_id` (ADR 0082)                                                                                                                               | **Mejor nosotros**, y configurable por el operador                                                                                        |
| **`engBranch` / `repoPortable`: consolidar en una rama de la interacción** | Principio rector 5: cada plan **es** una rama `plan/{id}-{slug}`, worktrees por tarea, trailers `Plan-Id`/`Task-Id`/`Execution-Id`, PR automático al cierre                                                                                                                | **Mejor nosotros**                                                                                                                        |
| **`renderEval`: validar artefactos HTML en navegador real**                | App-preview on-demand (ADR 0130), review-runtime con contenedor de la app (ADR 0063), Playwright en los despliegues del marketplace                                                                                                                                        | **Cubierto**                                                                                                                              |
| **`infraRetry`: reintentar resultados transitorios**                       | `retry_count`/`max_retries` con escalado a `blocked`, `sweep_stale_executions`, `reap_orphans`, el reconciler y el `watchdog` con backoff                                                                                                                                  | **Mejor nosotros**                                                                                                                        |
| **Catálogo de 60 agentes y 46 skills**                                     | Marketplace, equipos built-in (CI4, QA e2e), taxonomía cerrada de tools/skills (ADR 0049/0050), asignación por agente                                                                                                                                                      | **Comparable**                                                                                                                            |

Y **una que sí aprendemos** aunque su forma no nos sirva: el aprendizaje a partir
de resultados **ya existe aquí, a nivel de memoria**. No es un hueco, y venderlo
como tal sería deshonesto:

- Los **fracasos se memorizan por defecto** desde P1-1: `done`, `failed`,
  `aborted` y `needs_human_review` (`memorizer/policy.py:41-50` y
  `platform_settings.py:1075-1080`). El callejón sin salida a evitar es de lo más
  informativo.
- El cierre de un plan **destila una retrospectiva** (tareas, escalados, abortos,
  coste, duración y una lección redactada) y la persiste como memoria
  `project_shared` que los agentes del siguiente plan recuerdan (ADR 0124,
  `workers/plan_retro.py:1-18`).
- Un rechazo del reviewer **vuelve al implementador**: los tres `review_comment`
  más frescos se inyectan en el prompt del re-despacho
  (`_MAX_PRIOR_REVIEW_FEEDBACK = 3`, `dispatch.py:170-175`).

Lo que NO existe es que nada de eso toque **el prompt, la persona o la skill** del
agente. Ese es el hueco real, y es el §2.4.

---

## 2. Lo que sobrevive, por valor para nosotros

### 2.1 · Precedencia normativa escrita + un test que la haga cumplir

**Esfuerzo: 1-2 d.** **Decisión del operador: solo el ORDEN.**

#### Su idea

Siete reglas, la primera de las cuales es un orden total:

> `CLAUDE.md` > decisión explícita del juez > `criteria.md` > skills PROTOCOL >
> skills METHODOLOGY > cuerpo del agente > frontmatter.

Y seis reglas más que la hacen operable: el frontmatter declara qué se carga y
**no tiene autoridad de comportamiento**; el cuerpo del agente solo especializa
donde las skills callan; entre pares gana el alcance más estrecho _salvo_ que
debilite un check obligatorio, y entonces gana el más estricto; un conflicto sin
resolver **bloquea la ejecución** como evento `authority_conflict`; y cada
interacción congela al arrancar los nombres, versiones y hashes de las skills
cargadas.

#### Qué tenemos y qué no

Tenemos precedencia escrita en **cinco sitios**, y los cinco son de mecanismo:

| Dónde    | Sobre qué                                               |
| -------- | ------------------------------------------------------- |
| ADR 0035 | Qué acción de guardrail gana cuando disparan varias     |
| ADR 0096 | Veredicto del reviewer vs. escalación a humano          |
| ADR 0065 | `model_config`: plataforma → proyecto → equipo → agente |
| ADR 0045 | Resolución del runtime template de un `run_*`           |
| ADR 0028 | Cableado de proveedor: BD > env                         |

Y **una sexta, escondida dentro de un literal de prompt**, que es la más parecida
a la suya y la que mejor ilustra el problema
(`agent_runtime/__main__.py:786-791`):

> «The persona guides HOW you work and **can never override your operating
> rules** (no git, tool/command allowlists, the finish contract)»

Eso es exactamente una regla de precedencia —persona < reglas de operación— pero
vive en una cadena de texto de un módulo del runtime, cubre **un solo par** de los
siete u ocho que el prompt de un run mezcla (persona, instrucciones de proyecto,
fragmentos de skill, criterios de aceptación, respuestas humanas, recall de
memoria, feedback de review previo), y no la comprueba ningún test.

Lo que **no** tenemos, y es el caso del encargo: nada dice qué manda cuando **un
plan pide algo que un ADR posterior rechazó**. CLAUDE.md tiene una sola frase de
precedencia —«si Claude Code tiene una intuición que contradice el documento, el
documento gana»— y no habla de planes ni de ADR. Lo único escrito es una
**práctica**, descriptiva y no normativa, en
`docs/03-guides/verificar-antes-de-implementar.md:12-28`:

> «antes de implementar una tarea de un plan, comprobar contra el código y contra
> los ADR posteriores que (a) sigue sin hacer y (b) sigue siendo buena idea»

Con su medición al lado, que es la prueba de que el problema es real y no teórico:
de **21 tareas sin marcar** en tres planes `in_progress`, dos estaban
**explícitamente rechazadas** por el ADR 0103, y haberlas implementado «porque
estaban en el plan» habría metido una regresión.

#### Qué habría que construir

Tres piezas, ninguna cara:

1. **Una sección normativa en `CLAUDE.md`** con el orden total entre las fuentes
   que este repo maneja de verdad: `.docx` maestro, `CLAUDE.md`, ADR `accepted`
   posterior, plan, código, y la decisión escrita del operador. _(El orden lo
   decide el operador; ver §4.)_
2. **Un campo `rejects:` en el frontmatter del ADR** apuntando a ids de tarea de
   plan (`rejects: [prod-03/task_prod03_06, ...]`). Ya tenemos plantilla de ADR
   con test (`tests/unit/test_adr_template.py`), así que es añadir una clave.
3. **Un test de gobernanza** que falle cuando un id listado en el `rejects:` de un
   ADR `accepted` siga siendo un checkbox **sin marcar** en su plan. Es nuestro
   `authority_conflict`, mecánico y sin infraestructura nueva:
   `tests/unit/test_docs_governance.py` ya tiene nueve tests exactamente de esta
   forma (cuentan planes, enlazan READMEs, comparan el árbol de CLAUDE.md con el
   repo).

**Lo que descartamos de su implementación**: bloquear la _ejecución_ con un evento
`authority_conflict` en un ledger de ficheros. Aquí el conflicto no es de runtime,
es documental, y el sitio donde tiene que doler es la suite de tests, no un run.

#### Nota aparte, y es un item distinto

La versión **de producto** de esta idea —qué manda dentro del prompt de un run
cuando la persona del agente, las instrucciones del proyecto, un fragmento de
skill y los criterios de aceptación se contradicen— es un problema real y
distinto, hoy resuelto solo por el **orden de concatenación de los preámbulos** y
por esa única frase del literal. No lo meto en esta ficha porque su coste y su
riesgo no se parecen: **merece su propio ADR**.

---

### 2.2 · Versionar el prompt del agente y sellarlo en el run

**Esfuerzo: 2-3 d.** **Decisión del operador: no.**

#### Su idea (regla 7 de la precedencia)

Cada interacción **congela al arrancar** los nombres, versiones y hashes de las
skills cargadas; una edición a mitad no aplica hasta la fase siguiente o con
aprobación del juez, y queda registrada.

#### El agujero que abre en nuestro código

Tenemos dos sellos por run, y ninguno sella lo que importa:

- `executions.prompt_version` (`domain.py:1281`) hashea **solo los prompts del
  propio runtime**: exactamente tres módulos —`providers`, `nudges`,
  `review_contract`— extraídos por AST (`prompt_version.py:33-37`). Está muy bien
  hecho: distingue un retoque de redacción de un refactor. Pero **no incluye ni un
  byte** de la `system_prompt` del agente, ni su persona, ni sus skills, ni sus
  tools.
- `executions.runtime_image_digest` (`domain.py:1288`) sella **la imagen**.

Y la ruta por la que un tenant edita un prompt de verdad no deja rastro ninguno.
`PUT /agents/{id}` es un `apply_partial_update` + `flush`
(`routers/agents/crud.py:225-245`): **sin fila de versión, sin evento de
auditoría, sin diff**. Un tenant-admin puede degradar un agente en silencio y
después no hay forma de saber qué cambió, ni cuándo, ni qué runs salieron de qué
texto.

La consecuencia práctica es que **todo el aparato de evals que ya existe no puede
demostrar nada**: el dashboard de calidad agrupa por `prompt_version`, y esa
etiqueta no se mueve cuando se mueve el prompt del agente.

#### Qué habría que construir

- Tabla `agent_prompt_versions` (tenant_id, agent_id, hash, texto, autor, `at`),
  escrita en el mismo `update_agent`. Append-only al estilo de
  `task_audit_repo`: sin UPDATE ni DELETE en la capa de repositorio.
- Un `agent_prompt_hash` en `executions`, poblado por el orchestrator cuando
  ensambla el request (ya thread-ea la persona: `build_persona_preamble`), y
  añadido a los filtros del dashboard de calidad junto a `prompt_version`.
- Ampliar el sello a la **capacidad resuelta** del run: el conjunto de skills y
  tools con el que salió, que hoy tampoco consta.

**Lo que descartamos de su implementación**: congelar la edición hasta la fase
siguiente. Aquí un run dura minutos y ya toma su copia del prompt al arrancar; el
problema no es la carrera, es la **amnesia**.

---

### 2.3 · Rigor por niveles: que `estimated_complexity` mande algo

**Esfuerzo: 5-8 d + ADR.** **Decisión del operador: SÍ, y toca el principio 11.**

#### Su idea

Se clasifica al entrar (S/M/L) y **cambia todo el aparato**: S no tiene adversario
ni manager y corre 6 checks mecánicos; M añade un adversario y el manager en modo
juez, 13 checks; L pone cinco revisores en paralelo, adjudicación cruzada y 21
checks. Y hay **auto-promoción**: si la interacción crece por encima de su
clasificación inicial, S→M o M→L a mitad.

#### Verificado: el clasificador existe y no lo consume nadie

`Task.estimated_complexity` (`domain.py:1134`) toma valores `xs|s|m|l|xl`, lo
asigna el planner por LLM con validación de vocabulario
(`chat/corrections_llm.py:131-133`) y lo persiste el sync al Kanban
(`chat/sync_to_kanban.py:477,561`). Sus **únicos dos consumidores** son el
estimador de coste (`chat/cost.py:461-464`) y su calibración
(`chat/cost_calibration.py:104`).

Ni la revisión ni la aprobación lo miran nunca:

- **Revisión**: toda tarea con `reviewer_agent_id` que entra en `in_review`
  dispara **exactamente un** run de review, con el mismo prompt y el mismo
  contexto, sea un typo o un rediseño (`dispatch.py:612-689`).
- **Validación humana**: todo plan cuyas tareas terminan pasa a
  `pending_human_validation` (`plan_progress.py:145-168`). Uniforme.
- **Política de aprobación**: 13 categorías × 4 presets
  (`shared_domain/approval_categories.py:24-38`,
  `seeds/builtin_approval_policies.py`). La dimensión es **qué acción**, nunca
  **cuánto cambia**.
- Un `git_commit` de una línea y uno de 4.000 caen en la misma categoría y pagan
  el mismo peaje.

**La única palanca sensible al tamaño que sí tenemos** —y hay que decirlo, porque
matiza el encargo— está en la **entrada**, no en la salida:
`_resolve_first_signature_target` (`routers/plans.py:1540-1556`) exige **doble
firma** cuando el coste máximo estimado del plan supera un umbral configurable. Su
default es `0`, o sea: **nunca**. Es una S/M por coste, en un solo punto del
ciclo, apagada de fábrica.

#### Qué habría que construir

Un `rigor_tier` derivado —no otro campo que rellene un LLM— con tres entradas ya
disponibles: `estimated_complexity` del planner, el **tamaño medido del diff** al
cerrar la tarea (tenemos `code_diff.py`, y la lección de que las operaciones sobre
`/data` van en el worker), y el riesgo de las categorías tocadas. Con tres
consumidores:

1. Cuántas pasadas de review y con qué exigencia.
2. Si el plan exige validación humana al cierre o puede cerrarse solo.
3. Qué preset de aprobación aplica, o si se endurece.

Y la **auto-promoción** suya, que es la parte más lista de su diseño y la más
barata de copiar: una tarea clasificada `s` que acaba tocando 40 ficheros **sube
de tier antes de revisarse**. Sin eso, el nivel es la promesa del planner y no un
hecho.

#### Por qué esto es decisión del operador y no mía

Porque el principio rector 11 dice que la validación humana se configura **por
proyecto y por categoría**, y añadir una dimensión de tamaño no es una mejora
técnica: **cambia qué se cierra sin humano**. El riesgo tiene nombre: si el tier
baja el listón y el clasificador se equivoca, el error se cierra solo. La
auto-promoción es precisamente el seguro contra eso, y por eso no las separaría.

---

### 2.4 · Reflexión estructurada del rechazo (`target` × `class`)

**Esfuerzo: 2-3 d.** **Decisión del operador: no.**

#### Su idea (el trozo barato de SkillOpt)

El manager aporta **0-3 reflexiones**, y cada una está obligada a apuntar a un
objetivo concreto (`target = skills/X | agents/Y`) y a clasificarse como
`rule_missing | rule_wrong | rule_ignored`. **Las observaciones genéricas se
descartan.** Cuando ≥3 señales de la misma clase se agrupan sobre el mismo
objetivo, se dispara el ciclo de evolución.

#### Por qué esto es lo que hay que copiar de SkillOpt, y no el resto

Porque el bucle completo (§2.6) es caro y presupone dos cosas que no tenemos,
mientras que **el dato sin el cual el bucle es imposible cuesta dos días**.

Hoy un rechazo produce prosa. El parser ya extrae estructura —
`<failed_criterion>`, `<testreport_evidence>`, `<what_to_fix>`, y desde `task_wf_61`
un `CriterionOutcome` por criterio con evidencia (`reviewer_bridge.py:52-75`)— pero
todo apunta **a la tarea**, nunca **al agente o a la skill que falló**. No se puede
responder «¿qué regla le falta al reviewer de CI4?» porque nadie lo ha anotado
nunca en un campo agregable.

#### Qué habría que construir

- Un bloque opcional en el contrato del veredicto: `<reflection target="…"
class="rule_missing|rule_wrong|rule_ignored">`, con tope duro de 3 y descarte de
  lo genérico (su restricción es la mitad del valor: sin ella, esto se llena de
  ruido en una semana).
- Persistirlo como `task_audit_events` de un `kind` nuevo, con `target` y `class`
  indexables.
- Un panel de agregación: «reglas señaladas ≥3 veces sobre el mismo objetivo».

Y ahí **parar**. Lo que hace con esa lista un humano —o, más adelante, un bucle—
es la decisión siguiente, y llega mucho mejor informada teniendo la lista.

---

### 2.5 · Pasada ciega + el delta como señal de contaminación

**Esfuerzo: 4-6 d + ADR.** **Decisión del operador: SÍ (coste × tier).**

#### Su idea

El adversario corre **dos veces**. En la primera ve una copia curada del estado
**sin** `handoff.md`, sin el log de aceptación, sin los otros revisores, y emite
hallazgos preliminares. En la segunda ve el estado completo **más** sus propios
preliminares inyectados. **El delta entre ambas listas es la medida**: si al leer
el razonamiento del autor ablanda o revierte su postura, es sugestionabilidad; si
apenas se mueve, es juicio independiente.

#### ¿Contradice al ADR 0095? No. Son cosas distintas, y la distinción importa

El ADR 0095 arregló que el reviewer **no veía el objeto que debía juzgar**: su
`/workspace` era un tmpfs vacío y el modelo agotaba 50 iteraciones buscando un
`composer.json` que nadie había montado. La decisión fue darle el worktree
read-only. Correcta y no discutible.

Ellos no esconden **el objeto** —el adversario ve el trabajo—; esconden **el
encuadre del autor**: su narración de lo que hizo y por qué. Son dos canales
distintos, y aquí el segundo está abierto de par en par:

```
review_context = {
    "acceptance_criteria": …,
    "implementer_output":  …,   # dispatch.py:894-906
    "test_report":         …,
}
```

`implementer_output` no es el último output: son **los tres últimos intentos del
implementador**, el más reciente **verbatim** y los anteriores recortados a 4.000
caracteres, etiquetados «attempt N — latest / earlier»
(`_REVIEW_PRIOR_OUTPUTS = 3`, `_format_prior_outputs`, `dispatch.py:182-206`). Se
añadió con buen criterio en P1-7 —en un ciclo con reintentos el reviewer perdía el
histórico— pero el efecto lateral es que **lo primero que lee el juez es la
autodefensa del acusado, tres veces**. Y encima el reviewer resuelve su modelo por
la misma cadena de herencia que el implementador (`_resolve_model_spec`,
`dispatch.py:851`): misma familia, mismos puntos ciegos, más la narrativa.

Aplicar el ADR 0095 al pie de la letra **no obliga** a inyectar la prosa: el 0095
pedía el **código**. Son ortogonales.

#### Qué habría que construir

- Un `review_pass` en `executions` (`blind` / `informed`).
- El orchestrator despacha la pasada 1 con `review_context` **sin**
  `implementer_output` (con worktree y criterios: el fallo del 0095 no vuelve,
  porque el objeto sigue montado), persiste sus hallazgos, y despacha la 2 con
  todo más los preliminares.
- El delta como evento de auditoría, y agregado como **métrica por reviewer**.

#### El coste, sin adornos

**Duplica los tokens de review** en el tier donde se active. Por eso va atado a la
idea 3 y por eso la decisión es del operador. Y hay dos variantes mucho más
baratas que merecen entrar en el ADR como opciones, no como consuelo:

- **Reordenar sin duplicar**: mover `implementer_output` **detrás** del diff y de
  los criterios, y marcarlo como dato del autor y no como hallazgo. Coste: horas.
  No mide nada, pero reduce el anclaje.
- **Detector de Goodhart barato**: correlacionar longitud/tono del
  `implementer_output` con la tasa de aprobación por reviewer. Si la correlación
  es alta, hay contaminación, y se ha medido sin pagar una sola pasada extra.
  Coste: 1-2 d, y usa datos que ya están en la BD.

Mi lectura honesta: **empezaría por el detector barato**. Si sale plano, la pasada
ciega no se paga.

---

### 2.6 · SkillOpt completo — sobrevive como idea, NO lo recomiendo ahora

**Esfuerzo: 15-20 d, y presupone 2.2 y 2.4.**

Su ciclo: agrupar señales, que **Codex (otra familia) proponga** parches acotados
—4-6 por ciclo, ≤10 líneas cada uno—, que el **director los puertee** contra
escenarios golden por dominio, y que los rechazados caigan en un fichero de
**memoria negativa** para no reproponerlos. El invariante: «el director juzga,
nunca redacta».

**Lo que ya tenemos, y es más de lo que parece**: `eval_datasets` con tres tipos
—`golden`, `regression`, `shadow`—, `eval_dataset_items` **promovidos desde tareas
reales aprobadas** con procedencia para deduplicar, `EvalRun` inmutable, un
merge-gate ejecutable (`api_server.evals.ci_run`) con umbral de regresión
configurable, y un workflow dedicado: `.github/workflows/eval-on-prompt-change.yml`.

**Y lo que hay que decir del todo:** ese workflow **nunca ha corrido de verdad**.

- Vigila **dos ficheros del repo** —`seeds/builtin_agents.py` y
  `seeds/qa_e2e_automator.py` (líneas 17-25)—, no la ruta por la que un tenant
  edita un prompt, que es la BD.
- CI no tiene secreto de proveedor, así que toma la rama `--dry-run` (líneas
  86-96), que valida el parseo de argumentos y sale 0.
- Y sus tres argumentos reales (`EVAL_SUBJECT_AGENT`, `EVAL_GOLDEN_DATASET`,
  `EVAL_BASELINE_RUN`) son variables de entorno **no definidas en ningún sitio**,
  con defaults de relleno: el nombre literal `changed-prompt-agent` y UUIDs
  todo-ceros.

Es, con nombre y apellidos, el patrón que nuestra propia guía tiene documentado
como el dominante de esta base: **mecanismo entregado, cero llamantes**
(`verificar-antes-de-implementar.md` §5).

Así que el hueco no es «no tenemos golden-set». Es que **el golden-set no está
enchufado a la puerta por la que se entra**. Ordenado por lo que yo haría:

- **4a — sellar el prompt (§2.2)**: 2-3 d. Sin esto nada de lo demás demuestra
  nada.
- **4b — cerrar la puerta de la BD**: editar el prompt de un agente que tiene
  dataset golden crea una **versión borrador** que debe pasar la eval antes de
  activarse. 5-8 d + ADR. **Decisión del operador**: ¿se le bloquea a un
  tenant-admin su propia edición? Es una restricción de producto con cara visible.
- **4c — el bucle** (agrupar ≥3, proponer parches ≤10 líneas, puertear, memoria
  negativa): **no ahora**. Es la pieza más vistosa y la que menos valor marginal
  aporta mientras 4a y 4b no existan, y trae un riesgo que ellos asumen porque su
  radio de daño es una máquina: aquí un parche automático a un prompt afecta a los
  runs de un tenant en producción.

---

## 3. Lo que descartamos porque no aplica

| Suyo                                                                                                                                       | Por qué no encaja                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **«Filesystem como estado»: `engagement/`, `iteration`, `validation-log.md`, `acceptance-log.md`; "`cat` reconstruye el cuadro completo"** | Choca de frente con el principio rector 1: cada tabla lleva `tenant_id`, con RLS y middleware. Un directorio en disco no tiene fila, no tiene política y no se consulta. **La idea de detrás —una traza append-only que reconstruye el caso entero— ya la tenemos y mejor**: `task_audit_events`, `audit_log` particionado y `steps_log`. Su virtud (no necesitar infraestructura) es exactamente nuestra restricción invertida                            |
| **Consilium de 5 revisores en paralelo (Opus + 2× GPT-5 + Sonnet + Haiku)**                                                                | Cinco revisores por tarea es inasumible en coste para nuestro volumen, y **una tarea tiene un `reviewer_agent_id`, singular** (`domain.py:1122`). El fondo —segunda opinión de otra familia— sí es viable y **sin ADR nuevo**: el catálogo cerrado del ADR 0021 ya incluye Azure AI Foundry vía APIM, que es linaje OpenAI. Pero es un item pequeño (un guard + una métrica de «reviewer e implementador resolvieron a la misma familia»), no un consilium |
| **Puente MCP a Codex como proveedor**                                                                                                      | El ADR 0021 cerró el catálogo en cuatro caminos y añadir un quinto **exige un ADR explícito**. Y no hace falta: la diversidad de familia ya está disponible dentro del catálogo                                                                                                                                                                                                                                                                            |
| **Whitelist cerrada de 22 rutas en `engagement/`, con `REJECT` por artefacto extraño**                                                     | Nuestros artefactos son filas con FK y worktrees de git bajo `/data/agent-platform/…`. El equivalente ya lo dan el esquema y el RLS                                                                                                                                                                                                                                                                                                                        |
| **Adversario en subproceso con copia curada del filesystem**                                                                               | Nosotros aislamos por **contenedor efímero** con bridge por sesión, cap-drop ALL y sin socket Docker (principio 2). La curación, si se hace (§2.5), es **del prompt**, que es donde está el canal de contaminación en nuestra arquitectura                                                                                                                                                                                                                 |
| **`size-detect.py --auto-promote` leyendo el frontmatter de `criteria.md`**                                                                | La clasificación nuestra ya la produce el planner y vive en una columna. Copiar el script sería reimplementar lo que tenemos en un sitio peor. **La auto-promoción sí se copia** (§2.3)                                                                                                                                                                                                                                                                    |
| **`director-verdict-check.py`: exit 1 si algún hallazgo no lleva `UPHELD`/`OVERRULED`/`DEFERRED`**                                         | Interesante, y **medio implementado ya**: `CriterionOutcome` da veredicto por criterio con evidencia. Lo que falta —verificar que el siguiente intento atiende **cada** criterio previamente fallado— es un item menor que no llega al top 5, pero que anoto por si el operador lo quiere: **3-5 d**                                                                                                                                                       |
| **`consGuard`, `contracts`, `replan`, `inPlaceSerial` y el resto de flags**                                                                | Cada uno compensa algo que un DAG con dependencias, `plan_corrections`, `dag_promotion` y el reconciler ya resuelven de forma estructural                                                                                                                                                                                                                                                                                                                  |

**Una nota sobre sus flags que sí vale la pena.** No el mecanismo —lo tenemos— sino
su **invariante**: «con todos los flags apagados el motor renderiza byte a byte
igual que la ruta sin flags». Nosotros tenemos decenas de `platform_settings` y
**ningún test que afirme eso** de ninguna. Es un detalle, no entra en el top 5,
pero es la clase de disciplina que evita que un flag apagado cambie algo por
accidente.

---

## 4. Lo que exigía decisión del operador — RESPONDIDO el 2026-08-12

**Las seis están decididas.** Las preguntas se conservan abajo con su enunciado
original: una decisión sin la pregunta que la motivó no se puede auditar, solo
obedecer. La respuesta va inmediatamente después de cada una.

| #   | Pregunta                             | Decisión                                                             |
| --- | ------------------------------------ | -------------------------------------------------------------------- |
| 1   | Precedencia ADR vs `CLAUDE.md`       | El ADR **está obligado a actualizar `CLAUDE.md`** en el mismo commit |
| 2   | ¿Rigor por tamaño del cambio?        | **Sí, solo las pasadas de review.** La validación humana no se toca  |
| 3   | ¿Pasada ciega del revisor?           | **Primero el detector barato**, y decidir con el dato                |
| 4   | ¿Evals bloqueantes al editar prompt? | **Sí, solo en `production` y `customer-external`**                   |
| 5   | ¿Revisor de otra familia?            | **Solo medirlo y avisar** en el Hub de Capacidad                     |
| 6   | ¿Entra SkillOpt?                     | **No ahora, con disparador escrito** (ver abajo)                     |

### Lo que estas seis respuestas tienen en común

Cinco de las seis eligen **medir antes de construir**, y conviene que quede
dicho porque no fue casualidad pregunta a pregunta:

- el detector de Goodhart antes que la pasada ciega (3),
- el aviso en el Hub antes que exigir familias cruzadas (5),
- el disparador escrito antes que el bucle de SkillOpt (6).

En los tres casos la alternativa cara seguía disponible, y en los tres se
prefirió el instrumento que produce el dato con el que decidir. Es coherente con
lo que este repo lleva aprendiendo todo agosto: **una medida que miente cuesta
más que no tener medida** — los `0 ms` de todos los pasos, el guard de i18n que
solo veía tildes, el healthcheck que no podía fallar.

### El disparador de SkillOpt (respuesta 6), escrito para que no se olvide

SkillOpt NO se descarta: se aplaza **con su condición**, y la condición son las
dos redes que sus propios frenos presuponen y nosotros no tenemos:

1. **Versionado del prompt del agente.** Hoy `PUT /agents/{id}` reescribe
   `system_prompt` sin versión, sin auditoría y sin diff
   (`routers/agents/crud.py:225-245`), y `executions.prompt_version` solo hashea
   tres módulos del runtime — ni un byte del prompt del agente. Sin esto, un
   parche automático que empeore un agente **no tiene a dónde volver**.
2. **Evals que bloqueen de verdad.** El golden-set existe entero pero corre
   siempre en `--dry-run` contra un dataset de ceros
   (`.github/workflows/eval-on-prompt-change.yml`). Sin esto, la regresión que
   debería frenar un parche malo no frena nada.

Las dos están aprobadas en las respuestas 4 y 2.2. **El día que existan, esta
decisión caduca y hay que reabrirla** — no es un «no» indefinido. Montar el bucle
antes que las redes sería exactamente lo contrario de lo que hace el framework
que lo inspira: ellos lo tienen porque PRIMERO tienen el golden-set.

### Las preguntas, tal como se formularon

1. **El orden de precedencia normativa (§2.1).** Mi propuesta para que haya algo
   concreto que aceptar o corregir: `.docx` maestro > `CLAUDE.md` > decisión
   escrita del operador > ADR `accepted` posterior > plan > código > intuición.
   La pregunta fina: **¿un ADR posterior gana a `CLAUDE.md` si lo contradice, o
   está obligado a actualizar `CLAUDE.md` en el mismo commit?** (Hay precedente de
   lo segundo: la excepción Fernet del ADR 0146 está escrita en `CLAUDE.md`
   precisamente porque «una excepción que no consta donde se busca no es una
   excepción».)
2. **¿El rigor se adapta al tamaño del cambio? (§2.3)** Toca el principio rector 11. Si sí: qué se relaja en el tier bajo —¿solo el número de pasadas de review,
   o también la validación humana al cierre del plan?— y si la auto-promoción es
   obligatoria (mi recomendación: sí, o el tier es una promesa y no un hecho).
3. **¿Pagamos la pasada ciega, y en qué tier? (§2.5)** Duplica el coste de review
   donde se active. Alternativa a considerar antes: el detector de Goodhart barato,
   1-2 d y sin tokens extra.
4. **¿Puede una eval bloquear a un tenant-admin que edita el prompt de su agente?
   (§2.6, 4b)** Es la pregunta de producto que decide si el golden-set sirve para
   algo o sigue siendo decorado.
5. **¿Preferimos o exigimos revisor de otra familia? (§3, fila del consilium)**
   Viable sin ADR nuevo vía Azure Foundry. Coste: un modelo más caro o más lento
   en el camino de review.
6. **¿Entra el bucle SkillOpt (4c) en el roadmap, aunque sea detrás de todo?**
   Mi recomendación es que no, pero es un no con fecha de caducidad: cuando 4a y
   4b existan, la conversación cambia.

---

## 5. Honestidad sobre este informe

**Cómo se hizo.** El script que lanzó este trabajo
(`wf-analisis-agentic-workflow.js`) prometía pasarme tres inventarios previos —el
del repo externo y dos del nuestro— pero **nunca los interpoló en el prompt**: la
plantilla de la fase de síntesis dice «van al final» y termina sin incluirlos
(líneas 141-182). Así que los inventarios (B) y (C) los reconstruí yo leyendo el
código, y su cobertura es la de una pasada mía, no la de tres agentes dedicados.
Todas las citas `fichero:línea` de este informe las he verificado directamente.

**Qué no pude verificar.**

- **Del repo externo solo he leído dos documentos**: la página del repositorio y
  `ARCHITECTURE.md`, vía WebFetch. **No he leído `agents/`, `skills/`, `scripts/`
  ni `workflows/`.** Por tanto todo lo que digo sobre _cómo_ implementan cada cosa
  —`size-detect.py`, `director-verdict-check.py`, `ledger.py`, los umbrales de
  SkillOpt, los 6/13/21 checks— viene de **su propia documentación**, no de su
  código. Un README puede prometer más de lo que el repo cumple; nosotros tenemos
  ocho meses de ejemplos.
- **No puedo comprobar si el gate de evals ha corrido alguna vez de verdad**: eso
  depende de secretos y variables del repositorio de GitHub, que no son legibles
  desde aquí. Lo que sí afirmo, y está en el fichero, es que el workflow toma la
  rama `--dry-run` cuando no hay secreto, y que sus tres argumentos no están
  definidos en ningún punto del repo.
- **El orden de precedencia dentro del prompt de un run** lo he inferido del
  ensamblado de preámbulos y de un literal de `__main__.py`; **no he recorrido
  entero** `graph.py` para confirmar que no haya otra regla de precedencia
  escondida en otro literal. Si alguien la encuentra, este informe subestima lo
  que ya tenemos en §2.1.

**Lo que este informe deliberadamente no hace**: proponer un plan. Cinco items,
sus esfuerzos y seis preguntas para el operador. El plan se escribe después de
responderlas, y en `docs/roadmap/` como manda la casa.
