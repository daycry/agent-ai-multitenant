---
title: "ADR 0159: Rigor de review por nivel del cambio — qué cambia en cada nivel y quién clasifica"
status: accepted
date: 2026-08-20
decided_on: 2026-08-27
deciders: [operador]
relates_to: [0013, 0079, 0083, 0086, 0087, 0095, 0096, 0099, 0151, 0154, 0158]
plan_referenced: gov-01-precedencia-prompts-y-rigor
task: [task_gov_08]
docs_language: es
---

# ADR 0159 — Rigor de review por nivel del cambio

> **Estado: `accepted` (2026-08-27) — opción D, con cuatro condiciones.** El
> texto nació `proposed` el 2026-08-20 con las cuatro opciones y su coste medido
> contra el código; el operador firmó la **D** el 2026-08-27. Lo que sigue por
> debajo de §«Decisión» es el expediente que sostuvo la elección y **se conserva
> tal cual**, con dos correcciones marcadas: al firmar se refutaron **dos
> premisas del propio documento** (§«Dos premisas de este documento eran
> falsas»), y una de ellas cambia el presupuesto de la opción A. Plan
> [`gov-01`](../roadmap/gov-01-precedencia-prompts-y-rigor.md), `task_gov_08`;
> la implementación es `task_gov_09`.

## Decisión

**Se elige la opción D — instrumentar y medir antes de gobernar.** Decisión del
operador del **2026-08-27**. Va con **cuatro condiciones** que forman parte de
esta firma y no de la implementación posterior: las cuatro son gratis hoy y
caras dentro de tres meses, cuando ya haya filas escritas, un nombre de columna
metido en un índice y algo leyendo el dato.

**Por qué D, en tres razones y por orden de peso:**

1. **No se tira trabajo.** D es un **prefijo estricto** de A, B y C: la columna
   en `executions` y el sellado del nivel en el run los necesitan las tres
   igual. Elegir D no es comprar opcionalidad pagándola con código que luego se
   borra — es hacer el primer tramo del camino de cualquiera de las otras tres.
2. **Contesta la pregunta de la que todo depende, y que hoy nadie sabe
   responder: cuántas tareas llegan con `estimated_complexity` NULL.** No es un
   dato de color. Con el fallback de la condición (a) —NULL ⇒ ALTO—, si la
   fracción de NULL es grande, **A y B degeneran en «dos pasadas para casi
   todo»**: eso no es graduar el rigor, es **duplicar la factura de review** y
   llamarlo política. Y el riesgo no es teórico: el planner sólo escribe el
   campo si el spec trae `complexity` y además cae dentro del conjunto de cinco
   valores; cualquier otra cosa —ausente, `"medium"`, `"M"`, un número— se
   descarta **en silencio** y la tarea queda NULL
   (`chat/sync_to_kanban.py:475-477`). Nadie ha contado nunca cuántas caen.
3. **Es la única sin riesgo de regresión.** D **sella un dato y no gobierna
   nada**: ningún despacho cambia de comportamiento, la guarda del orchestrator
   no se toca, el veredicto se sigue aplicando igual. A y B mueven la guarda de
   idempotencia **y** el momento en que el worker aplica el veredicto — dos
   piezas cuyo fallo no se ve como un error, sino como una tarea que se queda
   quieta en `in_review` o como una review doble que cuesta el doble. C cambia
   el contrato del reviewer, que es la entrada de un juicio.

Y una razón de método que este repo ya tiene medida: es el patrón que eligieron
**cinco de las seis** decisiones del 2026-08-12, y el que evita el modo de fallo
dominante de esta base —mecanismo entregado, cero llamantes—, del que el propio
`estimated_complexity` es el ejemplar de manual: existe desde la migración
**0002** (`migrations/versions/20260521_0002_domain_minimum.py:551`, `String(4)`
nullable) y en más de un año no ha tenido **un solo consumidor de review**.

**Lo que esta firma NO decide, y sigue sin decidirse:** si el nivel bajo puede
permitirse menos rigor. Ésa es exactamente la pregunta que la medición viene a
contestar, y la contesta el criterio de salida de la condición (d) — no esta
firma.

### Condición (a) — NULL ⇒ nivel ALTO

