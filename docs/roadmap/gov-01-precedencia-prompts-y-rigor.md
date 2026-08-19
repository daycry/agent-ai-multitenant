---
plan_id: gov-01-precedencia-prompts-y-rigor
title: Precedencia normativa, prompts versionados y rigor por tamaño — las seis decisiones del 2026-08-12
status: approved
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 2-3 semanas
estimated_effort_person_days: 14
estimated_cost_human_eur: 5.600 € – 8.400 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: análisis de AgentShekel/agentic-workflow + seis decisiones del operador (2026-08-12)
docs_language: es
---

# Plan `gov-01` — precedencia, prompts versionados y rigor por tamaño

> **Fuente de verdad del QUÉ y el POR QUÉ:**
> [`2026-08-12-analisis-agentic-workflow.md`](2026-08-12-analisis-agentic-workflow.md),
> §4, donde están las seis preguntas con la respuesta del operador al lado. Este
> documento es el CÓMO. Ante conflicto entre los dos, gana el informe; ante
> conflicto entre ambos y el código ya mergeado, se para y se re-verifica.

## En una frase

Las seis decisiones tienen un hilo común que conviene leer antes que las tareas:
**cinco de las seis eligen medir antes de construir**. Por eso el plan está
ordenado así y no por tamaño — las fases 3 y 4 producen el dato con el que se
decide si las caras merecen la pena.

## El hallazgo que reordena las prioridades

El informe no encontró que nos faltaran ideas: encontró que **tres mecanismos ya
existen y no los consume nadie**.

- `Task.estimated_complexity` (`xs`…`xl`) se calcula desde el planner y **solo lo
  lee el estimador de coste**. Review y aprobación no lo miran jamás.
- El golden-set de evals existe entero —datasets, merge-gate, workflow que se
  dispara al cambiar un prompt— y **corre siempre en `--dry-run`** contra un
  dataset de ceros.
- Hay precedencia escrita en cinco ADR, **ninguna** sobre qué manda cuando un
  plan y un ADR se contradicen.

Por eso la mayor parte de este plan es cableado, no construcción.

## Estado de esta pasada (2026-08-19)

Se cerraron las **tres** casillas que no dependen de una decisión del operador:
`task_gov_01` (fase 0), `task_gov_06` y `task_gov_07` (fase 3). Las ocho
restantes siguen abiertas, así que el plan NO pasa a
`pending_human_validation` y el `status` se queda en `approved` — además,
`marketplace-v2-despliegue` ya está `in_progress` y la regla dura de `CLAUDE.md`
sólo admite una fase activa a la vez.

**Los ids de test se renombraron a `auto_govp_*` / `human_govp_*`.** Los
originales (`auto_gov_01_a`…`auto_gov_09_a`, `human_gov_01`…`03`) los usa ya
[`prod-15-gobernanza-roadmap-docs`](prod-15-gobernanza-roadmap-docs.md), y dos
planes con el mismo id de test hacen imposible saber cuál falló al leer un
informe de CI.

## Avisos al implementador (léelos, ahorran horas)

1. **Verifica los números antes de usarlos.** Las citas `fichero:línea` de abajo
   se comprobaron el 2026-08-12; en este repo se mueven cada semana.
2. **La fase 1 desbloquea a la 2.** Sin versionado del prompt, una eval que
   bloquea no tiene a dónde volver: rechaza el cambio pero no puede decir contra
   qué versión comparaba. No las reordenes.
3. **`--dry-run` no es un fallo del workflow, es su rama sin secreto.** Antes de
   «arreglarlo», lee `eval-on-prompt-change.yml:63-94`: la rama existe a
   propósito para que un fork sin credenciales no falle. Lo que falta es la OTRA
   rama, no quitar ésta.
4. Los tests que escribas deben poder **fallar**: rompe la implementación,
   comprueba el rojo, restaura.

---

## Fase 0 — La precedencia normativa (1-2 días)

La más barata de las seis y la única que resuelve un problema que está pasando
**ahora**: durante agosto, un plan pidió tres veces algo que un ADR posterior
había rechazado, y se resolvió a ojo cada vez.

### `task_gov_01` — La regla de precedencia, escrita y con test

