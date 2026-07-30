---
title: "ADR 0135: Qué autoriza exactamente una aprobación humana (extensión del ADR 0020)"
status: proposed
date: 2026-07-29
deciders: [operador]
relates_to: [0016, 0020, 0048, 0104, 0111, 0114]
plan_referenced: prod-03-guardrails-validacion-humana
task: task_prod03_06
---

# ADR 0135: Qué autoriza exactamente una aprobación humana

> **Estado: `proposed`.** El [ADR 0020](./0020-task-awaiting-human-approval.md)
> documenta el bucle como **limitación aceptada**; esto es su extensión y hace
> falta decidirla antes de implementar `task_prod03_06`. La decisión no es «¿lo
> arreglamos?» (sí) sino **«qué queda autorizado cuando un humano aprueba»**, y
> esa pregunta tiene respuestas con perfiles de riesgo distintos.

## Contexto verificado

### El bucle es real y está en el código

El [ADR 0020](./0020-task-awaiting-human-approval.md#L94-L101) lo dejó escrito
con todas las letras: «aprobar → backlog → re-ejecutar volverá a proponer la
misma acción sensible y volverá a aparcarse — bucle». Recorrido hoy, paso a paso:

1. El nodo `plan` del loop consulta el gate **antes** de ejecutar la tool y, si
   la categoría exige humano, para el run
   ([graph.py:856-878](../../docker/agent-runtimes/agent-runtime/agent_runtime/graph.py#L856-L878)),
   devolviendo `approval = {category, action: {tool, args}}`.
2. El worker convierte eso en una `ApprovalRequest` y aparca la task
   ([execution.py:1459-1471](../../apps/workers/src/workers/execution.py#L1459-L1471)
   → `request_approval_if_needed`).
3. El humano aprueba →
   [`resolve_approval`, approval_repo.py:217-278](../../apps/api-server/src/api_server/db/approval_repo.py#L217-L278):
   `Execution → done`, `Task → backlog`, `assigned_agent_id → NULL`.
4. El dispatcher re-asigna la task y monta un spec nuevo. Ese spec lleva
   `approval_policy` (la política del proyecto) y **nada más sobre la
   aprobación**: `run_spec.py` serializa solo la política
   ([run_spec.py:120-121](../../apps/workers/src/workers/run_spec.py#L120-L121))
   y el runtime solo la lee
   ([`__main__.py:936`](../../docker/agent-runtimes/agent-runtime/agent_runtime/__main__.py#L936)).
5. El gate, que no tiene memoria, vuelve a aparcar la misma acción.

`approved_actions` **no existe en el código**: la única ocurrencia en todo el
repo está en el propio plan `prod-03`. `tests/integration/test_approval_no_repark_loop.py`
tampoco existe.

### El bucle no está acotado por nada

Esto es lo que convierte una molestia de UX en un problema de coste:

- `resolve_approval` **no toca `retry_count`**. El contador de reintentos solo lo
  bumpean los rechazos de review
  ([reviewer_bridge.py:271-272](../../apps/api-server/src/api_server/reviewer_bridge.py#L271-L272),
  [execution.py:416-424](../../apps/workers/src/workers/execution.py#L416-L424)),
  no las re-ejecuciones por aprobación. No hay máximo.
- Los presupuestos son **por ejecución** (el envelope de `budgets/`, techo
  configurable por [ADR 0113](./0113-presupuestos-ampliables-por-proyecto.md)), y
  cada re-dispatch estrena presupuesto entero. El [ADR 0114](./0114-ask-human-no-terminal.md)
  lo dice como virtud para `ask_human` («el reloj pausado sale gratis: al ser
  re-dispatch, cada run tiene su presupuesto propio»); aquí es el agujero.
- El timeout de aprobaciones (24 h, configurable y **desactivable** —
  `get_approval_timeout_hours` / `get_approval_expiry_enabled`,
  [approval_repo.py:64-92](../../apps/api-server/src/api_server/db/approval_repo.py#L64-L92))
  solo caza requests **pendientes**
  ([approval_repo.py:318-371](../../apps/api-server/src/api_server/db/approval_repo.py#L318-L371)),
  así que un humano que responde diligentemente **mantiene el bucle vivo**.

Neto: cada vuelta quema un contenedor, un worktree y un run completo de tokens, y
el único freno es que el humano se rinda. Con el preset `customer-external`
—donde varias categorías están en `human_required`
([ADR 0104](./0104-default-approval-policy-preset.md))— es el escenario normal, no
el patológico.

### La maquinaria para arreglarlo ya existe, para OTRA categoría

Éste es el hallazgo que cambia el coste de la decisión. El
[ADR 0114](./0114-ask-human-no-terminal.md) resolvió el mismo problema de
transporte para `ask_human`: el dispatcher **ya lee `ApprovalRequest` resueltas de
esta task y las mete en el spec del run siguiente**
([dispatch.py:1635-1672](../../apps/orchestrator/src/orchestrator/dispatch.py#L1635-L1672)):

```python
select(ApprovalRequest.action, ApprovalRequest.reason)
.where(
    ApprovalRequest.task_id == task.id,
    ApprovalRequest.tenant_id == task.tenant_id,   # BYPASSRLS → predicado explícito
    ApprovalRequest.category == "human_question",  # ← el único filtro que sobra
    ApprovalRequest.status == ApprovalRequestStatus.APPROVED,
)
.order_by(ApprovalRequest.resolved_at.desc())
.limit(self._HUMAN_ANSWERS_MAX)
```

Quitar el filtro de categoría (o añadir un segundo lector hermano) y emitir
`request["approved_actions"]` es **el mismo patrón, ya probado**, con el predicado
de tenant ya puesto donde el Principio nº1 lo exige. Lo caro de
`task_prod03_06` no es el transporte: es **decidir qué se compara**, que es lo
que este ADR resuelve.

### La dependencia declarada de la tarea ya está satisfecha

`task_prod03_06` declara depender de `task_prod03_04` (el `UPDATE` condicional que
cierra la carrera aprobar-vs-timeout). Ese guard **ya existe**:
`claim_pending_approval`
([approval_repo.py:172-215](../../apps/api-server/src/api_server/db/approval_repo.py#L172-L215))
es un `UPDATE … WHERE id=:id AND status='pending'` que devuelve si ganó la
transición, y `resolve_approval` lo usa y devuelve `None` cuando pierde
([approval_repo.py:242-254](../../apps/api-server/src/api_server/db/approval_repo.py#L242-L254));
su propio docstring cita `prod-03 task_prod03_04`. Quede claro para quien
planifique: **esta tarea no está bloqueada por ahí**, solo por esta decisión.

### Dos detalles que condicionan el diseño

**El gate solo vive en el sandbox.** `agent_runtime/approval.py` es el único
punto de aplicación; el worker se limita a serializar la política
(`run_spec.py:120-121`) y no re-comprueba nada, ni para las tools que él mismo
media (`stack_exec`, [ADR 0093](./0093-ejecucion-de-stack-mediada-por-worker-stack-exec.md)).
Así que la autorización que se diseñe aquí **es una capacidad que se entrega al
sandbox**, no una comprobación del lado servidor. No es un problema nuevo (el
sandbox ya tiene las tools cableadas), pero sí una razón para que la lista sea
mínima y no una llave maestra.

**El nombre de la tool ya se canonicaliza.** `ApprovalGate.review` resuelve
alias antes de mirar el mapa
([approval.py:149-161](../../docker/agent-runtimes/agent-runtime/agent_runtime/approval.py#L149-L161),
[ADR 0048](./0048-fuente-unica-nombres-tool.md)). Cualquier clave de
autorización tiene que hacer lo mismo o un alias (`file_write` vs `write_file`)
se colará o fallará al comparar.

**Existe un normalizador, y no sirve para esto.** `LoopDetector._fingerprint` es
`json.dumps(action, sort_keys=True, default=str)`
([loop_detection.py:56-59](../../docker/agent-runtimes/agent-runtime/agent_runtime/loop_detection.py#L56-L59)).
Reutilizarlo es tentador y es un error: está calibrado para detectar
repetición dentro de un run, no para decidir una autorización, y `default=str`
hace que dos objetos distintos con el mismo `str()` colisionen. Sirve como
inspiración del formato, no como implementación.

**Nota lateral, no menor**: cuando la decisión trae un lote paralelo
([ADR 0111](./0111-tool-calling-en-paralelo-runs.md)), los elementos que exigen
aprobación se **expulsan silenciosamente** del lote
([graph.py:799-810](../../docker/agent-runtimes/agent-runtime/agent_runtime/graph.py#L799-L810)):
no se ejecutan **y no generan `ApprovalRequest`**. El humano nunca ve esas
acciones. Cualquier diseño de autorización debe decidir si también las cubre; si
no, quedan como el único camino por el que una acción sensible desaparece sin
dejar rastro para el humano.

## Opciones (cuatro ejes de decisión)

### Eje 1 — Qué queda autorizado (granularidad)

| Opción                                                                                                  | Qué autoriza                                 | Riesgo                                                                                                                                                                                                                                                                                         | Qué pasa con el bucle                                                                     |
| ------------------------------------------------------------------------------------------------------- | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| **G1 — la acción exacta**: `(tool canónico, hash de args)`                                              | Solo esa llamada, byte a byte                | El más bajo: lo autorizado es exactamente lo que el humano leyó                                                                                                                                                                                                                                | Se cierra **si** el modelo regenera args idénticos. Si cambia un espacio, vuelve el bucle |
| **G2 — la tool**: cualquier llamada a `write_file`                                                      | Todas las llamadas a esa tool durante el TTL | Alto: se aprobó «escribe `config.py`» y queda autorizado escribir `.env`                                                                                                                                                                                                                       | Se cierra siempre                                                                         |
| **G3 — la tool + su categoría**: como G2, pero la autorización caduca si la categoría de la tool cambió | Igual que G2 en la práctica                  | Casi idéntico a G2. La diferencia real es estrecha pero existe: con MCP, la categoría se deriva del `security_level` de la fila y `tool_categories_from_specs` puede cambiarla entre runs ([approval.py:89-120](../../docker/agent-runtimes/agent-runtime/agent_runtime/approval.py#L89-L120)) | Se cierra siempre                                                                         |
| **G4 — la categoría entera**: `code_changes` autorizado                                                 | Todas las tools de esa categoría             | El más alto: aprobar un `write_file` autoriza `shell_exec`, `delete_file` y `run_pytest`, que comparten categoría hoy                                                                                                                                                                          | Se cierra siempre                                                                         |

G4 merece un párrafo aparte porque es el que más tienta («el humano ya dijo que
sí a que este agente toque código») y el que más se aleja de lo que el humano
leyó: en el mapa actual, `code_changes` agrupa `shell_exec`, `stack_exec`,
`write_file`, `delete_file`, `run_pytest`, `run_lint`, `run_typecheck`,
`run_build` y `agent_invoke`
([approval.py:36-49](../../docker/agent-runtimes/agent-runtime/agent_runtime/approval.py#L36-L49)).
Aprobar «escribe este fichero» autorizaría ejecutar shell arbitrario. **G4 no
debería elegirse.**

### Eje 2 — Alcance

- **S1 — la misma task** (lo que el modelo de datos ya soporta: la
  `ApprovalRequest` lleva `task_id`, y el lector del ADR 0114 filtra por él).
- **S2 — la misma execution**: inútil, la execution se cierra al aprobar.
- **S3 — el plan o el proyecto**: la autorización sobrevive a la task. Cómodo y
  peligroso: el humano aprobó una acción en el contexto de una tarea concreta.

### Eje 3 — Vigencia

- **T1 — un solo canje**: la autorización se consume al ejecutarse la acción. Si
  el agente necesita escribir cinco ficheros, son cinco aprobaciones.
- **T2 — TTL temporal** (p. ej. 60 min desde `resolved_at`).
- **T3 — mientras la task siga viva**.

Con S1, T3 y T2 se parecen mucho (una task rara vez vive más de unas horas), así
que el eje real es **T1 (un canje) frente a «varios canjes»**.

### Eje 4 — Qué pasa con un «casi igual»

El modelo propone algo parecido pero no idéntico (un salto de línea de más, el
`path` con otra forma, el `body` reordenado).

- **N1 — re-aparcar**: estricto y honesto, pero deja el bucle vivo justo en el
  caso que más lo provoca (un LLM no es determinista).
- **N2 — caer a G2/G3 con TTL corto** (el fallback que propone el plan): cierra
  el bucle a costa de autorizar algo que el humano no leyó. **Es el sitio exacto
  donde «un hash demasiado laxo autoriza más de lo que el humano leyó» se
  materializa**, y el fallback lo hace por diseño, no por accidente.
- **N3 — re-aparcar, pero enseñando el diff**: la segunda `ApprovalRequest` lleva
  la acción aprobada anteriormente y el delta, así el humano confirma en dos
  segundos en vez de volver a leerlo todo. No cierra el bucle: lo hace **barato**
  para el humano y visible.

## Decisión propuesta (recomendación)

**G1 + S1 + T1 + N3**, es decir: _aprobar autoriza esa acción exacta, en esa
task, una vez; un «casi igual» vuelve a preguntar, pero enseñando qué cambió._

Con dos reglas de normalización que hay que escribir explícitamente, porque el
riesgo vive ahí:

1. **Se hashea TODO el `args`, verbatim.** La única normalización permitida es
   estructural y sin pérdida: canonicalizar el nombre de la tool con
   `to_canonical`, serializar con claves ordenadas y UTF-8, y hashear con
   SHA-256. **No** se recorta espacio en blanco, **no** se baja a minúsculas,
   **no** se omiten campos «poco importantes».
2. **Lo que se hashea es lo que la UI enseñó.** El hash se calcula sobre el
   `ApprovalRequest.action` persistido, que es la fuente de lo que el revisor
   leyó. Si algún día la UI resume el `action` en vez de mostrarlo entero, esta
   garantía se rompe y hay que revisar este ADR.

Por qué esta combinación y no el fallback del plan:

- El plan propone «normalización + fallback por (tool, categoría) con TTL corto»
  para evitar que el bucle vuelva. Ese fallback **convierte el riesgo en el
  camino normal**: basta que el modelo sea no-determinista —lo es— para que la
  autorización se degrade sola a «la tool entera». Un mecanismo de seguridad cuya
  ruta habitual es la laxa no es un mecanismo de seguridad.
- N3 acepta que el bucle no se cierra al 100 % y ataca en cambio lo que hace daño:
  el **coste** por vuelta. Y ese coste se puede acotar aparte, sin tocar la
  semántica de la autorización (ver más abajo).

**Recomendación adicional, independiente de la anterior y probablemente más
valiosa que ella**: acotar el bucle. Bastan dos cosas, ninguna de las cuales
necesita decidir nada de este ADR:

- **contar las re-ejecuciones por aprobación** en `resolve_approval` (bumpear
  `retry_count`, o un contador propio, y escalar a `blocked` al llegar al
  máximo). Cinco líneas. Hoy el bucle es literalmente infinito;
- **enseñar en la UI de la request** cuántas veces se ha aprobado ya la misma
  acción de la misma task. Un humano que ve «aprobada 3 veces» deja de aprobar y
  llama a alguien, que es la respuesta correcta.

Si sólo hubiera presupuesto para una cosa, **el contador vale más que la
autorización**: convierte un agujero de coste sin fondo en un fallo acotado y
visible, y no puede autorizar por error nada que el humano no leyera.

## Consecuencias

**Si se acepta G1 + S1 + T1 + N3:**

- `task_prod03_06` se concreta en: (1) lector nuevo en el dispatcher, hermano de
  `_read_prior_human_answers`, que emite `approved_actions`
  `[{tool, args_hash, category, resolved_at}]` con predicado `tenant_id`
  explícito y `limit` acotado; (2) `run_spec.build_spec` lo serializa junto a
  `approval_policy`; (3) `ApprovalGate.review` recibe la lista y, antes de
  aparcar, compara `(to_canonical(tool), sha256(args))`; (4) el canje se marca
  para que sea de un solo uso.
- Aparece un **estado nuevo que persistir**: «esta autorización ya se consumió».
  No cabe en `ApprovalRequest.status` sin ambigüedad (`approved` ≠ `approved y
usada`). Es la única parte de esta decisión que **puede pedir esquema**, y
  este carril no puede crear migraciones — ver la nota de más abajo.
- El riesgo 4 del plan `prod-03` («hash de args frágil») deja de mitigarse con el
  fallback laxo y pasa a mitigarse con el diff + el contador. Hay que reescribir
  ese riesgo en el plan.
- Los lotes paralelos ([ADR 0111](./0111-tool-calling-en-paralelo-runs.md)) siguen
  expulsando en silencio los elementos sensibles. **Queda igual que hoy**; se
  anota como deuda con nombre en vez de como comportamiento no documentado.

**Si se acepta G2/G3 con TTL** (más barato: no hace falta hash, ni normalización,
ni diff): el bucle se cierra siempre y el precio es que una aprobación autoriza
un uso de la tool que el humano no revisó. Es defendible en los presets
`sandbox`/`development`, donde casi nada está en `human_required`; **no** lo es en
`production`/`customer-external`, que son precisamente los presets donde el gate
se usa. Si se elige esta vía, debería ser **por preset**, no global.

**Si no se decide nada**: el bucle sigue, sin contador y sin techo, y cada
proyecto con `customer-external` quema runs completos hasta que el operador se
cansa. Es la única opción que no debería elegirse por omisión.

## Fuera del alcance de este ADR

- La **resumption verdadera** (el agente continúa desde donde se aparcó, con el
  contexto intacto) — Opción B del [ADR 0020](./0020-task-awaiting-human-approval.md#L71-L92)
  y prod-06. Sigue siendo la solución correcta a largo plazo; esto es el mínimo
  viable que no la contradice.
- La categoría `unlisted_category: auto|human_required` (decisión 3 de prod-03):
  es la misma familia fail-open/fail-closed, pero se decide en el ADR de política
  de fallo del motor.
- El seguimiento del coste acumulado por task, que haría el contador redundante
  con algo mejor. Hoy no existe.
- **La 14ª categoría de acción sensible** para `kanban_update`. Es una decisión de
  producto abierta que otro tramo dejó anotada explícitamente en el mapa
  ([approval.py:78-85](../../docker/agent-runtimes/agent-runtime/agent_runtime/approval.py#L78-L85)):
  la tool mueve tareas del tablero, ninguna de las 13 categorías cubre «gestión de
  tareas/plan», e inventar la 14ª toca los cuatro presets y la UI. Hoy la tool no
  está cableada, así que no hay agujero **hoy**; el día que se recablee habrá que
  decidirlo. No se decide aquí, pero conviene que el operador sepa que le espera.

## Verificación

Las afirmaciones que tienen que quedar fijadas por un test —el
`tests/integration/test_approval_no_repark_loop.py` que la tarea pide y que hoy
no existe:

1. Aparcar → aprobar → re-ejecutar proponiendo **exactamente** la misma acción →
   **no** se aparca, la tool corre.
2. La **misma tool con args distintos** → **sí** se aparca (es lo que separa G1
   de G2, y si este test falta, el hash puede estar autorizando la tool entera
   sin que nadie lo note).
3. Una tool **distinta de la misma categoría** → **sí** se aparca (lo que separa
   G1 de G4).
4. La acción autorizada, **usada dos veces** → la segunda se aparca (T1).
5. La autorización de la task A **no** vale en la task B, ni siquiera del mismo
   plan (S1).
6. Un alias (`file_write` frente a `write_file`) **no** consigue ni evadir la
   autorización ni fallar al compararla ([ADR 0048](./0048-fuente-unica-nombres-tool.md)).
7. Cross-tenant: una `ApprovalRequest` aprobada de otro tenant con el mismo
   `task_id` **nunca** aparece en `approved_actions` (el lector es BYPASSRLS; el
   predicado de tenant es la única defensa, igual que en
   [dispatch.py:1652-1653](../../apps/orchestrator/src/orchestrator/dispatch.py#L1652-L1653)).
8. Con el contador puesto: N aprobaciones de la misma acción en la misma task →
   la task acaba `blocked` con un motivo legible, no en bucle.

Y el criterio negativo, por si alguien reescribe esto en el futuro: **si borro la
lista de acciones autorizadas del spec y el test 1 sigue pasando, el test no vale
nada.** El caso 1 es el único que puede quedar verde por accidente (basta que el
modelo del doble de test no vuelva a proponer la acción).

## Nota de esquema (fuera de carril)

El canje de un solo uso (T1) necesita persistir «esta autorización ya se
consumió». Este ADR **no** crea la migración; queda anotado para quien la
implemente: la opción más pequeña es una columna nueva en `approval_requests`
(p. ej. `consumed_at TIMESTAMPTZ NULL`, reversible con un `DROP COLUMN`), que no
cambia ninguna semántica existente y deja `status` como está. La alternativa sin
esquema —derivar el consumo de la existencia de una `Execution` posterior— es
frágil y no distingue «se ejecutó» de «se re-aparcó por otra cosa».