Una tarea sin clasificar recibe el rigor **máximo**: en la instrumentación de
ahora y en cualquier opción que se implemente después. **Un cambio sin
clasificar no es un cambio pequeño.** El default contrario convierte un fallo
del planner —silencioso, como acaba de verse— en una puerta abierta, y la abre
justo en el caso en el que nadie miró.

Aplica **ya**, aunque D no gobierne nada, y por un motivo que sólo es barato
ahora: la columna se sella con el nivel **efectivo** (`alto` para NULL), no con
el `estimated_complexity` crudo. Si se sellara el crudo, la medición mediría al
clasificador en vez de al rigor, y el día que se encienda A habría que
reinterpretar el histórico entero para saber qué rigor tuvo cada run.

### Condición (b) — degradar exige motivo y queda auditado; promover, no

Subir de nivel es inofensivo: se revisa de más. Bajarlo es **saltarse rigor**, y
eso convierte al nivel en una entrada de seguridad. Degradar exige motivo
escrito y deja un evento en `task_audit_events`; promover no exige nada. Es la
misma asimetría que la válvula del gate de evals de `task_gov_05`.

Verificado antes de escribirlo, porque de ello depende que la condición sea
barata:

- **El apuntador de auditoría ya es genérico y no hace falta tocarlo.**
  `append_audit_event(session, *, tenant_id, task_id, kind, actor, payload, at)`
  vive en `apps/api-server/src/api_server/db/task_audit_repo.py:29-52`, y el
  modelo declara `kind` como `String(32)` **libre** —sin enum, sin CHECK— y
  `actor` como `String(128)` con formato de convención (`"user:<uuid>"`,
  `"agent:reviewer"`, `"system:plan_runner"`), en
  `apps/api-server/src/api_server/db/models.py:1189-1191`. Emitir un
  `review_tier_changed` no pide migración ni tipo nuevo: es **una llamada**.
- **Y hoy no se emite ninguno.** El endpoint que escribe
  `estimated_complexity` es `PUT /projects/{project_id}/tasks/{task_id}`
  (`apps/api-server/src/api_server/routers/tasks.py:407`), que lo aplica dentro
  de `apply_partial_update(task, payload_for_obj, enum_fields=("status",
"priority", "estimated_complexity"))` —
  `apps/api-server/src/api_server/routers/tasks.py:490-493`. **`routers/tasks.py`
  no importa ni llama a `append_audit_event` en ninguna línea**: los diecinueve
  llamantes del repo están en `approval_repo`, `execution_repo`,
  `human_agents/review`, `reviewer_bridge`, `routers/human_inbox`,
  `routers/task_lifecycle`, `orchestrator/dispatch`, `workers/execution`, el
  reconciler, el `stale_sweeper` y `test_runtime_task` — en ninguno de los dos
  routers de tareas. Hoy un humano puede bajar una tarea de `xl` a `xs` por API
  y **no queda rastro de que alguien lo hiciera**.

> **Corrección de ruta al paso.** El cuerpo de este ADR (§«Las tres preguntas»,
> punto 2) dice `PATCH /tasks/{id}`. El verbo es **`PUT`** y la ruta cuelga del
> proyecto. La semántica sí es de actualización parcial (`model_fields_set` +
> `apply_partial_update`), pero quien busque el endpoint por «PATCH» no lo
> encuentra.

### Condición (c) — la columna NO puede llamarse `review_pass`

El nombre **ya está reclamado**, con otra semántica y **sobre la misma tabla**.
El informe del 2026-08-12 propone un `review_pass` en `executions` con valores
`blind` / `informed`
([`2026-08-12-analisis-agentic-workflow.md`](../roadmap/2026-08-12-analisis-agentic-workflow.md),
línea 394): eso etiqueta **qué clase** de pasada es, no **cuál** es. Dos ejes
distintos con un nombre — y como los dos son enteros pequeños o etiquetas
cortas, nada falla: agrega mal, en silencio.

Y hay una segunda colisión, a **una letra** de distancia y del lado equivocado
de la trampa que este mismo ADR avisa más abajo: `review_passed: bool | None` es
la clave del estado del grafo del agent-runtime
(`docker/agent-runtimes/agent-runtime/agent_runtime/state.py:115`, usada en
`graph.py:381` y en once sitios más), o sea el booleano del nodo `self_review`
— la **caja #1**, la que no es ésta.