- [x] **Título**: Sección de precedencia en `CLAUDE.md` + `rejects:` en el frontmatter de los ADR + guarda de gobernanza
  - ✅ **Cerrada (2026-08-19).** Las tres piezas, y la premisa del recon
    verificada antes de tocar nada: **cero** ADR con `rejects:` y **cero**
    menciones de precedencia en `CLAUDE.md`.
  - **(a)** `CLAUDE.md` §«Qué manda cuando dos documentos se contradicen», con la
    cadena del 2026-08-12, la obligación de actualizarlo en el MISMO commit y el
    precedente Fernet del ADR 0146 citado. `## Sobre el Documento Maestro` remite
    a la sección nueva para que las dos no digan cosas distintas.
  - **(b)** `rejects:` puesto en los **cuatro** ADR cuya relación se verificó
    contra el roadmap, casilla a casilla: **0133**→`task_prod09_12`,
    **0141**→`task_prod08_shared_logging_08` + `task_prod08_metrics_workers_05`,
    **0150**→`task_prod07_09`, **0151**→`task_prod13_15`. Las cinco casillas
    están `[x]` y cerradas en negativo desde antes; este campo sólo hace
    mecánico lo que ya estaba en prosa. El **0117 (b) NO entra**: retiró una
    promesa de `CLAUDE.md` (`task.human_validation_required`), no una casilla, y
    `rejects:` apunta a casillas. Y el renderer de ADR
    (`tech_writer/adr.py`) aprende el campo, de modo que un ADR nuevo puede
    nacer con él — se omite entero cuando está vacío, como las secciones de cola.
  - **(c)** `tests/docs/test_adr_precedence.py` (8): existencia del id, casilla
    `[x]`, y **cita de vuelta** del documento rechazado al ADR — sin esa tercera
    regla el campo sería una anotación de un solo lado y el implementador que
    abre el plan seguiría sin enterarse.
  - **Rojo verificado**, como pedía el enunciado: inventando en el 0150 un
    `rejects: [task_prod07_09, task_gov_02, task_que_no_existe_9999]` caen los
    **tres** tests (referencia muerta, casilla abierta y falta de cita de vuelta).
    También se rompió el renderer emitiendo `rejects: []` siempre → 2 rojos.
  - **Hallazgo lateral** (no arreglado, no es este carril): los ADR **0107** y
    **0108** tienen frontmatter que **PyYAML no carga** — `related: [hallazgo #11
(…), ADR 0072]`, donde el `#` abre un comentario dentro de una secuencia de
    flujo. Por eso el parseo del `rejects:` es un escáner de líneas y no
    `yaml.safe_load`: si dependiera de PyYAML, un `rejects:` en cualquiera de
    esos dos sería invisible y la guarda pasaría en verde ignorando el fichero roto.
- **Tiempo**: 1-2 días · **Complejidad**: s
- **Descripción**: Tres piezas, y la tercera es la que hace que las dos primeras
  no envejezcan.

  **(a) El orden, en `CLAUDE.md`.** Decisión del operador del 2026-08-12:
  `.docx` maestro > `CLAUDE.md` > decisión escrita del operador > ADR `accepted`
  posterior > plan > código > intuición. Y la regla fina que se firmó:
  **un ADR que contradiga el `CLAUDE.md` está OBLIGADO a actualizarlo en el mismo
  commit**. No gana por ser posterior: gana porque al aceptarse deja el
  `CLAUDE.md` diciendo la verdad. Hay precedente y hay que citarlo — la excepción
  Fernet del [ADR 0146](../05-architecture-decisions/0146-fernet-en-db-vs-vault.md)
  vive en `CLAUDE.md` precisamente porque «una excepción que no consta donde se
  busca no es una excepción».

  **(b) `rejects:` en el frontmatter del ADR.** Lista de `plan_id` o `task_id`
  cuyas casillas quedan invalidadas por esta decisión. Hoy esa relación existe
  —el ADR 0117 (b) retiró `task.human_validation_required`, el 0150 retiró dos
  sub-tareas de `task_prod07_09`— pero vive en prosa, así que solo la encuentra
  quien ya sabe que está. Con el campo, un implementador que abre una casilla
  puede preguntar «¿la rechaza algún ADR?» de forma mecánica.

  **(c) El test.** En `tests/docs/`: todo ADR con `rejects:` apunta a plan_ids o
  task_ids que EXISTEN (si no, es una referencia muerta), y toda casilla nombrada
  en un `rejects:` está marcada `[x]` con su nota de cierre en negativo. Es el
  guard que convierte la regla en algo comprobable en vez de una costumbre.

