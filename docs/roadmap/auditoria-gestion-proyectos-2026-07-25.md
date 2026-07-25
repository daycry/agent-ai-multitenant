---
title: Auditoría integral del workflow de gestión de proyectos
date: 2026-07-25
status: completed
scope:
  - agentes y personas
  - tools, MCP y skills
  - prompts de ejecución
  - contenedores de ejecución, test y revisión
  - git y worktrees
  - generación de planes y modos de chat
  - observabilidad del plan
findings: 39
branch: plan/runs-visor-trabajo
baseline_commit: a17ed99f
remediation: ./remediacion-gestion-proyectos-2026-07-25.md
---

# Auditoría integral del workflow de gestión de proyectos

## §1 Alcance y método

Revisión de punta a punta de todo lo que rodea a la implementación de proyectos: cómo se
define un agente, cómo se le arma el prompt, qué herramientas ve, cómo llegan las tools MCP,
cómo se lanza y aísla su contenedor, cómo se materializa el trabajo en git, cómo nace un plan
desde el chat, cómo se aprueba, se ejecuta, se revisa y se cierra, y qué ve el humano mientras
tanto.

**Método.** Tres barridos paralelos del árbol (pipeline de ejecución / capa de inteligencia /
capa de producto) seguidos de una **fase de re-verificación individual**: cada hallazgo se
abrió en su fichero y se confirmó contra el código antes de entrar en este informe. Los que no
sobrevivieron están en §8, con el motivo. Los `fichero:línea` de este documento son citas
comprobadas, no inferencias.

**Baseline.** Rama `plan/runs-visor-trabajo`, commit `a17ed99f`. Posterior a la remediación
del dominio Proyecto del 2026-07-18 (42 hallazgos) y a los ADR 0127 (OAuth MCP), 0128 (tools
MCP por proyecto), 0129 (servicios e imagen de runtime por proyecto) y 0130 (app-preview).

**Fuera de alcance.** Marketplace, SSO, backups, notificaciones (auditadas en AUD16),
instalador, córtex y asistente personal. Tampoco se ha ejecutado nada contra el stack vivo:
esta es una auditoría de código, no de comportamiento en runtime.

---

## §2 Resumen ejecutivo

**38 hallazgos verificados**, ninguno de fuga cross-tenant ni de escalada de privilegios. El
sistema es sólido en su núcleo — el claim atómico del dispatch, la frontera de tenant, el
envelope de aislamiento del agent-runtime y el fence anti-inyección están bien hechos (§9). Lo
que falla es **la última milla en tres sitios distintos**, y el patrón se repite:

> Hay funcionalidad construida, correcta y testeada que **nunca se conecta al consumidor
> final**. El progreso del plan se calcula y nadie lo pide. El PR se abre y nadie lo enseña.
> Las tools MCP se permiten y nunca se le anuncian al modelo. El OAuth se implementa y el
> runtime no lo pasa. Los perfiles seccomp se escriben y no se pinean. Los prompts tienen
> columna de versión y nadie la rellena.

Esa es la conclusión de la auditoría: el problema dominante **no es de diseño ni de calidad de
código, es de cableado del último tramo**. Casi todos los arreglos son pequeños.

El caso más puro es **A-13**: existe un subsistema completo de compresión jerárquica de
conversaciones —resúmenes persistidos, sustitución en la vista de contexto, feed original
intacto para auditoría, tests de integración de tres pisos— y **no lo llama nadie**. Ni
siquiera se escribió el `Summariser` de producción que su propio docstring anticipaba. Mientras
tanto, el chat de planning trunca a 50 mensajes por el extremo equivocado. La respuesta a
«cómo conservamos el contexto» ya estaba decidida e implementada desde el Plan 03; solo hay que
encenderla.

### Distribución

| Severidad | Recorrido humano | Convergencia | Infra | Observabilidad | Total  |
| --------- | ---------------- | ------------ | ----- | -------------- | ------ |
| Crítico   | 2                | 1            | 0     | 0              | **3**  |
| Alto      | 5                | 4            | 2     | 2              | **13** |
| Medio     | 4                | 5            | 4     | 5              | **18** |
| Bajo      | 1                | 0            | 3     | 1              | **5**  |
| **Total** | **12**           | **10**       | **9** | **8**          | **39** |

### Las diez que arreglaría primero

| #   | Hallazgo                                                                                | Por qué primero                                                                                                                                                   |
| --- | --------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | **A-01/A-02/A-13** El chat de planning carga y prompta los **50 mensajes más antiguos** | Rompe el producto pasados 50 mensajes. Invertir el orden es de una línea, pero deja el mal diseño: la ventana debe ser por tokens con condensado y sticky (A-13). |
| 2   | **B-01** Las tools MCP del proyecto nunca se anuncian al modelo                         | El ADR 0128 no entrega su promesa: la tool está permitida y el agente no sabe que existe.                                                                         |
| 3   | **A-03** `summary` viaja como string donde el esquema espera objeto                     | Cualquier `PUT` del plan da 422 y la UI pinta una tarjeta vacía.                                                                                                  |
| 4   | **C-01** El test-runtime arranca con `HOME=/workspace`                                  | Las cachés del toolchain caen en el worktree y `git add -A` las commitea a la rama del plan.                                                                      |
| 5   | **A-04** Las estimaciones del plan son ficticias                                        | El Gantt y el presupuesto que ve el operador no contienen información.                                                                                            |
| 6   | **D-01/D-02** Ni progreso del plan ni PR visibles                                       | El operador aprueba a ciegas y no ve el resultado. Ambos ya calculados en backend.                                                                                |
| 7   | **B-02** Un agente sin tools asignadas no ve ni `read_file`                             | Asimetría entre lo que puede ejecutar y lo que sabe que puede.                                                                                                    |
| 8   | **A-05** «Generar Plan» duplica planes                                                  | No es idempotente; el segundo huérfano compite por las mismas tareas.                                                                                             |
| 9   | **B-05** De los cuatro hooks de guardrails solo dos están cableados                     | El prompt pliega contenido de fichero y salida MCP y nunca se escanea (ppio 10).                                                                                  |
| 10  | **A-06/A-07** Ni replanificación ni spec editable                                       | Un plan mal planteado no tiene arreglo salvo rechazarlo entero.                                                                                                   |

---

## §3 Eje A — Recorrido humano (11 hallazgos)

### A-01 · CRÍTICO · El chat de planning muestra los 50 mensajes más antiguos

`list_messages` ordena **ascendente** con `limit=50` por defecto, y la UI llama sin `after`:

```python
# apps/api-server/src/api_server/routers/conversations.py:422
stmt = stmt.order_by(Message.id).limit(limit)
```

```ts
// apps/admin-panel/app/admin/projects/[id]/chat/page.tsx:248
queryFn: () => apiFetch<Message[]>(`/conversations/${activeConversationId}/messages`),
```

El endpoint está bien diseñado — soporta `after` con UUID v7 ordenable, y el docstring lo
explica. Simplemente **nadie lo usa**.

**Escenario de fallo.** Un turno de planning emite entre 6 y 10 mensajes (framing del PM, un
mensaje por especialista, síntesis). A los 6-8 turnos se superan los 50. A partir de ahí, al
recargar la página: el feed se congela en los primeros 50 mensajes; `isFinishPlanningReady`
busca el último mensaje `agent` de esa lista truncada y **el botón «Generar Plan» desaparece
para siempre**; e `isReplyInFlight` evalúa un mensaje viejo, así que el poll de respaldo
decide mal si hay respuesta en vuelo.

**Mitigación existente.** Hay un `DELETE /messages` cableado a un botón de limpiar. El usuario
puede recuperarse borrando su histórico — pero perdiendo el contexto de la planificación.

**Ojo con el arreglo fácil.** Invertir el orden corrige el síntoma y deja intacto el problema
de fondo: no hay paginación hacia atrás (`after` avanza, no existe `before`), así que con
cualquier ventana fija el usuario **nunca puede releer** lo que quedó fuera. Ver A-13.

---

### A-02 · CRÍTICO · El equipo planifica con el histórico más antiguo

El mismo defecto, en el prompt:

```python
# apps/api-server/src/api_server/chat/responder.py:845-855
select(Message).where(...).order_by(Message.created_at).limit(50)
```

Pasado el mensaje 51, **el equipo de planning deja de ver lo que el usuario acaba de pedir**.
Sigue razonando sobre el arranque de la conversación. Es peor que A-01 porque no hay señal
visible: el chat responde, con aparente normalidad, a una pregunta que ya no es la actual.

Nótese que `latest_user_text` (línea 866) hace `reversed(rows)` para el retrieval RAG — sobre
la ventana ya truncada, así que también recupera documentación para el mensaje equivocado.

**Aquí invertir el orden no basta**, y es importante entender por qué: ver A-13.

---

### A-13 · ALTO · El subsistema de compresión de conversaciones existe y no se usa

Este hallazgo es la causa raíz de A-01 y A-02, y es **el ejemplo más puro de la tesis de esta
auditoría**: no falta diseño, falta enchufarlo.

`api_server/db/conversation_compression.py` implementa una compresión **jerárquica** de
conversaciones, completa y con tests de integración
(`tests/integration/test_conversation_compression.py`, 4 casos incluida la jerarquía de tres
pisos):

- `compress_old_messages` toma la ventana más antigua de mensajes no cubiertos, la resume y
  persiste el resumen como un mensaje `system` sintético con `is_summary=True` y un adjunto
  `summary_replaces` con los UUID que sustituye. **El feed original se conserva íntegro para
  auditoría**; solo la _vista de contexto_ sustituye.