**Los nombres que se usan son `review_tier` (el nivel que gobernó el run) y
`review_attempt` (el ordinal de la pasada).** Ninguno de los dos aparece hoy en
el repo: se comprobó con `grep -rn "review_tier\|review_attempt"` sobre todo el
árbol y el único resultado es la línea de este ADR que hay que corregir.

Es el fallo exacto que este repo ya pagó con `EvalRun.subject_prompt_version`:
una columna de una tabla de runs cuyo nombre promete una semántica que el dato
no lleva, y que nadie descubre porque **no rompe nada** — el dashboard de
calidad agrupó todo bajo «(sin versión)» durante todo el Plan 14, y se midió
calidad sin poder atribuirla a ningún cambio. Está escrito en dos sitios del
propio repo para que no se repita
(`migrations/versions/20260726_0119_execution_prompt_version.py:3-11` y
`apps/api-server/src/api_server/review_contamination.py:56`), y aun así el
riesgo aquí es peor: `subject_prompt_version` estaba **vacía**, que al menos se
ve; un `review_pass` reutilizado estaría **lleno del eje equivocado**.

### Condición (d) — criterio de salida escrito, con fecha

Sin esto, D es el mecanismo estándar de aparcamiento, y el precedente está en el
propio expediente: `estimated_complexity` lleva desde la migración 0002 sin un
consumidor de review, y el aplazamiento de SkillOpt (ADR
[0158](0158-skillopt-aplazado-con-disparador.md)) sobrevivió a su propio
disparador hasta que alguien escribió un test que lo mirase.

**Ventana de medición:** desde el despliegue de la instrumentación hasta el
**2026-10-31**. **Quién revisa:** el **operador** —el único `deciders` de este
ADR—, sobre `review_tier` y el veredicto de los runs de review de esa ventana.
La revisión produce, en el mismo acto, o una enmienda a este ADR o la apertura
de `task_gov_09` con la opción que toque.

| Lo que diga el dato                                                                                    | Qué se hace                                                                                                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Runs cuyo `review_tier` es `alto` **por NULL** (no por `m`/`l`/`xl`) **> 30 %**                        | **Ni A ni B.** El problema no es el rigor, es el clasificador: primero se arregla que el planner clasifique —y que se audite que clasifica—, porque graduar sobre un dato que falta un tercio de las veces duplica el coste, no lo gradúa. |
| NULL **≤ 30 %** y la tasa de rechazo del nivel bajo (`xs`/`s`) es **menos de la mitad** de la del alto | **Opción B** (A + auto-promoción por tamaño medido). El dato respalda que el nivel bajo lleve menos rigor, y la auto-promoción es la red contra el clasificador que se equivoca a la baja — el riesgo que A sola deja abierto.             |
| NULL ≤ 30 % y las dos tasas de rechazo se parecen (diferencia **< 10 puntos**)                         | **No se gradúa por pasadas**: el nivel no predice el rechazo, así que dar dos pasadas al alto es gastar sin comprar nada. Se decide entre **C** (profundidad) y cerrar el asunto.                                                          |

**Sobre los números: 30 % y 10 puntos no están medidos, y eso es deliberado.**
Este mismo ADR le reprocha a la opción B que «un umbral N inventado hoy es un
número que nadie ha medido»; el reproche vale igual aquí. Lo que los hace útiles
no es su precisión, es que están escritos **antes** de ver el dato — que es lo
que impide leer la medición para que confirme lo que ya se quería hacer. Moverlos
después de mirar es legítimo, pero **exige enmendar este ADR por escrito
diciendo por qué**; cambiarlos de cabeza al leer el resultado es exactamente el
aparcamiento que esta condición viene a impedir.

**Y una honestidad sobre la mecanización, porque el repo ya tiene la guarda para
esto.** El campo `reopen_when:` del frontmatter (ADR
[0158](0158-skillopt-aplazado-con-disparador.md), guarda
[`tests/docs/test_adr_deferrals.py`](../../tests/docs/test_adr_deferrals.py))
convierte un aplazamiento en algo que salta solo. **Aquí no se ha podido
declarar**, y conviene decir por qué en vez de dejar la ausencia sin explicar:
la guarda exige que cada id de `reopen_when` **exista** en `docs/roadmap/` **y
cite de vuelta al ADR**, y hoy no hay ninguna casilla que represente «revisar la
medición» — `task_gov_09` es la implementación, no la revisión, y además no cita
a este ADR (comprobado con el propio parser de la guarda:
`_cites_adr(task_gov_09.text, "0159")` devuelve `False`). Declararlo contra un id
inexistente rompería `test_reopen_when_points_at_something_that_exists`, que es
poner el aplazamiento en rojo, no en verde.

