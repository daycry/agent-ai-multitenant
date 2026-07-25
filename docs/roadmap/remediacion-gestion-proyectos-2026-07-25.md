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

- [ ] **Título**: propagar el `input_schema` de las tools MCP del proyecto hasta
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

#### `task_wf_11` — Un agente sin grants ve las tools que puede ejecutar

- [ ] **Título**: cuando `allowed_tools` está ausente (sin restricción por agente), anunciar
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

- [ ] **Título**: construir el `httpx.Auth` con `build_oauth_provider` cuando el servidor
      declara `auth_kind="oauth"` y pasarlo como `auth=` en `MCPToolRunner.connect`. El
      almacenamiento en Vault y el flujo interactivo ya existen; falta el último salto.
- **Hallazgo**: B-03 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `docker/agent-runtimes/agent-runtime/agent_runtime/mcp_tools.py:286-291`,
  `agent_runtime/__main__.py:298-318`
- **Tests**: unit con un provider falso verificando que `MCPClient.connect` recibe `auth`;
  regresión de que un servidor sin OAuth sigue conectando sin él.
- **Criterio de aceptación**: un servidor MCP remoto con OAuth conectado desde la UI funciona
  dentro de un run.

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

- [ ] _(parcial f51c4fa5 — falta el cruce allowlist↔esquema)_ **Título**: test de contrato que fije, para cualquier combinación de agente / proyecto /
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

- [ ] **Título**: bloque nuevo del preámbulo con el estado de los servidores MCP, presente
      solo cuando alguno falló, fenced como el resto de datos no confiables.
- **Hallazgo**: B-07 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `agent_runtime/__main__.py:350-425,692-751`
- **Tests**: unit del preámbulo con un servidor caído → contiene el aviso; sin fallos → el
  preámbulo no cambia.

---

## Ola 2 — Infra de ejecución (P0 · ~2,5 d)

#### `task_wf_20` — El test-runtime deja de contaminar el worktree