- **Tests automáticos**:

  ```yaml
  - id: auto_govp_01_a
    runtime: python-pytest
    command: "pytest tests/docs/test_adr_precedence.py -q"
  - id: auto_govp_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_adr_template.py tests/unit/test_docs_governance.py -q"
  ```

  El primero debe fallar de verdad al inventar un `rejects:` que apunte a una
  casilla inexistente Y al apuntar a una casilla abierta.

  Ejecutados el 2026-08-19: `auto_govp_01_a` → **8 passed**; `auto_govp_01_b` →
  **47 passed** (36 del renderer, con los tres casos nuevos de `rejects:`, y 11
  de la guarda documental que vigila que el `CLAUDE.md` editado siga cuadrando
  con el repo).

---

## Fase 1 — Versionar el prompt del agente (2-3 días)

**Habilita la fase 2 entera.** Es la idea nº2 del informe y la única de las cinco
sin decisión pendiente.

### `task_gov_02` — El `system_prompt` deja de reescribirse sin rastro

- [ ] **Título**: Historial versionado del prompt del agente, con diff y autor
- **Tiempo**: 2 días · **Complejidad**: m
- **Descripción**: Hoy `PUT /agents/{id}` sobrescribe `system_prompt`
  (`routers/agents/crud.py:225-245`) **sin versión, sin auditoría y sin diff**.
  Si la calidad de un agente cae, no hay forma de saber qué cambió ni de volver.

  Tabla nueva `agent_prompt_versions` con `tenant_id` + RLS `FORCE` (patrón de
  `user_invitations`, migración 0127): `agent_id`, `version`, `system_prompt`,
  `persona`, `changed_by`, `created_at`, y un `parent_version_id` para la cadena.
  El `PUT` inserta una fila ANTES de escribir, y el endpoint de lectura devuelve
  el histórico con el diff calculado.

  Lo que NO se hace aquí: rollback automático. Poder volver es la fase 2.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_agent_prompt_versions.py -q -p no:randomly"
  - id: auto_govp_02_b
    runtime: python-pytest
    command: "pytest tests/security/test_rls_invariant.py -q"
  ```

### `task_gov_03` — `prompt_version` sella el prompt del AGENTE, no tres módulos

- [ ] **Título**: `executions.prompt_version` incluye la versión del prompt del agente
- **Tiempo**: 1 día · **Complejidad**: s
- **Descripción**: Hoy `_PROMPT_MODULES`
  (`agent_runtime/prompt_version.py:33-37`) hashea `providers`, `nudges` y
  `review_contract` — el andamiaje del runtime. **Ni un byte del `system_prompt`
  del agente.** O sea que dos runs con el mismo `prompt_version` pueden haber
  corrido con personas completamente distintas, y la etiqueta que existe para
  atribuir un cambio de comportamiento **no puede atribuir nada**.

  El dispatch pasa la versión de `task_gov_02` en el `AGENT_TASK_SPEC` y el
  runtime la mezcla en el hash. Un test debe fijar que **cambiar el
  `system_prompt` cambia el `prompt_version`** — es la propiedad entera.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_03_a
    runtime: python-pytest
    command: "pytest docker/agent-runtimes/agent-runtime/tests/test_prompt_version.py -q"
  ```

---

## Fase 2 — Que las evals dejen de ser decorado (3-4 días)

Decisión del operador: **bloquean, pero solo en `production` y
`customer-external`**. En desarrollo y sandbox avisan y dejan guardar.

### `task_gov_04` — El gate de evals corre de verdad

