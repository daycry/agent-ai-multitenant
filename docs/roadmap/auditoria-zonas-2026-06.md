---
title: "Auditoría por zonas — junio 2026"
type: auditoria
status: informe
date: 2026-06-22
author: claude-opus (workflow multi-agente)
complementa: auditoria-produccion-2026-06.md
docs_language: es
---

# Auditoría por zonas — 2026-06

> Auditoría **complementaria** a `auditoria-produccion-2026-06.md` (que derivó los planes `prod-01..16`). Esta pasada se centra en las zonas que pidió el operador — **memoria, chats, ejecuciones, asistente, voz (STT/TTS/avatar), UI conversacional** — y barre el resto del sistema. Los hallazgos marcados _(solapa prod)_ ya están cubiertos por algún `prod-NN`.

## Metodología

- **Workflow multi-agente**: 14 zonas, un auditor por zona (Opus, effort `high`) leyendo el código real, **verificación adversarial de cada hallazgo** contra el código (sesgo a descartar falsos positivos) y una síntesis transversal.
- **67 hallazgos confirmados** tras verificación adversarial (de ~120 candidatos; el resto se descartó por falso positivo o malentendido del flujo).
- La severidad mostrada es la **ajustada por la verificación** (`orig→ajustada` cuando difiere).

> ⚠️ **Limitación de esta corrida**: el límite de sesión (reset 02:00 Europe/Madrid) cortó la **verificación adversarial** de 4 zonas (`auth-rbac-multitenancy`, `db-migraciones-dominio`, `frontend-plataforma`, `observabilidad-deploy`) y parte de `kb-rag-ingestion`, además de la **síntesis automática**. Para esas zonas se incluye el **resumen del auditor** (sin verificación individual), claramente marcado: **requieren una segunda pasada de verificación** antes de accionarse. La síntesis transversal y la priorización de abajo las he elaborado yo a partir de los hallazgos confirmados.

---

## Resumen ejecutivo — prioridades absolutas

| #   | Severidad           | Hallazgo                                                                                                                                                                                                                                                                   | Zona              |
| --- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| 1   | **critical**        | El worker de producción **no monta el worktree git** por tarea: el trabajo del agente corre en tmpfs efímero y **nunca se commitea** (Principios 4/5 incumplidos: sin rama de plan, sin diffs, sin PR). La maquinaria existe pero solo se invoca en `plan_runner` (demos). | Ejecuciones       |
| 2   | **critical**        | El **motor de guardrails no está cableado** en ninguna ruta de ejecución: `GuardrailPipeline`/`resolve_config` solo se usan en tests. El Principio 10 (guardrails en 4 puntos) es **decorativo**.                                                                          | Guardrails        |
| 3   | **critical→medium** | `ClaudeAgentProvider` escribe `ANTHROPIC_API_KEY`/`CLAUDE_CODE_OAUTH_TOKEN` en `os.environ` **global del proceso** api-server → **bleed de credencial cross-tenant** bajo concurrencia.                                                                                    | LLM providers     |
| 4   | **high**            | Cualquier miembro del tenant puede **falsificar mensajes `agent`** y el adjunto `finish_planning` que materializa un plan con tareas controladas por el usuario (suplantación + integridad de origen).                                                                     | Chats             |
| 5   | **high**            | Las **respuestas del equipo corren como `asyncio.create_task`** en proceso: se **pierden al reiniciar/redeploy** y nada las reintenta (chat colgado para siempre).                                                                                                         | Chats             |
| 6   | **high**            | **XSS** en el renderer markdown del chat: `href` de enlaces sin validar esquema → `javascript:`/`data:` en contenido de agente (LLM influenciable) roba el JWT de `localStorage`.                                                                                          | UI conversacional |
| 7   | **high**            | **No existe cancelación/timeout** señalizable de una ejecución en curso: cancelar en el Kanban no mata el contenedor ni revoca la task Celery (gasto LLM imparable).                                                                                                       | Ejecuciones       |
| 8   | **high**            | Tools `docker_command` (`run_pytest/lint/typecheck/build`) **inejecutables** en el runtime (sin Docker) pero anunciados como ejecutables; y los tools **custom nunca se anuncian al LLM** (spec sin `input_schema`).                                                       | Tools/MCP         |

---

## Temas transversales (síntesis)