- [ ] **Título**: `HOME` del test/stack-runtime pasa a `/home/agent` (lo que ya declaran las
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

- [ ] **Título**: aplicar `pids_limit` y los perfiles seccomp/apparmor configurados al
      contenedor de test/stack, reutilizando la lógica de `isolation.py` en vez de duplicarla
      — extraer el tronco común a una función compartida.
- **Hallazgo**: C-02 (alto) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/test_runtime.py:921-945`,
  `apps/workers/src/workers/isolation.py:88-153`
- **Tests**: unit comparando los kwargs de ambos envelopes campo a campo (el test falla si uno
  gana una protección y el otro no).

#### `task_wf_22` — Los tests de aceptación salen del worker `default`

- [ ] **Título**: encolar `_run_task_tests` a la cola `test` con espera acotada, siguiendo el
      patrón que `stack_exec` ya aplica por riesgo de deadlock, en vez del `await` inline.
- **Hallazgo**: C-04 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/execution.py:846-911,1500-1510`,
  `apps/workers/src/workers/celery_client.py:148-160`
- **Tests**: unit de que la fase de tests despacha a la cola `test`; regresión de que un fallo
  de la fase de tests sigue sin romper un run ya terminado.

#### `task_wf_23` — El run-lock sobrevive al hard kill

- [x] _(hecho, ver git log)_ **Título**: derivar el TTL del lock del `execution_hard_time_limit_s` efectivo (más
      margen), no del presupuesto de contenedor, para que nunca caduque antes que el run.
- **Hallazgo**: C-05 (medio) · **Tiempo**: 0,25 d
- **Ficheros**: `apps/workers/src/workers/tasks/run_cycle.py:218-228`
- **Tests**: unit de que `lock_ttl > hard_time_limit` para todos los kinds.

#### `task_wf_24` — Un reintento no reinstala las dependencias

- [ ] **Título**: acotar el `clean` de `sync_to_head` para que no arrase los directorios de
      dependencias (o `clean -fd` sin `-x` más una limpieza explícita de artefactos de build),
      conservando el determinismo que motivó el `-x`.
- **Hallazgo**: C-06 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/git_repos.py:487-514`
- **Tests**: unit de que `vendor/` sobrevive a un `sync_to_head` y un fichero de build no.
- **Nota**: decidir si la exclusión se declara por runtime-template (el template sabe cuáles
  son sus directorios de dependencias) o por convención global. Preferible lo primero.

---

## Ola 3 — Ceguera operativa (P1 · ~5,5 d)

Casi todo el backend existe. Esta ola es mayoritariamente cableado y UI.

#### `task_wf_30` — Una cabecera de plan, no cuatro secciones sueltas

- [ ] **Título**: **un** endpoint `GET /plans/{id}/status` que devuelva progreso X/Y (sobre
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

#### `task_wf_32` — WebSocket de plan

- [ ] **Título**: stream `/ws/plan/{project_id}` con las transiciones de estado de plan,
      consumido por el tablero gerencial, siguiendo el patrón del stream de kanban.
- **Hallazgo**: D-03 (medio) · **Tiempo**: 0,75 d
- **Ficheros**: `apps/api-server/src/api_server/routers/ws.py:264-300`,
  `app/admin/board/page.tsx`
- **Tests**: integración del stream con auth por query param; e2e de que un cambio de estado
  se refleja sin recargar.

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

- [ ] **Título**: sección «Retrospectiva» en el detalle del plan cerrado (leyendo la memoria
      `project_shared` que ya escribe `plan_retro`) y vista del último standup.
- **Hallazgo**: D-05 (medio) · **Tiempo**: 0,75 d
- **Ficheros**: `apps/workers/src/workers/plan_retro.py`, `standup.py`, detalle del plan
- **Tests**: unit de render con y sin retro disponible.

#### `task_wf_35` — Configuración de proyecto sin agujeros

- [ ] **Título**: UI para `execution_budgets`, `guardrails_config` de proyecto,
      `budget_*`, `secrets_vault_id` y `human_task_review_mode` en el hub del proyecto.
      `allowed_domains` **ya tiene UI**: no tocar.
- **Hallazgo**: D-06 (medio) · **Tiempo**: 1 d
- **Ficheros**: `app/admin/projects/[id]/page.tsx` y una sección nueva
- **Tests**: e2e de guardar y releer cada campo.

#### `task_wf_36` — Una sola definición de plan completado

- [ ] **Título**: decidir y unificar. Recomendación: `completed` significa «validado por el
      humano» (lo que hace hoy el camino real), y el estado del PR se refleja aparte
      (`task_wf_31`). Entonces `transition_to_completed` se ajusta o se retira, sus tests se
      migran, y `CLAUDE.md` se corrige.
- **Hallazgo**: D-07 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/api-server/src/api_server/plan_progress.py:307-336`,
  `routers/review.py:509-528`, `tests/integration/test_plan_completion.py`, `CLAUDE.md`
- **Tests**: los de `test_plan_completion.py` migrados a la definición elegida.
- **Nota**: es un cambio de criterio de producto. Si el operador prefiere la definición
  estricta (PR mergeado), la tarea cambia de forma y necesita el webhook de merge.

---

## Ola 4 — Workflow del humano (P1 · ~6 d · parcialmente gated)

#### `task_wf_40` — Acciones humanas en la ficha de tarea

- [ ] **Título**: reintentar, reasignar con guía y desbloquear desde `TaskDetailSheet`,
      cableados a los endpoints de `task_lifecycle.py` que ya existen. Resuelve la tarea
      `blocked` que no está escalada sin desbloquear el plan entero.
- **Hallazgo**: A-09 (medio), F-6 · **Tiempo**: 1 d
- **Ficheros**: `components/tasks/task-detail-sheet.tsx`,
  `apps/api-server/src/api_server/routers/task_lifecycle.py:111-201`
- **Tests**: e2e de cada acción; regresión de RBAC (un miembro no-admin no ve las que exigen
  admin).

#### `task_wf_41` — «Aprobar y arrancar»

- [ ] **Título**: acción combinada que encadena las transiciones cuando la política del
      proyecto no exige doble firma. La cadena sigue pasando por los mismos gates; solo se
      ahorra clics.
- **Hallazgo**: A-08 (medio), F-5 · **Tiempo**: 0,5 d
- **Ficheros**: `plan-lifecycle-section.tsx:72-120`, `routers/plans.py`
- **Tests**: integración de que con doble firma configurada la acción **no** se ofrece.

#### `task_wf_42` — Editor del spec antes de aprobar

- [ ] **Título**: tabla editable de tareas (título, descripción, rol, complejidad,
      dependencias, criterios) sobre el `PUT /plans/{id}` existente, habilitada solo en
      `draft`/`pending_approval`. Incluye recuperación de un ciclo de DAG: el error 422 se
      traduce a un mensaje legible que señala las tareas del ciclo y deja editarlas.
- **Hallazgo**: A-07, A-11, F-2 · **Tiempo**: 2 d
- **Depende de**: `task_wf_02` (sin el arreglo de `summary`, el `PUT` falla)
- **Ficheros**: `app/admin/projects/[id]/plans/[planId]/`, `routers/plans.py:595-640`
- **Tests**: e2e de editar una dependencia y guardar; e2e de introducir un ciclo → mensaje
  legible, no JSON crudo.

#### `task_wf_43` — Las @-menciones tienen efecto (o se retiran)

- [ ] **Título**: parsear las menciones del mensaje del usuario y pasarlas a `pm_decide` como
      preferencia de especialistas. Si se decide no implementarlo, **retirar la afordancia**
      del compositor: una mención que no hace nada es peor que no tenerla.
- **Hallazgo**: A-10 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `chat/planning_llm.py:276-343`, `chat/page.tsx` (compositor)
- **Tests**: unit de que una mención explícita entra en la selección de especialistas.

#### `task_wf_44` — ADR de replanificación en caliente 🔒 GATED

- [ ] **Título**: redactar el ADR que decida cómo se replanifica un plan `in_progress`: qué
      pasa con las tareas en vuelo (¿se cancelan, se dejan terminar?), con la rama git y sus
      commits ya hechos, con el presupuesto consumido, y si la replanificación exige
      re-aprobación. **No implementar hasta que el operador acepte el ADR.**
- **Hallazgo**: A-06 (alto), F-1 · **Tiempo**: 0,5 d (solo el ADR)
- **Entregable**: `docs/05-architecture-decisions/01XX-replanificacion-en-caliente.md`,
  status `proposed`, con al menos dos opciones y una recomendación.

#### `task_wf_45` — Implementar la replanificación 🔒 GATED tras `task_wf_44`

- [ ] **Título**: según lo que decida el ADR. Estimación provisional, a revisar cuando el ADR
      esté aceptado.
- **Tiempo**: 2 d (estimación gruesa)

---

## Ola 5 — Guardrails, prompts y deuda (P2 · ~5 d)

#### `task_wf_50` — Cablear `pre_llm` y `post_llm`

- [ ] **Título**: invocar los dos hooks que faltan en el ciclo del runtime — el prompt antes
      de enviarlo al modelo y la respuesta al recibirla — cumpliendo el principio rector 10.
      Mantener el fail-open del pipeline y el baseline `warn`, para que cablearlos no cambie
      el comportamiento hasta que un tenant endurezca su política.
- **Hallazgo**: B-05 (alto) · **Tiempo**: 1 d · **Prioridad real**: P1
- **Ficheros**: `agent_runtime/graph.py`, `agent_runtime/guardrails.py:47-128`
- **Tests**: unit de que un prompt con contenido marcado dispara el hook; regresión de que con
  la política por defecto ningún run cambia de resultado.

#### `task_wf_51` — `update_plan` pasa por los guardrails

- [ ] **Título**: hacer que el scratchpad pase por `pre_tool`/`post_tool` como cualquier otra
      tool, sin perder su naturaleza de capacidad del loop.
- **Hallazgo**: B-06 (medio) · **Tiempo**: 0,25 d
- **Ficheros**: `agent_runtime/graph.py:950-971`
- **Tests**: unit de que un `update_plan` con contenido marcado se registra en los eventos de
  guardrail.

#### `task_wf_52` — Versionado de prompts

- [ ] **Título**: hash estable del conjunto de prompts del runtime (system prompts +
      preámbulos + nudges), expuesto por el runtime y propagado a
      `EvalRun.subject_prompt_version`. Enciende el dashboard de calidad por release que ya
      está construido.
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

- [ ] **Título**: batería directa del módulo del que dependen las guardas de convergencia, y
      retirar de sus tablas las tools que no existen en el runtime (`search_code`,
      `apply_patch`).
- **Hallazgo**: B-09 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `agent_runtime/tool_classification.py`, tests nuevos
- **Tests**: cobertura de las tres clases (research / producing / read-only) y del latch de
  `has_produced`.

#### `task_wf_54` — Resolver el ADR 0108 (canales de veredicto)

- [ ] **Título**: cerrar el ADR ya `proposed` y unificar los dos contratos de veredicto que
      hoy recibe un run de reviewer.
- **Hallazgo**: B-10 (medio) · **Tiempo**: 0,75 d
- **Ficheros**: `agent_runtime/providers.py:112-147`, `__main__.py:436-497`,
  `docs/05-architecture-decisions/0108-*.md`
- **Tests**: e2e de un run de review completo tras la unificación.

#### `task_wf_55` — Pinear los perfiles seccomp/apparmor

- [ ] **Título**: exportar `WORKERS_SECCOMP_PROFILE` y `WORKERS_APPARMOR_PROFILE` en los
      compose, verificando en dev que los runs siguen funcionando con el perfil endurecido.
- **Hallazgo**: C-03 (medio) · **Tiempo**: 0,5 d
- **Ficheros**: `docker/docker-compose*.yml`, `docker/.env.example`
- **Tests**: `tests/security/test_seccomp_profiles.py` + smoke de un run real en dev.
- **Nota**: gated para producción hasta validar en dev que ningún toolchain rompe.

#### `task_wf_56` — `pump.join()` acotado

- [ ] **Título**: timeout generoso con log de la anomalía, conservando el drenaje completo del
      caso normal.
- **Hallazgo**: C-07 (bajo) · **Tiempo**: 0,25 d
- **Ficheros**: `apps/workers/src/workers/container.py:196-212`

#### `task_wf_57` — Retirar el código muerto

- [ ] **Título**: eliminar `runtime_pool.py`, `TestcontainersMode` +
      `build_dind_proxy_run_kwargs` (con su script de demo y sus tests) y el
      `ReviewRuntimeManager` en memoria. Prioridad al camino de testcontainers: es el único
      que monta el socket Docker y no lo ejercita nada en producción.
- **Hallazgo**: C-08 (bajo) · **Tiempo**: 0,5 d
- **Tests**: suites verdes tras el borrado; `assert_no_docker_socket` sigue cubierto.

#### `task_wf_58` — Test-contrato reconciler ↔ dispatch

- [ ] **Título**: fijar con un test la promesa que hoy es un comentario: ambas rutas producen
      la misma transición sobre el mismo fixture.
- **Hallazgo**: C-09 (bajo) · **Tiempo**: 0,5 d
- **Ficheros**: `apps/workers/src/workers/maintenance/reconciler.py:319-438`,
  `apps/orchestrator/src/orchestrator/dispatch.py:371-537`

---

## Ola 6 — Palancas de calidad y coste (P1 · ~4,25 d)

No son bugs: el sistema funciona sin esto. Son las mejoras con mejor relación
impacto/esfuerzo de toda la auditoría. Detalle y evidencia en §7c del informe.

#### `task_wf_60` — El reviewer juzga el DIFF, no ficheros enteros

- [ ] **Título**: entregar al review-runtime el **diff del rango de la tarea** como artefacto
      primario del prompt, dejando `read_file` para el contexto de alrededor. Lo calcula el
      **worker** antes de lanzar el runtime —es quien tiene `data_root` y git— contra el HEAD
      de la rama del plan, porque en un primer run el trabajo aún no está commiteado. Se
      entrega ya hecho, igual que el `<test-report>`: **no hay que dar git al contenedor**.
      Reutilizar la maquinaria de `tasks/code_diff_task.py`, que ya hace exactamente esto para
      el visor de la UI.
- **Hallazgo**: M-2 · **Tiempo**: 1,5 d
- **Ficheros**: `apps/workers/src/workers/tasks/code_diff_task.py` (reutilizar),
  `apps/workers/src/workers/execution.py` (montar el diff en el `review_context`),
  `agent_runtime/providers.py:403-496` (`_review_messages`),
  `agent_runtime/review_harvest.py:79-110`
- **Tests**: unit de que el prompt de review contiene el diff y no el volcado completo; unit
  de que un cambio de 30 ficheros ya no se trunca a 15; e2e de un review real que rechaza
  citando líneas concretas del diff.
- **Criterio de aceptación**: en una tarea que toca 12 líneas de un fichero de 800, el prompt
  de review contiene las 12 líneas y su contexto, no las 800.
- **Nota**: conservar el harvest de ficheros como _fallback_ para runs sin worktree (análisis
  y diseño), donde hoy el review en prosa funciona bien.

#### `task_wf_61` — Veredicto por criterio

- [ ] **Título**: que el reviewer emita un resultado **estructurado** —cada criterio de
      aceptación con `pass`/`fail` y su evidencia— en vez de prosa con un único
      `<failed_criterion>`. Continuación natural del ADR 0087. Habilita tres cosas: la UI
      enseña al humano qué criterio falló, el `what_to_fix` tiene diana, y los resultados son
      medibles entre runs (alimenta el sistema de evals, hoy ciego — ver `task_wf_52`).
- **Hallazgo**: M-3 · **Tiempo**: 1 d · **Depende de**: `task_wf_54` (fusión de los dos
  canales de veredicto — no tiene sentido estructurar dos contratos que van a unificarse)
- **Ficheros**: `agent_runtime/providers.py:124-147`, `__main__.py:436-497`,
  `apps/workers/src/workers/execution.py` (`_apply_review_verdict`), UI del detalle de tarea
- **Tests**: unit del parseo del veredicto estructurado; regresión de que un veredicto en el
  formato antiguo sigue siendo aceptado durante la transición.

#### `task_wf_62` — Trazabilidad del runtime: digest en vez de etiqueta flotante

- [ ] **Título**: resolver la etiqueta `:v1` a **digest** en el lanzamiento y persistirlo en la
      fila de `executions`. Hoy reconstruir `agent-runtime-php-phpunit:v1` cambia en silencio
      lo que ejecuta toda tarea PHP, sin forma de saber qué build produjo un resultado ni de
      volver atrás.
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

- [ ] **Título**: que el agente de una tarea reciba, en el preámbulo, **qué hicieron las tareas
      de las que depende**: título y el `summary` que su agente entregó en `submit_result`
      (ya persistido en `executions.output`). Bloque nuevo del preámbulo, mismo patrón que
      `build_prior_failure_preamble` / `build_comments_preamble`, fenced como dato de terceros.
      Acotado: solo dependencias **directas** completadas, con tope de caracteres.
- **Hallazgo**: N-1 (feature) · **Tiempo**: 1 d
- **Ficheros**: `apps/orchestrator/src/orchestrator/dispatch.py` (`_assemble_run_request`),
  `agent_runtime/__main__.py:692-751` (`assemble_system_preamble`)
- **Tests**: unit de que una tarea con dos dependencias completadas recibe ambos resúmenes;
  unit de que sin dependencias el preámbulo no cambia; unit del fence.
- **Criterio de aceptación**: en un plan encadenado, el agente de la tarea 3 cita en su
  razonamiento el contrato que estableció la tarea 1 en vez de reinventarlo.
- **Por qué importa**: hoy `depends_on` solo se usa para reconciliar el DAG. Un plan largo no
  es un equipo trabajando sobre un diseño común, son N tareas aisladas compartiendo directorio.

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

- [ ] **Título**: semáforo de solo-lectura antes del botón de aprobar, componiendo resolvedores
      que **ya existen**: asignación por rol (`sync_to_kanban._resolve_assignment` en modo dry),
      desglose de coste, validación del DAG y cobertura de criterios de aceptación. Salida:
      «3 tareas sin agente elegible, 2 sin criterios, coste estimado 47 €, camino crítico de 8
      tareas sin paralelismo».
- **Hallazgo**: N-3 (feature) · **Tiempo**: 1,5 d
- **Depende de**: nada, pero **se potencia mucho con `task_wf_42`** (editor del spec): detectas
  el problema y lo corriges sin cambiar de pantalla.
- **Ficheros**: `routers/plans.py` (endpoint nuevo), `chat/sync_to_kanban.py:251-288` (extraer
  la resolución a un modo sin escritura), `chat/cost.py`, `chat/dag.py`,
  `plans/[planId]/plan-lifecycle-section.tsx`
- **Tests**: integración del preflight sobre un plan con rol inexistente → lo reporta sin
  escribir nada; unit de que no muta el plan.
- **Criterio de aceptación**: aprobar un plan con tareas sin agente deja de ser una sorpresa
  posterior.

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