- [ ] **Título**: La rama con secreto de `eval-on-prompt-change.yml`, contra un dataset real
- **Tiempo**: 1-2 días · **Complejidad**: m
- **Descripción**: El workflow existe y toma siempre la rama `--dry-run`, con sus
  tres argumentos apuntando a variables no definidas y UUIDs todo-ceros
  (`.github/workflows/eval-on-prompt-change.yml:80-94`). Y vigila **dos ficheros
  del repo**, no la ruta por la que un tenant edita un prompt de verdad.

  Aquí: sembrar un dataset golden real, definir las variables, y que la rama con
  secreto ejecute la evaluación. **No quites la rama `--dry-run`**: existe para
  que un fork sin credenciales no falle, y eso sigue siendo correcto.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_04_a
    runtime: python-pytest
    command: "pytest tests/docs/test_supply_chain_docs.py tests/unit/test_eval_gate_config.py -q"
  ```

### `task_gov_05` — La eval bloquea al editar un prompt, según el preset

- [ ] **Título**: `PUT /agents/{id}` corre la eval y bloquea en `production` / `customer-external`
- **Tiempo**: 2 días · **Complejidad**: m
- **Descripción**: Al cambiar `system_prompt`, se lanza la eval contra el golden
  set. En un proyecto `production` o `customer-external`, un resultado peor que
  el umbral **rechaza la escritura** con el detalle de qué escenarios empeoraron;
  en `development` y `sandbox` se guarda y se avisa.

  Dos cosas que no son opcionales: (1) **el mensaje dice qué empeoró**, no «la
  eval falló» — un rechazo mudo se salta desactivando la feature; (2) hay una
  válvula de escape documentada para el caso de eval caída, porque un tenant-admin
  bloqueado por una infraestructura que no responde es una llamada de soporte y
  un incentivo a apagar el gate.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_05_a
    runtime: python-pytest
    command: "pytest tests/integration/test_prompt_edit_eval_gate.py -q -p no:randomly"
  ```
  Nodos irrenunciables: bloquea en `production`, NO bloquea en `development`, y
  el mensaje de rechazo nombra los escenarios que empeoraron.

---

## Fase 3 — Medir antes de construir (2-3 días)

Las dos tareas que el operador prefirió a sus alternativas caras. Producen el
dato con el que se decidirá si aquéllas merecen la pena.

### `task_gov_06` — Detector de Goodhart: ¿el revisor juzga o repite?

- [x] **Título**: Medir cuánto se parece el veredicto del revisor al relato del implementador
  - ✅ **Cerrada (2026-08-19).** Premisa verificada contra el código antes de
    medir nada: `_format_prior_outputs` + `_REVIEW_PRIOR_OUTPUTS = 3` en
    `orchestrator/dispatch.py` — el revisor recibe los tres últimos intentos y,
    cuando sólo hay uno, **verbatim**; y `_build_review_request` resuelve el
    modelo con el MISMO `_resolve_model_spec` que el implementador.
  - **La métrica**: `api_server/review_contamination.py`, pura y determinista
    (sin reloj, sin red, sin LLM). Tres números por review: `phrase_overlap`
    (contención **dirigida** revisor→autor sobre 5-gramas), `verbatim_share`
    (superficie del veredicto cubierta por tiradas literales de 12 tokens
    compartidas) y `echoed_conclusion` (el veredicto frente al `finish_status`
    que el propio autor se puso; `None` cuando no se autoevaluó — un dato
    ausente no es un dato negativo y sesgaría la media de la semana). Lleva
    `METRIC_VERSION` para que un agregado de meses no mezcle dos fórmulas: es
    el fallo que ya se pagó con `EvalRun.subject_prompt_version`.
  - **Cableada**, que es la mitad que esta base se suele dejar
    (`verificar-antes-de-implementar.md` §5): `workers/execution.py` la calcula
    en la rama `if request.review:` con el veredicto YA aplicado y deja un
    evento de auditoría `review_contamination` + una línea structlog que va a
    Loki, que es por donde se leerá la ventana de `human_govp_03` sin escribir
    SQL. `kind` propio y no un campo dentro de `review_comment`: un APPROVE sin
    desglose de criterios no emite `review_comment`, así que colgarlo de ahí
    perdería la métrica en la mitad de los casos que interesa medir. Best-effort
    en SAVEPOINT, como `_persist_guardrail_events`.
  - **Un detalle que sólo aparece leyendo el ciclo real**: el «relato del autor»
    excluye la ejecución del propio revisor **y** las de su mismo `agent_id` —
    un review no concluyente se re-despacha (ADR 0095 D3), así que la ejecución
    anterior puede ser otra pasada del revisor y compararlo consigo mismo daría
    contaminación altísima por construcción.
  - **Rojos verificados**: (1) cambiar la contención por un Jaccard simétrico →
    caen `test_containment_is_directional_not_symmetric` y
    `test_copying_the_author_verbatim_scores_high`; (2) tokenizar con `\S+` (sin
    descartar el andamiaje markdown) → cae
    `test_markdown_scaffolding_is_invisible_to_the_metric`; (3) borrar la llamada
    del worker → caen las dos guardas de cableado. La guarda de cableado se
    endureció **por ese tercer rojo**: contar apariciones del nombre daba verde
    con la llamada borrada (el comentario y la `async def` ya suman dos), así que
    busca la invocación `await …(`.
