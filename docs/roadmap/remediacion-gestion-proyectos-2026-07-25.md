---
plan_id: remediacion-gestion-proyectos-2026-07-25
title: Remediación del workflow de gestión de proyectos — cableado del último tramo
status: pending_approval
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 41
created_by: claude-opus-5-audit-2026-07-25
docs_language: es
priority: P0
source_audit: auditoria-gestion-proyectos-2026-07-25
---

# Plan de remediación — Workflow de gestión de proyectos (2026-07-25)

## Cabecera

| Campo             | Valor                                                                                 |
| ----------------- | ------------------------------------------------------------------------------------- |
| **ID del Plan**   | `remediacion-gestion-proyectos-2026-07-25`                                            |
| **Prioridad**     | P0 (olas 0-2) · P1 (olas 3-4, 6-7) · P2 (ola 5)                                       |
| **Bloqueado por** | Ninguno. No solapa con `remediacion-proyecto-integral-2026-07-17`                     |
| **Rama sugerida** | `plan/runs-visor-trabajo` (continuidad)                                               |
| **Método**        | TDD + commits atómicos, un commit por tarea                                           |
| **Origen**        | [auditoria-gestion-proyectos-2026-07-25](./auditoria-gestion-proyectos-2026-07-25.md) |

## Resumen

38 hallazgos verificados. El patrón dominante no es de diseño: es **funcionalidad construida
y correcta que nunca se conectó al consumidor final**. Por eso la mayoría de las olas 0-3 son
arreglos pequeños con impacto desproporcionado — el trabajo caro ya está hecho río arriba.

Las olas están ordenadas por impacto dividido por coste. Las olas 0, 1 y 2 son P0 y suman unos
7 días: recuperan el producto, hacen que los agentes vean lo que pueden usar y cierran la
contaminación del worktree. La ola 4 contiene la única pieza realmente grande
(replanificación) y está **gated tras un ADR**.

## Criterios de cierre del plan

1. Todos los checkboxes marcados `[x]` con su test automático en verde.
2. Suites `unit` + `integration` sin regresiones respecto al baseline `a17ed99f`.
3. Tests humanos `human_wf_01..05` (§Tests humanos) validados por el operador.
4. Entrada en `docs/07-changelog/remediacion-gestion-proyectos.md`.
5. Los tres ADR de la ola 4 resueltos o explícitamente diferidos por el operador.

---

## Ola −1 — Lo que está roto AHORA MISMO en el dev desplegado (P0 · ~1,25 d)

Sale de la auditoría de comportamiento (§11 del informe), no de leer código. **Va antes que
todo lo demás**: hay 5 de 10 planes en un estado del que no pueden salir solos.

#### `task_wf_m1` — Tarea `in_progress` sin ejecución: rescatarla y cerrar el agujero

- [x] _(hecho 8ca290dd)_ **Título**: el barrido de tareas atascadas trata hoy solo «tarea `in_progress` cuyo
      último run es terminal»; con `latest is None` hace `continue`
      (`reconciler.py:164`) y la tarea se queda `in_progress` **para siempre**, congelando el
      DAG y el plan. Añadir el caso: una tarea `in_progress` **sin ninguna ejecución** y más
      antigua que el umbral vuelve a `ready` (es lo que `_revert_to_ready` habría hecho si el
      dispatch hubiera fallado limpiamente). Además, **rescatar las dos vivas**: el plan «MVP —
      API Hello World en PHP» lleva 7 días congelado.
- **Hallazgo**: V-1 (crítico, verificado en vivo) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/maintenance/reconciler.py:115-179`
- **Tests**: unit de que una tarea `in_progress` sin ejecución y vieja vuelve a `ready`; unit
  de que una reciente **no** se toca (no pisar un dispatch en vuelo); regresión del caso
  existente.
- **Criterio de aceptación**: crear la situación a mano y ver que el reconciler la recupera en
  una pasada.

#### ~~`task_wf_m2` — Plan `blocked` con todas las tareas `done`: cerrarlo~~ · DESCARTADA

- [x] **Descartada tras verificar el código** (2026-07-25). La propuesta era extender el
      reconciler para cerrar automáticamente los planes `blocked` con todo terminal.
      **Habría reintroducido un bug crítico ya resuelto**: `_reconcile_unblocked_plans` excluye
      esos snapshots a propósito (`reconciler.py:619-626`, hallazgo C-1 de la auditoría del
      2026-07-10) porque un snapshot todo-terminal es la firma del bloqueo por **review
      expirada** (C8 F40), y revertirlo re-arma el autostart de review en bucle de 48 h.
      Verificado en BD: los tres planes tienen `review_sessions.status='expired'` y cero tareas
      abiertas — son exactamente ese caso. **El reconciler hace lo correcto.**
      El problema real es de señal, y lo cubre `task_wf_m3`.

#### `task_wf_m3` — Que un plan que espera al humano se vea

- [x] _(hecho 71798ca0)_ **Título**: el diseño dice «este bloqueo lo levanta el humano» y **no hay nada que le
      diga al humano que tiene algo que levantar**: tres planes llevan entre 2 y 5 días
      esperando un clic que nadie sabe que hay que dar. Añadir a la bandeja unificada
      (`routers/human_queue.py`) un ítem nuevo — plan `blocked` **sin tareas abiertas** (la
      firma de «espera desbloqueo humano») — con su antigüedad, y el mismo aviso en la tarjeta
      del tablero gerencial. Cubre también el caso V-1 residual: cualquier plan sin transición
      en N días con tareas no terminales.
- **Hallazgos**: V-1 + V-2 (la remediación correcta de V-2) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/api-server/src/api_server/routers/human_queue.py`,
  `app/admin/board/page.tsx`
- **Tests**: integración de que un plan `blocked` sin tareas abiertas aparece en la cola con
  `kind` propio; unit de que uno con tareas abiertas **no** aparece (ese sí está genuinamente
  bloqueado por trabajo, no esperando al humano).
- **Criterio de aceptación**: los tres planes varados de hoy salen en la bandeja del humano con
  su antigüedad y un enlace que lleva a la acción de desbloqueo.
- **Nota**: no tocar el reconciler. La exclusión de `_reconcile_unblocked_plans` es correcta y
  hay un test que la fija.

---

## Ola 0 — Bugs que rompen el producto hoy (P0 · ~5,25 d)

> **Corrección de severidad tras la auditoría de comportamiento**: A-01/A-02 bajan de
> «crítico» a **alto-latente**. Cero conversaciones superan los 50 mensajes (máximo real: 9),
> así que el bug **nunca se ha alcanzado**. Sigue en la primera ola porque el arreglo son horas,
> pero la urgencia real está en la Ola −1.

Seis arreglos localizados más un rediseño (`task_wf_06`). El orden importa: las correcciones
primero, porque son las que devuelven el producto a un estado usable en horas; el rediseño del
contexto después, sobre esa base ya estable.

#### `task_wf_00` — El chat de planning carga los mensajes RECIENTES

- [x] _(hecho 66e683b1)_ **Título**: invertir la ventana de `list_messages` para que devuelva los **últimos** N
      mensajes (orden descendente en la consulta, ascendente en la respuesta) manteniendo la
      paginación `after` intacta, y consumir el endpoint con un límite explícito desde la UI.
      Añadir `before` para que la UI pueda cargar hacia atrás: sin él, cualquier ventana fija
      deja al usuario sin forma de releer lo que quedó fuera.
