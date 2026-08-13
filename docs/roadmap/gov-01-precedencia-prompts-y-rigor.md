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

- [ ] **Título**: Sección de precedencia en `CLAUDE.md` + `rejects:` en el frontmatter de los ADR + guarda de gobernanza
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
  - id: auto_gov_01_a
    runtime: python-pytest
    command: "pytest tests/docs/test_adr_precedence.py -q"
  - id: auto_gov_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_docs_governance.py -q"
  ```
  El primero debe fallar de verdad al inventar un `rejects:` que apunte a una
  casilla inexistente Y al apuntar a una casilla abierta.

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
  - id: auto_gov_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_agent_prompt_versions.py -q -p no:randomly"
  - id: auto_gov_02_b
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
  - id: auto_gov_03_a
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
  - id: auto_gov_04_a
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
  - id: auto_gov_05_a
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

- [ ] **Título**: Medir cuánto se parece el veredicto del revisor al relato del implementador
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
  - id: auto_gov_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_review_contamination_metric.py -q"
  ```

### `task_gov_07` — Aviso de linaje compartido entre autor y revisor

- [ ] **Título**: El Hub de Capacidad avisa cuando implementador y revisor son de la misma familia
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
  - id: auto_gov_07_a
    runtime: vitest
    command: "npx vitest run app/admin/projects --reporter=dot"
  ```

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
  - id: auto_gov_08_a
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
  - id: auto_gov_09_a
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
  - id: auto_gov_10_a
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
  - id: auto_gov_11_a
    runtime: python-pytest
    command: "pytest tests/docs/ -q"
  ```

---

## Tests humanos del Plan

```yaml
- id: human_gov_01
  title: La precedencia resuelve un caso real
  steps: >-
    Coge uno de los tres casos de agosto en que un plan pedía algo que un ADR
    posterior rechazó (0117 b, 0150, 0141). Comprueba que hoy, con la regla y el
    campo `rejects:`, se resuelve leyendo el frontmatter y sin deliberar.
- id: human_gov_02
  title: Editar un prompt en producción y ver que la eval te para
  steps: >-
    En un proyecto `production`, edita el `system_prompt` de un agente
    empeorándolo a propósito. Debe rechazarse Y decir QUÉ escenarios empeoraron.
    Repite en un proyecto `development`: debe guardar y avisar.
- id: human_gov_03
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