- Es jerárquico: una ventana que ya contiene resúmenes produce un resumen de nivel superior
  que los reemplaza, ganando un piso de abstracción cada vez.
- `load_context_window` recorre de nuevo a viejo, incluye cada resumen en lugar del rango que
  cubre, y devuelve `uncovered[-max_messages:]` — **los más recientes, no los más antiguos**.

O sea: **la función que hace lo correcto ya está escrita**. El problema es quién la llama.

**Estado real del cableado:**

| Pieza                      | Estado                          | Evidencia                                                                                                                                                                                                             |
| -------------------------- | ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `load_context_window`      | Se usa en **un** sitio          | `planning_context.py:264` — alimenta `project_context` (el contexto auxiliar: kanban, planes previos, config)                                                                                                         |
| El histórico que ve el LLM | **No la usa**                   | `responder.py:848-854` hace su propia consulta cruda `.order_by(created_at).limit(50)` → `history_from_messages` → `PlanningState.chat_history`                                                                       |
| `compress_old_messages`    | **Cero llamadas en producción** | Solo `tests/integration/test_conversation_compression.py`                                                                                                                                                             |
| `Summariser` de producción | **No existe**                   | Solo `ScriptedSummariser`, el doble de test. El docstring del módulo (línea 18-20) dice que «el agent loop y los flujos RAG del Plan 04 enchufarán un summariser real sobre `shared_llm.LLMProvider`» — nunca ocurrió |

La consecuencia se resume en una frase: **el cargador correcto alimenta el contexto
secundario, y el roto alimenta la conversación que el modelo realmente lee**. Y como nadie
comprime, nunca existe un solo resumen que sustituir, así que ni siquiera el camino correcto
está haciendo su trabajo hoy.

**Lo que sí queda por diseñar: el contrato del summariser.** El `Protocol` está definido pero
vacío, y en un chat de planificación un resumen genérico no sirve. Un detalle no obvio, y
decisivo dado que el módulo es explícitamente jerárquico:

> **Comprimir prosa jerárquicamente degrada de forma compuesta; comprimir un registro
> estructurado es una fusión.** Si el resumen es un párrafo, plegar S1+S2+S3 en S4 es «resume
> los resúmenes» y a los tres pisos los requisitos se han evaporado. Si el resumen es
> estructurado (`requisitos` / `decisiones` / `descartado` / `abierto`), plegar es concatenar
> y deduplicar, y los requisitos sobreviven **literales** por muchos pisos que se suban.

De ahí sale el requisito de producto: el summariser debe emitir **estructura, no prosa**, y
debe preservar en particular lo **descartado** («no queremos multi-idioma»), porque un descarte
que se pierde hace que el equipo vuelva a proponerlo cada pocos turnos.

Con eso, los «requisitos sticky» no necesitan un mecanismo aparte: son una **cláusula
verificable del contrato del summariser** — un resumen que pierde un requisito enunciado es un
bug, y se testea.

**Queda además** un techo por tokens como segunda guarda (`max_messages` sigue siendo un
contador, aunque con compresión activa su letalidad baja mucho) y un detalle de escala:
`_load_all_messages` (`conversation_compression.py:93-99`) carga **la conversación entera** en
memoria en cada turno para luego recortar. Correcto pero O(n), y pasará a ejecutarse dos veces
por turno en cuanto se cablee.

**Alcance**: el mecanismo vive sobre `Message`/`Conversation`, así que sirve a **todos los
modos de chat**, no solo a planning.

---

### A-03 · ALTO · `summary` viaja como string donde el contrato es objeto

```python
# apps/api-server/src/api_server/chat/responder.py:534
"summary": draft.get("summary") or "",          # str
```

```python
# apps/api-server/src/api_server/schemas/plans.py:42
summary: dict[str, Any] = Field(default_factory=dict)
```

`create_plan` persiste el draft de la conversación **sin pasar por Pydantic** — lo dice el
propio comentario (`plans.py:263-266`: «the conversation draft (PROY2-12) bypasses Pydantic»).
La validación de DAG sí se re-aplica; la de forma, no.

**Consecuencias.** (a) Cualquier `PUT /plans/{id}` que reenvíe el spec devuelve 422 contra un
plan nacido del chat. (b) `SummarySection` hace `Object.keys()` sobre el string, obtiene
`["0","1","2"…]`, concluye que hay resumen, entra en esa rama y renderiza **una tarjeta
«Resumen» vacía** porque busca `summary.description`.

---

### A-04 · ALTO · Las estimaciones del plan son ficticias

`_normalise_plan_draft` emite `complexity` pero **nunca `estimated_hours`**:

```python
# apps/api-server/src/api_server/chat/planning_llm.py:479-489
tasks.append({"id":…, "title":…, "description":…, "role":…,
              "complexity": complexity, "depends_on":…, "acceptance_criteria":…})
```

Y el cálculo de coste humano cae al default:

```python
# apps/api-server/src/api_server/chat/cost.py:100
hours = _coerce_hours(task.get("estimated_hours"), default_task_hours)   # 4 h
```

**Resultado.** Para todo plan nacido del chat — que son todos, en la práctica — el Gantt pinta
**barras idénticas** (el camino crítico es arbitrario) y el «coste humano» del desglose es
exactamente `nº_tareas × 4 h × tarifa`. Un número que parece información y no lo es. La
`complexity` (xs…xl) sí se emite y sí se usa para el coste de IA, así que **el mapa
complexity→horas ya existe conceptualmente**: solo falta aplicarlo.

---

### A-05 · ALTO · «Generar Plan» no es idempotente: duplica planes

`create_plan` no comprueba si la conversación ya produjo un plan. `_draft_from_conversation`
sigue encontrando el mismo attachment, y el back-link se sobrescribe:

```python
# apps/api-server/src/api_server/routers/plans.py:304-308
if payload.conversation_id is not None:
    conv = await session.get(Conversation, payload.conversation_id)
    if conv is not None:
        conv.related_plan_id = plan.id
```

Volver al chat y pulsar el botón otra vez crea un plan gemelo. El primero queda huérfano del
back-link pero **sigue existiendo, sincronizable y ejecutable**: dos planes con las mismas
tareas sobre el mismo proyecto.

---

### A-06 · ALTO · No existe replanificación

Búsqueda exhaustiva sobre `apps/`: **cero ocurrencias** de `replan` / `re_plan` /
`regenerate_plan` (case-insensitive). Las únicas salidas cuando el diseño de un plan resulta
equivocado a mitad de ejecución son:

1. Reintento por tarea (`task_lifecycle.py`, `apply_task_retry`) — no cambia el plan.
2. `reassign_with_guidance` — no cambia el plan.
3. Correcciones (ADR 0107) — **exigen `plan.status == 'rejected'`**, es decir, rechazar
   formalmente el plan entero tras la validación humana.

Un plan `in_progress` cuyo diseño se revela erróneo en la tarea 3 de 12 no puede reabrir el
chat de planning, ni regenerar el tramo pendiente, ni insertar una tarea intermedia con
dependencias. Hay que dejarlo terminar mal y rechazarlo. Esto es, con diferencia, **el mayor
hueco funcional del workflow**.

---

### A-07 · ALTO · El spec del plan es de solo lectura en toda la UI

Todas las referencias a `specification` en `apps/admin-panel` son lecturas
(`plans/[planId]/page.tsx:90`, `plans/page.tsx:37`, y tests). No hay editor de tareas,
dependencias, roles, complejidad ni criterios de aceptación **antes de aprobar**.

Lo único que se puede añadir es una free-task suelta o correcciones post-rechazo. El endpoint
`PUT /plans/{id}` acepta `specification` y **no lo usa nadie** (y contra un plan del chat
fallaría por A-03).

Combinado con A-06, esto significa: **el spec que produce el LLM es inmutable**. Si una tarea
está mal descrita, o falta una dependencia, o el rol asignado es el equivocado, la única
herramienta del operador es re-pedirle el plan entero al modelo.

---

### A-08 · MEDIO · Cuatro acciones secuenciales entre «plan generado» y «agentes trabajando»

`create_plan` nace en `draft`, y `PlanLifecycleSection` ofrece exactamente un botón por
estado, nunca dos a la vez:

```ts
// plan-lifecycle-section.tsx:72-75
const canSendToApproval = status === "draft";
const canApprove = status === "pending_approval" || status === "pending_second_approval";
const canStart = status === "approved";
```

Generar Plan → Enviar a aprobación → Aprobar plan → Empezar ejecución. Ninguno puede saltarse
ni combinarse, y no existe un «aprobar y arrancar». La doble firma y el gate RBAC justifican
la separación en un entorno de producción; en Sandbox y Desarrollo (dos de las cuatro
plantillas de política) es fricción pura.

---

### A-09 · MEDIO · Una tarea bloqueada por un fallo ordinario no tiene salida individual

`list_escalated_tasks` incluye solo `awaiting_human_approval`, o `blocked` **cuyo último run
terminó en `needs_human_review`** (o con uno de los cuatro abort-codes históricos). El
docstring lo declara intencional:

> «A plain `blocked` (latest run not escalated) is a different kind of block and stays OUT of
> this panel.» — `plans.py:1493-1494`

El criterio es defendible. El problema es que **el otro camino no existe**: `TaskDetailSheet`
solo ofrece botones de criterios de aceptación (generar / editar / guardar) y comentarios —
verificado en las líneas 318-432. Ninguna acción de ciclo de vida.

**Resultado.** Una tarea que quedó `blocked` por un run `failed` ordinario no sale en el panel
de escaladas y no tiene acciones en su ficha. Las únicas vías son arrastrarla a mano en el
Kanban o **desbloquear el plan entero**, que reinicia el presupuesto de reintentos de todas
sus hermanas.