- **Hallazgo**: A-01 (crítico) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/api-server/src/api_server/routers/conversations.py:419-424`,
  `apps/admin-panel/app/admin/projects/[id]/chat/page.tsx:248`
- **Tests**: unit del endpoint con 60 mensajes → devuelve los 50 últimos en orden
  cronológico; unit de `before` paginando hacia atrás; regresión de `after`; e2e del botón
  «Generar Plan» visible tras 60 mensajes.
- **Criterio de aceptación**: con 60 mensajes, recargar muestra el último turno, «Generar
  Plan» sigue presente, y se puede subir en el scroll hasta el mensaje 1.

#### `task_wf_01` — El equipo planifica sobre el contexto reciente (corrección)

- [x] _(hecho, ver task_wf_06a)_ **Título**: misma inversión en la carga del histórico que alimenta el prompt de
      planning, verificando que `latest_user_text` recupera el mensaje de usuario realmente
      más reciente. **Es la corrección urgente; el rediseño es `task_wf_06`.**
- **Hallazgo**: A-02 (crítico) · **Tiempo**: 0,25 d
- **Ficheros**: `apps/api-server/src/api_server/chat/responder.py:845-867`
- **Tests**: unit que con 60 mensajes el prompt contiene el mensaje 60 y no el 1.

#### `task_wf_02` — `summary` respeta su contrato

- [x] _(hecho 47f27efc)_ **Título**: emitir `summary` como objeto (`{"description": …}`) desde
      `_finish_planning_attachment` y `_normalise_plan_draft`, con coerción retrocompatible en
      `create_plan` para los planes ya persistidos con string. Validar el draft de
      conversación contra `PlanSpecification` antes de persistir, en vez de saltarse Pydantic.
- **Hallazgo**: A-03 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `chat/responder.py:534`, `chat/planning_llm.py:493-498`,
  `routers/plans.py:251-262`
- **Tests**: unit de forma del attachment; integración `PUT /plans/{id}` reenviando el spec de
  un plan nacido del chat → 200, no 422; migración de lectura para filas antiguas.
- **Criterio de aceptación**: un plan generado por chat se puede reenviar íntegro por `PUT` sin
  error, y la tarjeta «Resumen» muestra texto o no se renderiza.

#### `task_wf_03` — Estimaciones reales en el plan

- [x] _(hecho 47f27efc)_ **Título**: derivar `estimated_hours` de `complexity` con un mapa explícito
      (xs/s/m/l/xl → horas) en `_normalise_plan_draft`, y permitir que el LLM lo sobrescriba
      si lo emite. El default de 4 h de `cost.py` pasa a ser el último recurso, no la norma.
      **El mapa debe vivir en un solo sitio y ser configurable**, porque `task_wf_07` lo
      sustituye por valores calibrados.
- **Hallazgo**: A-04 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `chat/planning_llm.py:479-489`, `chat/cost.py:28-30,100`
- **Tests**: unit del mapa complexity→horas; unit de que el desglose de coste de un plan con
  complejidades mixtas ya no es `n × 4 h`.
- **Criterio de aceptación**: el Gantt de un plan con tareas xs y xl pinta barras de anchos
  distintos.

#### `task_wf_04` — «Generar Plan» es idempotente, y lo dice

- [x] _(hecho 2e40b0bb)_ **Título**: si la conversación ya tiene `related_plan_id` y ese plan sigue vivo (no
      `cancelled`/`rejected`), `create_plan` devuelve el plan existente (200) en vez de crear
      un gemelo. **La UI no se queda callada**: muestra «esta conversación ya generó el plan
      X» con enlace, y una acción explícita para crear otro si es lo que se quiere. Una
      idempotencia silenciosa evita el duplicado y deja al operador sin saber por qué su clic
      no hizo nada.
- **Hallazgo**: A-05 (alto) · **Tiempo**: 0,75 d
- **Ficheros**: `routers/plans.py:224-310`, `app/admin/projects/[id]/chat/page.tsx:573-640`
- **Tests**: integración de doble POST desde la misma conversación → un solo plan; e2e de que
  el segundo clic muestra el aviso con enlace.
- **Criterio de aceptación**: pulsar «Generar Plan» dos veces no produce dos planes y el
  usuario entiende por qué.

#### `task_wf_05` — Las fases del plan tienen nombre

- [x] _(hecho 47f27efc)_ **Título**: alinear el contrato de fase entre backend y frontend (`title`, con lectura
      tolerante de `name` para specs antiguos) en las dos vistas que lo consumen.
- **Hallazgo**: A-12 (bajo) · **Tiempo**: 0,25 d
- **Ficheros**: `plan-spec-sections.tsx:202`, `plan-sync-section.tsx:165`
- **Tests**: unit de render con un spec de chat real → las opciones del desplegable llevan
  texto.

#### `task_wf_06` — Encender la compresión de conversaciones que ya existe

- [x] _(hecho 8a095da1 + 5b49093d)_ Apartados a/b/c/d/e completos.

> **No hay que construir una estrategia de contexto: hay una, completa y con tests
> (`db/conversation_compression.py`), que nunca se enchufó.** Esta tarea la cablea. Sustituye
> a la propuesta inicial de copiar el patrón del agent-runtime, que habría duplicado —peor— un
> subsistema existente y mejor adaptado (el del chat persiste los resúmenes y es auditable; el
> del runtime es efímero y vive solo en el prompt).

**a) Cablear el cargador.** `responder.py:848-854` deja su consulta cruda y usa
`load_context_window`, igual que ya hace `planning_context.py:264`. Esto **resuelve A-01 y
A-02 correctamente y gratis**: la función ya devuelve los más recientes y ya sustituye
resúmenes.

- **Tiempo**: 0,25 d · **Ficheros**: `chat/responder.py:845-867,120-123`
- **Tests**: unit de que con 60 mensajes el histórico contiene el 60 y no el 1; unit de que un
  resumen presente sustituye al rango que cubre.

**b) Un `Summariser` de producción con doble representación.** Implementar el `Protocol`
(`conversation_compression.py:48-51`) sobre `shared_llm.LLMProvider`, como su docstring previó.
Cuatro decisiones de diseño, todas con precedente en el repo:

1. **Prosa para el humano, estructura para el pliegue.** El mensaje de resumen se muestra en
   el feed, así que `content` es Markdown legible. El **registro estructurado** viaja en
   `attachments`, como segundo `kind` junto al `summary_replaces` que ya existe:
   ```json
   {"kind": "summary_record",
    "requisitos": [...], "decisiones": [...], "descartado": [...], "abierto": [...]}
   ```
2. **El pliegue jerárquico es HÍBRIDO — y esto es la pieza clave.** Al comprimir una ventana
   que ya contiene resúmenes, el LLM resume **solo los mensajes crudos nuevos**; los
   `summary_record` existentes se **fusionan de forma determinista** (concatenar + deduplicar).
   Un requisito registrado en el piso 1 se copia **literal** hasta el piso 5 sin volver a
   pasar por un modelo. Eso acota la degradación por completo, que es justo lo que la
   compresión jerárquica de prosa no puede hacer.
3. **Modelo: el del propio chat.** Reutilizar la instancia de `provider` que el turno **ya
   construyó** (`build_chat_provider`), no resolver otra. `chat_model_config` existe
   precisamente para que el chat use un modelo más ligero que el de ejecución
   (`responder.py:176-182`), así que el operador ya tiene la palanca si el resumen sale caro.
   **No añadir un eje de configuración nuevo** para esto.
4. **Modo de fallo explícito.** Copiar la forma de `DistillationResult`
   (`memorizer/distillation.py:127-141`): un discriminante `cause` con
   `ok | llm_empty | llm_unparseable | llm_error`. Ese diseño nació justamente porque
   conflatar los tres hacía indiagnosticable el fallo; el summariser tiene el mismo riesgo.
   Nunca lanza hacia el turno: sin resumen, la conversación simplemente sigue sin comprimir.

- **Tiempo**: 1 d · **Ficheros**: nuevo `chat/summariser.py`, `db/conversation_compression.py`
  (el pliegue híbrido)
- **Tests**: **el que importa** — un requisito y un descarte enunciados en el mensaje 1
  sobreviven **literales** a tres pisos de compresión; unit del merge determinista de
  `summary_record`; unit de cada valor de `cause`; unit de que el `content` en prosa nunca se
  usa como entrada del pliegue.

**c) Disparar la compresión, alineada a turnos.** Llamar a `compress_old_messages` después de
cada turno, best-effort y fuera del camino crítico, con el `schedule_after_commit` que el
módulo de conversaciones ya usa (`routers/conversations.py:386-396`). Un beat no vale: dejaría
una ventana entre «la conversación se alargó» y «se comprimió» en la que cae el turno
siguiente.

**Ajustar los umbrales**: los defaults (`threshold_messages=20`, `window_messages=10`) están
pensados para un chat 1-a-1, no para este. Aquí **un turno son 6-10 mensajes** (framing del PM

- N especialistas + síntesis), así que comprimirían a mitad de turno: el resumen se llevaría el
  framing y cuatro especialistas dejando fuera la síntesis, partiendo un intercambio coherente.
  Regla: **la ventana nunca corta dentro de un turno** — un turno empieza en cada mensaje
  `author_kind='user'`, así que se pliegan turnos enteros. No necesita estado nuevo.

* **Tiempo**: 0,5 d · **Ficheros**: `chat/responder.py`, `db/conversation_compression.py`
* **Tests**: integración de que tras N turnos existe una fila `is_summary=True`, el feed
  original sigue completo, y ningún resumen cubre un turno a medias.

**d) Que el humano vea que hay un resumen.** Hoy `is_summary` llega hasta el tipo del
frontend (`chat/page.tsx:64,330`) y **no se renderiza distinto**: un resumen aparecería como un
mensaje `system` cualquiera. Marcarlo visualmente y hacerlo desplegable («resume 10 mensajes —
ver originales»); los originales siguen en la BD y `GET /messages` los devuelve, así que
desplegar no necesita endpoint nuevo.

- **Tiempo**: 0,5 d · **Ficheros**: `app/admin/projects/[id]/chat/page.tsx`

**e) Techo por tokens como segunda guarda.** `max_messages` sigue siendo un contador; con
compresión activa su letalidad baja mucho, pero un presupuesto de tokens evita que 50 filas
largas desborden igual.

- **Tiempo**: 0,25 d

- **Hallazgo**: A-13 (alto) · **Tiempo total**: 2,5 d · **Depende de**: `task_wf_01`
- **Criterio de aceptación**: una conversación de planning de 200 mensajes produce un plan que
  respeta los requisitos enunciados al principio **y** no vuelve a proponer nada que el
  usuario descartó por el camino.
- **Entregado** (5b49093d): `chat/summariser.py` (`LLMSummariser` + parseo con las cuatro
  causas), `SummaryRecord` / `split_window` / `render_record` / `aligned_window_size` /
  `estimate_tokens` en `db/conversation_compression.py`,
  `compress_conversation_best_effort` en `responder.py` (40/20, alineado a turnos, disparado
  al **principio** del turno para reusar el provider ya abierto y que el turno lea el resumen
  fresco), techo de 24 000 tokens en la ventana de contexto y tarjeta plegable en el feed.
  Tests: 22 unit (`test_conversation_summary_record.py`, `test_chat_summariser.py`), 4
  frontend (`summaryFoldedCount`) y 4 de integración nuevos — entre ellos **el decisivo**,
  tres pisos reales encadenados con el requisito y el descarte del mensaje 1 llegando
  literales al piso 3.
- **Ajuste sobre el diseño escrito**: el registro se renderiza **dentro de `content`**, no solo
  en `attachments`. `history_from_messages` únicamente pasa `content` al prompt, así que un
  registro que viviera solo en el attachment sería invisible para el modelo y la garantía de
  supervivencia sería falsa justo en el punto donde importa.
- **Alcance**: el mecanismo vive sobre `Message`/`Conversation`, así que beneficia a **todos
  los modos de chat**, no solo a planning.
- **Diferido**: `_load_all_messages` (`conversation_compression.py:93-99`) carga la
  conversación entera cada turno, y pasará a hacerlo dos veces. Correcto pero O(n); acotarlo
  (traer las N más recientes por id desc y resolver cobertura sobre esa ventana) solo importa
  a escala. Anotado, no incluido.

---

## Ola 1 — Lo que el agente no ve (P0 · ~3 d)

Sin esta ola, los ADR 0127 y 0128 no entregan lo que prometen.

#### `task_wf_10` — Las tools MCP del proyecto se anuncian al modelo

- [x] _(hecho 193ab8ea)_ **Título**: propagar el `input_schema` de las tools MCP del proyecto hasta
      `build_model_tool_schemas`. La vía limpia es que `_assemble_run_request` añada las tools
      MCP resueltas a `tool_specs` (con nombre `<server>.<tool>`, descripción y esquema), de
      modo que la fuente de esquemas ya existente las recoja sin tocar el resolvedor.
- **Hallazgo**: B-01 (crítico) · **Tiempo**: 0,75 d
- **Ficheros**: `apps/orchestrator/src/orchestrator/dispatch.py:719-731`,
  `apps/api-server/src/api_server/agent_tools_enforcement.py:238-319`,
  `apps/workers/src/workers/agent_tool_schemas.py:264-288`
- **Tests**: unit de que un proyecto con MCP y un agente sin grants produce un `spec` cuyo
  bloque `model.tools` contiene `<server>.<tool>` con su esquema; regresión de que el
  allowlist sigue filtrando por rol.
- **Criterio de aceptación**: un run de un agente con una tool MCP de proyecto permitida
  recibe su esquema y puede invocarla en la primera iteración.
- **Entregado**: `_project_mcp_tool_rows` es ahora la ÚNICA fuente de la que salen tanto los
  nombres permitidos como los esquemas anunciados (derivarlos por separado es cómo apareció
  B-01); `serialize_project_mcp_tool_specs` + `merge_tool_specs` en
  `agent_tools_enforcement.py`. No hizo falta tocar el runtime: `register_tool_specs` ignora
  las entradas `mcp_tool`, así que el spec sirve solo como fuente de esquemas. Test de
  integración del camino entero (fila `Tool` → dispatch → `ExecutionRequest` → agent-spec) con
  un agente **sin grants**.

#### `task_wf_11` — Un agente sin grants ve las tools que puede ejecutar

- [x] _(hecho 43d1e74c)_ **Título**: cuando `allowed_tools` está ausente (sin restricción por agente), anunciar
      el conjunto de tools cableadas por defecto en vez de solo las de sistema — alineando lo
      que el modelo ve con lo que el registry deja ejecutar.
- **Hallazgo**: B-02 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/agent_tool_schemas.py:250-262`
- **Tests**: unit de los tres casos: `None` (sin restricción → catálogo cableado + sistema),
  `[]` (deny-all → nada), lista concreta (solo esas + sistema). El caso `[]` **no debe
  cambiar**: es el deny-all del modo discusión.
- **Criterio de aceptación**: un agente recién creado sin asignaciones ve `read_file`,
  `write_file` y `stack_exec`.

#### `task_wf_12` — El OAuth de MCP llega al runtime

- [x] _(hecho 18a8c2a8 + 6fb35e7c)_ **Título**: construir el `httpx.Auth` con `build_oauth_provider` cuando el servidor
      declara `auth_kind="oauth"` y pasarlo como `auth=` en `MCPToolRunner.connect`. El
      almacenamiento en Vault y el flujo interactivo ya existen; falta el último salto.