Así que la fecha de arriba es **prosa**, y la prosa es justo lo que esa guarda
existe para desconfiar. **Lo primero que hay que hacer al abrir `task_gov_09`**
es cerrar ese hueco con una edición a dos lados, que esta firma no toca porque
sólo alcanza a este fichero: una casilla nueva en `gov-01` cuyo cierre sea la
revisión de la medición y que cite al ADR 0159, y aquí
`reopen_when: [<esa casilla>]`.

## Dos premisas de este documento eran falsas (refutadas al firmar, 2026-08-27)

Las dos venían del recon del 2026-08-20, las dos suenan plausibles, y **una de
las dos cambia el presupuesto de la opción A**. Se dejan en su sitio, corregidas
y marcadas, en vez de borrarlas: quien vuelva a esta pregunta dentro de seis
meses tiene que ver que se comprobaron.

### (1) La guarda de idempotencia NO impide una segunda pasada legítima

**Lo que decía:** «la guarda de idempotencia que hoy protege de un evento
re-entregado es exactamente la que impide una segunda pasada legítima»
(`dispatch.py:655-672`).

**Lo que hace la guarda:** filtra por `Execution.status == "running"` —
`apps/orchestrator/src/orchestrator/dispatch.py:662-673`, con el predicado en la
línea **668**. Es decir, bloquea **concurrencia**, no **secuencia**: una segunda
pasada despachada cuando la primera ya terminó **no ve nada `running`** y pasa
la guarda sin tocarla.

**Lo que sí impide la segunda pasada** son otras dos cosas, y ninguna estaba en
el presupuesto:

1. **El worker aplica el veredicto en el acto y saca la tarea de `in_review`.**
   `_apply_review_verdict` (`apps/workers/src/workers/execution.py:392`, llamado
   desde `:1658`) parsea el `<verdict>` y llama a `apply_reviewer_verdict`
   (`apps/api-server/src/api_server/reviewer_bridge.py:252`), que mueve la tarea
   a `done` (approve) o a `backlog` / `blocked` (reject) —
   `reviewer_bridge.py:266-272`. Cuando el orchestrator pudiera despachar la
   pasada 2, la tarea **ya no está en `in_review`**, y el propio
   `_on_task_in_review` se va por `task.status != _IN_REVIEW`
   (`dispatch.py:632`). Para que haya dos pasadas hay que **aplazar el
   veredicto** hasta la última: eso es un cambio en el worker y en
   `reviewer_bridge`, no en la guarda.
2. **El `min_age` del reconciler.** El único camino que hoy vuelve a anunciar un
   `in_review` espera `_RECONCILE_REVIEW_MIN_AGE = 5 min`
   (`apps/workers/src/workers/maintenance/reconciler.py:37-41`), y el beat corre
   cada 90 s (`apps/workers/src/workers/beat_schedule.py:138`). Una pasada 2 que
   llegue por ahí llega **tarde y sin control de cuántas van**.

**Consecuencia, que es lo que hay que escribir:** el presupuesto de A —«4-5 d, de
los cuales ~2 la guarda y sus tests de re-entrega»— **está medido sobre un tercio
del recorrido**. La guarda es la parte fácil; lo que A no presupuestó es aplazar
el veredicto (tocar el punto exacto donde el reviewer se vuelve autoritativo —
ADR [0087](0087-self-review-autoritativo-escalado-humano.md) y
[0096](0096-precedencia-verdict-vs-escalacion-review.md)) y un contador de
pasadas que no dependa del `min_age` de un reconciler. **Ninguna de las dos es
lo que se contó.**

### (2) NO hay «exactamente una» ejecución de review por tarea

**Lo que decía:** «hoy hay exactamente una ejecución de review por entrada en
`in_review`».

**Lo que pasa:** ya se producen varias sobre la misma tarea, por dos caminos
vivos:

