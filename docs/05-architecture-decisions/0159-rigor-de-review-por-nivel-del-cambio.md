---
title: "ADR 0159: Rigor de review por nivel del cambio — qué cambia en cada nivel y quién clasifica"
status: proposed
date: 2026-08-20
deciders: [operador]
relates_to: [0013, 0083, 0086, 0087, 0095, 0096, 0099, 0151, 0154]
plan_referenced: gov-01-precedencia-prompts-y-rigor
task: [task_gov_08]
docs_language: es
---

# ADR 0159 — Rigor de review por nivel del cambio

> **Estado: `proposed`.** Este documento **no decide**: deja las opciones con su
> coste medido contra el código para que el operador elija. Lo escribe Claude
> Code el 2026-08-20 preparando `task_gov_08` del plan
> [`gov-01`](../roadmap/gov-01-precedencia-prompts-y-rigor.md); `task_gov_09`
> (la implementación) **no se toca** hasta que esto se firme.

## Lo que YA está decidido y no se pregunta aquí

El operador respondió el **2026-08-12** (decisión 2 del informe
[`2026-08-12-analisis-agentic-workflow.md`](../roadmap/2026-08-12-analisis-agentic-workflow.md)):

- **Sí** al rigor por tamaño del cambio.
- **Sólo las pasadas de review.** La **validación humana al cierre del plan NO se
  toca**: sigue siendo del operador siempre, en cualquier nivel. Es la frontera
  con el principio rector 11 y no está en discusión.

Lo que queda abierto —y es lo que este ADR pone sobre la mesa— son **cuatro
preguntas**: qué opción se implementa, quién clasifica y con qué auditoría, si el
operador puede promover un nivel a mano, y qué pasa cuando no hay clasificación.

## Verificado contra el código (2026-08-20), porque el enunciado envejece

| Hecho                                                                                                                                                          | Dónde                                                                                                                 |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `Task.estimated_complexity` es `String(4)` **nullable**, valores `xs\|s\|m\|l\|xl`                                                                             | `db/domain/plans_tasks.py:175`                                                                                        |
| Lo produce el **planner**: el spec trae `complexity` y se acepta sólo si está en el conjunto; si no, queda **NULL**. Nadie lo audita                           | `chat/sync_to_kanban.py:475-477` (y `:561` al crear)                                                                  |
| **Un solo consumidor**: la calibración de coste                                                                                                                | `chat/cost_calibration.py:104`                                                                                        |
| Que no lo consuma el dispatch **no es un descuido**: el routing por complejidad se propuso y se **descartó** (opción B, operador 2026-06-26)                   | ADR [0083](0083-colas-heavy-gpu-routing-o-recorte.md)                                                                 |
| Un humano **ya puede escribirlo hoy** por API, en creación y en edición parcial                                                                                | `routers/tasks.py:363` y `:492` (`enum_fields`)                                                                       |
| Hoy hay **exactamente una** ejecución de review por entrada en `in_review`, y la idempotencia se guarda mirando «¿hay alguna ejecución `running` de la tarea?» | `orchestrator/dispatch.py:612-676`                                                                                    |
| `executions` **no tiene** columna que diga que un run es de review, ni índice de pasada                                                                        | `db/domain/executions.py:41-140`                                                                                      |
| `executions` está **particionada** por rango sobre `created_at`                                                                                                | ADR [0151](0151-retencion-de-tablas-append-only.md); FK hacia ella, ADR [0154](0154-fk-hacia-tablas-particionadas.md) |
| El tamaño del cambio **sí se sabe medir**, pero hoy sólo por **plan**, bajo demanda y en el worker                                                             | `code_diff.py` (`--numstat`), ruta `plans.py:452+` (ADR [0099](0099-visor-diffs-codigo-flujo-conflictos.md))          |
| `submit_result(summary, files_changed, …)` con `files_changed` es **prosa de ADR, no código**: cero apariciones del campo en el repo fuera de esa línea        | ADR [0086](0086-contrato-salida-estructurada-review-finish.md), línea 74                                              |