- **Tiempo**: 1-2 días · **Complejidad**: s
- **Descripción**: Hoy el revisor recibe los tres últimos intentos del
  implementador, **el último verbatim** (`dispatch.py:182-206`), y resuelve el
  mismo modelo que él. O sea que hereda su encuadre entero antes de opinar.

  En vez de pagar la pasada ciega a ciegas (4-6 días y un ADR), **medir primero**:
  una métrica por review que compare el veredicto con el relato del autor —
  solapamiento de n-gramas, coincidencia de conclusiones, reutilización literal
  de frases. Cero tokens extra: es post-proceso de texto que ya existe.

  Si el número sale alto, la pasada ciega queda justificada con evidencia. Si
  sale bajo, nos hemos ahorrado duplicar el coste de review. **El resultado de
  esta tarea es un dato, no una feature**, y así hay que leerlo.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_review_contamination_metric.py -q"
  ```
  Ejecutado el 2026-08-19: **21 passed** (18 de la métrica + 3 guardas de que el
  worker la llama de verdad, en la rama de review y con su `kind` propio).

### `task_gov_07` — Aviso de linaje compartido entre autor y revisor

- [x] **Título**: El Hub de Capacidad avisa cuando implementador y revisor son de la misma familia
  - ✅ **Cerrada (2026-08-19).** Confirmado lo que el enunciado ya avisaba: el
    mapa `KIND_TO_LITELLM_FAMILIES` existe y **no se ha duplicado** —
    `capabilities.model_families()` lo lee de `pricing/litellm_sync.py`, con un
    test que lo compara entrada por entrada contra el mapa vivo. Y `model_origin`
    ya se resolvía, serializaba y pintaba: se ha reutilizado, no rehecho.
  - **Backend**: `shared_lineage_warning()` compara **familias**, no proveedores
    —`claude_sdk` y `copilot` son entradas distintas del catálogo y el segundo
    sirve modelos de Anthropic, así que un «¿son proveedores distintos?» ingenuo
    daría por bueno el peor caso—, y el endpoint lo emite con el `code` estable
    `shared_model_lineage`, bilingüe como el resto de avisos del Hub.
  - **Quién es «el revisor»**: el agente de rol `reviewer` del equipo del
    proyecto, la MISMA fuente que usa el planner al materializar tareas
    (`sync_to_kanban._resolve_assignment`). No `tasks.reviewer_agent_id`: el Hub
    es una vista por agente, no por tarea, y preguntarle a una tarea concreta
    ataría el aviso al azar de cuál se mirase.
  - **Se comparan los proveedores EFECTIVOS**, resueltos por la misma
    `resolve_model_config_chain` que el dispatch, default de plataforma incluido.
    Comparar los `model_config` crudos daría «no comparten linaje» para dos
    agentes que en realidad heredan los dos el mismo default, que es el caso más
    común de todos.
  - **Frontend**: `sharedLineageNotice()` en `lib/capability/hub.ts` + su caja en
    el Hub, emparejando por `code` y nunca por el texto castellano — hacerlo por
    texto ya dejó muerta la rama EN una vez. Tono neutro (`Info`), no de aviso:
    no bloquea nada, y el operador decidió expresamente quedarse en avisar.
  - **Lo que descubrió el código y no la especificación**: `model_config
["provider"]` guarda HOY las dos formas — el catálogo cerrado del ADR 0021 y
    `DEFAULT_MODEL_CONFIG` usan el **kind** (`claude_sdk`), pero los **once**
    agentes built-in se siembran con la **familia** (`anthropic`,
    `seeds/builtin_agents.py`). Un resolutor que entendiera sólo una de las dos
    daría «sin linaje compartido» justo para los equipos built-in, que son los
    que más lo comparten. Se aceptan las dos, y hay test para cada una.
  - **Rojo verificado**: quitando el bloque de render del Hub caen los dos tests
    de pintado (es/en) y sobreviven los del selector — o sea que las dos mitades
    se comprueban por separado.
  - **Regresión**: `pytest tests/integration/test_capabilities_endpoint.py -q -p