- **Hallazgo**: B-03 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py:286-291`,
  `agent_runtime/__main__.py:298-318`
- **Tests**: unit con un provider falso verificando que `MCPClient.connect` recibe `auth`;
  regresión de que un servidor sin OAuth sigue conectando sin él.
- **Criterio de aceptación**: un servidor MCP remoto con OAuth conectado desde la UI funciona
  dentro de un run.
- **El plan subestimó el alcance**: daba por hecho que el servidor «declara `auth_kind`», y
  **`auth_kind` no se persiste** en `project.mcp_servers` (el frontend lo deduce del catálogo
  por URL). El runtime no tenía por tanto ni forma de saber que un servidor usa OAuth ni el
  tenant/proyecto para la ruta de Vault. Entregado: `template_for_url`/`uses_oauth` en
  `shared_mcp.catalog` (la misma búsqueda que ya hacía el panel, ahora compartida),
  `MCPServerConfig.oauth_ref` **separado** de `auth_ref`, `serialise_servers_for_run` en el
  dispatch (que es quien conoce tenant+proyecto) y `build_oauth_auth` en el runtime. Una URL
  fuera del catálogo NO se trata como OAuth.
- **Sigue pendiente el test humano** (`human_wf_03` extendido): el handshake real contra un
  servidor OAuth vivo no se puede ejercitar sin navegador — es el mismo riesgo residual (c)
  que el propio ADR 0127 declaró.

#### `task_wf_13` — `send_notification` deja de ser una promesa falsa

- [x] _(hecho f51c4fa5)_ **Título**: retirar `send_notification` del anuncio al modelo (igual que se hizo con
      `kanban_update`/`agent_invoke` en AUD16-02) **y** de `RUNTIME_WIRED_TOOL_NAMES`, dejando
      las tres coherentes. Añadir un test-contrato que impida que una tool sin ejecutor real
      vuelva a entrar en la lista de cableadas.
- **Hallazgo**: B-04 (alto) · **Tiempo**: 0,25 d
- **Ficheros**: `packages/shared-domain/src/shared_domain/tool_names.py:109-142`,
  `apps/workers/src/workers/agent_tool_schemas.py:168-181`
- **Tests**: contrato «toda tool en `RUNTIME_WIRED_TOOL_NAMES` tiene un ejecutor que no
  devuelve `not wired`».

#### `task_wf_15` — Un invariante que mata la familia entera

- [x] _(hecho f51c4fa5 + ec461d8c)_ **Título**: test de contrato que fije, para cualquier combinación de agente / proyecto /
      modo: **toda tool del allowlist efectivo tiene esquema anunciado, y todo esquema
      anunciado corresponde a un ejecutor real que no devuelve `not wired`**. B-01, B-02 y
      B-04 son tres instancias del mismo fallo; sin el invariante volverán a aparecer con la
      próxima vía de asignación.
- **Hallazgos**: B-01, B-02, B-04 (clase) · **Tiempo**: 0,5 d
- **Depende de**: `task_wf_10`, `task_wf_11`, `task_wf_13`
- **Ficheros**: test nuevo cruzando `agent_tools_enforcement`, `agent_tool_schemas` y el
  registry del runtime
- **Criterio de aceptación**: introducir a mano una tool en el allowlist sin esquema hace
  fallar el test.

#### `task_wf_14` — El prompt sabe si un servidor MCP no conectó

- [x] _(hecho 31279990)_ **Título**: bloque nuevo del preámbulo con el estado de los servidores MCP, presente
      solo cuando alguno falló, fenced como el resto de datos no confiables.
- **Hallazgo**: B-07 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `agent_runtime/__main__.py:350-425,692-751`
- **Tests**: unit del preámbulo con un servidor caído → contiene el aviso; sin fallos → el
  preámbulo no cambia.

---

## Ola 2 — Infra de ejecución (P0 · ~2,5 d)

#### `task_wf_20` — El test-runtime deja de contaminar el worktree

- [x] _(hecho 2fc6af07)_ **Título**: `HOME` del test/stack-runtime pasa a `/home/agent` (lo que ya declaran las
      imágenes) con su tmpfs correspondiente, replicando el patrón del agent-runtime. Verificar
      que las cachés del catálogo (`COMPOSER_HOME`, `npm_config_cache`…) siguen resolviendo, y
      que con `dep_cache_mount` activo el bind sigue ganando.
- **Hallazgo**: C-01 (alto) · **Tiempo**: 0,75 d
- **Ficheros**: `apps/workers/src/workers/test_runtime.py:895-945`
- **Tests**: unit de que `HOME` no es `/workspace` y de que existe tmpfs para él; integración
  de un `composer install` real que **no** deja ficheros nuevos sin trackear en el worktree.
- **Criterio de aceptación**: tras un `stack_exec` que escribe caché, `git status` en el
  worktree sale limpio.

#### `task_wf_21` — El test-runtime hereda el envelope endurecido

- [x] _(hecho f3843e6f)_ **Título**: aplicar `pids_limit` y los perfiles seccomp/apparmor configurados al
      contenedor de test/stack, reutilizando la lógica de `isolation.py` en vez de duplicarla
      — extraer el tronco común a una función compartida.
- **Hallazgo**: C-02 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/test_runtime.py:921-945`,
  `apps/workers/src/workers/isolation.py:88-153`
- **Tests**: unit comparando los kwargs de ambos envelopes campo a campo (el test falla si uno
  gana una protección y el otro no).

#### `task_wf_22` — Los tests de aceptación salen del worker `default`

- [x] _(hecho 78bce8eb)_ **Título**: encolar `_run_task_tests` a la cola `test` con espera acotada, siguiendo el
      patrón que `stack_exec` ya aplica por riesgo de deadlock, en vez del `await` inline.
- **Hallazgo**: C-04 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/execution.py:846-911,1500-1510`,
  `apps/workers/src/workers/celery_client.py:148-160`
- **Tests**: unit de que la fase de tests despacha a la cola `test`; regresión de que un fallo
  de la fase de tests sigue sin romper un run ya terminado.
- **Entregado** (78bce8eb): `dispatch_test_runtime_and_wait` + `test_phase_wait_budget_s` en
  `workers/tasks/test_runtime_task.py`, siguiendo el patrón de `run_stack_command_and_wait`.
- **Se sigue esperando el resultado a propósito**: el reviewer se despacha después y necesita
  un `<test-report>` real; convertirlo en fire-and-forget reabriría la carrera de C1/F51. Lo
  que cambia es DÓNDE se hace el trabajo, no si se espera. Presupuesto = suma de los timeouts
  de los checks (corren en serie en el mismo contenedor) + margen de arranque/teardown, con
  techo duro de 1 h; un `timeout_s` corrupto cae al default en vez de restar. Se conserva el
  invariante best-effort: broker caído, sin worker en `test` o presupuesto vencido → `{}`,
  nunca una excepción sobre un run ya terminado.

#### `task_wf_23` — El run-lock sobrevive al hard kill

- [x] _(hecho, ver git log)_ **Título**: derivar el TTL del lock del `execution_hard_time_limit_s` efectivo (más
      margen), no del presupuesto de contenedor, para que nunca caduque antes que el run.
- **Hallazgo**: C-05 (medio) · **Tiempo**: 0,25 d
- **Ficheros**: `apps/workers/src/workers/tasks/run_cycle.py:218-228`
- **Tests**: unit de que `lock_ttl > hard_time_limit` para todos los kinds.

#### `task_wf_24` — Un reintento no reinstala las dependencias

- [x] _(hecho 4725ff45)_ **Título**: acotar el `clean` de `sync_to_head` para que no arrase los directorios de
      dependencias (o `clean -fd` sin `-x` más una limpieza explícita de artefactos de build),
      conservando el determinismo que motivó el `-x`.
- **Hallazgo**: C-06 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/git_repos.py:487-514`
- **Tests**: unit de que `vendor/` sobrevive a un `sync_to_head` y un fichero de build no.
- **Nota**: decidir si la exclusión se declara por runtime-template (el template sabe cuáles
  son sus directorios de dependencias) o por convención global. Preferible lo primero.
- **Resuelto**: se declara por plantilla (`RuntimeTemplate.dependency_dirs`) **y** se pasa la
  UNIÓN de todas al `clean`, porque un worktree puede tener varios stacks a la vez (monorepo
  con backend PHP y frontend node): limitarse al template por defecto del proyecto seguiría
  arrasando los del otro. Hay test que impide colar un directorio de BUILD en esa lista.

---

## Ola 3 — Ceguera operativa (P1 · ~5,5 d)

Casi todo el backend existe. Esta ola es mayoritariamente cableado y UI.

#### `task_wf_30` — Una cabecera de plan, no cuatro secciones sueltas

- [x] _(hecho 6e4c5f37 + 0a16f4ed)_ **Título**: **un** endpoint `GET /plans/{id}/status` que devuelva progreso X/Y (sobre
      `compute_plan_progress`, ya escrito y testeado), estado del PR (`pr_url` / `pr_branch` /
      `pr_error`) y coste real contra estimado, y **un** componente de cabecera que los pinte
      juntos en el detalle del plan, con la versión reducida en la tarjeta del tablero.
      Consolida lo que en la primera versión de este plan eran cuatro tareas: menos código, y
      el operador ve el estado del plan de un vistazo en vez de en cuatro sitios.
- **Hallazgos**: D-01, D-02, D-04 (altos y medio) · **Tiempo**: 1,5 d
- **Ficheros**: `apps/api-server/src/api_server/plan_progress.py:101`,
  `routers/plans.py`, `routers/runs.py` (agregación de tokens/€ por plan),
  `app/admin/projects/[id]/plans/[planId]/`, `app/admin/board/page.tsx`
- **Tests**: integración del endpoint con las tres piezas; unit de render en los estados
  interesantes (sin PR, con PR, con `pr_error`, coste por encima del estimado).
- **Criterio de aceptación**: abrir un plan cerrado muestra, sin desplazarse, en qué punto
  está, dónde está su PR y cuánto ha costado frente a lo previsto.
- **Entregado**: `GET /plans/{id}/status` (`aggregate_actual_spend` + `build_plan_cost_status`)
  y `PlanStatusHeader` en el detalle del plan. Tres decisiones que conviene no deshacer:
  **no se resta EUR de USD** (la estimación de IA y el gasto real son USD y comparan directo;
  la estimación humana es EUR y mide horas de persona — un número que mezclase las dos sería
  inventado); **un run fallido cuenta como gasto** (excluirlo maquillaría el coste justo en
  los planes que más cuestan); y **`over_estimate` solo se enciende si hay estimación**.
- **Pendiente**: la versión reducida en la tarjeta del tablero gerencial (`app/admin/board`),
  que va con `task_wf_32` (el WebSocket de plan que la refresca).

#### `task_wf_32` — WebSocket de plan

- [x] _(hecho, con el enunciado corregido)_ **Título**: stream **`/ws/plans` de TENANT** (no
      `/ws/plan/{project_id}`) con las transiciones de estado de plan, consumido por el
      tablero gerencial.
- **Hallazgo**: D-03 (medio) · **Tiempo**: 0,75 d (real ≈1,5)
- **Ficheros**: `api_server/events.py` (stream `events:plans` + publicador),
  `routers/ws.py`, `routers/_helpers.py` (`move_plan`), `routers/{plans,review,
task_lifecycle}.py`, `orchestrator/dispatch.py`, `workers/maintenance/{reconciler,
review_runtimes}.py`, `app/admin/board/page.tsx`
- **Las dos correcciones del recon, aplicadas**: (1) el socket es de **tenant** — el tablero
  lista los planes de todo el tenant y uno por proyecto dejaría rancias las demás tarjetas;
  es el primer socket **sin recurso**, así que su autorización es «tener tenant» + el filtro
  del pump, y un superadmin sin tenant elegido no puede abrirlo. (2) El 80% era el lado
  **productor**: se cablearon los 8 sitios del api-server (vía `move_plan`, que transiciona
  y anuncia en una sola llamada) **y los 4 de UPDATE crudo** del orchestrator, el reconciler
  y el barrido de reviews caducadas — que son justo los que producen
  `pending_human_validation` y `blocked`, las dos transiciones sin gesto humano.