---

### A-10 · MEDIO · Las @-menciones del chat no hacen nada

El compositor inserta `@rol` en el texto y tiene un `parsePendingMention` completo. El backend
**no parsea menciones del usuario**: la única aparición del concepto en `chat/` es
`planning_graph.py:214`, y se refiere a los especialistas que menciona el PM, no el humano.
`pm_decide` elige los especialistas por su cuenta.

Es una afordancia que sugiere control y no lo da. Peor que no tenerla.

---

### A-11 · MEDIO · Un ciclo en el DAG mata la generación sin ruta de recuperación

`_normalise_plan_draft` limpia auto-referencias y dependencias desconocidas, pero
deliberadamente **no ciclos** (`planning_llm.py:461-463` lo delega al endpoint). `create_plan`
responde 422 con `{"error":"dag_cycle","cycle":[...]}` y el botón muestra el cuerpo crudo del
error.

El usuario ve un JSON, no puede editar el borrador (A-07), y su única salida es pedirle el
plan otra vez al modelo — con la misma probabilidad de reproducir el ciclo.

---

### A-12 · BAJO · Las fases del plan se pintan sin nombre

El backend emite `{"title": …, "tasks": [...]}` (`planning_llm.py:516-524`). El frontend lee
`.name` en los dos sitios donde aparece:

```ts
// plan-spec-sections.tsx:202
<p className="font-medium">{phase.name}</p>
// plan-sync-section.tsx:165
<option key={i} value={i}>{p.name}</option>
```

La lista de fases sale sin título y **el desplegable «sincronizar una fase» muestra opciones en
blanco** — el operador elige a ciegas entre `Fase 1`, `Fase 2`… indistinguibles.

---

## §4 Eje B — Convergencia de los agentes (10 hallazgos)

### B-01 · CRÍTICO · Las tools MCP del proyecto nunca se anuncian al modelo

El ADR 0128 establece que las tools MCP las aporta el proyecto, sin grant por agente. El
dispatch extiende correctamente el allowlist:

```python
# apps/orchestrator/src/orchestrator/dispatch.py:726-730
project_mcp_tool_names = await resolve_project_mcp_tool_names(session, project, role=agent.role)
allowed_tools = extend_allowlist_with_project_mcp(allowed_tools, project_mcp_tool_names)
tool_specs = await serialize_agent_tool_specs(session, agent.id)   # ← POR AGENTE
```

Pero `build_model_tool_schemas` obtiene los esquemas de solo tres fuentes: los runtime-only,
el seed `BUILTIN_TOOLS`, y `tool_specs` — que acaba de resolverse **por agente**, no por
proyecto (`agent_tool_schemas.py:264-279`). Una tool `<server>.<tool>` que llega solo por la
vía del proyecto no está en ninguna de las tres.

```python
# agent_tool_schemas.py:283-287
for name in effective:
    entry = by_name.get(name)
    if entry is not None and name not in seen:   # ← sin esquema, se salta en silencio
```

**Escenario de fallo.** Proyecto con el MCP de Atlassian conectado y `jira.create_issue`
permitida al rol PM. El runtime conecta el servidor y registra la tool; el allowlist la
acepta. Pero el modelo **nunca recibe su esquema**, así que no sabe que existe. Solo podría
llamarla adivinando el nombre exacto y la forma de los argumentos. La tarea que dependa de esa
tool gira hasta agotar iteraciones.

El bucle del catálogo tiene un comentario que explica precisamente por qué esto no debería
pasar («Custom/MCP tools arrive via `tool_specs`, not this builtin loop»,
`agent_tool_schemas.py:208-210`) — la premisa era cierta bajo grants por agente y dejó de
serlo con el ADR 0128.

---

### B-02 · ALTO · Un agente sin tools asignadas no ve ni `read_file`

```python
# apps/workers/src/workers/agent_tool_schemas.py:256
effective: list[str] = list(tool_names or [])
```

Si el agente no tiene filas en `agent_tools`, el dispatch omite la clave `allowed_tools`, aquí
`tool_names` llega `None`, y `effective` queda con **solo los 6 nombres de sistema**
(`memory_recall`, `memory_store`, `task_comment`, `rag_search`, `update_plan`, `ask_human`).

El registry del runtime, en cambio, interpreta lo mismo como «sin restricción» y no bloquea
nada. Así que el agente **puede ejecutar `write_file` y `stack_exec` pero no sabe que
existen**. Un agente recién creado sin pasar por la pantalla de asignación de tools puede
recordar, buscar y comentar — pero no escribir código, que es su trabajo.

---

### B-03 · ALTO · El OAuth de MCP (ADR 0127) no llega al runtime

La infraestructura está completa: `build_oauth_provider` devuelve un `httpx.Auth`,
`MCPClient.connect` acepta `auth=` y lo reenvía a los transportes HTTP
(`client.py:175, 261, 273`). Pero el runner del contenedor no lo pasa:

```python
# docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py:289-291
async with MCPClient.connect(config, vault_resolver=self._vault_resolver) as session:
```

Búsqueda de `build_oauth_provider` en todo el árbol: `shared_mcp/oauth.py` (definición),
`shared_mcp/__init__.py` (re-export), `mcp_oauth_flow.py:8` (**mención en docstring**) y
`tests/unit/test_shared_mcp_oauth.py`. **Ningún consumidor de producción.**

Un servidor con `auth_kind="oauth"` conecta sin credencial. El flujo interactivo de «Conectar»
guarda el token en Vault correctamente; el runtime nunca lo recoge.

---

### B-04 · ALTO · `send_notification` se le anuncia al modelo y siempre falla

Está en el catálogo asignable (`tool_names.py:49`), en `RUNTIME_WIRED_TOOL_NAMES`
(`tool_names.py:124`) y aliasada desde `notify_user` (`tool_names.py:84`). Como
`is_runtime_wired` la acepta, **el filtro del catálogo la anuncia al modelo**. Y el ejecutor:

```python
# agent_runtime/orchestration_tools.py:101-107
def notify_user(self, args): ...
    return self._not_wired("notify_user")     # ok=False, "not wired"
```

Es el mismo patrón que AUD16-02 corrigió para `kanban_update` y `agent_invoke` — a esas dos se
las retiró del anuncio (`agent_tool_schemas.py:171-173`) **pero no de
`RUNTIME_WIRED_TOOL_NAMES`**, y a `send_notification` no se la retiró de ninguno de los dos
sitios. El agente la llama, quema un turno y recibe un error de plataforma que no puede
resolver.

---

### B-05 · ALTO · Solo dos de los cuatro hooks de guardrails están cableados

El motor soporta `pre_llm` / `post_llm` / `pre_tool` / `post_tool`
(`shared_guardrails/types.py:28-30`), y el principio rector 10 de `CLAUDE.md` los exige en los
cuatro puntos del ciclo. Búsqueda de `pre_llm|post_llm` en todo `agent-runtime`: **una sola
ocurrencia, y es un comentario** (`tools.py:16`).

El prompt que se envía al modelo pliega contenido de ficheros del repo, salida de servidores
MCP y memoria recuperada — exactamente el material de una inyección — y **nunca se escanea**.
Su respuesta tampoco.

---

### B-06 · MEDIO · `update_plan` esquiva los guardrails

```python
# agent_runtime/graph.py:950-971
if tool == "update_plan":
    plan_text = str(args.get("plan") or "").strip()[:_AGENT_PLAN_MAX_CHARS]
    ...
    return {"agent_plan": plan_text or state.get("agent_plan"), ...}
result, guardrail_events = self._screened_tool_call(tool, args)   # ← inalcanzable para update_plan
```

Retorna antes de `_screened_tool_call`. Su contenido se convierte en **sticky permanente** del
prompt (se re-inyecta cada turno, fuera de la ventana de 8 items para no ser evictado), así
que es el texto con mayor persistencia de todo el run — y el único que no pasa por
`pre_tool`/`post_tool`.

---

### B-07 · MEDIO · Un fallo de conexión MCP es invisible para el prompt

`_wire_mcp_servers` captura el fallo por servidor, lo loguea y emite un step `mcp_wire` — el
operador lo ve. Pero los bloques que compone `assemble_system_preamble` son: persona,
comentarios del equipo, respuestas humanas, rechazos previos, fallo previo, contexto de review
y fragments de skills. **No hay bloque de estado de MCP.**

El agente con un criterio de aceptación que exija consultar Jira no sabe que Jira no conectó.
Reintenta, gira y agota el presupuesto sin poder explicar por qué.

---

### B-08 · MEDIO · Cero versionado de prompts, con un dashboard esperándolo

Todos los system prompts, preámbulos y nudges son constantes de módulo: no hay hash, ni id de
release, ni fecha, ni tabla. Existe la columna `EvalRun.subject_prompt_version`
(`db/evals.py:374`) y **ningún caller de producción la puebla**: los únicos son el parámetro
de `evals/shadow.py:198,225` y `tests/integration/test_llm_judge.py:173`.

Río abajo hay una pantalla entera construida sobre ese campo —
`app/admin/eval-quality/page.tsx` agrupa por release de prompt y el rollup de
`routers/eval_quality.py:315-332` hace `group_by(subject_prompt_version)`. Con el campo
siempre `NULL`, **todas las filas caen en «(sin versión)»**.

No se puede correlacionar una regresión de calidad con un cambio de prompt. Cada vez que se
toca un system prompt se pierde la trazabilidad de su efecto.

---

### B-09 · MEDIO · `tool_classification.py` no tiene tests