1. **Re-anuncio del reconciler.** `_reconcile_orphan_reviews`
   (`apps/workers/src/workers/maintenance/reconciler.py:344`) busca tareas
   `in_review` con reviewer IA, sin ejecución viva y sin nada reciente
   (`_orphan_review_needs_reannounce`, `:165`) y **republica
   `task.status_changed` con `new_status=in_review`**, con lo que
   `_on_task_in_review` despacha **otra** ejecución de review. Corre cada 90 s.
2. **Re-despacho de la review no concluyente (ADR
   [0095](0095-reviewer-contexto-codigo-y-convergencia.md) D3).** Cuando el run
   de review no termina `done` —crash, timeout, cancel, `model_unresolved`—, su
   salida es un error, no un juicio: tratarlo como `reject` re-implementaría una
   tarea posiblemente correcta. Así que el worker **no aplica veredicto**, sube
   `retry_count` y deja la tarea en `in_review` («Below the cap, stay
   in_review», `apps/workers/src/workers/execution.py:427-432`) para que se
   vuelva a despachar, con tope en `max_retries` → `blocked`
   (`apps/workers/src/workers/execution.py:440-455`).

Y hay un tercero, que es además el más común y el que mejor lo demuestra: un
**reject** manda la tarea a `backlog` con `retry_count++`
(`reviewer_bridge.py:267-268`), el implementador la rehace, vuelve a `in_review`
y se despacha **otra ejecución de review de la misma tarea**.

**Consecuencia:** lo que falta para las opciones A y B **no es capacidad de
despacho** —ya la hay, por tres caminos— sino las dos piezas que ninguno de esos
caminos tiene: **un contador de pasadas** (`review_attempt`, condición c) y
**aplazar el veredicto** hasta la última. Presupuestar «despachar la 2ª pasada»
como el trabajo nuevo es presupuestar lo que ya existe.

### Dónde están propagadas estas dos frases

No se corrigen aquí porque esta firma sólo alcanza a este fichero, pero quien las
arregle no debería tener que buscarlas:

- [`docs/01-overview/03-diagrams.es.md`](../01-overview/03-diagrams.es.md),
  líneas **300-302** — las dos frases, en el mismo bullet.
- [`docs/01-overview/03-diagrams.md`](../01-overview/03-diagrams.md), líneas
  **298-301** — la versión EN de lo mismo.
- [`docs/roadmap/gov-01-precedencia-prompts-y-rigor.md`](../roadmap/gov-01-precedencia-prompts-y-rigor.md),
  líneas **838-841** — la nota de `task_gov_08`, que repite lo de la guarda
  citando `orchestrator/dispatch.py:655-672` y es de donde sale el presupuesto
  mal medido.

## Un aparte que este ADR omitía: ya hay un mando de rigor por tamaño, y está apagado

`plan_approval_double_signature_threshold` existe, es de plataforma (global, sin
`tenant_id`), y su **default es `"0"`** — o sea, apagado: con 0 siempre basta una
firma. Verificado en los dos sitios donde vive: la definición del registro
(`apps/api-server/src/api_server/platform_settings_registry.py:179-193`,
categoría «planes», `type="decimal"`, `default="0"`, `min_value=0`, sin
`max_value`) y la constante de la BD
(`apps/api-server/src/api_server/db/platform_settings.py:1118-1122`,
`DEFAULT_DOUBLE_SIGNATURE_THRESHOLD = "0"`). Lo consume
`POST /plans/{id}/approve` (`apps/api-server/src/api_server/routers/plans.py:1408-1428`):
por encima del umbral el plan pasa a `pending_second_approval` y lo tiene que
cerrar **un firmante distinto**. Es el ADR
[0079](0079-rol-aprobacion-de-planes-project-approval.md).

**No sustituye a este ADR**, y decir en qué se diferencia es la mitad útil del
aparte: gobierna la **entrada** (la aprobación del plan) y no la salida (la
review); su magnitud es el **coste estimado**, no el tamaño del cambio; y toca
justo la pieza que la decisión del 2026-08-12 dejó **fuera de alcance** — la
firma humana.

Pero está instalado, y subirlo cuesta **un cambio de setting desde el panel**.
Quien quiera «más ojos en lo caro» mientras D mide no tiene que esperar a
`task_gov_09`.

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