### ⚠️ La trampa que hay que decir antes que nada: hay DOS cosas llamadas «review»

1. **`self_review`**, un nodo del grafo **dentro de una ejecución**, acotado por
   `max_review_retries` — **límite duro de plataforma** (default 3) que vive en
   `platform_settings` **sin `tenant_id`** y que un tenant no puede aflojar
   (ADR [0013](0013-agent-loop-langgraph.md)).
2. **El reviewer**, una ejecución **aparte** despachada al entrar la tarea en
   `in_review`, cuyo veredicto es autoritativo (ADR
   [0087](0087-self-review-autoritativo-escalado-humano.md), [0096](0096-precedencia-verdict-vs-escalacion-review.md)).

«El número de pasadas de review» de este ADR es **(2)**. Cablear el nivel a
`max_review_retries` sería tocar (1): una salvaguarda global, sin dimensión por
tarea, con semántica de reintentos de auto-revisión. Está escrito aquí porque el
nombre invita al error y el coste de equivocarse es una regresión de seguridad,
no un bug visible.

## Opciones

### Opción A — El nivel decide cuántas pasadas de reviewer, y nada más

Dos niveles: **bajo** (`xs`, `s`) → 1 pasada, como hoy; **alto** (`m`, `l`, `xl`
y **NULL**) → 2 pasadas, la segunda con reviewer distinto si el proyecto lo
tiene.

- **Coste real, y no está donde parece**: el despacho de la 2ª pasada es
  trivial; lo caro es que **la guarda de idempotencia que hoy protege de un
  evento re-entregado es exactamente la que impide una segunda pasada legítima**
  (`dispatch.py:655-672`). Hace falta estado que distinga «pasada 2 de 2» de
  «este evento ya lo vi»: columnas `review_tier` y `review_pass` en `executions`
  (migración sobre tabla particionada, patrón conocido) y la guarda pasando a
  mirar la pasada, no «hay algo corriendo».
- **Estimación**: 4-5 d, de los cuales ~1 es la migración y ~2 la guarda y sus
  tests de re-entrega.
- **Contra**: duplica el coste de review donde se active, y el ahorro del nivel
  bajo es _no hacer_ lo que hoy tampoco se hace — o sea, **el neto es más gasto**,
  no menos. Quien lea «rigor por tamaño» esperando ahorrar, aquí no ahorra.

### Opción B — A, más auto-promoción por tamaño medido

Igual que A y, además, una tarea clasificada `xs`/`s` que acabe tocando más de N
ficheros **sube de nivel antes de revisarse**. Es «la parte más lista de su
diseño» según el informe (§2.3) y el **seguro** contra el clasificador que se
equivoca.

- **Coste añadido**: hoy **nada mide el diff de una TAREA**. El `--numstat`
  existe por plan, bajo demanda, y corre en el worker porque la api-server no
  monta `agent-data`. Hay que medir el worktree de la tarea al entrar en
  `in_review` y persistir el número junto al nivel (si no, no se puede auditar
  por qué se promovió).
- **Estimación**: +2-3 d sobre A.
- **Contra**: un umbral N inventado hoy es un número que nadie ha medido; empieza
  siendo conservador y se queda.

### Opción C — El nivel decide la PROFUNDIDAD de una única pasada

Una sola pasada siempre, pero el contrato del reviewer cambia con el nivel
(checklist más larga, exigencia de evidencia por criterio).

- **Coste**: no toca despacho ni idempotencia ni migración; es prompt + contrato
  - tests. **2-3 d.**
- **Contra**: es la opción que **peor se mide**. «Revisó más a fondo» no deja
  rastro comprobable; dos pasadas sí. Y la contaminación que mide `task_gov_06`
  no baja por alargar la checklist de la misma pasada.

### Opción D — Instrumentar primero, gobernar después

Registrar el nivel en la ejecución (columna + backfill NULL) **sin que gobierne
nada**, y medir durante unas semanas: tasa de rechazo por nivel, retrabajo por
nivel, cuántas tareas llegan con `estimated_complexity` NULL. Con ese dato se
decide si el nivel bajo puede permitirse menos rigor.