no:randomly` → **7 passed** contra la base `agentic_ola3_l3`, y los tests
    vivos del Hub (`i18n.test.tsx` + `capability-hub.test.ts`) siguen verdes.
  - **Corregido el `command:` declarado**: apuntaba a `app/admin/projects`, donde
    no hay ni un `*.test.tsx`, así que `npx vitest run` habría salido != 0 por
    «no test files found». Ahora nombra el fichero que existe y que se ejecutó.
- **Tiempo**: 1 día · **Complejidad**: s
- **Descripción**: El override por proyecto **ya existe**: la cadena
  agente→equipo→proyecto→plataforma (`db/platform_settings.py:1017`) resuelve el
  modelo, y el Hub ya muestra de qué nivel viene. Lo que falta no es poder
  configurarlo: es que el sistema **sepa que compartir linaje importa**.

  Se añade la comparación de familia entre el agente que implementa y el que
  revisa, y un aviso en el Hub cuando coinciden. Ni bloquea ni cambia nada:
  convierte una decisión invisible en visible. El operador decidió expresamente
  quedarse aquí y no exigirlo — un proyecto sin segundo proveedor no puede
  quedarse sin poder cerrar reviews.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_07_a
    runtime: vitest
    command: "npx vitest run components/capability/shared-lineage.test.tsx --reporter=dot"
  - id: auto_govp_07_b
    runtime: python-pytest
    command: "pytest tests/unit/test_shared_model_lineage.py -q"
  ```
  Ejecutados el 2026-08-19 (el de vitest, desde `apps/admin-panel/`):
  `auto_govp_07_a` → **9 passed** (6 del selector + 3 de que el Hub lo pinta);
  `auto_govp_07_b` → **14 passed**.

---

## Fase 4 — El rigor se adapta al tamaño (4-6 días)

Decisión del operador: **sí, pero solo las pasadas de review**. La validación
humana al cierre del plan NO se toca — sigue siendo del operador siempre.

### `task_gov_08` — ADR del rigor por niveles

- [ ] **Título**: ADR que fija qué cambia en cada nivel y quién clasifica
- **Tiempo**: 4 h · **Complejidad**: s
- **Descripción**: Nace `accepted` con la decisión del 2026-08-12 escrita
  —incluida su frontera: **la validación humana no participa**—, y con lo que
  queda por resolver como parte del ADR: quién clasifica (hoy el planner emite
  `estimated_complexity` y nadie lo audita), si el operador puede promover un
  nivel a mano, y qué pasa cuando la clasificación no existe (el fallback debe
  ser el nivel ALTO: un cambio sin clasificar no es un cambio pequeño).
- **Tests automáticos**:
  ```yaml
  - id: auto_govp_08_a
    runtime: python-pytest
    command: "pytest tests/docs/test_docs_internal_links.py -q"
  ```

### `task_gov_09` — `estimated_complexity` gobierna las pasadas de review