Lo importan `providers.py:67`, `nudges.py:15` y `graph.py:72`. Búsqueda en `tests/` y en
`docker/agent-runtimes/agent-runtime/tests/`: **cero referencias directas**.

De este módulo dependen todas las guardas de convergencia: `_is_producing_tool` latchea
`has_produced`, que decide si un run agota-y-aborta o escala a `blocked`; `_is_readonly_tool`
gobierna el batching de lecturas y las guardas por novedad. Un cambio en su clasificación
altera silenciosamente el comportamiento de finalización de todos los runs.

Además clasifica dos tools que **no existen en el runtime**: `search_code` como research y
`apply_patch` como producing (ambas fuera de `RUNTIME_WIRED_TOOL_NAMES`).

---

### B-10 · MEDIO · Un run de reviewer recibe dos contratos de veredicto

Coexisten cinco contratos de sistema (`_DECIDE_SYSTEM`, `_REVIEW_RUN_SYSTEM`, `_REVIEW_SYSTEM`,
`_ASSESS_SYSTEM` en `providers.py`, más `_REVIEW_VERDICT_INSTRUCTION` en `__main__.py`). En un
run de review se aplican **dos a la vez**: `_REVIEW_RUN_SYSTEM` con su formato `<verdict>` y
el preámbulo `build_review_preamble` repitiendo el mismo formato.

El propio código documenta que esa duplicación ya provocó contratos en competencia una vez
(`providers.py:118-123`), y el **ADR 0108 está `proposed`** precisamente para fusionarlos.
Sigue abierto.

---

## §5 Eje C — Infra de ejecución (9 hallazgos)

### C-01 · ALTO · El test/stack-runtime arranca con `HOME=/workspace`

```python
# apps/workers/src/workers/test_runtime.py:895
env: dict[str, str] = {"HOME": template.workspace_mount_path}   # "/workspace"
```

`/workspace` es el **worktree bind-montado en lectura-escritura**. Tres cosas señalan que esto
es un descuido, no un diseño:

1. Es exactamente el bug que ya se corrigió en el agent-runtime, con la causa raíz escrita en
   el código: _«with HOME=/workspace that landed in the agent's project worktree and the agent
   read it back, polluting every model_call's context»_ (`isolation.py:115-119`).
2. Veinte líneas más abajo, en el mismo fichero, el código promete lo contrario: _«never
   clobber HOME (the template owns it and the toolchain caches hang off it)»_
   (`test_runtime.py:915-916`).
3. Las propias imágenes declaran `ENV HOME=/home/agent`.

**Escenario de fallo.** El agente pide `composer install` por `stack_exec`. Composer escribe
`~/.composer` → `/workspace/.composer`. Al terminar la tarea, `commit_task` hace `git add -A`
sobre el worktree. Salvo que el `.gitignore` del proyecto lo cubra —y no lo cubrirá, porque no
es un path que un proyecto normal ignore— **la caché entera se commitea a la rama del plan** y
acaba en el PR.

Efecto colateral: como `HOME` apunta al bind RW en vez de a la capa read-only, el problema de
permisos que debería haber delatado esto queda enmascarado.

---

### C-02 · ALTO · El test/stack-runtime no aplica `pids_limit` ni perfiles endurecidos

Comparación de los dos envelopes:

|                     | agent-runtime (`isolation.py`) | test/stack-runtime (`test_runtime.py:935-944`) |
| ------------------- | ------------------------------ | ---------------------------------------------- |
| `cap_drop: ALL`     | ✅ L127                        | ✅ L935                                        |
| `no-new-privileges` | ✅ L103                        | ✅ L936                                        |
| `read_only` root    | ✅ L129                        | ✅ L937                                        |
| `mem_limit`         | ✅ L131                        | ✅ L943                                        |
| **`pids_limit`**    | ✅ L132                        | ❌                                             |
| **seccomp custom**  | ✅ L105-109                    | ❌                                             |
| **apparmor**        | ✅ L111-113                    | ❌                                             |
| `user 1000`         | ✅ L133                        | ✅ L939                                        |

El contenedor que **ejecuta código del proyecto y comandos del toolchain con egress a
registries** es el que menos contención tiene. Una fork bomb en un `composer install`
malicioso, o simplemente un `make -j` desbocado, no encuentra techo de procesos. Los sidecars
auxiliares del ADR 0129 sí tienen `pids_limit` (`test_runtime.py:379`); el principal no.

Matiz importante: el seccomp **por defecto de Docker sigue activo** (el código nunca pasa
`seccomp=unconfined`). La ausencia es del perfil endurecido, no de todo perfil.

---

### C-03 · MEDIO · Los perfiles seccomp/apparmor endurecidos existen y no se pinean

`docker/seccomp/agent-runtime.json` y `docker/apparmor/agent-runtime.profile` están escritos y
el código sabe cargarlos (`isolation.py:105-113`). Pero los settings que los activan tienen
default vacío y **ningún compose exporta `WORKERS_SECCOMP_PROFILE` ni
`WORKERS_APPARMOR_PROFILE`**. Un compose llega a montar `./seccomp:/etc/agentic/seccomp:ro`
sin exportar la variable que lo usaría.

Los servicios de la plataforma sí llevan el ancla `*default-seccomp`; los **contenedores
efímeros de agente, que son los que ejecutan código no confiable**, corren con el perfil por
defecto. Trabajo hecho y desconectado.

---

### C-04 · MEDIO · Los tests de aceptación corren dentro del worker de la cola `default`

```python
# apps/workers/src/workers/execution.py:908
await _run_test_runtime(test_request, settings)
```

Es un `await` directo, no un `send_task`. La fase de tests posterior al run ocupa el slot del
worker `default` hasta N × 600 s. Con `--concurrency=2`, dos tareas en fase de tests dejan la
cola de ejecución de agentes parada.

`stack_exec` **ya resolvió este mismo problema** enrutando explícitamente a la cola `test` por
riesgo de deadlock (`celery_client.py:157`). El camino de tests post-run no aplicó el mismo
criterio.

---

### C-05 · MEDIO · El run-lock caduca antes que el hard kill

```python
# apps/workers/src/workers/tasks/run_cycle.py:224
lock_ttl = settings.container_timeout_with_grace_for_kind("claude_sdk") + 300
```

`7200 (claude_sdk) + 120 (grace) + 300 = 7620 s`, contra un `execution_hard_time_limit_s` de
**7800 s** (`platform_settings.py:905`). Ventana de 180 s en la que el lock ya no existe y el
run sigue vivo.

El comentario del propio lock explica el escenario que previene: una redelivery `acks_late`
que provisiona el mismo worktree, cuyo `sync_to_head` hace `reset --hard` + `clean -fdx` sobre
el trabajo en vuelo del primer run. Es una ventana estrecha y solo se abre en runs que llegan
al límite duro — pero es exactamente el caso peor, el run largo que más trabajo tiene que
perder.

---

### C-06 · MEDIO · `sync_to_head` borra las dependencias en cada reintento

```python
# apps/workers/src/workers/git_repos.py:505-507
_run_git("fetch", str(self._repo_path), branch, cwd=wt)
_run_git("reset", "--hard", "FETCH_HEAD", cwd=wt)
_run_git("clean", "-fdx", cwd=wt)
```

Deliberado y documentado («so the agent starts from a deterministic state»). El coste es que
`-x` incluye los ficheros ignorados: **`vendor/`, `node_modules/`, `.venv/` se borran en cada
reintento** de la misma tarea, forzando una reinstalación completa. El camino de review lo
evita a propósito (`review_runtime_task.py:222-232`, «Sin `clean` a propósito»); el de
implementación no.

Con la caché de dependencias del ADR 0094 el golpe se amortigua, pero no desaparece: la
descompresión y el enlazado se repiten enteros.

---

### C-07 · BAJO · `pump.join()` sin timeout

```python
# apps/workers/src/workers/container.py:208
pump.join()
```

Deliberado, y el motivo es bueno: un corte a media lectura pierde la cola del log, incluida la
línea `execution.finished` que la UI necesita. El riesgo es que si el stream no llega nunca a
EOF —daemon colgado, socket a medio cerrar— el hilo del worker queda bloqueado
indefinidamente **después** de que el contenedor ya salió. Un `join(timeout=N)` generoso con
log de la anomalía conserva la intención sin el bloqueo eterno.

---

### C-08 · BAJO · Código muerto en el paquete de workers

- **`runtime_pool.py`** (393 líneas, pool elástico por plan): su único consumidor es
  `scripts/demo_human_06_c_pool_policies.py`.
- **`TestcontainersMode` + `build_dind_proxy_run_kwargs`** (~90 líneas, y el único sitio que
  monta el socket Docker a propósito): ningún `TestRuntimeSpec` de producción pasa
  `testcontainers=`; sus únicas referencias fuera del módulo están en
  `tests/security/test_pentest_findings.py`.
- **`ReviewRuntimeManager`** (índice en memoria): el camino real persiste en `review_sessions`
  sin usar esta clase, hasta el punto de que el cap por tenant tuvo que reimplementarse aparte.

No es urgente, pero el `TestcontainersMode` en concreto mantiene vivo un camino de código que
monta el socket Docker — precisamente lo que el principio rector 2 prohíbe — sin que nada en
producción lo ejercite.

---

### C-09 · BAJO · El reconciler espeja al dispatch, por diseño

`_reconcile_complete_plans` reimplementa `_on_task_done`: mismos snapshots, misma máquina de
estados, mismo `UPDATE … WHERE status='in_progress' RETURNING`, mismo autostart. El docstring
lo declara: _«Mirrors `orchestrator._on_task_done` exactly … so the reconciler never diverges»_
(`reconciler.py:323-326`).