- **Coste**: **1-2 d** (la migración y el sellado, que las otras tres necesitan
  igual: no es trabajo tirado).
- **A favor**: es literalmente el patrón que eligieron **cinco de las seis**
  decisiones del 2026-08-12 — medir antes de construir — y el que evita el modo
  de fallo dominante de esta base (mecanismo entregado, cero llamantes).
- **Contra**: aplaza el valor. Y hay una pregunta que el dato **no** contesta:
  cuánto rigor de más estamos pagando hoy en tareas triviales.

**Recomendación de este documento** (no es la decisión): **D y luego B**. D es un
prefijo estricto de las otras tres, así que no compra opcionalidad con trabajo
tirado; y si se va directo a gobernar, que sea con la auto-promoción incluida,
porque A sin B deja que un error de clasificación baje el listón sin red.

## Las tres preguntas que la opción no resuelve

1. **¿Quién clasifica, y quién lo audita?** Hoy: el planner, sin auditoría. Si el
   nivel gobierna algo, pasa a ser una entrada de seguridad y merece constar en
   `task_audit_events` cuando cambia — sobre todo si el cambio lo hace un humano.
2. **¿Puede el operador promover a mano?** Técnicamente **ya puede**
   (`PATCH /tasks/{id}` acepta `estimated_complexity`), así que la pregunta real
   no es si se permite sino si se permite **degradar**: subir el nivel es
   inofensivo, bajarlo es saltarse rigor y debería exigir motivo y quedar
   auditado, como la válvula del gate de evals de `task_gov_05`.
3. **¿Qué pasa sin clasificación?** Recomendación firme, y el plan ya la escribe
   como nodo irrenunciable: **NULL ⇒ nivel ALTO**. Un cambio sin clasificar no es
   un cambio pequeño, y el default contrario convierte un fallo del planner en
   una puerta abierta.

## Riesgos

- **El nivel es una promesa del planner, no un hecho.** Sin auto-promoción (B),
  el riesgo tiene nombre: el clasificador se equivoca a la baja y el error se
  revisa menos.
- **Recalcular el nivel al leer.** Si el planner cambia de criterio, los runs
  viejos dejarían de explicar por qué tuvieron el rigor que tuvieron. El nivel se
  **sella en la ejecución**, como `prompt_version` y `runtime_image_digest`.
- **Confundirlo con la validación humana.** Está fuera por decisión expresa; un
  test tiene que afirmarlo (un plan `xs` sigue exigiendo la firma del operador).

## Qué se comprueba si esto se acepta

- Una tarea **sin** `estimated_complexity` recibe el rigor **máximo**.
- Un evento `in_review` **re-entregado** no produce una pasada extra (la
  idempotencia sobrevive al cambio de guarda).
- Un plan de nivel bajo **sigue exigiendo** validación humana al cierre.
- El nivel que gobernó un run **se lee de la ejecución**, no se recalcula.

## Referencias

- [`docs/roadmap/gov-01-precedencia-prompts-y-rigor.md`](../roadmap/gov-01-precedencia-prompts-y-rigor.md)
  — `task_gov_08` (este ADR) y `task_gov_09` (la implementación).
- [`docs/roadmap/2026-08-12-analisis-agentic-workflow.md`](../roadmap/2026-08-12-analisis-agentic-workflow.md)
  §2.3 y §4 — la idea original y la decisión del operador.
- ADR [0013](0013-agent-loop-langgraph.md) — `max_review_retries` y las
  salvaguardas del bucle (la otra cosa llamada «review»).
- ADR [0087](0087-self-review-autoritativo-escalado-humano.md) y
  [0096](0096-precedencia-verdict-vs-escalacion-review.md) — autoridad y
  precedencia del veredicto.
- ADR [0095](0095-reviewer-contexto-codigo-y-convergencia.md) — qué ve el
  reviewer (una 2ª pasada hereda ese contexto).