- [ ] **Título**: El número de pasadas de review depende del nivel del cambio
- **Tiempo**: 4-5 días · **Complejidad**: l
- **Descripción**: `Task.estimated_complexity` (`xs`…`xl`) se calcula en el
  planner (`sync_to_kanban.py:477`) y hoy solo alimenta al estimador de coste.
  Aquí pasa a decidir cuántas pasadas de review lleva la tarea.

  Tres cosas que definen si esto sale bien:
  1. **Fallback al nivel alto.** Sin clasificación, rigor máximo. Lo contrario
     convierte un fallo del planner en una puerta abierta.
  2. **El nivel se registra en la ejecución**, no se recalcula al leer. Si el
     planner cambia de criterio, los runs viejos deben seguir explicando por qué
     tuvieron el rigor que tuvieron.
  3. **La validación humana no se toca.** Está escrito en el ADR y hay que
     comprobarlo con un test: un plan `xs` sigue exigiendo la firma del operador
     al cierre.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_09_a
    runtime: python-pytest
    command: "pytest tests/integration/test_review_passes_by_tier.py -q -p no:randomly"
  ```
  Nodo irrenunciable: una tarea SIN `estimated_complexity` recibe el rigor máximo.

---

## Fase 5 — El dato que hace posible aprender (2-3 días)

### `task_gov_10` — Reflexión estructurada del rechazo

- [ ] **Título**: El rechazo se registra como `target` × `class`, no como prosa
- **Tiempo**: 2-3 días · **Complejidad**: m
- **Descripción**: Hoy un rechazo se memoriza y se reinyecta como texto. Sirve
  para el reintento inmediato y **no agrega**: no se puede preguntar «¿por qué se
  rechaza más en este proyecto?».

  El veredicto pasa a llevar, además de la prosa, un par acotado: `target` (qué
  se rechaza: el código, los tests, el alcance, el formato del entregable) y
  `class` (por qué). Vocabulario CERRADO y corto, con tope de tres por veredicto,
  y lo genérico se descarta en vez de guardarse — una etiqueta «otros» que se
  lleva el 60 % no informa de nada.

  Es el dato sin el cual ningún bucle de mejora es posible, y sirve por sí solo
  aunque ese bucle no llegue nunca.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_10_a
    runtime: python-pytest
    command: "pytest tests/unit/test_reject_taxonomy.py tests/integration/test_review_verdict_shape.py -q -p no:randomly"
  ```

### `task_gov_11` — El disparador de SkillOpt, escrito para que no se olvide

- [ ] **Título**: Nota de decisión aplazada CON su condición de reapertura
- **Tiempo**: 1 h · **Complejidad**: s
- **Descripción**: El operador aplazó SkillOpt —el bucle que convierte rechazos
  repetidos en parches a las instrucciones del agente— **con disparador escrito**,
  no indefinidamente. Queda anotado en el ADR de la fase 4 y en el informe: se
  reabre cuando existan las dos redes que sus propios frenos presuponen y que
  este plan construye — `task_gov_02` (versionado) y `task_gov_05` (evals que
  bloquean).

  Montar el bucle antes que las redes sería lo contrario de lo que hace el
  framework que lo inspira: ellos lo tienen porque PRIMERO tienen el golden-set.

- **Tests automáticos**:
  ```yaml
  - id: auto_govp_11_a
    runtime: python-pytest
    command: "pytest tests/docs/ -q"
  ```

---

## Tests humanos del Plan

```yaml
- id: human_govp_01
  title: La precedencia resuelve un caso real
  steps: >-
    Coge uno de los tres casos de agosto en que un plan pedía algo que un ADR
    posterior rechazó (0117 b, 0150, 0141). Comprueba que hoy, con la regla y el
    campo `rejects:`, se resuelve leyendo el frontmatter y sin deliberar.
- id: human_govp_02
  title: Editar un prompt en producción y ver que la eval te para
  steps: >-
    En un proyecto `production`, edita el `system_prompt` de un agente
    empeorándolo a propósito. Debe rechazarse Y decir QUÉ escenarios empeoraron.
    Repite en un proyecto `development`: debe guardar y avisar.
- id: human_govp_03
  title: El número del detector de Goodhart, leído
  steps: >-
    Tras una semana de runs, mira la métrica de contaminación. La DECISIÓN de si
    se implementa la pasada ciega se toma con ese número delante — es el objetivo
    entero de la fase 3.
```

## Criterios de cierre

1. Las once casillas marcadas `[x]` con sus tests en verde.
2. Los tres tests humanos validados por el operador.
3. Entrada en `docs/07-changelog/gov-01-precedencia-prompts-y-rigor.md`.
4. PR mergeado a `master`.

## Lo que este plan NO hace, y por qué

- **No implementa la pasada ciega del revisor.** Se mide primero (fase 3). Si el
  dato la justifica, será su propio plan con su ADR.
- **No implementa SkillOpt.** Aplazado con disparador escrito (`task_gov_11`).
- **No toca la validación humana.** El rigor por niveles se queda en las pasadas
  de review por decisión expresa del operador.
- **No adopta el consilium de cinco revisores** ni el puente MCP a Codex: Azure
  Foundry ya nos da linaje OpenAI dentro del catálogo cerrado del ADR 0021.