| Hecho                                                                                                                                                                                                                                                                                                                                                                                                            | Dónde                                                                                                                 |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `Task.estimated_complexity` es `String(4)` **nullable**, valores `xs\|s\|m\|l\|xl`                                                                                                                                                                                                                                                                                                                               | `db/domain/plans_tasks.py:175`                                                                                        |
| Lo produce el **planner**: el spec trae `complexity` y se acepta sólo si está en el conjunto; si no, queda **NULL**. Nadie lo audita                                                                                                                                                                                                                                                                             | `chat/sync_to_kanban.py:475-477` (y `:561` al crear)                                                                  |
| **Un solo consumidor**: la calibración de coste                                                                                                                                                                                                                                                                                                                                                                  | `chat/cost_calibration.py:104`                                                                                        |
| Que no lo consuma el dispatch **no es un descuido**: el routing por complejidad se propuso y se **descartó** (opción B, operador 2026-06-26)                                                                                                                                                                                                                                                                     | ADR [0083](0083-colas-heavy-gpu-routing-o-recorte.md)                                                                 |
| Un humano **ya puede escribirlo hoy** por API, en creación y en edición parcial                                                                                                                                                                                                                                                                                                                                  | `routers/tasks.py:363` y `:492` (`enum_fields`)                                                                       |
| ~~Hoy hay **exactamente una** ejecución de review por entrada en `in_review`, y la idempotencia se guarda mirando «¿hay alguna ejecución `running` de la tarea?»~~ **FALSO en las dos mitades — refutado el 2026-08-27**, ver §«Dos premisas de este documento eran falsas». La guarda mira `Execution.status == "running"`: bloquea concurrencia, no secuencia; y ya hay varias ejecuciones de review por tarea | `orchestrator/dispatch.py:612-676` (predicado en `:668`)                                                              |
| `executions` **no tiene** columna que diga que un run es de review, ni índice de pasada                                                                                                                                                                                                                                                                                                                          | `db/domain/executions.py:41-140`                                                                                      |
| `executions` está **particionada** por rango sobre `created_at`                                                                                                                                                                                                                                                                                                                                                  | ADR [0151](0151-retencion-de-tablas-append-only.md); FK hacia ella, ADR [0154](0154-fk-hacia-tablas-particionadas.md) |
| El tamaño del cambio **sí se sabe medir**, pero hoy sólo por **plan**, bajo demanda y en el worker                                                                                                                                                                                                                                                                                                               | `code_diff.py` (`--numstat`), ruta `plans.py:452+` (ADR [0099](0099-visor-diffs-codigo-flujo-conflictos.md))          |
| `submit_result(summary, files_changed, …)` con `files_changed` es **prosa de ADR, no código**: cero apariciones del campo en el repo fuera de esa línea                                                                                                                                                                                                                                                          | ADR [0086](0086-contrato-salida-estructurada-review-finish.md), línea 74                                              |

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
  trivial; hace falta estado que distinga «pasada 2 de 2» de «este evento ya lo
  vi»: columnas `review_tier` y `review_attempt` en `executions` (migración
  sobre tabla particionada, patrón conocido).

  > ⚠️ **Corregido el 2026-08-27.** Este bullet decía que lo caro era «la guarda
  > de idempotencia que hoy protege de un evento re-entregado, que es
  > exactamente la que impide una segunda pasada legítima
  > (`dispatch.py:655-672`)». **Es falso**: la guarda filtra por
  > `Execution.status == "running"`, o sea bloquea concurrencia, no secuencia.
  > Lo que impide de verdad la segunda pasada es que el worker **aplica el
  > veredicto en el acto** y saca la tarea de `in_review`, más el `min_age` del
  > reconciler. Y el nombre `review_pass`, que este bullet proponía, **está
  > reclamado** con otra semántica. Los dos puntos, con evidencia, en §«Dos
  > premisas de este documento eran falsas» y en la condición (c).

- **Estimación**: 4-5 d, de los cuales ~1 es la migración y ~2 la guarda y sus
  tests de re-entrega. **Este número no vale**: está medido sobre un tercio del
  recorrido, porque no incluye aplazar el veredicto ni el contador de pasadas
  (misma sección de refutaciones). Rehacerlo es parte de `task_gov_09`.
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