La intención es correcta —la red de convergencia no debe depender del camino vivo— pero la
garantía es un comentario, no un test. Son ~80 líneas espejo en dos aplicaciones distintas y
ya divergieron una vez en el pasado. Un test de contrato que ejercite ambas rutas contra el
mismo fixture fijaría la promesa.

---

## §6 Eje D — Observabilidad (8 hallazgos)

### D-01 · ALTO · El progreso del plan se calcula y nadie lo pide

`compute_plan_progress` produce el conteo X/Y, la etiqueta y el coste acumulado
(`plan_progress.py:101`). Sus consumidores: `orchestrator/plan_runner.py:271` —que es la demo
in-memory, no cableada en producción— y los tests de integración. **No hay endpoint
`GET /plans/{id}/progress`.**

Las tarjetas del tablero gerencial muestran únicamente el badge de estado. Un plan de 12
tareas lleva tres semanas en `in_progress` y el operador no puede saber si va por la 2 o por
la 11 sin abrir el Kanban y contar a mano. Es la señal más pedida de un tablero de planes y
está calculada, testeada y desconectada.

---

### D-02 · ALTO · El PR del plan no se muestra en ningún sitio

`pr_url`, `pr_branch` y `pr_error` viajan en `PlanResponse` (`schemas/plans.py:132-134`).
Búsqueda de los tres en todo `apps/admin-panel`: **cero ocurrencias**.

El recorrido completo termina en silencio: el operador da el veredicto, la tarea de auto-PR se
encola, y no ve ni el enlace al PR resultante **ni el motivo si falló**. Un `pr_error` por PAT
caducado o por `push_policy: forbidden` es indistinguible del éxito desde la interfaz.

---

### D-03 · MEDIO · No hay WebSocket de plan

`routers/ws.py` expone cuatro streams: `executions`, `kanban`, `conversation`, `documents`.
Ninguno para cambios de estado de **plan**.

El tablero gerencial —la vista superior del doble Kanban, principio rector 6— no tiene ni
stream ni `refetchInterval`. Un plan que pasa a `pending_human_validation` o a `blocked` no se
refleja hasta que alguien recarga. La vista que existe precisamente para que un gestor la deje
abierta es la única que no se actualiza sola.

---

### D-04 · MEDIO · No hay coste real contra estimado a nivel de plan

`CostBreakdownSection` pinta solo el presupuesto estimado. `GET /runs?plan_id=` existe y
ninguna pantalla lo usa con ese filtro (la de runs filtra por veredicto). No hay ninguna vista
de «tokens y euros consumidos por este plan».

Con A-04 encima —las horas estimadas son ficticias— el resultado es que **el operador no tiene
ninguna cifra fiable de coste**, ni antes ni después. Los datos reales existen en
`executions`; falta agregarlos por plan.

---

### D-05 · MEDIO · Standup y retrospectiva producen salida que nadie ve

`workers/standup.py` (ADR 0120) emite una notificación diaria y `workers/plan_retro.py`
(ADR 0124) escribe una memoria `project_shared` al cerrar un plan. Búsqueda de
`standup|retro|retrospectiva` en `apps/admin-panel/app`: solo aparece `leaderboard`.

No hay pantalla de standup ni sección «Retrospectiva» en el detalle del plan. **El aprendizaje
de un plan cerrado es invisible para el humano**: lo consumen los agentes vía recall, el
operador no.

---

### D-06 · MEDIO · Configuración de proyecto sin interfaz

Verificado por búsqueda en `.tsx`, sin UI: `execution_budgets` (ADR 0113), `guardrails_config`
a nivel de proyecto (solo existe el default de plataforma), `budget_amount` /
`budget_currency` / `budget_period*`, `secrets_vault_id`, `human_task_review_mode`.

Todos son columnas con validador y camino de API funcionando. La única forma de fijarlos es
`curl`.

> **Descartado durante la re-verificación:** `allowed_domains` **sí tiene UI**, en
> `projects/[id]/commands/page.tsx:344` («allowed_domains (P1-03)»). Se reportó como huérfano
> y no lo es.

---

### D-07 · MEDIO · Dos definiciones contradictorias de plan completado

La función pura exige que el PR esté mergeado:

```python
# apps/api-server/src/api_server/plan_progress.py:307-336
def transition_to_completed(current_status, *, human_verdict, pr_merged) -> TransitionResult:
    """Plan goes to ``completed`` iff human approved AND PRs merged."""
    if not pr_merged:
        return TransitionResult(..., reason="PR not merged yet")
```

El camino real no la llama. `submit_verdict` transiciona con `transition_plan_status` y
**después** encola el PR:

```python
# apps/api-server/src/api_server/routers/review.py:515-528
transition_plan_status(plan, "completed" if body.verdict == "approved" else "rejected")
...
if pr_ctx is not None:
    await enqueue_open_plan_pr(...)
```

El orden es deliberado (ADR 0072 fase 2, documentado en el comentario). El problema es que
`transition_to_completed` sigue existiendo con una definición incompatible, tiene tests de
integración que la ejercitan, y contradice a `CLAUDE.md`, cuyo criterio de cierre nº5 es «PR
del plan mergeado». Hoy un plan llega a `completed` con un `pr_error` y nadie se entera (D-02).

Hay que decidir cuál de las dos es la verdad y borrar la otra.

---

## §7 Características nuevas propuestas

Separadas de los bugs a propósito: esto **no** son defectos, son huecos de producto.

| #       | Característica                                                                                                                                                                       | Qué resuelve                                                                                                                                |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------- |
| **F-1** | **Replanificación en caliente.** Reabrir el chat de planning sobre un plan `in_progress`, con las tareas ya terminadas como contexto inmutable, y regenerar solo el tramo pendiente. | A-06. Es el hueco más grande del workflow. Necesita ADR: qué pasa con las tareas en vuelo, con la rama git y con el presupuesto ya gastado. |
| **F-2** | **Editor del spec antes de aprobar.** Tabla editable de tareas (título, descripción, rol, complejidad, dependencias, criterios) sobre `PUT /plans/{id}`, que ya existe.              | A-07, A-11. Convierte el borrador del LLM en un punto de partida en vez de un ultimátum.                                                    |
| **F-3** | **Panel de plan vivo.** `GET /plans/{id}/progress` + WebSocket de plan + PR + coste real contra estimado, en la cabecera del detalle.                                                | D-01 a D-04 de una vez. Casi todo el backend ya está.                                                                                       |
| **F-4** | **Versionado de prompts.** Hash del conjunto de prompts del runtime, propagado a `EvalRun.subject_prompt_version` en cada run.                                                       | B-08. Enciende un dashboard que ya está construido.                                                                                         |
| **F-5** | **«Aprobar y arrancar».** Un botón que encadena las transiciones cuando la política del proyecto no exige doble firma.                                                               | A-08.                                                                                                                                       |
| **F-6** | **Acciones humanas en la ficha de tarea.** Reintentar, reasignar con guía, desbloquear — desde `TaskDetailSheet`, sin pasar por el panel de escaladas ni desbloquear el plan entero. | A-09. Los endpoints ya existen en `task_lifecycle.py`.                                                                                      |
| **F-7** | **Estado de MCP en el prompt.** Un bloque del preámbulo que declare qué servidores conectaron y cuáles no.                                                                           | B-07. Convierte un giro ciego en un fallo explicable.                                                                                       |

### Tres features que añadiría, por orden de valor

**N-1 · Brief de las tareas predecesoras.** _(Verificado: hoy no existe.)_

En un plan de 12 tareas con dependencias DAG, el agente de la tarea B **no sabe nada de lo que
hizo la tarea A**, de la que depende. `depends_on` se usa en el dispatch **solo** para
reconciliar el estado del DAG y decidir desbloqueos (`dispatch.py:433-451`,
`TaskSnapshot.depends_on`); nunca entra en el prompt. Lo que recibe el agente es: su título, su
descripción, sus criterios, su persona, los comentarios, el feedback de sus **propios** intentos
previos — y una lista de 60 rutas del worktree.

O sea: ve que aparecieron ficheros, pero no **quién los hizo, por qué, ni con qué contrato**.
Tiene que inferir el diseño de su predecesor leyendo código, y a menudo lo reinterpreta.

Esto es, en mi opinión, la razón estructural de que un plan largo salga incoherente: no es un
equipo trabajando sobre un diseño común, son doce tareas aisladas que comparten un directorio.

El arreglo es barato porque **el dato ya está persistido**: el `summary` que cada agente entrega
en `submit_result` vive en `executions.output`. Basta con componer un bloque de preámbulo con
las tareas upstream completadas y su resumen, siguiendo el mismo patrón que ya usan
`build_prior_failure_preamble` y `build_comments_preamble`. Coste: ~1 día. Impacto: alto en
todo plan de más de tres tareas encadenadas.

**N-2 · Intervención en caliente sobre un run vivo.**

Hoy ves un run irse por el camino equivocado en el visor y tu única palanca es **matarlo**. Los
comentarios de tarea llegan al run _siguiente_, no a este. Se pierden 20 iteraciones y su
presupuesto sabiendo desde la tercera que iba mal.

La fontanería está a medio poner: `_watch_for_cancel` (`execution.py:1318-1330`) **ya sondea la
fila de la ejecución cada 3 segundos** y solo mira `cancel_requested_at`; y el contenedor **ya
llama a la API interna** para memoria, RAG y `stack_exec`, así que existe un canal de vuelta.