- **Tests**: 4 unit, uno de ellos el **guard estático**: cualquier módulo que escriba el
  estado de un plan y no lo anuncie rompe la suite. Es lo que impide que el sitio nº 13
  vuelva a dejar el tablero rancio.

#### `task_wf_33` — Estimaciones calibradas con datos reales

- [ ] **Título**: cerrar el bucle de estimación. El sistema ya cierra planes, escribe
      retrospectivas (`plan_retro`, ADR 0124) y guarda duraciones y tokens reales en
      `executions`; falta que eso vuelva a la estimación. Calcular, por proyecto (con
      _fallback_ al tenant y luego a la plataforma), las horas y tokens medianos **reales** por
      nivel de complejidad, y usarlos en lugar del mapa estático de `task_wf_03`. Sin
      histórico suficiente se sigue usando el mapa, señalando en la UI que la estimación aún
      no está calibrada.
- **Hallazgo**: A-04 (mejora sobre la corrección mínima) · **Tiempo**: 1 d
- **Depende de**: `task_wf_03`, `task_wf_30` (la agregación de coste real)
- **Ficheros**: `chat/cost.py`, `workers/plan_retro.py`, tabla o vista de calibración
- **Tests**: unit de que con histórico se usan las medianas reales y sin él el mapa estático;
  unit del _fallback_ proyecto → tenant → plataforma.
- **Criterio de aceptación**: tras cerrar tres planes en un proyecto, la estimación del cuarto
  refleja lo que de verdad cuesta una tarea `l` **en ese proyecto**, no una constante.
- **Por qué importa**: un mapa estático `complexity → horas` es la misma ficción que el
  default de 4 h, solo que mejor vestida. Esta tarea es la que hace que el presupuesto
  signifique algo.

#### `task_wf_34` — Standup y retrospectiva visibles

- [x] _(hecho — la retro; el standup ya se veía)_ **Título**: sección «Retrospectiva» en el
      detalle del plan cerrado, leyendo la memoria `project_shared` que ya escribe
      `plan_retro`.
- **Hallazgo**: D-05 (medio) · **Tiempo**: 0,75 d
- **Ficheros**: `packages/shared-domain/src/shared_domain/memory_tags.py` (nuevo),
  `workers/plan_retro.py`, `routers/plans.py` (`GET /plans/{id}/retro`),
  `plans/[planId]/plan-retro-section.tsx` (nuevo)
- **El bloqueo que el plan se saltaba** (lo detectó el recon): una retro **no se podía
  atribuir a su plan**, porque el INSERT fijaba `tags` a una constante. Orden correcto y
  seguido: (1) escribir `plan:{id}` en `tags`; (2) endpoint; (3) UI.
- **Tests**: 2 unit del etiquetado + 3 de integración del endpoint + 4 de render.
- **Degradación deliberada**: las retros escritas ANTES del etiquetado no se pueden
  atribuir; el plan simplemente no enseña retro. Nada de backfill por coincidencia de
  texto: emparejaría mal en cuanto dos planes del proyecto compartan título, y enseñar la
  retro de OTRO plan es peor que no enseñar ninguna.
- **Standup**: fuera de alcance, ya se ve en la bandeja (`inbox/page.tsx`) y su endpoint
  existe. El standup es de TENANT, no sabe de proyectos.

#### `task_wf_35` — Configuración de proyecto sin agujeros

- [x] _(hecho, en tres tramos)_ **Título**: UI para `execution_budgets`, `guardrails_config`
      de proyecto, `budget_*` y `human_task_review_mode` en el hub del proyecto.
      `allowed_domains` **ya tenía UI**: no se tocó. `secrets_vault_id` **se retiró** en vez
      de dársela (está deprecated y no lo lee nadie).
- **Hallazgo**: D-06 (medio) · **Tiempo**: 1 d
- **Ficheros**: `components/projects/governance-section.tsx` + `lib/project-governance.ts`
  (nuevos), `app/admin/projects/[id]/page.tsx`
- **Tres tramos**: `39f1ebbf` cerrar el no-op mudo (la API aceptaba los dos primeros sin
  validar y aguas abajo se descartaban en silencio; sin esto la pantalla habría mentido
  igual) · `8566be6b` retirar `secrets_vault_id` · esta sección.
- **Tests**: 11 unit de la conversión formulario↔API + 6 de render. El que más importa:
  **un campo vacío viaja como `null`, no como `0`** — un presupuesto de cero impediría
  arrancar cualquier run.
- **Decisión**: los guardrails se editan como **JSON**, no con un formulario. Su forma es
  `{guardrails: {hook: [{type, …}]}}` con parámetros propios por tipo; un formulario
  tendría que replicar ese catálogo y divergiría del esquema a la primera. El backend valida
  con el MISMO parser que el worker, así que un error aquí es el que habría en ejecución.

#### `task_wf_36` — Una sola definición de plan completado

- [x] _(hecho 482fe5a0)_ **Título**: decidir y unificar. Recomendación: `completed` significa «validado por el
      humano» (lo que hace hoy el camino real), y el estado del PR se refleja aparte
      (`task_wf_31`). Entonces `transition_to_completed` se ajusta o se retira, sus tests se
      migran, y `CLAUDE.md` se corrige.