1. **"Construido pero no cableado".** Patrón sistémico: mucha maquinaria existe como código puro, testeado en aislamiento, **sin consumidor en producción** → promesas decorativas. Casos: motor de guardrails (#2), `resolve_config` por capas, `stream()` de los 4 providers (código muerto), `embedding_model_id` por KB (metadato muerto), `max_output_bytes` de MCP (inalcanzable), `Skill.required_tools` (sin enforcement). **Acción:** auditar "implementado" vs "cableado/enforced" como criterio de cierre.
2. **Durabilidad/idempotencia ausente fuera del flujo feliz.** Respuestas de chat que se pierden (#5), auto-destilación de memoria sin dedup ante redelivery, asignación de tareas con carrera (sin CAS atómico), cancelación inexistente (#7). El equipo **conoce el patrón** (`supersede_running_executions`, dedup manual del asistente) pero **no lo aplica uniformemente**.
3. **Frontera de confianza difusa: la salida del LLM/usuario se trata como confiable.** XSS en markdown (#6), prompt-injection persistente vía memoria del asistente, falsificación de mensajes agent (#4), secretos arrastrados del `steps_log` a memoria persistente sin scrubbing.
4. **Aislamiento/egress con grietas (Principio 2).** Credencial en env global (#3), SSRF en http tools (sin filtro de IP privada/metadata), allowlist del egress-proxy global vs per-proyecto (http_request roto en el stack endurecido), `python_function` con red/FS del host heredados.
5. **Divergencia arquitectura documentada vs real.** `apps/personal-assistant`, `apps/memorizer`, `apps/web-app` son **solo `.gitkeep`** (la lógica vive embebida en api-server); dos vocabularios de estado de tarea (`awaiting_human` vs enum de dominio); `claude_sdk` corre el chat de planning **degradado** (sin reasoning) por incompatibilidad de turnos.
6. **El artefacto central no se entrega en el camino real (#1).** Es el hallazgo más grave para la promesa de producto: la plataforma agéntica produce stdout pero **ningún commit versionado**.

---

## Hallazgos por zona (verificados)

### 1 · Memoria de los agentes — 6 confirmados

> Bien estructurada (scopes con CHECK, owner-pointers server-side, recall híbrido BM25+vector+entity con RRF, sin fuga cross-tenant evidente). Riesgos en idempotencia y crecimiento.

- **[medium] (bug) Auto-destilación sin dedup ni idempotencia** — `workers/memorizer.py:368` `persist_memory_candidates` sin guard; `_persist_routed` commitea por-grupo en transacciones separadas; sin UNIQUE `(source_execution_id, content)`. Redelivery/reintento de Celery (`task_acks_late=True` global) **duplica memorias** (otra llamada LLM + filas repetidas) y deja estado parcial. El path manual SÍ deduplica; el automático no. → UNIQUE parcial con `ON CONFLICT DO NOTHING` o guard por `source_execution_id`; una sola transacción.
- **[low] (gap) Sin expiración/olvido ni cap de crecimiento por tenant** _(solapa prod)_ — no hay tarea beat de retención; único borrado es soft-delete manual. Crecimiento monótono de `memory_entries`. _(Nota: la afirmación del auditor de "sin índice HNSW" es **falsa** — la migración 0020 sí crea `ix_memory_entries_embedding_hnsw`.)_ → política de retención (TTL episodic, conservar semantic) + beat de olvido.
- **[medium] (security) Secretos arrastrados del `steps_log`/output a memoria persistente** _(solapa prod)_ — `memorizer/distillation.py:95-112` vierte `execution.output[:1500]` + últimos steps al destilador sin scrubbing; el `content` se persiste y se reinyecta a futuros agentes (potencialmente `team_shared`/`global`). → aplicar el scrubber de secretos antes de destilar/persistir.
- **[low] (risk) `recall()` query de detalle sin `tenant_id` explícito** — `memorizer/recall.py:398-401` `WHERE id = ANY(:ids)` depende solo de RLS (las 3 queries candidatas sí filtran tenant). Defensa-en-profundidad rota en ese punto. → añadir `AND tenant_id = :tenant_id AND deleted_at IS NULL`.
- **[low] (tech_debt) Dimensión de embedding (768) sin validación runtime** — cambiar el modelo de embedding rompe el recall vectorial en silencio. → validar longitud del vector y registrar el modelo en metadata.
- **[low] (bad_decision) `apps/memorizer/` vacío (.gitkeep)** — diverge de CLAUDE.md (memorizer como app). → retirar el placeholder y documentar (módulo en api-server + tarea en workers) o promover a app.

### 2 · Chats y conversaciones — 9 confirmados

> Bien estructurado (WS por conversación tenant-aware, materialización por adjunto, errores best-effort con timeouts) pero con agujeros de integridad, durabilidad, concurrencia y coste.

- **[high] (security) Falsificación de mensajes `agent` + adjunto `finish_planning`** — `conversations.py:275-339` acepta `author_kind` del payload; el validador no comprueba derecho a hablar como agente. Un user puede POSTear un `agent`+`finish_planning` con `specification.tasks` y `plans.py` lo materializa. → forzar `author_kind='user'` en escrituras humanas; marcar el origen server-side del adjunto que materializa.
- **[high] (risk) Respuestas como `asyncio.create_task` en proceso** — `responder.py:838-867` `schedule_reply`; se pierden al reiniciar, sin reintento; el polling del front se queda en "pensando" indefinidamente. → mover a Celery (durabilidad/reintentos) o marcador `reply_pending` + barrido de arranque.
- **[medium] (bug) Sin control de concurrencia por conversación** — mensajes rápidos lanzan respondedores solapados (sin lock); pueden producir 2 adjuntos `finish_planning` contradictorios. → lock Redis `SET NX conv:{id}:replying` o cola por conversación.
- **[medium] (gap) Planning multi-agente: N+3 llamadas LLM/turno sin gate de presupuesto ni registro de tokens** _(solapa prod-07)_ — `responder._stream_planning` no consulta `budget_pause` ni acumula coste. → pre-check de budget + contabilizar tokens como en executions.
- **[low] (bad_decision) El replay del WS reenvía TODO el backlog** (`ws.py:153` `last_id='0'`) y choca con la paginación REST; hasta 10k entradas de golpe. → tailear desde "ahora"/último id conocido por el cliente.
- **[medium] (tech_debt) `claude_sdk` corre el planning degradado (sin reasoning)** — `responder.py:787-795` quita el effort para evitar "max turns (8)"; el proveedor preferido (ADR 0021) razona peor en el chat. → adaptar el adaptador de planning (subir turn budget o una sola llamada por turno).
- **[low] (tech_debt) `asyncio.run` por cada llamada LLM del planning** dentro de un worker thread → crea/destruye event loop por paso. → reescribir `LLMPlanningModel` async y correr el sub-grafo en el loop del endpoint.
- **[low] (risk) Desajuste de orden:** el responder ordena por `created_at`, la paginación por `id` (UUIDv7). → unificar a `Message.id`.
- **[low] (tech_debt) `_draft_from_conversation` carga TODOS los mensajes agent sin LIMIT** — `plans.py:127-151`. → filtrar en SQL por `attachments @> '[{"intent":"finish_planning"}]'` + `ORDER BY id DESC LIMIT 1`.

### 3 · Ejecuciones, orquestación y workers — 5 confirmados

> Máquina de estados y eventos bien construidos en sus piezas puras, pero el **camino de ejecución real está desconectado de la promesa de producto**.

- **[critical] (gap) El worker de producción no monta el worktree git** — `workers/execution.py:557-569` construye `ContainerSpec` **sin** `workspace_host_path` (→ `/workspace` es tmpfs efímero); bare-repos/worktrees/commit solo se invocan en `orchestrator/plan_runner.py` (demos). Sin rama de plan, sin diffs, sin PR. → cablear `BareRepoManager.ensure_repo` + `WorktreeManager.add` + `commit_task(...CommitTrailers...)` en `conduct_execution`.
- **[medium] (risk) Carrera de asignación sin lock/CAS** — `dispatch.py:364-396` lee y marca `in_progress` sin `FOR UPDATE`/CAS; dos réplicas pueden duplicar ejecuciones. El patrón CAS ya existe para planes (`_on_task_done`) pero no se aplica aquí. → `UPDATE ... WHERE id=:id AND status='ready' RETURNING id`.
- **[high] (gap) Sin cancelación/timeout de un run en curso** — `executions.py` solo expone GET; cancelar una tarea no revoca la task Celery ni mata el contenedor. → endpoint cancel que revoque la task, mate el contenedor por label y finalice el row `cancelled`.
- **[medium] (bug) El PUT de tasks no valida transiciones** — `tasks.py:231-307` escribe el status directo sin `task_state_machine.transition_task_status`; permite `done→in_progress`, `cancelled→ready`. → enrutar por la máquina de estados, mapear error a 409.
- **[low] (tech_debt) Dos vocabularios de estado** (`task_lifecycle` legacy `awaiting_human` vs enum de dominio). → unificar en el enum de dominio.

### 4 · Asistente personal (actual) — 9 confirmados

> Núcleo razonablemente bien construido (aislamiento RLS correcto, tool-calling provider-agnóstico real, caps defensivos). Los hallazgos top son funcionales/arquitectónicos, **no fugas cross-tenant**.

- **[high] (gap) El asistente no persiste la conversación** — `routers/assistant.py:222-229` pasa `chat_history=[{user, message_actual}]` fijo; sin `conversation_id` ni hilo. No puede responder follow-ups: la UX conversacional es ilusoria. → persistir el hilo (tabla tenant-scoped, RLS) y cargar los últimos N turnos. _(El córtex resuelve esto desde su F1.)_
- **[high] (bad_decision) Tool-results reinyectados como mensaje `system`** en vez de pares `assistant.tool_call`+`role:tool` — `llm.py:84-92`; protocolo OpenAI roto para copilot/azure/ollama → el modelo re-emite tool_calls (de ahí el dedup defensivo). → construir el historial con el patrón canónico.
- **[medium] (risk) `POST /assistant/chat` sin rate-limiting ni pre-check de presupuesto** _(solapa prod-07)_ — hasta ~7 llamadas LLM/request. → throttle por (tenant,user) + pre-check de budget.
- **[medium] (tech_debt) `apps/personal-assistant/` vacío** — el loop LLM corre en el api-server (compite por el event loop). → ADR: extraer a worker o asumir/documentar que vive en api-server.
- **[medium] (risk) `resolve_assistant_model` no re-valida el `model_id` en runtime** — un modelo retirado llega al provider y solo falla como 502 (sin fallback al platform_default). → revalidar barato (pure-DB) el override y caer al default.
- **[low] (bad_decision) El path `claude_sdk` con tools descarta SIEMPRE el texto del turno** (`claude_agent.py:270-292` `content=''`) → pierde el preámbulo/razonamiento. → conservarlo como `last_content`.
- **[low] (bug) El cap por-tool cuenta rondas previas, no la actual** — `graph.py:178-184`; en una sola ronda pueden colarse N escrituras (rompe la garantía "1 `remember_about_me`/turno"). → contar también las `kept` de la ronda en curso.
- **[low] (tech_debt) `remember_about_me` no acota nº/longitud de `tags`** — solo el prompt frena la basura. → validar/recortar en `remember_user_fact`/schema.
- **[low] (security) `augment_system_prompt` concatena hechos sin sanitizar** → prompt-injection persistente (acotado al propio usuario). → inyectar como datos delimitados, no como texto de sistema.

### 5 · Modo voz: STT, TTS y avatar — 9 confirmados

> F1 (push-to-talk, por turno, sin streaming) implementado de punta a punta y respeta ADR 0021 (sin 5º provider). Bug de formato de audio, validaciones ausentes y lazo bloqueante.

- **[high] (bug) Desajuste de formato de audio: el cliente envía WebM/Opus pero el servidor declara `audio/wav`** — `voice-call.tsx:197-208` `MediaRecorder` sin mimeType; `assistant_voice.py:111` `content_type="audio/wav"` hardcodeado, sin extensión. **El ciclo de voz probablemente no funciona end-to-end** en navegador real (los unit tests pasan con bytes fake). → propagar el MIME real / capturar PCM16 16kHz (AudioWorklet, como dice el ADR) + test de integración con blob no-wav.
- **[low] (security) El `voice` id no se valida en servidor** pese a que UI/ADR dicen que sí — `assistant_voice.py:171-174` lo reenvía verbatim a Kokoro. → allowlist server-side de las 6 voces.
- **[low] (bad_decision) El procesamiento del turno bloquea el lazo de recepción** (`assistant_voice.py:219-223`): sin barge-in, sin reset, desconexión no detectada. → tarea concurrente cancelable + leer el socket en paralelo (patrón `_pump`).
- **[low] (risk) Sin límite de sesiones de voz concurrentes** (STT+TTS+LLM por sesión) → agotamiento en single-machine. → semáforo/cuota global y por tenant.
- **[low] (risk) Logout no cierra una videollamada de voz abierta** _(solapa prod)_ — sesión validada solo en el accept. → re-validar la sesión Redis por turno.
- **[medium] (gap) Cero cobertura de test del endpoint WS de voz** (handshake, auth/RLS cross-tenant 1008, eot, error frames) — el criterio F1 del ADR exige justo eso. → tests de integración del WS.
- **[low] (gap) Fallos STT/TTS no-listos se colapsan en error genérico y filtran la excepción cruda** (host interno). → mapear 503 "iniciándose", no interpolar `exc`.
- **[low] (security) JWT en query string del WS** queda en logs de proxy _(solapa prod, patrón global de WS)_. → subprotocolo WS o cookie; scrubbing entretanto.
- **[low] (tech_debt) Avatar SVG/amplitud** vs requisito "firme" (TalkingHead.js + visemas) del ADR 0073 — deuda esperada F3/F4; rastrearla antes de cerrar el ADR.

### 6 · UI en modo conversacional — 6 confirmados

- **[high] (security) XSS en el renderer markdown del chat** — `lib/plan-draft-md.tsx:63-78` pone `href` sin validar esquema; React no bloquea `javascript:`. Contenido de agente (LLM influenciable por el composer/@mentions) → robo del JWT en `localStorage`. → allowlist `^(https?:|mailto:)` antes de renderizar (aplica también al preview del composer).
- **[medium] (gap) No hay reconexión de WebSocket** (`lib/ws.ts:58-76` solo `close()` en cleanup); tras idle-timeout/sleep el feed deja de actualizarse (el modo voz queda muerto). → reconexión con backoff+jitter + refetch al reconectar.
- **[medium] (gap) El chat del asistente NO renderiza markdown** (`assistant/page.tsx:242` texto plano) pese al requisito global. → reutilizar el renderer endurecido.
- **[medium] (bug) `createMediaElementSource` por turno sobre el mismo AudioContext** → fuga de nodos/RAF; el audio puede enmudecer tras la 1ª respuesta. → AudioContext efímero por reproducción, cancelar RAF, desconectar en `onended`.
- **[low] (gap) El composer del chat no maneja error de envío** (sin `onError`): un POST fallido se pierde en silencio. → `onError` inline + conservar borrador + dedup en `onSuccess`.
- **[low] (risk) El indicador "pensando" depende solo de que el último mensaje sea del usuario** → falsos negativos/positivos (un `system` intermedio lo apaga). → derivarlo de una señal explícita del backend (evento `agent.thinking`).

### 7 · Proveedores LLM y shared-llm — 8 confirmados

> Abstracción bien diseñada (Protocol, `_acquire` por-llamada, errores tipados, secretos de Vault, dos vías de resolución). Problemas en paridad y un env global.

- **[medium] (security) `ClaudeAgentProvider` escribe credenciales en `os.environ` global** — `claude_agent.py:61-64`; alcanzable desde el chat de equipo y el asistente en el api-server multi-tenant → bleed cross-tenant. → no mutar env de proceso: env scoped al subproceso CLI, o serializar con lock + set/restore, o **prohibir claude_sdk en el api-server** (solo worker/runtime aislado).
- **[medium] (bug) El streaming SSE descarta los `tool_calls`** — `_openai_compat.py:111-131` solo lee `delta.content`; `StreamChunk` ni tiene campo `tool_calls`. → acumular `delta.tool_calls` por índice o rechazar `tools` no vacío en `stream()`.
- **[medium] (gap) `stream()` implementado y testeado en los 4 providers pero NUNCA consumido** — el "streaming" del planning es publicación paso-a-paso por Redis, no token-streaming. → cablear a un SSE real con test de integración, o degradar `stream()` a opcional.
- **[medium] (bug) `parse_chat_completion` sin guarda ante cuerpos 200 malformados** (`choices` vacío) — `_openai_compat.py:79` → IndexError/KeyError no tipado escapa de la capa LLM. → envolver en `ProviderError` (sin volcar el body).
- **[medium] (gap) Sin reintentos/backoff ante 429/5xx transitorios** _(solapa prod-07)_ — un único POST; no lee `Retry-After`. → retry con backoff+jitter en la capa compartida.
- **[medium] (bad_decision) Paridad rota: `claude_sdk` ignora `max_tokens`/`temperature`; `azure_foundry` ignora el `model` por-llamada** — divergencia observable entre providers. → mapear donde se pueda y documentar.
- **[low] (gap) El streaming OpenAI-compat nunca emite `usage`** (no manda `stream_options.include_usage`) → sin accounting de tokens en streaming.
- **[low] (risk) Degradación silenciosa de Vault a "sin credencial"** — `factory.py:182-188` `secret={}` y continúa → 401/403 confuso en vez de 503 claro. → distinguir "sin path" de "Vault falló".

### 8 · Guardrails y egress — 8 confirmados

> El motor (shared-guardrails) es código puro de alta calidad **pero sin enforcement real en ejecución**. La superficie de egress viva carece de anti-SSRF.

- **[critical] (gap) El motor de guardrails no está cableado en ninguna ruta de ejecución** _(solapa prod)_ — `GuardrailPipeline` solo en `guardrails/planning.py`, cuyas funciones no tienen llamador (solo tests). El runtime lo dice explícito ("lands in Plan 11"). PII/secret-leak/prompt-injection/allowed_domains **no se ejecutan**. → cablear en el bucle del agent-runtime (alrededor de cada model-call y tool-call) + `resolve_config`.
- **[high] (gap) La resolución por capas plataforma→tenant→proyecto (`resolve_config`) nunca se usa** _(solapa prod)_ — pipelines con dicts hard-coded; sin tabla que persista config por tenant/proyecto. → persistencia RLS-scoped + sembrar baseline locked.
- **[low] (security) `http_request`/`http_endpoint` sin anti-SSRF** — solo compara hostname contra allowlist; no resuelve DNS ni rechaza IP privada/loopback/metadata (169.254.169.254) → SSRF/DNS-rebinding. → resolver y validar TODAS las IPs + pinning.
- **[medium] (bad_decision) Allowlist del egress-proxy global/estática** (solo hosts LLM) contradice la allowlist por-proyecto de `http_request` → http_request a APIs externas roto en el stack endurecido. → generar el filtro por unión de allowlists por-proyecto, o quitar la afirmación engañosa.
- **[medium] (gap) Acciones `redact`/`transform`/`escalate_to_human` son advisory** — el pipeline es puro, ningún host aplica side-effects; ni BLOCK se honra hoy. → host de side-effects (redacción real, pausa de validación humana integrada con aprobaciones).
- **[low] (risk) `egress_proxy_url` vacío por defecto + red no-internal = egress libre** — footgun de configuración sin guardia. → validación de arranque (red no-internal ⇒ proxy obligatorio).
- **[low] (risk) `tinyproxy` permite patrón amplio `*.azure-api.net`** a cualquier host allowlisted, CONNECT opaco sin SNI pinning. → estrechar el patrón al recurso del operador.
- **[low] (gap) ADR 0067 (web-search/fetch) en `proposed`** — ausencia correcta (no abrir egress sin ADR accepted). → al aprobarse, exigir anti-SSRF + allowlist efectiva + enforcement real como prerequisitos.

### 9 · Tools, MCP y skills — 6 confirmados

- **[medium] (bug) `docker_command` (`run_pytest/lint/typecheck/build`) inejecutable en el runtime** — `from_env()` falla siempre (el runtime no tiene Docker) pero se anuncia como ejecutable y asignable → toda la familia de tests vía docker_command está muerta. → ejecutar en el worker (que tiene DOCKER_HOST→proxy) y delegar, o marcar NO-wired.
- **[high] (bug) Los tools custom (`http_endpoint`/`python_function`/`docker_command`) nunca se anuncian al LLM** — `_tool_to_spec` omite `input_schema` → `build_model_tool_schemas` los salta. La feature estrella de tools custom (Plan 05/06.18) es inerte. → incluir `input_schema`+`description` en el spec.
- **[medium] (security) `python_function` en subprocess con red/FS del host heredados** — puede leer `AGENT_TASK_SPEC` con el token interno y alcanzar la red interna. → ejecutar en contenedor efímero (network none, cap-drop ALL) o restringir a privileged + no escribir el token en ficheros legibles.
- **[medium] (gap) `MCPServerConfig.max_output_bytes` inalcanzable** — ni el schema (extra=forbid) ni los conversores lo propagan → tope fijo 64 KiB. → añadirlo al modelo y propagarlo, o borrar el campo.
- **[low] (gap) `Skill.required_tools` nunca se valida/auto-asigna** → un prompt de skill puede pedir tools ausentes (fallos "unknown tool"). → validar/avisar en `set_agent_skills`.
- **[low] (tech_debt) El docstring de `render_command` afirma escapar shell pero pasa strings verbatim** — la seguridad real viene del paso argv. → corregir el docstring o `shlex.quote` real.

### 10 · Knowledge Bases, RAG e ingestión — 1 confirmado (resto sin verificar)

- **[medium] (bug) `embedding_model_id` por KB es metadato muerto** — `ingestion.py:84-87` construye `OllamaEmbedder()` sin model_id; nadie lee `kb.embedding_model_id` (aunque `update_kb` lo protege con 409). El default escrito (`nomic-embed-text-v1.5`) ni coincide con el real (`nomic-embed-text`). → leer el modelo de la KB en ingesta+query, o eliminar el campo de la API/UI.

---

## Zonas con verificación PENDIENTE (cortadas por el límite de sesión)

> Hallazgos detectados por el auditor pero **sin verificación adversarial individual** ni desglose confirmado. Tratar como **leads a verificar**, no como hechos confirmados, antes de accionar.

### Auth, RBAC y multi-tenancy

Base sólida (dos roles de BD, RLS con `set_config` por sesión, tenant_id nunca confiado del cliente). Leads: la **revocación de membership desde `/admin` NO mata sesiones Redis** (a diferencia de SCIM); el rol **`system_operator` definido pero sin gate que lo aplique**; el **mapeo grupo-IdP→rol cableado en UI pero nunca invocado en login**; **no existe endpoint para promover `system_admin`** (relevante para añadir `system_owner` — ver plan del córtex). Varios solapan prod-09/prod-14.

### BD, migraciones y dominio

Esquema maduro (89 revisiones lineales, single head, RLS con WITH CHECK desde 0008, UUIDv7). Leads: **incoherencia de configuración FTS entre `memory_entries` y `chunks`** (bug de relevancia + seq-scan en RAG); **tablas sin FORCE RLS** y políticas que castean `tenant_id::text`; **dimensión pgvector fija (768)** vs `embedding_model_id` configurable; **FKs a `agents` sin índice de soporte**. El hueco de junctions sin tenant_id/RLS ya está en prod-14.

### Frontend plataforma (admin-panel / web-app)

Next.js maduro (StateBlock/error-boundary consistentes, markdown XSS-safe con `skipHtml` en la mayoría, mermaid `securityLevel:strict`, ~90 specs e2e, casi cero `any`). Leads: **subida de documentos a KB omite `X-Tenant-Id`** (misrouting cross-tenant para superadmins vía picker); **sin manejo global de expiración de sesión (401)**; la **página de review usa un modelo de auth/transport divergente y probablemente roto**; **i18n EN en su mayoría sin implementar** (prod-16); **`apps/web-app` completamente vacío**.

### Observabilidad, ops y despliegue

Cimientos correctos (OTEL tracing idempotente, logging JSON con masking PII, backup/restore fail-closed con restore per-tenant). Leads: el **data-plane prometido (OTLP/Tempo, Loki, métricas de app) esencialmente ausente** (solo trazas a consola + métricas de host); **el endpoint que recibe las alertas no existe** (eslabón roto crítico); el **watchdog es código muerto que además viola el aislamiento de socket Docker**; **fugas de secretos en el restore per-tenant**.

---

## Próximos pasos

1. **Segunda pasada de verificación** de las 4 zonas pendientes (cuando reset el límite) para confirmar/descartar sus leads.
2. **Implementación priorizada** (ver `prioridad-codigo-limpio-mantenible`): empezar por bugs confirmados + quick wins de bajo riesgo con TDD y refactor oportunista. Los **GATED** (egress, cambios de aislamiento, nuevo rol) van por ADR + luz verde del operador.
3. Cruzar con `prod-01..16` para no duplicar (los _(solapa prod)_).
4. Cada fix entra como tarea trazable; los hallazgos `critical` (#1 worktree, #2 guardrails) son cambios arquitectónicos que probablemente merecen su propio mini-plan.