Diseño: un campo de guía en la ejecución que el bucle del agente consulta una vez por
iteración y, si hay algo, lo inyecta como sticky del turno siguiente — el mismo mecanismo que
ya usa el feedback de review. Coste honesto: ~2 días, porque añade un endpoint, una
comprobación por iteración y UI en el visor. Lo que ahorra es presupuesto real y frustración.

**N-3 · Preflight del plan antes de aprobar.**

Hoy apruebas un plan y **te enteras después** de que tres tareas no tienen agente elegible (el
rol no existe en el equipo → `_resolve_assignment` avisa y deja el asignado a `NULL`), de que
dos no tienen criterios de aceptación, o de que las doce están en cadena sin ningún paralelismo
posible.

Un preflight es **composición de resolvedores que ya existen, en modo solo-lectura**: la
asignación por rol de `sync_to_kanban`, el desglose de coste, la validación del DAG y la
cobertura de criterios. Devuelve un semáforo antes del botón de aprobar: _«3 tareas sin agente,
2 sin criterios, coste estimado 47 €, camino crítico de 8 tareas sin paralelismo»_.

Coste: ~1,5 días, y ninguno de sus componentes hay que inventarlo. Encaja especialmente bien
con el editor del spec (F-2): detectas el problema y lo corriges sin salir de la pantalla.

> **Candidato no investigado**: un cortacircuitos de presupuesto **a nivel de plan** (existe
> `budget_pause_block` por run y `budget_amount` por proyecto; dado que «Plan = unidad de
> cambio», el plan parece la unidad natural). No lo he verificado, así que no lo propongo
> formalmente.

---

## §7b Corrección mínima contra mejora real

Cinco hallazgos admiten dos arreglos muy distintos: el que hace desaparecer el síntoma y el
que resuelve el problema. La diferencia de coste es pequeña y la de resultado no. Se listan
aparte para que la decisión sea explícita, no un descuido.

| #                    | Corrección mínima                                            | Mejora real                                                                                                                                                                                                                                                                                                                                       | Δ coste |
| -------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------- |
| **A-01/A-02** (A-13) | Invertir el orden: cargar los 50 **últimos**                 | **Encender el subsistema de compresión jerárquica que ya existe** (`conversation_compression.py`): cablear `load_context_window` en `responder.py`, escribir el `Summariser` de producción que falta (estructurado, no prosa) y disparar `compress_old_messages` tras cada turno. Más `before` en el endpoint para poder releer hacia atrás.      | +1,5 d  |
| **A-04**             | Mapa estático `complexity → horas`                           | **Calibración con datos reales**: el sistema ya cierra planes y escribe retrospectivas (`plan_retro`, ADR 0124), y `executions` guarda duraciones reales. Alimentar la estimación con el histórico del propio proyecto convierte un número inventado en una previsión. Un mapa estático es la misma ficción que el default de 4 h, mejor vestida. | +1 d    |
| **B-01/B-02**        | Enhebrar los esquemas que faltan en los dos casos detectados | **Fijar el invariante**: «toda tool del allowlist efectivo tiene esquema anunciado, y todo esquema anunciado es ejecutable». Un test de contrato mata la familia entera de bugs — B-01, B-02 y B-04 son tres instancias del mismo fallo, y volverán a aparecer con la siguiente vía de asignación.                                                | +0,5 d  |
| **D-01…D-04**        | Cuatro endpoints y cuatro secciones de UI                    | **Una cabecera de plan**: un endpoint que devuelva progreso, PR, coste real y estimado, y un componente que los pinte juntos. Menos código, y el operador ve el estado del plan de un vistazo en vez de en cuatro sitios.                                                                                                                         | −0,5 d  |
| **A-05**             | Devolver el plan existente en silencio                       | Decírselo al usuario: «esta conversación ya generó el plan X», con enlace y opción explícita de crear otro. La idempotencia silenciosa evita el duplicado y deja al operador sin saber por qué su clic no hizo nada.                                                                                                                              | +0,25 d |

Neto: **+2,25 días** sobre el plan mínimo. Es la parte del trabajo que responde a «mejora del
workflow» en vez de a «arregla el bug», y va incorporada al plan de remediación.

---

## §7c Mejoras por zona: prompts, reviewers y dockers

Tres palancas que no son bugs — el sistema funciona sin ellas — pero que cambian la calidad o
el coste de forma desproporcionada a su esfuerzo.

### Prompts de implementación

**M-1 · El prompt no está construido para reutilizar caché.** Cero directivas de caché en todo
el árbol. Y hay una asimetría concreta: el preámbulo se ensambla **una sola vez al arrancar el
contenedor** (`__main__.py:887`) y es inmutable durante todo el run — persona (hasta 8 000
caracteres), fragments de skills y el contrato de sistema. Un run `claude_sdk` puede dar 50
iteraciones, así que ese prefijo estable se reenvía ~50 veces.

La parte de sistema **sí** está bien colocada (estable, y separada del mensaje de usuario en
`_system_content`). Lo que se pierde es el histórico: `_decide_messages` **reconstruye un único
mensaje de usuario grande en cada turno** en vez de ir añadiendo mensajes a una lista. Los
proveedores que hacen caché automática por prefijo no pueden aprovechar nada de la
conversación acumulada, solo el system.

**Cuánto se gana depende del proveedor y hay que medirlo antes de prometerlo**: el catálogo
(ADR 0021) no incluye la Messages API de Anthropic en crudo, así que no aplica un
`cache_control` explícito; Azure OpenAI cachea prefijos automáticamente a partir de cierto
tamaño, Ollama reutiliza su KV local, y `claude_sdk` gestiona su propia sesión (ADR 0097). El
hallazgo verificable y accionable es doble: **no hay ninguna medición de aciertos de caché**, y
**la construcción de mensajes renuncia al prefijo estable del histórico** por diseño, no por
necesidad.

### Reviewers

**M-2 · El reviewer juzga FICHEROS ENTEROS, no el diff. Es la mayor palanca de calidad de
review del sistema.**

Lo que recibe hoy: `_harvest_worktree_files` lee el contenido **completo** de los ficheros del
worktree (`review_harvest.py:79-110`, tope de 40 ficheros escaneados, 200 KB por fichero) y
`_review_messages` los vuelca como `--- ruta ---` + contenido, acotado a **15 ficheros ×
12 000 caracteres** (`providers.py:280-281, 452-465`). No existe ningún diff en el runtime, y
ambos contratos de sistema **prohíben git explícitamente**, incluido `git diff`
(`providers.py:106-110`, `:136`).

Tres consecuencias, todas caras:

1. **Gasta el contexto en código que no ha cambiado.** En un fichero de 800 líneas donde la
   tarea tocó 12, el reviewer lee las 800.
2. **No puede distinguir el cambio de esta tarea de lo que ya estaba.** Juzga el estado final,
   no la aportación. En un plan donde varias tareas tocan el mismo repo, esto es grave: el
   reviewer no sabe qué está revisando realmente.
3. **Se trunca a 15 ficheros.** Un cambio de 30 ficheros se juzga viendo la mitad — y el orden
   `prefer` mitiga pero no resuelve.

Y otra vez el mismo patrón: **la plataforma ya calcula diffs**. `plan_code_diff`
(`tasks/code_diff_task.py`) corre en el worker —que es quien tiene el `data_root` y git— y
alimenta el visor de la UI, verificado sobre 107 ficheros. El consumidor que más lo necesita no
lo recibe.

La mejora: darle al reviewer **el diff del rango de la tarea** como artefacto primario, y
dejarle `read_file` para el contexto de alrededor. El worker puede calcularlo antes de lanzar
el review-runtime (contra el HEAD de la rama del plan, porque en un primer run el trabajo aún
no está commiteado). No requiere darle git al contenedor — se le entrega ya hecho, igual que el
`<test-report>`.

**M-3 · Veredicto por criterio en vez de prosa holística.** Hoy el reviewer cierra con
`<verdict>` y, si rechaza, un bloque con **un** `<failed_criterion>`. Un resultado estructurado
—cada criterio con `pass`/`fail` y su evidencia— daría tres cosas que hoy no existen: la UI
podría enseñar al humano exactamente qué criterio falló y por qué; el `what_to_fix` tendría
diana precisa en vez de una descripción; y los resultados serían **medibles entre runs**, que
es justo lo que el sistema de evals necesita y no recibe (ver B-08). Es la continuación natural
del ADR 0087, que ya hizo el review autoritativo de tres estados.

### Sistema de dockers

**M-4 · Las 14 imágenes de runtime están pineadas a una etiqueta flotante.**

```python
# packages/shared-test-runtimes/src/shared_test_runtimes/catalog.py:31-41
_IMAGE_TAG = "v1"
def _image(slug: str) -> str:
    return f"agent-runtime-{slug}:{_IMAGE_TAG}"
```

Reconstruir `agent-runtime-php-phpunit:v1` cambia **en silencio** lo que ejecuta toda tarea PHP
del sistema. No hay forma de saber qué build produjo un resultado, ni de volver atrás cuando
una imagen nueva rompe algo. Para una plataforma cuyo propósito es ejecución reproducible y
auditable, es un hueco real.

La mejora es barata: **resolver la etiqueta a digest en el lanzamiento y guardarlo en la fila
de `executions`**. A partir de ahí un run es reproducible y una imagen mala es atribuible.
Emparejado con el versionado de prompts (B-08) cierra la trazabilidad entera de un run: **qué
prompt y qué imagen** lo produjeron.

**M-5 · Cada `stack_exec` paga el arranque completo del contenedor.** No hay pool caliente —
`runtime_pool.py` existe, está muerto (C-08) y alguien claramente pensó en esto. La caché de
dependencias (ADR 0094) amortiza la instalación, pero no el arranque. **Medir antes de
construir**: si el arranque es una fracción pequeña del comando típico, no compensa la
complejidad de un pool con su propio ciclo de vida y sus propios reapers.