- **Hallazgo**: D-07 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/api-server/src/api_server/plan_progress.py:307-336`,
  `routers/review.py:509-528`, `tests/integration/test_plan_completion.py`, `CLAUDE.md`
- **Tests**: los de `test_plan_completion.py` migrados a la definición elegida.
- **Nota**: es un cambio de criterio de producto. Si el operador prefiere la definición
  estricta (PR mergeado), la tarea cambia de forma y necesita el webhook de merge.
- **Decidido** (2026-07-25): se aplica la recomendación — `completed` = validado por el
  humano. La definición estricta habría exigido un webhook de merge que no existe y habría
  dejado cada plan colgado de un evento que nadie emite. **Si el operador prefiere la
  estricta, esto se revierte y se planifica el webhook.**
- **Corrección al plan**: `CLAUDE.md` NO había que tocarlo. Su principio 5 ya dice «al
  completar el plan se abre un PR automático» —completar primero, PR después—, el orden real.
  Los criterios de cierre con «PR mergeado» de su protocolo de roadmap gobiernan las FASES de
  desarrollo de esta plataforma, no la máquina de estados del producto: son otra cosa.

---

## Ola 4 — Workflow del humano (P1 · ~6 d · parcialmente gated)

#### `task_wf_40` — Acciones humanas en la ficha de tarea

- [x] _(hecho, solo frontend)_ **Título**: reintentar, reasignar con guía y desbloquear desde
      `TaskDetailSheet`, cableados a los endpoints de `task_lifecycle.py` que ya existen.
      Resuelve la tarea `blocked` que no está escalada sin desbloquear el plan entero.
- **Hallazgo**: A-09 (medio), F-6 · **Tiempo**: 1 d
- **Ficheros**: `components/tasks/task-human-actions.tsx` (nuevo, extraído del panel de
  escaladas), `components/tasks/task-detail-sheet.tsx`,
  `app/admin/plans/[id]/escalated/page.tsx`
- **Tests**: 10 de render (`task-human-actions.test.tsx` + `task-detail-sheet.test.tsx`),
  incluida la regresión de RBAC (un miembro no-admin no las ve) y el espejo del gate de
  estados del backend. Los e2e existentes del panel de escaladas siguen valiendo porque los
  `data-testid` se conservaron.
- **Nota**: el backend estaba entero, no se tocó. `retry` **es** el «desbloquear»: no había
  una quinta acción que añadir.

#### `task_wf_41` — «Aprobar y arrancar»

- [x] _(hecho, backend + UI)_ **Título**: acción combinada que encadena las transiciones cuando la política del
      proyecto no exige doble firma. La cadena sigue pasando por los mismos gates; solo se
      ahorra clics.
- **Hallazgo**: A-08 (medio), F-5 · **Tiempo**: 0,5 d
- **Ficheros**: `plan-lifecycle-section.tsx:72-120`, `routers/plans.py`
- **Tests**: integración de que con doble firma configurada la acción **no** se ofrece.

#### `task_wf_42` — Editor del spec antes de aprobar

- [x] _(hecho, backend + UI)_ **Título**: tabla editable de tareas (título, descripción, rol,
      complejidad, horas, dependencias, criterios) sobre el `PUT /plans/{id}` existente,
      habilitada solo en `draft`/`pending_approval`. Incluye recuperación de un ciclo de DAG:
      el 422 se traduce a un mensaje legible con los TÍTULOS de las tareas del ciclo y el
      editor sigue abierto con lo escrito.
- **Hallazgo**: A-07, A-11, F-2 · **Tiempo**: 2 d
- **Depende de**: `task_wf_02` (sin el arreglo de `summary`, el `PUT` falla)
- **Ficheros**: `plan-spec-editor-section.tsx` + `lib/plan-spec-edit.ts` (nuevos),
  `plans/[planId]/page.tsx`, `routers/plans.py` (`_require_spec_editable`)
- **Tests**: 11 unit de las piezas puras + 6 de render + 6 de integración del gate.
- **Gate del backend** (lo que el recon detectó que faltaba): `update_plan` aceptaba
  `specification` en CUALQUIER estado con solo `require_tenant_member`. Ahora 409
  `spec_not_editable` fuera de `draft`/`pending_approval`/`in_progress`.
  **`in_progress` queda abierto A PROPÓSITO**: la replanificación en caliente ya existe hoy
  por esa vía y la gobierna el **ADR 0132** (`task_wf_44`); cerrarla aquí sería implementar
  por la puerta de atrás una decisión pendiente de aprobación humana. La UI sí es más
  estrecha: el editor no se ofrece en `in_progress`.

#### `task_wf_43` — Las @-menciones tienen efecto (o se retiran)

- [x] _(hecho)_ **Título**: parsear las menciones del mensaje del usuario y pasarlas a
      `pm_decide` como preferencia de especialistas. Se implementó (no se retiró): la
      mención **manda** sobre el juicio del modelo y sobre el heurístico de palabras clave,
      y manda **acotando** — pedir un rol no debe convocar a otros cuatro.
- **Hallazgo**: A-10 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `chat/planning_llm.py` (`_mentioned_roles` + precedencia en `pm_decide`),
  `routers/conversations.py` (`GET /projects/{id}/planning-roles`, nuevo),
  `schemas/conversations.py`, `chat/page.tsx` (compositor), `e2e/agent-mentions.spec.ts`
- **Tests**: 7 unit (`test_planning_llm.py`) + 3 de integración del endpoint.
- **Decisiones**: `ask_user` y `finish_planning` NO se sobreescriben — el segundo es el turno
  que produce el botón «Generar Plan» y robarlo cuesta más que posponer la mención.
  Mencionar **solo** al PM es la mención simétrica («contéstame tú»): silencia el empujón
  determinista. El desplegable ya no hardcodea los nueve roles del enum: lee el equipo REAL,
  que es el mismo conjunto con el que el servidor intersecta.

#### `task_wf_44` — ADR de replanificación en caliente 🔒 GATED

- [x] _(hecho — el ADR está escrito; **falta que el operador decida**)_ **Título**: redactar
      el ADR que decida cómo se replanifica un plan `in_progress`.
- **Hallazgo**: A-06 (alto), F-1 · **Tiempo**: 0,5 d (solo el ADR)
- **Entregable**: `docs/05-architecture-decisions/0132-replanificacion-en-caliente.md`,
  status `proposed`, tres opciones para (a), tres para (c), y recomendación en cada una.
- **Reencuadre que trajo el recon**: no se diseña desde cero. La mitad **aditiva** ya
  funciona hoy (PUT del spec + `sync-to-kanban` que admite `in_progress`); el ADR gobierna
  un camino que ya se puede recorrer. El agujero real es que `sync_to_kanban` es
  estrictamente aditivo, así que **editar o borrar** una tarea del spec no llega nunca al
  tablero: el operador cree que ha replanificado y el equipo sigue con el plan viejo.
- **Recomendaciones del ADR**: (a) reconciliación de tres vías por estado de la tarea —
  lo que no ha empezado se actualiza/cancela, lo que está en vuelo se **rechaza con 409**
  nombrándolo, lo terminal no se reescribe; (b) nada se cancela solo; (c) sin
  re-aprobación de momento, por coherencia con el ciclo de correcciones del ADR 0107, que
  ya añade tareas a un plan aprobado sin volver a firmar; (d) registrar el evento, no
  versionar el documento.

#### `task_wf_45` — Implementar la replanificación 🔒 GATED tras `task_wf_44`

- [ ] **Título**: según lo que decida el ADR. Estimación provisional, a revisar cuando el ADR
      esté aceptado.
- **Tiempo**: 2 d (estimación gruesa)
- **BLOQUEADA**: el **ADR 0132** está escrito y en `proposed`. Hasta que el operador elija
  entre A1/A2/A3 y C1/C2/C3 no hay alcance que implementar. El propio ADR deja escritas
  las seis afirmaciones que sus tests tendrán que fijar.

---

## Ola 5 — Guardrails, prompts y deuda (P2 · ~5 d)

#### `task_wf_50` — Cablear `pre_llm` y `post_llm`

- [x] _(hecho)_ **Título**: invocar los dos hooks que faltan en el ciclo del runtime — el
      prompt antes de enviarlo al modelo y la respuesta al recibirla. Fail-open y baseline
      `warn` intactos: hay test de que con la política por defecto **ningún run cambia de
      resultado**.
- **Faltaba una segunda mitad que el plan no nombraba**: el baseline solo declaraba
  `post_tool`, así que aunque los hooks estuvieran cableados no habrían tenido nada que
  ejecutar. Ahora declara los tres con la MISMA acción `warn` — cambia qué se mira, no qué
  se hace con ello. Y el seam (`run_hook`) no pasaba `prompt`/`response`, que son los campos
  que leen esos dos hooks: el contrato ya existía en `shared_guardrails` y nadie lo usaba.
- **Semántica del `block`** (solo si un tenant lo configura): en `pre_llm` **no se manda el
  prompt** y el run corta con `guardrail_blocked` —bloquearlo después de mandarlo no
  bloquearía nada—; en `post_llm` la decisión se reescribe a un `noop` con el motivo, que es
  un rechazo visible del que el modelo se recupera al turno siguiente.
- **Hallazgo**: B-05 (alto) · **Tiempo**: 1 d · **Prioridad real**: P1
- **Ficheros**: `agent_runtime/graph.py`, `agent_runtime/guardrails.py:47-128`
- **Tests**: unit de que un prompt con contenido marcado dispara el hook; regresión de que con
  la política por defecto ningún run cambia de resultado.

#### `task_wf_51` — `update_plan` pasa por los guardrails

- [x] _(hecho)_ **Título**: hacer que el scratchpad pase por `pre_tool`/`post_tool` como
      cualquier otra tool, sin perder su naturaleza de capacidad del loop. Un plan bloqueado
      **no se guarda a medias**: el sticky anterior sigue valiendo y el modelo recibe el
      motivo. Era el único camino al contexto sin escudo, y el de más permanencia — el plan
      se relee todos los turnos.
- **Hallazgo**: B-06 (medio) · **Tiempo**: 0,25 d
- **Ficheros**: `agent_runtime/graph.py:950-971`
- **Tests**: unit de que un `update_plan` con contenido marcado se registra en los eventos de
  guardrail.

#### `task_wf_52` — Versionado de prompts

- [x] _(hecho la etiqueta y su persistencia; la propagación a `EvalRun` la cierra
      `task_wf_52b`, que es quien crea el productor)_ **Título**: hash estable del conjunto de
      prompts del runtime, expuesto por el runtime y persistido en `executions.prompt_version`
      (migración **0119**, con índice `(tenant_id, prompt_version)`).
- **Cómo se calcula**: por AST, todos los **literales de cadena largos** de los módulos que
  hablan con el modelo, excluidos los docstrings. Ni el módulo entero (un refactor movería
  la versión sin mover ningún prompt) ni solo las constantes (los nudges están escritos EN
  LÍNEA dentro de las funciones, y se quedarían fuera la mitad de los prompts).
- **Tests**: 10, y los dos que importan son opuestos — la etiqueta **se mueve** al editar un
  prompt (incluido un nudge en línea) y **no se mueve** al renombrar una constante o mejorar
  un docstring. Más uno que falla si el descubrimiento deja de encontrar prompts: el hash
  del vacío también es estable, y ése es el modo de fallo silencioso de esta pieza.
- **Hallazgo**: B-08 (medio), F-4 · **Tiempo**: 1 d
- **Ficheros**: `agent_runtime/providers.py`, `__main__.py`, `evals/shadow.py:198`
- **Tests**: unit de que el hash cambia al tocar un prompt y no cambia entre dos arranques
  idénticos; integración de que un `EvalRun` nuevo lleva versión no nula.

#### `task_wf_52b` — Encender los evals: sembrar, lanzar y muestrear

- [ ] **Título**: el subsistema de evals (Plan 14) está construido entero —7 módulos, 7 tablas,
      18 endpoints, dashboard— y **las siete tablas están vacías porque no hay ninguna vía de
      producirlas** (V-6). Tres piezas, en este orden:
      **(a) Sembrar** un dataset dorado mínimo con sus criterios e ítems, a partir de tareas
      reales ya cerradas cuyo resultado se considere bueno. Es la parte con trabajo humano de
      verdad y la que decide si el resto sirve: un dataset malo mide ruido.
      **(b) `POST /eval-runs`** para poder lanzar una corrida contra un dataset. Hoy el router
      tiene CRUD de las entradas y solo lectura de las salidas: **no hay productor**.
      **(c) El beat de shadow evals**, que muestrea el 5 % de tareas reales completadas y las
      pasa por el juez. `record_shadow_eval` ya existe y no lo llama nadie.
- **Hallazgo**: V-6 (alto, verificado en vivo) · **Tiempo**: 2,5 d (a: 1 d, b: 0,75 d, c: 0,75 d)
- **Depende de**: `task_wf_52` (versionado de prompts) — sin versión, las corridas no se pueden
  atribuir a un cambio y el dashboard sigue agrupando bajo «(sin versión)».
- **Ficheros**: `apps/api-server/src/api_server/routers/evals.py`,
  `apps/api-server/src/api_server/evals/shadow.py`,
  `apps/workers/src/workers/beat_schedule.py`, seeds del dataset
- **Tests**: integración de que `POST /eval-runs` produce filas en `eval_runs` + `eval_results`;
  integración de que el beat muestrea y registra sin tocar `tasks` ni `executions` (la decisión
  vinculante del Plan 14: **shadow nunca bloquea ni altera la ejecución real**).
- **Criterio de aceptación**: el dashboard `/admin/eval-quality` deja de estar vacío y agrupa
  por _release_ de prompt con datos reales.
- **Decisión del operador**: la tasa del 5 % cuesta una llamada de juez por tarea muestreada.
  Si se prefiere empezar a 0 % y subirla a mano, la tarea (c) sigue valiendo — cablea el
  mecanismo y deja el grifo cerrado.
- **Por qué importa**: es el **único instrumento** del sistema para saber si los agentes mejoran
  o empeoran. Sin él, `task_wf_60` (reviewer con diff), `task_wf_63` (caché) y cualquier cambio
  de prompt se entregan sin poder demostrar que mejoran nada.

#### `task_wf_53` — Tests de `tool_classification`

- [x] _(hecho — 38 tests; la retirada NO se hace, ver abajo)_ **Título**: batería directa del
      módulo del que dependen las guardas de convergencia.
- **Desviación deliberada**: el plan pedía retirar `search_code` y `apply_patch` «porque no
  existen en el runtime». **No se retiran, y hay un test que fija por qué**: estas tablas
  clasifican VERBOS tras quitar el namespace (`_base_tool_name`), no tools registradas. Un
  proyecto puede aportar `patcher.apply_patch` por MCP, y entonces sacarlo de
  `_PRODUCING_TOOLS` haría que `has_produced` no prendiera y el run se escalara habiendo
  producido — que es exactamente la regresión (C2/F24) que motivó el stripping de namespace.
- **Hallazgo**: B-09 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `agent_runtime/tool_classification.py`, tests nuevos
- **Tests**: cobertura de las tres clases (research / producing / read-only) y del latch de
  `has_produced`.

#### `task_wf_54` — Resolver el ADR 0108 (canales de veredicto)

- [x] _(ya estaba resuelto — premisa obsoleta, verificado)_ **Título**: cerrar el ADR 0108.
- **Hallazgo**: B-10 (medio) · **Tiempo**: 0,75 d
- **La premisa está obsoleta en dos cosas**: el ADR **no está `proposed`** —lo aceptó el
  operador el 2026-07-12— y la decisión aceptada es la **opción C: NO unificar**. Unificar
  ahora contradiría un ADR aceptado y reabriría el riesgo de convergencia del ADR 0095, que
  es justo lo que la opción C evita.
- **Sus consecuencias YA están aplicadas, verificado una por una**: el ancla cruzada en los
  dos parsers (`reviewer_bridge.py:103` y `providers.py:918`, cada uno apuntando al otro y
  explicando por qué la divergencia es intencional), la fuente única del wire-format
  (`review_contract.py`) y el test de contrato cruzado (`test_review_verdict_wire_contract`,
  3 tests en verde).
- **La normalización que el ADR nombra como siguiente paso de menor riesgo ya existe
  también**: los 3 campos ricos del reject del canal externo (`failed_criterion` /
  `testreport_evidence` / `what_to_fix`) llegan al implementador por
  `prior_review_feedback` con ese mismo shape (`dispatch.py:1451`). El `feedback` de la
  self-review no viaja por ahí porque **no es un dato entre runs**: es un sticky del propio
  run. Dos lifetimes distintos, no dos formatos del mismo dato.
- **Desbloquea `task_wf_61`**: se estructura el canal que emite veredicto entre runs, no
  «los dos que iban a unificarse».

#### `task_wf_55` — Pinear los perfiles seccomp/apparmor

- [x] _(hecho el cableado; el smoke en dev es del operador)_ **Título**: exportar los
      perfiles en el compose de dev.
- **Hallazgo**: C-03 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `docker/docker-compose.manuals.yml` (workers + workers-aux),
  `docker/.env.example`, `tests/security/test_seccomp_profiles.py`
- **Corrección del hallazgo**: C-03 decía «no están cableados en NINGÚN entorno». Es falso
  para el instalador, que ya los pina en el compose que genera (y hay test desde entonces).
  Lo que faltaba era el stack de **dev**, o sea: el entorno donde se prueba todo era el
  menos protegido de los dos, y un choque con el allowlist estricto solo aparecería en
  producción.
- **seccomp**: pinado, con override por env (`WORKERS_SECCOMP_PROFILE_PATH=` lo desactiva
  sin tocar el compose — si la vía rápida fuera borrar la línea, nadie se enteraría de que
  se relajó).
- **AppArmor**: **vacío por defecto a propósito**. Pinar un perfil que el host no tiene
  cargado hace fallar la creación del contenedor, y el dev típico es Docker Desktop sobre
  WSL2, que no trae AppArmor: pinarlo rompería TODOS los runs. Se activa por env en un host
  Linux con el perfil cargado. Hay test de que no se hard-pine.
- **PENDIENTE del operador**: el smoke de un run real con el perfil estricto. No se puede
  hacer desde aquí (exige desplegar) y es la única parte de la tarea que queda.

#### `task_wf_56` — `pump.join()` acotado

- [x] _(hecho)_ **Título**: timeout generoso (120 s) con log de la anomalía, conservando el
      drenaje completo del caso normal. Lo que aporta no es cortar antes: es que un daemon
      colgado DEJE RASTRO en vez de inmovilizar el worker con el slot de la cola ocupado.
- **Hallazgo**: C-07 (bajo) · **Tiempo**: 0,25 d
- **Ficheros**: `apps/workers/src/workers/container.py:196-212`

#### `task_wf_57` — Retirar el código muerto

- [x] _(hecho — ~1.400 líneas fuera)_ **Título**: eliminar `runtime_pool.py`,
      `TestcontainersMode` + `build_dind_proxy_run_kwargs` y el `ReviewRuntimeManager` en
      memoria.
- **Hallazgo**: C-08 (bajo) · **Tiempo**: 0,5 d
- **Alcance real**: además de lo listado, `plan_runner.py` (único importador de
  `runtime_pool`, demo sin cablear), `TenantCapExceeded`, los ajustes huérfanos
  `dind_proxy_*` y **9 ficheros de test** que solo ejercitaban lo borrado.
- **Lo que NO se borró**: `workers/review_runtime.py` sigue vivo — `sign_review_url`,
  `verify_review_url`, `ReviewRuntimeSpec` y `DEFAULT_TENANT_CAP` están en producción. Solo
  se fue la clase en memoria. El **cap por tenant** sigue cubierto por
  `tests/unit/test_review_tenant_cap.py`, que prueba el camino real (el de
  `review_runtime_task`), no el del manager muerto.
- **Tests**: el test de seguridad que endurecía el proxy DinD se sustituye por un
  invariante **más fuerte**: ningún módulo del worker ni del agent-runtime puede volver a
  nombrar `/var/run/docker.sock`. Endurecer una vía de escape que nadie usa valía menos que
  no tenerla. `assert_no_docker_socket` sigue cubierto.

#### `task_wf_58` — Test-contrato reconciler ↔ dispatch

- [x] _(hecho — y además se elimina la duplicación)_ **Título**: fijar con un test la promesa
      que hoy es un comentario. Se hizo lo de un paso más allá: la secuencia de decisión vive
      ahora en **`decide_plan_closure`**, que llaman las dos vías. Una promesa en un
      comentario se rompe sin que nada falle; una función compartida no puede divergir.
- **Tests**: 19 — dos guards estáticos (ambos módulos la llaman; ninguno recompone la
  secuencia a mano) + la tabla de snapshots, incluidos los tres casos que costaron
  auditorías. De paso apareció un caso que la pieza suelta resolvía mal: un snapshot **vacío**
  satisface «todas hechas» por vacuidad y cerraba el plan; ahora la decisión compartida lo
  rechaza sin depender de que el llamante se acuerde de filtrarlo.
- **Hallazgo**: C-09 (bajo) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/maintenance/reconciler.py:319-438`,
  `apps/orchestrator/src/orchestrator/dispatch.py:371-537`