**Recomendación de este documento** (escrita el 2026-08-20, cuando aún no era la
decisión): **D y luego B**. D es un prefijo estricto de las otras tres, así que
no compra opcionalidad con trabajo tirado; y si se va directo a gobernar, que sea
con la auto-promoción incluida, porque A sin B deja que un error de clasificación
baje el listón sin red.

> **Lo que se firmó el 2026-08-27** es **D**, y el «luego B» **no** va incluido:
> queda condicionado al criterio de salida de la condición (d). Ir directo a B
> sin medir la fracción de NULL es justamente el escenario de «dos pasadas para
> casi todo» que la razón 2 de §«Decisión» descarta.

## Las tres preguntas que la opción no resuelve — contestadas al firmar

> Las tres quedaron contestadas el 2026-08-27 por las condiciones (a) y (b) de
> §«Decisión». Se conserva el planteamiento porque explica **por qué** cada una
> importa; la respuesta vinculante es la condición, no este apartado.

1. **¿Quién clasifica, y quién lo audita?** → condición (b). Hoy: el planner, sin auditoría. Si el
   nivel gobierna algo, pasa a ser una entrada de seguridad y merece constar en
   `task_audit_events` cuando cambia — sobre todo si el cambio lo hace un humano.
2. **¿Puede el operador promover a mano?** → condición (b). Técnicamente **ya
   puede** (el `PUT /projects/{project_id}/tasks/{task_id}` —este documento
   escribió «PATCH», ver la corrección de ruta en la condición (b)— acepta
   `estimated_complexity`), así que la pregunta real
   no es si se permite sino si se permite **degradar**: subir el nivel es
   inofensivo, bajarlo es saltarse rigor y debería exigir motivo y quedar
   auditado, como la válvula del gate de evals de `task_gov_05`.
3. **¿Qué pasa sin clasificación?** → condición (a). Recomendación firme, y el plan ya la escribe
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

## Qué se comprueba ahora que está aceptado

Lo que exige la **opción D con sus cuatro condiciones**, que es menos de lo que
exigiría A pero no es cero:

- El nivel que gobernó un run **se lee de la ejecución** (`review_tier`), no se
  recalcula al leer.
- Se sella el nivel **efectivo**: una tarea **sin** `estimated_complexity`
  aparece como `alto`, nunca como NULL ni como «bajo por defecto» — condición (a).
- **Nada de comportamiento cambia con D**: con la columna escrita, el número de
  ejecuciones de review de una tarea y el momento en que se aplica el veredicto
  son los mismos que antes. Es la afirmación que hace de D la opción sin riesgo
  de regresión, y por eso hay que comprobarla, no suponerla.
- Una **degradación** de nivel deja un evento en `task_audit_events` con motivo;
  una **promoción** no exige ninguno — condición (b).
- Ni `review_pass` ni `review_passed` aparecen como nombre de la columna nueva
  — condición (c).
- Un plan de nivel bajo **sigue exigiendo** validación humana al cierre. Sigue
  fuera de alcance por decisión expresa del 2026-08-12, y el test lo afirma.

Y lo que se comprobará **sólo si la medición lleva a A o a B**, que hoy no toca:
que un evento `in_review` **re-entregado** no produce una pasada extra cuando la
guarda deje de mirar «hay algo corriendo».

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
  reviewer (una 2ª pasada hereda ese contexto), y su **D3**: la review no
  concluyente que se vuelve a despachar, uno de los caminos que refutan la
  premisa (2).
- ADR [0079](0079-rol-aprobacion-de-planes-project-approval.md) —
  `plan_approval_double_signature_threshold`, el mando de rigor por coste que ya
  está instalado y apagado (§«Un aparte que este ADR omitía»).
- ADR [0158](0158-skillopt-aplazado-con-disparador.md) y la guarda
  [`tests/docs/test_adr_deferrals.py`](../../tests/docs/test_adr_deferrals.py) —
  el mecanismo `reopen_when:` y por qué la condición (d) no ha podido usarlo
  todavía.
- [`docs/01-overview/03-diagrams.es.md`](../01-overview/03-diagrams.es.md) y
  [`docs/01-overview/03-diagrams.md`](../01-overview/03-diagrams.md) — donde
  están propagadas las dos frases falsas, en ES y EN.