> **Explícitamente NO es un hallazgo**: que las imágenes sean locales (`agent-runtime-<slug>`,
> sin registry) es coherente con el alcance de Docker Compose en una máquina (principio rector
> del proyecto). El comentario del catálogo anticipa un push a registry «futuro»; mientras el
> alcance no cambie, no hace falta.

---

## §8 Candidatos descartados en la re-verificación

Se reportaron y **no son ciertos** contra este baseline:

| Candidato                                       | Realidad                                                                                                                                                                                                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `allowed_domains` de proyecto sin UI            | Tiene UI: `projects/[id]/commands/page.tsx:344`.                                                                                                                                                                                                                         |
| Duplicación entre `admin-panel` y `web-app`     | `apps/web-app/` contiene solo `.gitkeep`. **No existe.** La duplicación real es interna a admin-panel (tres Kanbans, cuatro mapas de estado). Esto cierra de hecho la resolución (c) del ADR 0117: no hay nada que consolidar ni que borrar, solo que actualizar el ADR. |
| `workers/secrets.py` completamente muerto       | Su `StaticSecretsProvider` lo usan los caminos de backup. Muerto es el staging de `/run/secrets` para runs, no el módulo.                                                                                                                                                |
| El test-runtime corre sin ningún perfil seccomp | El perfil **por defecto de Docker** sigue activo; lo ausente es el endurecido. Reclasificado como C-02/C-03.                                                                                                                                                             |

---

## §9 Lo que está bien

Para no dar una imagen falsa. Estas cosas se revisaron a fondo y están sólidas:

- **Claim atómico del dispatch.** `UPDATE … WHERE status='ready' RETURNING id` cierra la
  carrera entre despachadores concurrentes, y la resolución del modelo ocurre **antes** del
  claim para no dejar tareas reclamadas sin poder ejecutarse.
- **Frontera de tenant en la ejecución.** `_prepare_run` verifica la pertenencia y lanza
  `CrossTenantExecutionError` antes de tocar disco. No se encontró ninguna vía cross-tenant.
- **Envelope de aislamiento del agent-runtime.** cap-drop ALL, no-new-privileges, root
  read-only, `pids_limit`, usuario no-root, red interna, tripwire de socket Docker y `HOME` en
  tmpfs fuera del worktree. Es el modelo que el test-runtime debería copiar (C-01, C-02).
- **Fence anti-inyección.** Todo texto de terceros que entra al preámbulo —comentarios,
  respuestas humanas, feedback de review— va encapsulado en marcas explícitas.
- **Tests de prompt del runtime.** Doce ficheros de test cubren persona, comentarios, feedback
  previo, fallo previo, preámbulo de review, inventario de tools, brief del worktree, resumen
  de progreso y contexto evictado. Es la parte mejor cubierta de la capa de inteligencia.
- **Contrato de claves de estado ejecutable.** `test_state_key_contract.py` fija la forma del
  estado del grafo en vez de confiarla a la disciplina.
- **Red de convergencia.** Reconciler, sweeper de ejecuciones rancias, reaper de huérfanos,
  expiración de review-runtimes y poda de worktrees cubren los caminos de fallo con cadencias
  distintas y guardas atómicas.
- **Aislamiento de los servicios auxiliares (ADR 0129).** El bridge per-sesión evita el error
  fácil de colgar las bases de datos auxiliares de la red compartida.

---

## §10 Límites de esta auditoría

Qué **no** se puede concluir de este informe, aunque su plan de remediación se ejecute entero.

**1. Es una auditoría de código, no de comportamiento.** No se ha ejecutado ni un solo plan
contra el stack vivo, ni se ha mirado la BD. Todo lo de aquí sale de leer ficheros. Eso
encuentra contratos rotos, cableado ausente y clases enteras de bug — y no encuentra
condiciones de carrera bajo carga, degradaciones de rendimiento, ni nada que solo aparezca
ejecutando. La única carrera reportada (C-05) se dedujo por aritmética de dos constantes; no
hay garantía de que sea la única.

**2. No se ha verificado que lo desplegado sea lo leído.** El baseline es `a17ed99f` en
`plan/runs-visor-trabajo`. Si el dev desplegado va por detrás o tiene parches sin empujar, los
hallazgos aplican al código, no necesariamente a lo que corre.

**3. Hay zonas fuera de alcance que tocan el flujo de proyecto.** Notificaciones en
particular: `task_unassignable`, `plan_approved`, `plan_rejected` son parte del workflow de
proyecto y aquí no se auditaron (AUD16 lo hizo, contra código anterior).

**4. Los falsos negativos son invisibles y no están acotados.** La re-verificación **refutó 4
de los candidatos** que produjo la exploración. Eso da una idea del ruido en la dirección
«reportado y falso», que se ha corregido. No dice nada sobre la dirección contraria — problemas
reales que la exploración no llegó a mirar. No hay estimación de esos, y por construcción no
puede haberla.

**5. La pregunta que más importa no se responde leyendo código.** «¿Producen los planes
software que funciona?» es empírica. Se puede verificar que el reviewer recibe ficheros en vez
del diff (M-2); no se puede verificar leyendo que arreglarlo hará buenas las revisiones. Y el
instrumento que el propio sistema tiene para medirlo —los evals— es justo el que está ciego
(B-08).

**6. «Correcto» no es el objetivo alcanzable en este sistema.** Una plataforma multi-tenant
donde LLMs escriben código tiene varianza irreducible. La meta razonable no es que los planes
no fallen, sino que cuando fallen sea **visible, atribuible y recuperable**. Buena parte de lo
propuesto va exactamente a eso —trazabilidad prompt+imagen, veredicto por criterio, preflight,
PR visible, intervención en caliente— pero ni con todo hecho desaparecen los fallos.

**Lo que de verdad subiría la confianza**, por orden:

1. **Ejecutar un plan real de punta a punta y observarlo.** Es el mayor punto ciego de este
   trabajo y ninguna cantidad de lectura lo sustituye.
2. Confirmar que lo desplegado coincide con la rama auditada.
3. Recuperar el veredicto de la suite de integración que quedó corriendo en una sesión previa.
4. Poblar los evals (`task_wf_52`) para que la calidad deje de ser una impresión.

---

## §11 Auditoría de comportamiento (BD viva, 2026-07-25)

Ejecutada sobre el stack desplegado (30 contenedores, todos `healthy`) y la BD real, en solo
lectura. Cambia conclusiones del §2 en las dos direcciones: **destapa dos fallos graves que
leer código no dio**, y **degrada tres hallazgos de crítico a latente**.

**Verificación previa**: la imagen `api-server:manuals` desplegada contiene el código auditado
—`runtime_services` (ADR 0129) presente, `load_context_window` con su `uncovered[-max_messages:]`
correcto, y `responder` **sin** usarlo y **con** `.limit(50)`—. Los tres últimos commits solo
tocan `admin-panel`, así que el backend desplegado coincide con el baseline. Queda cerrada la
reserva nº2 del §10.

### V-1 · CRÍTICO · Tareas huérfanas en `in_progress`, invisibles al reconciler para siempre

Dos tareas del plan «MVP — API Hello World en PHP» llevan **7 días** en `in_progress` **sin
ninguna fila en `executions`**. El plan tiene 2 de 4 tareas hechas y **no puede completarse
nunca**.

La causa está en la propia red de convergencia:

```python
# apps/workers/src/workers/maintenance/reconciler.py:164
if latest is None or not _stuck_task_needs_reconcile(...):
    continue
```

`latest is None` → `continue`. El barrido de tareas atascadas solo sabe tratar «tarea
`in_progress` cuyo último run es terminal». Una tarea que el dispatch **reclamó**
(`ready`→`in_progress`, claim atómico) pero cuya ejecución nunca llegó a crearse cae por todas
las redes a la vez:

- el reconciler la salta explícitamente (`latest is None`),
- `sweep_stale_executions` opera sobre `executions` — no hay ninguna,
- `orphan_reaper` opera sobre contenedores — no hay ninguno.

No hay reintento, ni alerta, ni señal en la UI. Y como retiene el DAG, **congela el plan
entero**. Es el fallo más grave de toda la auditoría y ninguna cantidad de lectura de código lo
habría clasificado como urgente: hacía falta ver los 7 días.

### V-2 · ALTO · Tres planes esperan un gesto humano que nadie sabe que hace falta

> **CORRECCIÓN (2026-07-25, durante la implementación).** La primera versión de este hallazgo
> decía que el reconciler «se olvida» de los planes `blocked` y proponía extender
> `_reconcile_complete_plans` para cerrarlos. **Era incorrecto, y el arreglo propuesto habría
> reintroducido un bug crítico ya resuelto.** Se deja la corrección visible porque el error es
> instructivo.

Tres de los diez planes están en `blocked` con **todas sus tareas `done`**: «Prueba custom tool

- skill», «Prueba MCP Atlassian v5 - final», «Plan E2E de confirmacion».

Lo que realmente ocurre: `_reconcile_unblocked_plans` **excluye a propósito** los snapshots
todo-terminales (`reconciler.py:619-626`), y el comentario explica por qué —hallazgo C-1 de la
auditoría del 2026-07-10—:

> «un snapshot TODO-terminal no puede venir del escalado por snapshot (exige ≥1 tarea blocked)
> — es la firma del bloqueo C8 F40 (review expirada). Revertirlo aquí re-promocionaría el plan
> y re-armaría el autostart de review en bucle de 48 h; **ese bloqueo lo levanta el humano**.»