---

## Ola 6 — Palancas de calidad y coste (P1 · ~4,25 d)

No son bugs: el sistema funciona sin esto. Son las mejoras con mejor relación
impacto/esfuerzo de toda la auditoría. Detalle y evidencia en §7c del informe.

#### `task_wf_60` — El reviewer juzga el DIFF, no ficheros enteros

- [x] _(hecho)_ **Título**: entregar al review-runtime el **diff de la tarea** como artefacto
      primario del prompt, dejando `read_file` para el contexto de alrededor. Lo calcula el
      **worker** (es quien tiene `data_root` y git) y se entrega ya hecho, igual que el
      `<test-report>`: **al sandbox no se le da git**.
- **Hallazgo**: M-2 · **Tiempo**: 1,5 d
- **Ficheros**: `workers/review_diff.py` (nuevo), `workers/execution.py` (lo calcula la
  provisión del workspace — único punto con worktree resuelto + git), `workers/run_spec.py`,
  `agent_runtime/__main__.py::build_review_preamble`
- **DOS fuentes, no una**, porque el momento del review no siempre es el mismo: el trabajo
  **sin commitear** (`git diff HEAD`, primer review) y, si no hay, **los commits de ESTA
  tarea** localizados por el trailer `Task-Id` (re-review tras un rechazo, donde el trabajo
  ya está commiteado y `git diff HEAD` saldría vacío). El plan solo contemplaba la primera.
- **Tests**: 14 — las dos fuentes, que los commits de una tarea HERMANA no se cuelan (un plan
  es una rama compartida), el truncado que avisa, que un fallo de git **no impide el review**,
  y que el diff va ANTES de la prosa del implementador y dentro del cerco de datos.
- **Fallback conservado**: sin diff (runs de análisis/diseño, o sin worktree) la sección no
  aparece y el review sigue con la cosecha de ficheros de siempre.

#### `task_wf_61` — Veredicto por criterio

- [x] _(hecho)_ **Título**: que el reviewer emita un resultado **estructurado** —cada criterio
      con `pass`/`fail` y su evidencia— en vez de prosa con un único `<failed_criterion>`.
- **Hallazgo**: M-3 · **Tiempo**: 1 d · **Dependía de**: `task_wf_54` (resuelto: el ADR 0108
  decidió NO unificar, así que se estructura el canal que emite veredicto ENTRE runs).
- **ADITIVO por diseño**: el `<verdict>` sigue mandando. Un reviewer que no emita el bloque
  —modelo que se lo salta, run de una imagen anterior— se comporta **exactamente** como
  antes, y hay test de esa propiedad: es lo que hace seguro encenderlo.
- **Formato de LÍNEA** (`- [pass] criterio — evidence: …`) y no tags anidados: el modelo lo
  produce sin equivocarse, el humano lo lee tal cual, y el marcador resiste la deriva de
  redacción que ya obligó a parsear el `<verdict>` con tolerancia.
- **Las tres cosas que habilita, entregadas**: la UI enseña qué criterio falló y con qué
  evidencia (`task-review-criteria.tsx`, sobre el historial de auditoría que ya existía); el
  `what_to_fix` tiene diana (un reject sin `<failed_criterion>` la deriva del desglose); y
  el resultado es medible entre runs, que es lo que `task_wf_52b` necesita.
- **Un APPROVE también deja desglose**: sin él, «aprobado» es indistinguible de «aprobado sin
  mirar». Ojo — eso obligó a filtrar los eventos de aprobación en
  `_read_prior_review_feedback`, o el implementador habría recibido un bloque VACÍO de
  «rechazos previos».
- **Tests**: 14 unit + 7 de render.

#### `task_wf_62` — Trazabilidad del runtime: digest en vez de etiqueta flotante

- [x] _(hecho — migración **0120**)_ **Título**: persistir el **digest** de la imagen que
      corrió en la fila de `executions`.
- **Cambio respecto al enunciado**: no se «resuelve la etiqueta en el lanzamiento», se lee el
  campo `Image` del **inspect del contenedor** — el id que el daemon ya resolvió. Preguntar
  por la etiqueta después del lanzamiento tiene una carrera: si se reasignó entre medias, se
  registraría la imagen equivocada justo en el caso que esta trazabilidad existe para
  detectar. Además sale gratis (el inspect ya se lee) y no añade una llamada al daemon.
- **Tests**: 3 — que se guarda, que un daemon que no lo reporta **no rompe el run** (degrada
  a `None`), y que no se consulta al cliente por la imagen (el fake revienta si se hiciera).
  También viaja en el camino de FALLO: saber qué build produjo un run roto vale más que
  saberlo de uno que salió bien.
- **Hallazgo**: M-4 · **Tiempo**: 0,75 d
- **Ficheros**: `packages/shared-test-runtimes/.../catalog.py:31-41`,
  `apps/workers/src/workers/test_runtime.py`, `container.py`, migración para la columna
- **Tests**: unit de que el digest se resuelve y se guarda; unit de que un fallo al resolverlo
  no impide el run (degrada a la etiqueta, con aviso).
- **Sinergia**: junto a `task_wf_52` (versionado de prompts) cierra la trazabilidad completa de
  un run — **qué prompt y qué imagen** lo produjeron.

#### `task_wf_63` — Medir la caché de prompt antes de optimizarla

- [ ] **Título**: instrumentar los aciertos de caché por proveedor y el coste por iteración,
      **antes** de tocar la construcción de mensajes. Con el dato encima de la mesa, evaluar
      pasar `_decide_messages` de «un mensaje de usuario grande reconstruido cada turno» a una
      lista incremental, que es lo que permite a los proveedores con caché automática por
      prefijo aprovechar también el histórico.
- **Hallazgo**: M-1 · **Tiempo**: 1 d
- **Ficheros**: `agent_runtime/providers.py:340-400`, `packages/shared-llm/` (telemetría)
- **Criterio de aceptación**: un informe con el coste por iteración y el porcentaje de prefijo
  reutilizado por cada uno de los cuatro proveedores.
- **Nota honesta**: el catálogo (ADR 0021) no incluye la Messages API de Anthropic en crudo,
  así que **no aplica un `cache_control` explícito**. La ganancia depende de la caché
  automática de cada proveedor y **puede ser pequeña**. Por eso esta tarea es de medición, no
  de optimización: no comprometerse a un ahorro sin el dato.

---

## Ola 7 — Features nuevas (P1 · ~4,5 d)

No corrigen nada roto. Son capacidades que el sistema no tiene y que cambian cómo se trabaja.
Justificación en §7 del informe.

#### `task_wf_70` — Brief de las tareas predecesoras

- [x] _(hecho)_ **Título**: que el agente reciba, en el preámbulo, **qué hicieron las tareas de
      las que depende**: título + el resumen que su agente entregó en `submit_result`. Bloque
      nuevo, fenced como dato de terceros, acotado a dependencias **directas** completadas.
- **Hallazgo**: N-1 (feature) · **Tiempo**: 1 d
- **Ficheros**: `orchestrator/dispatch.py` (`_read_predecessor_briefs`),
  `workers/run_contract.py` + `run_spec.py`, `agent_runtime/__main__.py`
  (`build_predecessors_preamble`)
- **Tests**: 8 de render + 4 del productor. Los que importan: el brief va **fenced** (son
  informes de OTROS runs, contexto y no instrucciones); una dependencia **sin resumen se
  descarta** («hizo algo» no es algo sobre lo que construir y ocupa prompt); y una tarea sin
  dependencias cuesta **una sola consulta** y no emite la clave.
- **Corrección de mi propia intuición**: puse el brief por encima de los comentarios humanos
  y el test me obligó a mirarlo — un comentario del humano es una instrucción directa sobre
  ESTA tarea, y enterrarla bajo dos resúmenes de dependencias degradaría la guía que más
  manda. Orden final: comentarios → briefs → skills.

#### `task_wf_71` — Intervención en caliente sobre un run vivo

- [ ] **Título**: poder **redirigir** un run en vuelo en lugar de solo matarlo. Campo de guía
      en la ejecución; el bucle del agente lo consulta una vez por iteración vía la API interna
      (canal que ya existe) y lo inyecta como sticky del turno siguiente, igual que el feedback
      de review. Botón en el visor de runs.
- **Hallazgo**: N-2 (feature) · **Tiempo**: 2 d
- **Ficheros**: `apps/workers/src/workers/execution.py:1318-1330` (el sondeo ya existe),
  `apps/api-server/src/api_server/routers/internal_agent.py`, `agent_runtime/graph.py`,
  `app/admin/executions/[id]/page.tsx`, migración para la columna
- **Tests**: unit de que la guía entra como sticky en la iteración siguiente; integración de
  que un run sin guía no paga coste adicional apreciable; e2e de redirigir un run vivo.
- **Nota de diseño**: la comprobación por iteración añade un _round-trip_. Medir su coste; si
  molesta, piggyback sobre una llamada existente a la API interna en vez de una dedicada.

#### `task_wf_72` — Preflight del plan antes de aprobar

- [x] _(hecho)_ **Título**: semáforo de solo-lectura antes del botón de aprobar, componiendo
      resolvedores que **ya existen**: asignación por rol en modo seco, DAG, criterios de
      aceptación y desglose de coste.
- **Hallazgo**: N-3 (feature) · **Tiempo**: 1,5 d
- **Ficheros**: `api_server/plan_preflight.py` (nuevo, PURO), `routers/plans.py`
  (`GET /plans/{id}/preflight`), `plans/[planId]/plan-preflight-section.tsx` (nuevo)
- **La decisión que lo hace fiable**: usa los MISMOS resolvedores que deciden en producción.
  Un preflight que dijera algo distinto de lo que luego hace el sistema sería peor que no
  tenerlo.
- **Qué reporta**: rol sin agente en el equipo (**serio** — se materializa sin agente y lo
  reparte la política de carga, no el rol pedido), ciclo en el DAG (**serio**), tarea sin
  criterios (**aviso** — el reviewer certifica contra ellos, y sin ellos juzga contra la
  descripción y rechaza en bucle), plan en fila india (**aviso** — tarda lo mismo con un
  agente que con diez), más camino crítico, paralelismo máximo y coste.
- **No bloquea la aprobación**: informa. Y **no muta nada** — hay test.
- **Tests**: 14 unit + 6 de render. Uno fija que un plan limpio lo DIGA: el silencio se lee
  como «no se ha comprobado».

---

## Tests humanos del plan

| id            | Qué validar                                                                                                                                                                                                 |
| ------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `human_wf_01` | Conversación de planning larga (>100 mensajes) que enuncia un requisito al principio: el chat muestra lo reciente, se puede subir hasta el mensaje 1, y el plan generado **respeta ese requisito inicial**. |
| `human_wf_02` | Plan generado por chat: resumen con texto, fases con nombre, Gantt con barras desiguales.                                                                                                                   |
| `human_wf_03` | Run con una tool MCP de proyecto: el agente la invoca en la primera iteración.                                                                                                                              |
| `human_wf_04` | Tras un `stack_exec` con instalación de dependencias, `git status` del worktree sale limpio.                                                                                                                |
| `human_wf_05` | Detalle del plan cerrado: progreso X/Y, enlace al PR, coste real y retrospectiva visibles.                                                                                                                  |

## Resumen de esfuerzo

| Ola | Tema                             | Prioridad | Días     |
| --- | -------------------------------- | --------- | -------- |
| −1  | **Roto ahora en dev** (V-1, V-2) | **P0**    | 1,25     |
| 0   | Bugs que rompen el producto      | P0        | 5,25     |
| 1   | Lo que el agente no ve           | P0        | 3,0      |
| 2   | Infra de ejecución               | P0        | 2,5      |
| 3   | Ceguera operativa                | P1        | 5,5      |
| 4   | Workflow del humano              | P1        | 6,5      |
| 5   | Guardrails, prompts y deuda      | P2        | 7,75     |
| 6   | Palancas de calidad y coste      | P1        | 4,25     |
| 7   | Features nuevas                  | P1        | 4,5      |
|     | **Total**                        |           | **40,5** |

Las olas 0-2 (10,75 días) son las que cambian el día a día. Recomiendo entregarlas como una
primera tanda cerrada antes de decidir sobre el resto.

### Nota sobre corrección mínima contra mejora real

La primera versión de este plan proponía, para cinco hallazgos, el arreglo que hace
desaparecer el síntoma en vez del que resuelve el problema. Corregido tras la revisión del
operador; el detalle y el coste diferencial están en §7b del informe. En resumen:

| Tarea                                      | Qué cambió                                                                                                                                                                                                                     |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `task_wf_06` (**nueva**)                   | Encender el subsistema de compresión jerárquica que ya existe (`conversation_compression.py`), en vez de solo invertir el orden de los 50 mensajes — o, como proponía la v2, construir uno nuevo copiando el del agent-runtime |
| `task_wf_15` (**nueva**)                   | Test de contrato del invariante allowlist↔esquema, que cubre la clase entera en vez de las tres instancias                                                                                                                     |
| `task_wf_33` (**nueva**)                   | Estimaciones calibradas con el histórico real, en vez de un mapa estático                                                                                                                                                      |
| `task_wf_30` (**fusionada**)               | Una cabecera de plan en lugar de cuatro secciones sueltas — la única que **resta** esfuerzo                                                                                                                                    |
| `task_wf_00`, `task_wf_04` (**ampliadas**) | Paginación hacia atrás; e idempotencia que se lo dice al usuario en vez de callar                                                                                                                                              |

Neto: **+2,75 días**. Es la parte que responde a «mejora del workflow» y no solo a «arregla el
bug».

Y una lección que vale para el resto del plan: en las dos veces que el operador ha empujado
sobre una propuesta, la respuesta correcta ha resultado ser **buscar si el problema ya estaba
resuelto en otra capa de este repositorio** antes de diseñar nada. Con A-13 la primera
propuesta era invertir una línea, la segunda construir un mecanismo nuevo, y la correcta era
encender uno que llevaba escrito y testeado desde el Plan 03.

---

## Revisión adversarial de lo entregado (2026-07-25/26)

Tres workflows de solo lectura sobre los 24 commits de la tanda: 24 revisores y 13
refutadores. Solo **2 commits salieron limpios** (`5c11f592`, `4725ff45`).

### Regresiones CONFIRMADAS y ya arregladas

| commit del arreglo | qué rompía                                                                                                                                                                           |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `1540f5e6`         | `8ca290dd` devolvía a `ready` toda tarea HUMANA aceptada hace >30 min: no tienen fila en `executions` por diseño. Su entrega daba 409 y un agente de IA se ponía a hacer su trabajo. |
| `b77591e2`         | `8a095da1` abrió una vía para que un miembro del tenant borrase mensajes AJENOS del contexto del equipo publicando un mensaje propio con `is_summary=true`. Sin rastro en el feed.   |
| `239e042b`         | `43d1e74c` anunciaba 17 tools de las que 11 no tienen ejecutor en esa rama del runtime. **`task_wf_11` queda REABIERTO.**                                                            |
| (en `b77591e2`)    | `f51c4fa5` dejó 2 tests de integración en rojo un día. El invariante lista↔ejecutor era una igualdad y el diseño no la sostiene: es direccional.                                     |

### Confirmados y PENDIENTES — no tocar sin leer esto entero

- [x] _(hecho `786b2dca`)_ **`f3843e6f` × `2fc6af07` se contradicen** (alto). El perfil AppArmor
      `agent-runtime` tiene `deny /home/** wklx` (`docker/apparmor/agent-runtime.profile:93`)
      y el commit anterior puso ahí el HOME del test-runtime, donde apuntan los
      `dep_cache_mount` y `cache_env` de TODAS las plantillas. Con
      `WORKERS_APPARMOR_PROFILE=agent-runtime` —que el instalador exporta por defecto,
      `compose_generator.py:721-722`, con test que lo fija— cada `composer install` /
      `npm ci` / `pip install` muere en «Permission denied», y si el perfil no está
      cargado en el host el daemon ni crea el contenedor. **Corrige de paso al informe**:
      C-03 afirmaba que ningún compose exporta esas variables y es falso para el
      instalador. Salidas: no aplicar AppArmor al test-runtime (conserva `pids_limit` +
      seccomp), o dar al perfil una excepción para `/home/agent`. Verificar antes si el
      agent-runtime sufre ya lo mismo: su HOME también es `/home/agent`.
  - **Resuelto**: sí lo sufría, desde hace un mes. El perfil (2026-05-31) codificó «el
    sandbox escribe en /workspace y /tmp» y el HOME del agente salió de /workspace a un
    tmpfs propio en `498ade16` (2026-06-26) sin que nadie volviera al perfil. `2fc6af07`
    no introdujo el choque: lo **amplió** a los `dep_cache_mount` de las 12 plantillas.
    Se elige la excepción en el perfil, no retirar AppArmor del test-runtime — el
    contenedor que más código ajeno ejecuta no puede ser el menos confinado. Y se QUITA
    el `deny` en vez de añadir un permiso a su lado: en AppArmor un `deny` gana a
    cualquier `allow`, así que las dos reglas juntas seguirían rotas pareciendo
    arregladas. El invariante nuevo se ancla a `workers.isolation.AGENT_HOME` y al
    catálogo, no a literales.
  - **Nota aparte**: `tests/security/` tiene **4 tests en rojo desde antes** de esta
    tanda (`test_every_prod_service_references_an_apparmor_profile`,
    `test_monitoring_app_services_drop_all_caps_and_block_privesc`,
    `test_every_tenant_owned_table_has_rls_enabled`,
    `test_every_prod_service_carries_the_trusted_hardening_baseline`). Verificado contra
    HEAD limpio. Uno de ellos es de RLS, o sea principio 1. Sin CI nadie los mira.
- [x] _(hecho `1c471101`)_ **`619f2a7b` alarga demasiado el veto** (alto). El TTL pasó a 25200 s. El lock solo
      se suelta en el `finally`, que un SIGKILL no ejecuta, así que tras un OOM o un
      `docker stop` la tarea queda in-despachable hasta ~7 h; en la ruta del hard-kill el
      veto pasa de 0 s a 17400 s. Y el reintento se PIERDE: `concurrent_run_locked`
      devuelve éxito, así que Celery ACKea el mensaje re-encolado por
      `task_reject_on_worker_lost`. `stale_sweeper.py:132-135` ya había decidido cerrar
      huérfanos a los 5 min «en vez de dejarlo 7 h de zombi vetando el re-despacho».
      El plan pedía anclar al `execution_hard_time_limit_s` **efectivo más margen**, no a
      la ventana de visibilidad. Ojo: el arreglo tiene que resolver las DOS caras — el
      hueco que motivó C-05 y este veto—, probablemente soltando el lock en el sweeper.
  - **Resuelto** soltándolo en el sweeper, como apuntaba la nota: es quien acaba de
    PROBAR que el titular está muerto. El TTL se queda anclado a la ventana de
    visibilidad (mover el ancla reabriría el hueco de C-05); lo que cambia es que ya no
    es el único camino de liberación. La liberación mantiene garantía de propiedad — el
    token del lock es `executions.celery_task_id` —, así que un lock readquirido por un
    run nuevo y legítimo no se toca nunca. De paso, `release_run_lock` pasa a devolver si
    borró de verdad: la primera versión contaba intentos y el contador
    `run_locks_released` habría mentido justo durante un incidente.