Verificado contra la BD: los tres planes tienen `review_sessions.status='expired'` y **cero**
tareas abiertas. Son exactamente el caso que el guard excluye. El reconciler hace lo correcto,
y cerrarlos automáticamente reintroduciría el ping-pong reconciler↔C8 F40 que costó un fix
crítico.

**El hallazgo real, entonces, no es de convergencia sino de señal**: el diseño dice «este
bloqueo lo levanta el humano» y **no hay nada que le diga al humano que tiene algo que
levantar**. Tres planes llevan entre 2 y 5 días esperando un gesto que nadie sabe que se espera.
`unblock_plan` existe y está a un clic — de un clic que nadie sabe que hay que dar.

Severidad revisada: sigue siendo **alto**, pero es un fallo de observabilidad, no de máquina de
estados. La remediación correcta es hacerlo visible (`task_wf_m3`), **no** tocar el reconciler.

### V-3 · El perfil de fallo agregado es histórico, y la tendencia es buena

`provider_error` es la causa de aborto nº1 por volumen (17 de 68) y **casi toda de un solo
día**: 15 el 2026-07-03, 1 el 07-02, 1 el 07-08. El mensaje es
`You've hit your session limit · resets … (HTTP 429)` — agotamiento de cuota, no un defecto del
sistema.

Y la calidad ha mejorado de forma clara:

| Ventana                   | Runs   | `done`        | Abortados |
| ------------------------- | ------ | ------------- | --------- |
| 2026-06-29/30             | 34     | 19 (56 %)     | 7         |
| 2026-07-03 (día de cuota) | 52     | 29 (56 %)     | 15        |
| **2026-07-23/24**         | **37** | **30 (81 %)** | **1**     |

Los agregados del §2 **subestiman la calidad actual**. Cualquier lectura del tipo «el 38 % de
los runs no cierra limpio» es arqueología: hoy son ~19 %, y de esos la mayoría son
`needs_human_review`, que es una escalada legítima, no un fallo.

### V-4 · Tres hallazgos de código bajan a latentes

La BD refuta que estos estén ocurriendo. Siguen siendo bugs reales; no son incendios.

| Hallazgo                               | Lo que dice la BD                                                                                                                                                                                                     |
| -------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **A-01 / A-02 / A-13** (chat truncado) | **Cero** conversaciones con más de 50 mensajes. El máximo en una conversación es **9**, y hay **18 mensajes en todo el sistema**. Cero filas `is_summary`. El bug nunca se ha alcanzado: es una mina, no un incendio. |
| **A-09** (tarea `blocked` invisible)   | Las dos tareas `blocked` tienen último run `needs_human_review`, así que **sí** salen en el panel de escaladas. Cero instancias del caso invisible.                                                                   |
| **D-02** (PR no visible)               | 1 plan con `pr_url`, **0** con `pr_error`. El radio de impacto hasta hoy es un plan.                                                                                                                                  |

**Corrección de severidad**: A-01/A-02 dejan de ser «crítico» y pasan a **alto-latente**. Siguen
siendo la primera ola porque el arreglo es de horas (y con `task_wf_06` es encender lo que ya
existe), pero no son una emergencia. V-1 y V-2 ocupan su lugar en la cabecera.

### V-5 · Dos observaciones que abren pregunta, no conclusión

- **El chat de planning apenas se usa**: 18 mensajes en total, máximo 9 por conversación. O los
  planes se crean por otra vía, o algo lo hace poco atractivo. No lo sé; es pregunta para el
  operador, no hallazgo.
- **El subsistema de evals nunca ha corrido**: ver V-6, que lo desarrolla.

### V-6 · ALTO · El subsistema de evals: siete tablas vacías y ninguna vía para llenarlas

B-08 se quedaba corto. No es que `subject_prompt_version` esté a `NULL`: **las siete tablas de
evals tienen cero filas** — `eval_datasets`, `eval_dataset_items`, `eval_criteria`, `eval_runs`,
`eval_results`, `eval_shadow_records`, `eval_drift_state`.

**Qué son los evals aquí** (Plan 14, ~85 KB en 7 módulos). Tres capacidades distintas:

1. **Dataset dorado + juez LLM** (`judge.py`, `metrics.py`, `diff.py`) — un conjunto de ítems
   con criterios; un sujeto (agente + versión de prompt) los resuelve, un modelo juez los
   puntúa y se obtienen métricas y un _diff_ contra una corrida base. Es la medición controlada.
2. **Shadow evals** (`shadow.py`) — muestrea el **5 % de las tareas reales completadas** y las
   replica por el juez para registrar una señal de calidad. **Nunca bloquea ni altera la
   ejecución real.** Es la vía pasiva, y la que explica la extrañeza: con 111 runs `done`
   debería haber ~5 registros.
3. **Deriva y puerta de CI** (`drift.py`, `ci_run.py`) — detección de regresión y un gate que
   corre al cambiar un prompt.

**Por qué no hay ninguno.** Los tres productores posibles están cortados, cada uno por su
motivo:

| Vía                     | Estado                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Manual / API**        | `routers/evals.py` expone 18 endpoints: CRUD completo de datasets, criterios e ítems —las **entradas**— y **solo lectura** para las corridas (`GET /eval-runs/{id}`, `GET /eval-runs/diff`). **No existe ningún `POST` que ejecute una eval.** Desde la UI o la API es imposible producir una.                                                               |
| **Shadow (automática)** | `record_shadow_eval` y `select_shadow_sample` tienen **cero llamantes de producción** (solo `tests/integration/test_shadow_eval.py`) y **no hay beat de Celery**. El muestreo del 5 % no ocurre nunca.                                                                                                                                                       |
| **CI**                  | El workflow `eval-on-prompt-change.yml` existe y está bien hecho, pero está **condicionado a que haya secreto de proveedor LLM**; CI no lo tiene, así que cae a `--dry-run` y termina con un _skip-with-notice_. Y aun corriendo, pasa UUID placeholder (`00000000-…`) como dataset y baseline. Además corre en GitHub Actions: nada aterrizaría en esta BD. |

Y por debajo de todo, un bloqueo más básico: **no hay datasets, ni criterios, ni ítems**. Aunque
existiera el botón de lanzar, no habría contra qué. El subsistema no está «sin ejecutar», está
**sin configurar**.

**Por qué importa más que otros hallazgos.** Es el mismo patrón que `conversation_compression`
(A-13) pero con más superficie construida: 7 módulos, 7 tablas, una migración, 18 endpoints y
un dashboard entero (`app/admin/eval-quality/`) que agrupa por _release_ de prompt. Todo
esperando un disparador que nadie escribió.

Y es **el único instrumento que el sistema tiene** para responder «¿están los agentes mejorando
o empeorando?» — justo la pregunta que la reserva nº5 del §10 declara incontestable leyendo
código. Sin evals, cada cambio de prompt es a ciegas, y las mejoras M-1/M-2/M-3 y `task_wf_52`
no se podrán demostrar: se notarán o no se notarán.

**Matiz honesto**: que la vía shadow no esté cableada puede haber sido un diferimiento
deliberado — cuesta una llamada de juez por tarea muestreada, y eso es dinero. Pero no hay
ningún ADR que lo diga, y el docstring del módulo la describe como el comportamiento **por
defecto de producción** con una tasa configurable por el operador. La intención era que
corriese.

### Lo que esta pasada NO cubre — DIFERIDO

No se ha ejecutado un plan nuevo de punta a punta. Todo lo anterior es observación pasiva sobre
lo ya ocurrido. Sigue en pie la reserva nº5 del §10: si los planes producen software que
funciona no se responde mirando estados, sino leyendo entregables.

> **PENDIENTE (diferido por decisión del operador, 2026-07-25)**: ejecutar un plan real de
> punta a punta **después** de implementar la remediación, para medir si el sistema va mejor.
>
> **Bloqueo técnico a resolver cuando se retome**: crear un plan por la API exige un token
> atado a una sesión de Redis (`SessionStore` + `encode_jwt`); no hay credencial de desarrollo
> documentada y `api_tokens` está vacía. Fabricar la sesión programáticamente lo bloquea el
> clasificador de permisos, con razón. Vías: (a) el operador crea el plan en la UI y el
> análisis se hace sobre la ejecución; (b) contraseña de `demo@example.com`; (c) Bearer del
> navegador; (d) regla de permiso explícita.
>
> **Parámetros sugeridos**: proyecto `hello-world` `123b2f2c-568f-499f-81d1-987de7322c27`
> (equipo «CodeIgniter 4 (copia)» — ojo, hay otro `hello-world` en `39023b1d`), 2-3 tareas
> pequeñas, `claude-sonnet-5` vía `claude_sdk` (la config real del proyecto). Vigilar cuota:
> es el proveedor que agotó sesión el 2026-07-03.
>
> **Línea base para comparar** (medida hoy, antes de remediar): 178 ejecuciones históricas,
> 62 % `done` global y **81 % en la ventana 07-23/24**; 10 planes, **1 completado**, 5 en
> estados sin salida automática.

---

## §12 Referencias

- Remediación propuesta: [remediacion-gestion-proyectos-2026-07-25.md](./remediacion-gestion-proyectos-2026-07-25.md)
- Auditoría anterior del dominio: [auditoria-proyecto-integral-2026-07-17.md](./auditoria-proyecto-integral-2026-07-17.md)
- ADR abiertos que esta auditoría toca: 0108 (canales de veredicto, `proposed`),
  0117 (resoluciones a/b/c), 0127 (OAuth MCP), 0128 (tools MCP por proyecto)