- [x] _(ADR 0131 `accepted` + opción C implementada en `c8f9e2cc`)_ **`task_wf_12` no funciona de punta a punta**: nadie fija `AGENT_VAULT_TOKEN` en
      todo el repo (solo se lee, en el runtime). Meter un token de Vault en el sandbox
      choca con el principio 2 de `CLAUDE.md`. **Necesita ADR**: o el worker resuelve el
      token y lo inyecta canjeado, o la llamada MCP va mediada por el worker como
      `stack_exec`.
  - **`docs/05-architecture-decisions/0131-credenciales-oauth-mcp-en-el-sandbox.md`**,
    `status: proposed`. Distingue lo que se confundía: un **token de Vault** es la llave
    del almacén; un **access token OAuth** es un secreto acotado y efímero. Inyectar lo
    segundo no contradice el principio 2 y es lo que la plataforma YA hace con la
    credencial del LLM y con git. La vía MCP es la única que pide al sandbox tener una
    llave del almacén. Tres opciones; recomendada la **C** (mediar por el API interno,
    como `stack_exec`), porque es la única que además resuelve el **refresco** del token
    —el problema real que A no cubre— sin meter el refresh token en el contenedor.
    Mientras no se decida, el fallo está contenido: `_wire_mcp_servers` captura por
    servidor y desde `task_wf_14` el motivo llega al preámbulo del agente.
- [x] _(hecho c49c3430 — la regla vive ahora en `plan_is_live`)_ **`2e40b0bb`**: `_live_plan_of_conversation` no filtraba `deleted_at`, así que un plan
      borrado retiene su conversación para siempre. Arreglo: un `select` con
      `Plan.deleted_at.is_(None)` en vez del `session.get`.
- [x] _(hecho a348e264 — filtrado en `_schema_index`; queda abierto solo `tool_is_runtime_wired`)_ **`f51c4fa5`**: `send_notification` se seguía anunciando por la vía de `tool_specs`
      (el drop del catálogo deja el nombre libre y el bucle de specs lo rellena), y
      `tool_is_runtime_wired` sigue diciendo que es ejecutable porque cortocircuita por
      `implementation_type`.

---

## Recon de premisas de las olas 3 y 4 (2026-07-26)

10 agentes de **solo lectura**, uno por tarea, contrastando cada premisa del plan
contra el código. Igual que en el recon anterior, la mayoría de los enunciados
**no se sostienen tal cual**: 9 de 10 salieron `partly`. Esto NO invalida las
tareas — el hueco suele ser real —, pero sí su alcance, sus ficheros y su
estimación. **Leer esto antes de implementar cualquiera de ellas.**

Journal completo: workflow `wf_d021112e-da7`.

### `task_wf_32` — WebSocket de plan · el enunciado está mal en dos ejes

- **El socket tiene que ser de TENANT, no `/ws/plan/{project_id}`.** El tablero
  gerencial lista `/plans` de todo el tenant (`board/page.tsx:157`,
  `list_all_plans` en `plans.py:488`); un socket por proyecto dejaría rancias las
  tarjetas de los demás proyectos. Y el auth de `ws.py` **no sirve tal cual**:
  los 4 sockets actuales validan propiedad de un recurso (`_owns_resource`), y
  aquí no hay recurso — es un patrón nuevo, no una copia del de kanban.
- **El plan se salta el lado PRODUCTOR, que es el 80% del trabajo.** El estado de
  plan se muta en **11 sitios de 3 servicios**, pese a que
  `plan_state_machine.py:30` se declara «la única puerta». Y las dos transiciones
  que motivan la tarea (`pending_human_validation` y `blocked`) se escriben con
  **UPDATE crudo** desde el orchestrator (`dispatch.py:470`, `:505`) y los workers
  (`reconciler.py:574`, `:686`) — enganchar el publicador solo a
  `transition_plan_status` no emitiría **ninguno de los dos casos**.
- Estimación real ≈ **1,5 d**, no 0,75.
- **Corrección a mi propia afirmación anterior**: dije que publicar en
  `events:tasks` «rompería el orchestrator». Falso: `consumer.py:229` captura el
  evento malformado, incrementa `malformed` y hace ACK. La decisión no cambia (no
  reutilizar ese stream), pero el motivo es ruido, no caída.
- **Corrección a la auditoría D-03**: decía que el tablero «no tiene ni stream ni
  refetchInterval». Parcialmente falso — ya abre `/ws/kanban/{project_id}`
  (`board/page.tsx:250`). Lo que no se refresca es solo la fila de PLANES.
- **Caso barato que conviene no dejar fuera**: invalidar `["plans","tenant"]` en
  `onKanbanEvent` refresca las tarjetas del proyecto seleccionado sin backend
  nuevo. No sustituye a la tarea (no cubre otros proyectos ni las transiciones
  humanas), pero es una línea.

### `task_wf_33` — Estimaciones calibradas · **necesita una decisión antes de empezar**

- Los **tokens sí** se pueden calibrar ya: `compute_ai_cost` acepta
  `complexity_estimates=` (el parámetro YA existe, `cost.py:423`).
- Las **horas humanas NO**, y esto es el bloqueo: son horas-persona en EUR, y el
  histórico de un agente es wall-clock de máquina. Calibrar una con la otra
  repite exactamente la mezcla de magnitudes que `task_wf_30` rechazó a
  propósito. **Recomendación: calibrar solo tokens/USD y dejar las horas humanas
  como mapa estático**, diciéndolo en la UI.
- Detalle no obvio: `pm_plan_draft` corre en un hilo **sin sesión de BD**, así que
  el mapa calibrado hay que calcularlo fuera e inyectarlo por
  `PlanningState.project_context`.

### `task_wf_34` — Retro y standup · reducir a la mitad

- El **standup ya se ve** en la bandeja (`inbox/page.tsx:380`) y su endpoint
  existe. Solo faltaría una tarjeta «último standup» **a nivel de tenant** — el
  standup no sabe de proyectos.
- La **retro sí tiene trabajo**, y el plan se salta el bloqueo: hoy una retro **no
  se puede atribuir a su plan**, porque `memory_entries` no tiene `plan_id` y el
  INSERT fija `tags` a una constante (`plan_retro.py:214`). Orden correcto:
  (1) escribir el identificador del plan; (2) `GET /plans/{id}/retro`; (3) UI.
- Las retros ya escritas no llevan identificador. **Recomendación: degradar** (no
  mostrar nada en planes cerrados anteriores) en vez de un backfill por texto.

### `task_wf_35` — Config de proyecto · 4 campos, no 5

- **`secrets_vault_id` sale del alcance**: está deprecated
  (`schemas/projects.py:346`), tiene cero lectores y `task_proy_f2` ya decidió no
  resucitarlo. Darle UI sería una regresión. La mini-tarea correcta es la
  **inversa**: quitarlo de `ProjectCreateRequest` y de la siembra.
- [x] _(hecho `39f1ebbf`)_ **Antes que la UI había un no-op silencioso**: la API
      aceptaba `execution_budgets` y `guardrails_config` sin validar y aguas abajo se
      descartaban sin decir nada. Ya se rechaza en la puerta con 422. Ojo: la
      revisión afirmaba que una config malformada **desactiva los guardrails**, y lo
      probé — **es falso**, el degradado cae al baseline y los runs siguen tamizados.

### `task_wf_40` — Acciones humanas · **solo frontend**, y es media tarea

- El backend está entero: **no tocar `task_lifecycle.py`**. El trabajo es extraer
  un `TaskHumanActions` de `escalated/page.tsx:289-517` y reutilizarlo en el
  sheet, visible solo en `awaiting_human_approval`/`blocked` y solo para admin.
- `retry` **ES** el «desbloquear»: no hay una quinta acción que añadir.
- ≈ 0,5 d, no 1.

### `task_wf_41` — Aprobar y arrancar · forma corregida

- Endpoint nuevo `POST /plans/{id}/approve-and-start` con el gate ESTRICTO
  (`require_can_approve_plan`), **válido solo desde `pending_approval`** (409
  desde cualquier otro estado) — que es la recomendación aprobada por el operador.
- **La doble firma no se salta**: si `_resolve_first_signature_target` resuelve
  `pending_second_approval`, firma y PARA.
- Decisión de atomicidad: **no revertir la firma** si el arranque falla por
  proyecto pausado — deja el plan `approved` y «Empezar ejecución» ya cubre la
  recuperación. En una transacción única se perdería la firma.

### `task_wf_42` — Editor del spec · **falta el gate en el backend**

- La premisa se sostiene, pero el plan solo pide UI. `update_plan`
  (`plans.py:795`) acepta `specification` **en cualquier estado** con solo
  `require_tenant_member`. Hay que **rechazar con 409** fuera de
  `draft`/`pending_approval`.
- El PUT pisa con defaults: hay que reenviar el spec **completo**, no parcial.
- El `{"error":"dag_cycle"}` se pinta **en crudo** hoy (`lib/api.ts:74`).

### `task_wf_43` — @-menciones · premisa correcta, arreglo pequeño

- Único `yes` del recon. Las menciones tienen que ganar al heurístico. El
  desplegable de la UI **hardcodea los roles** (`page.tsx:77`) en vez de leer el
  equipo real.

### `task_wf_44` / `task_wf_45` — Replanificación · el ADR es **0132**

- **0132**, no 0131 (ya usado) ni 0106 (es un hueco, no reutilizar).
- Reencuadre importante: **no es diseñar la replanificación desde cero**. La
  mitad aditiva **ya funciona hoy y está sin gate** (PUT de spec sin guarda +
  sync-to-kanban admite `in_progress` + botón en la UI). El ADR gobierna un
  camino que ya existe. Arranca de ADR 0022:177-179, que ya lo preveía.
- Lo que el ADR tiene que decidir: (a) ediciones y **borrados** del spec sobre
  tareas ya materializadas — hoy el sync es solo aditivo y el Kanban **diverge en
  silencio** (`sync_to_kanban.py:180`); (b) tareas en vuelo (reutilizar
  `cancel_tasks_and_executions`, no inventar); (c) si un replan exige
  **re-aprobación**, que implicaría tocar `_TRANSITIONS`; (d) traza/versionado del
  spec, que no existe.
- `task_wf_45` **queda bloqueada** hasta ese ADR: sin decisión no hay alcance.

### Los ~30 hallazgos restantes

Verificados uno a uno y **refutados**, o de severidad baja. Están en los journals de los
workflows `wf_a8e61154-c98`, `wf_4af6bb33-5cd` y `wf_a0ebb673-bd0`.

### Lección de la tanda

Las tres regresiones tienen la misma forma: **generalizar una inferencia que solo valía
en un caso**. «Sin ejecución ⇒ el run no arrancó» (falso en la ruta humana), «sin
restricción ⇒ puede llamarlo todo» (falso sin `tool_specs`), «el lector honra la
cobertura» (falso si el cliente la escribe). Y en dos de los tres, **el test que escribí
bendecía la generalización** en vez de cuestionarla.
