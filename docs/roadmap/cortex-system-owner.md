---
title: "Córtex del Owner + rol system_owner"
type: plan
status: pending_human_validation
date: 2026-06-22
started_at: 2026-06-23
completed_at: null
author: claude-opus (workflow multi-agente: research + panel de diseño + jueces)
blocking_plan: null
related_adrs: ["0074", "0075", "0076", "0077", "0078", "0021", "0064", "0070", "0073", "0059", "0067"]
docs_language: es
---

# Córtex del Owner — una mente sintética para el `system_owner`

> **✅ IMPLEMENTADO Y DESPLEGADO** (verificado 2026-07-06 — auditoría de estado del roadmap). Este
> diseño maestro quedó en `pending_approval` mientras las 5 fases que describe (ver
> [cortex-fases.md](cortex-fases.md)) se implementaban por completo entre 2026-06-24 y 2026-07-06.
> Desviación real vs. diseño: la búsqueda web salió por el ADR 0067 (provider-agnóstica,
> egress-proxy), no por el ADR 0076 (WebSearch/WebFetch nativas de claude_sdk) que este documento
> recomendaba como "camino preferente". Los ADRs 0075-0078 siguen en `proposed` en sus propios
> ficheros pese a que el código que autorizan ya está en producción — pendiente de promoción
> formal, no de implementación.

> Diseño producido por un workflow multi-agente (research de 5 áreas → panel de 3 arquitecturas independientes → jueces → síntesis). El diseño ganador (score 90/100) es una **arquitectura cognitiva por capas** sobre el sustrato existente. La **crítica adversarial automática no llegó a correr** (límite de sesión); la suplo en la sección [Crítica de restricciones y seguridad](#crítica-de-restricciones-y-seguridad).

## Visión (del operador)

Además de `system_admin`, un rol **`system_owner`** (el dueño del despliegue) con un asistente personal que **NO controla proyectos**, sino una especie de **"córtex cerebral"**: tools de búsqueda en Internet, análisis y **razonamiento profundo**; **curioso**, con ganas de **aprender DE MÍ**; que **gestiona emociones y estados anímicos**; que genera **memoria** y una **identidad** propia; con **puntuaciones visibles** de sus emociones/estados. En resumen: **simular una mente humana**, no un LLM básico como el asistente actual.

## Principio rector del diseño

**Extender el sustrato existente, nunca duplicarlo.** El asistente actual ya aporta: loop de tool-use (`assistant/graph.py`), seam de modelo provider-agnóstico (`assistant/llm.py`, ADR 0021), memoria privada (`memory_entries` + `memorizer`), resolución de modelo con herencia, contexto RLS, y modo voz (ADR 0073). El córtex **reutiliza** todo eso y añade lo que hoy NO existe: **estado persistente entre turnos**, **estado afectivo + identidad que evolucionan**, y **bucles cognitivos de fondo**.

> **Honestidad de producto (no negociable):** la UI deja claro en todo momento que las puntuaciones son las de un **modelo computacional de afecto auditable**, no sentimientos reales ni consciencia.

## Diferencias decisivas con el asistente de tenant (verificadas en código)

1. **Hilo conversacional persistente** — hoy `graph.py` recibe `chat_history` del caller y el asistente arranca de cero cada POST (`routers/assistant.py:222-229`). El córtex persiste el hilo (ver hallazgo "asistente sin persistencia" en la auditoría).
2. **Estado afectivo + identidad** que **modula** la respuesta y **evoluciona** entre turnos.
3. **Bucles de fondo (Celery beat)**: reflexión, curiosidad, mantenimiento — corren cuando nadie habla.

---

## Arquitectura recomendada

### Rol `system_owner`

Moldeado sobre el patrón probado `is_system_admin` (claim JWT, `AuthPrincipal`, `require_*`, BYPASSRLS). **NO** es un valor del enum `UserRole` (que es por-membership y rompería RLS/SSO): es **columna booleana global** `users.is_system_owner` (NOT NULL, server_default false) con **UNIQUE parcial `WHERE is_system_owner`** (invariante singleton). Cadena completa: claim `own` en `encode_jwt`/`get_principal`; `AuthPrincipal.is_system_owner`; bootstrap del primer usuario como owner; propagación en login/MFA/SSO; **guardrail SSO** (no grantable por grupo, como `is_system_admin`); `is_system_owner` en `/me`; frontend `use-current-user` + `RoleGuard 'system_owner'` + grupo NAV **"Córtex"** `systemOwnerOnly`.

**Decisión de seguridad:** NO redefinir `require_system_admin` in-place a "admin OR owner" (sobrecarga un primitivo usado en todo endpoint admin). En su lugar, **dependencia compuesta `require_admin_or_owner`** donde el owner deba tocar superficies admin, y `require_system_owner` para el córtex. **Revocación más estricta**: las dependencias del córtex **verifican `is_system_owner` contra BD por request** (no solo el claim del token).

### Modelo afectivo — PAD + appraisal OCC + drives (ADR 0075)

Modelo dimensional **PAD** (Mehrabian-Russell) continuo, con etiqueta categórica **derivada solo para UI**. Tres capas con escalas temporales:

- **Emoción** (rápida, minutos): vector PAD `valence[-1,1]/arousal[0,1]/dominance[-1,1]` + `intensity[0,1]`, vive en **Redis**, decae hacia el baseline (homeostasis).
- **Mood/ánimo** (lento, horas-días): EMA de la emoción (`mood = α·mood + (1-α)·emoción`, α≈0.98); snapshots a PostgreSQL.
- **Drives homeostáticos** (el motor de la curiosidad): `curiosity`, `bonding`, `coherence`, `competence` ∈ [0,1]; decaen con el tiempo, se sacian con eventos; un drive bajo sesga el appraisal y **motiva el bucle de fondo**.

**Appraisal asíncrono** (decisión clave): el turno responde primero; un **Celery task posterior** (distilador afectivo, **Ollama local barato, sin egress**) puntúa el turno contra drives/identidad y emite `delta PAD + razón`; el motor **determinista** lo aplica. Saca coste/latencia del hot-path, **tolera Ollama caído (fail-open: delta=0)** y desacopla scoring de generación. La dinámica (decay/EWMA/clamps, piso/techo del mood, tasa de cambio del baseline) es **determinista, fuera del LLM, auditable**. El afecto **modula** (tono, `reasoning_effort`, expresión del avatar) pero **nunca bloquea** una acción ni la respuesta al owner.

### Identidad evolutiva (ADR 0074)

**Tabla singleton `cortex_identity`** con blob JSONB `identity_state` (evita la fragmentación de N-filas sin pagar columnas tipadas en MVP). Campos: `name` (el córtex se autonombra en onboarding), `core_values[]`, `traits` (Big-Five [0,1] que sesgan la dinámica afectiva), `affect_params`, `mood_baseline` (PAD set-point), `narrative` (autobiografía en 1ª persona), `relationship_model` (lo que cree saber del owner), `learning_goals[]`, `language` (es|en), `version`, `updated_by`. **Evolución** por onboarding (co-construcción) + **reflexión periódica** que reescribe la narrativa y deriva `traits`/`baseline` **clampeado**. El owner edita valores/nombre (override) pero **no la narrativa**. **Guardrail de auto-modificación**: bound por ciclo + diff versionado en `cortex_identity_history`.

### Memoria — EXTENDER `memory_entries`, cero segundo almacén (ADR 0077)

El córtex es **usuario singleton privado**: `scope='private'` + `user_id=owner` + `metadata_.cortex=true` (patrón exacto que ya usa el asistente). **Se respeta el CHECK SQL `type IN ('episodic','semantic')`**: los subtipos van en `metadata_.kind`.

- **Episódica emocional** (`type='episodic'`, `metadata_.emotion={valence,arousal,dominance,intensity,mood_label,appraisal_reason}`): de aquí salen las **puntuaciones** que ve el owner.
- **Semántica/autobiográfica** (`type='semantic'`, `metadata_.kind ∈ {identity, reflection, owner_model, learning}`).
- **Escritura** vía `persist_memory_candidates` **directo** (NO vía `workers/memorizer.py`, que enruta `episodic→project_shared` y rompería el scope private).
- **Recall asociativo real**: cablear `memorizer/recall.py` (BM25+vector+entity, RRF) reemplazando el MVP recencia-N; filtro estricto `user_id=owner`.
- **Olvido** (fase final, ADR 0077): `retention_score` → soft-delete/consolidación; **`kind ∈ {identity, owner_model}` NUNCA se auto-olvida**.

### Bucle cognitivo (ADR 0078)

- **Reactivo (por turno, async, mismo event loop httpx — no bridgear sync+`asyncio.run`)**: percepción (afecto de Redis + identidad + historia real de `cortex_turns`) → recall híbrido + `augment_system_prompt` extendido (identidad + mood que sesga el tono + memorias) → **deliberación** (razonamiento profundo: `claude_sdk run_agent` con `effort` modulado; degradación al loop clásico con `reasoning_effort` si no hay SDK) → acción → persistencia del turno. El appraisal+encode es **asíncrono** (Celery).
- **Bucles de fondo (Celery beat — NUEVO)**: (1) **Reflexión** (sintetiza insights, reescribe narrativa, deriva baseline clampeado, sacia `coherence`); (2) **Curiosidad** (si `curiosity` baja, elige tema de las entities que el owner menciona → WebSearch → digest → memoria(learning); inicia el tema en el próximo encuentro); (3) **Mantenimiento** (decay, retention, olvido, snapshots). **Kill-switch**: budget caps en Redis + circuit-breaker + (opcional) owner-approval gate para las primeras persecuciones autónomas. Idempotencia por `metadata_`.

### Razonamiento profundo y egress (ADR 0076)

Sale **exclusivamente de `claude_sdk`** en modo agéntico (`run_agent` con `effort high|xhigh|max`) y/o `reasoning_effort` (ADR 0070), respetando el catálogo cerrado (ADR 0021). **Precondición bloqueante verificada:** `run_agent` (`claude_agent.py:425-430`) llama `_build_options` **sin** `effort` (a diferencia de `complete`/`stream`) → sin un fix de una línea, el razonamiento profundo se ignora en silencio.

**Egress correcto:** el córtex corre en el **api-server (servicio confiable)**, cuya salida es directa por `agentic-net` — **distinta del egress-proxy del sandbox de agentes**. Camino recomendado: **WebSearch/WebFetch nativas del Claude Agent SDK** vía `allowed_tools` → Anthropic gestiona el fetch (**anti-SSRF gratis, sin abrir egress en runtimes, sin depender del ADR 0067**). Camino degradado (si el owner no usa claude_sdk): tool web propia desde el api-server con **anti-SSRF OBLIGATORIO** (un fetch sin anti-SSRF desde el api-server confiable alcanza Vault/red interna/metadata — **peor** que en sandbox) → requiere su propio ADR.

### Puntuaciones y visualización

Los scores **son** los campos PAD (todo float → todo graficable). Endpoints (gated `require_system_owner`): `GET /owner/cortex/mind` (snapshot), `/affect/timeseries`, `/episodes?emotion=`, `/identity/history`; WS `/ws/owner/cortex/telemetry`. **Panel de Mente** (componente nuevo): diales PAD en vivo, espacio PAD 2D con estela, gráfico de mood, mapa afectivo de episodios (hover = `appraisal_reason`), barras de drives, tarjeta de identidad (radar Big-Five + narrativa con preview Markdown), timeline de evolución, "lo que está aprendiendo". **Copy honesto** en todo el panel.

### Datos

**Reutilizar `memory_entries`** (sin tocar esquema). **Tablas nuevas** (tenant-less/singleton, `get_admin_sessionmaker` BYPASSRLS, aislamiento por `owner_user_id` explícito; migraciones reversibles a partir de **0090**, HEAD dev = 0089): `users.is_system_owner`, `cortex_conversations`, `cortex_turns`, `cortex_affect_snapshots`, `cortex_identity`, `cortex_identity_history`. **Redis**: `cortex:affect:{owner}` (decay lazy), `cortex:budget:{owner}`, pub/sub `cortex:telemetry:{owner}`. Resolución de modelo: clave `cortex.default_model` clonando `resolve_assistant_model` (validado ADR 0021 + reasoning_effort), `reasoning_effort` alto por defecto.

### UI / Voz / Avatar

Nueva superficie `app/admin/cortex` (gated `isSystemOwner`), grupo NAV "Córtex" separado. Layout dos columnas: **chat con hilo persistente** (réplica del patrón de `app/admin/assistant/page.tsx` + textarea con preview Markdown + indicador "pensando profundo") + **Panel de Mente**. **Voz/avatar**: reutilizar `VoiceSession` (inyectando el cerebro del córtex), `voice_clients.py`, `voice-call.tsx`, `avatar-face.tsx`; WS nuevo `/ws/owner/cortex/voice` clonando el gate de `assistant_voice.py` con `require_system_owner`. **Modulación afectiva**: frame `{type:'affect', valence, arousal, dominance, mood_label, drives}` → color/expresión/sway/parpadeo del avatar; voz Kokoro modulada por arousal.

---

## Decisiones abiertas (requieren visto bueno del owner)

| #   | Decisión                             | Recomendación                                                                                                                                                                                                        |
| --- | ------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Fuente del appraisal afectivo        | **Distilador asíncrono post-turno** (Ollama local, fail-open). Evita sycophancy del auto-scoring y la latencia/fragilidad del Ollama síncrono. Trade-off: el dial PAD se actualiza ~1-2s tras la respuesta.          |
| 2   | Estructura de la identidad           | **Tabla singleton con blob JSONB** desde F3. Promover a columnas tipadas (con ADR) solo si se necesitan índices/constraints.                                                                                         |
| 3   | Drives en el MVP afectivo            | **Incluir drives** desde la fase afectiva como estado observable; su capacidad de disparar comportamiento llega con el bucle de curiosidad. Sin ellos, "curioso por aprender de mí" es solo mood-flavored prompting. |
| 4   | Acceso del owner a superficies admin | **Dependencia compuesta `require_admin_or_owner`**; `require_system_admin` intacto (menor radio de impacto).                                                                                                         |
| 5   | Búsqueda web sin claude_sdk          | **MVP sin búsqueda web fuera de claude_sdk** (documentar claude_sdk como camino preferente). El camino degradado solo tras ADR con anti-SSRF obligatorio.                                                            |
| 6   | Revocación del claim `own`           | **Verificar `is_system_owner` contra BD por request** en las dependencias del córtex (es el principal singleton: tenant-less, BYPASSRLS, egress, memoria).                                                           |
| 7   | Política de olvido                   | **Olvido con protección explícita** (`identity`/`owner_model` nunca), fase final, tras ADR aprobado; todo reversible (soft-delete/merge, nunca delete físico).                                                       |

---

## Plan por fases

> Cada fase produce software funcional y testeable por sí misma. Las migraciones son reversibles (a partir de 0090). **Las fases que tocan egress/aislamiento/rol nuevo están GATED por sus ADRs (0074-0078) y el visto bueno del owner.**

### Fase 0 — Cimiento `shared-llm` + rol `system_owner` (ADR 0074)

- **Fix bloqueante**: añadir parámetro `effort` a `ClaudeAgentProvider.run_agent` y propagarlo a `_build_options` (`claude_agent.py:425-430`) + test.
- Migración `users.is_system_owner` (Boolean NOT NULL default false) + UNIQUE parcial `WHERE is_system_owner` (0089→0090).
- Claim `own` en `encode_jwt`/`get_principal`; `AuthPrincipal.is_system_owner`; `require_system_owner`; `require_admin_or_owner` (no redefinir `require_system_admin`).
- Bootstrap del primer usuario como owner; propagación login/MFA/SSO; guardrail SSO; `is_system_owner` en `/me`.
- Frontend: `use-current-user`, `RoleGuard 'system_owner'`, grupo NAV "Córtex" `systemOwnerOnly`.
- Tests cross-rol/gating (403), singleton, aislamiento.

### Fase 1 — Córtex conversacional con memoria persistente (mente útil mínima)

- Migración `cortex_conversations` + `cortex_turns` (hilo persistente).
- Grafo del córtex (extraer/reusar el turn-loop + topes; tools propias owner-scoped; `remember` capado 1/turno).
- Resolución `cortex.default_model` clonando `resolve_assistant_model`.
- Deliberación sobre `claude_sdk run_agent` con effort modulado + WebSearch/WebFetch nativas; degradación limpia a loop clásico/503.
- Cablear `memorizer.recall()` híbrido; escritura via `persist_memory_candidates` directo.
- Página `app/admin/cortex` con chat de hilo persistente, preview Markdown.
- Tests: propagación de effort, persistencia de turnos, recall no-cross-owner, cap de escritura, degradación sin SDK.

### Fase 2 — Modelo afectivo + Panel de Mente (ADR 0075)

- Migración `cortex_affect_snapshots` + estado vivo en Redis (decay lazy).
- Motor PAD determinista (decay/update/EWMA, clamps, drives) en código puro testeable.
- Distilador afectivo asíncrono (Celery, Ollama local) fail-open.
- Suite de calibración (interacciones canónicas → rangos PAD esperados).
- Endpoints `/mind`, `/affect/timeseries`, `/episodes`; WS `/ws/owner/cortex/telemetry`.
- Panel de Mente (diales PAD, espacio 2D, mood, mapa de episodios, drives); copy honesto.
- El mood sesga el system_prompt del siguiente turno.

### Fase 3 — Identidad evolutiva + reflexión (ADR 0074/0078)

- Migración `cortex_identity` + `cortex_identity_history`.
- Onboarding de identidad (autonombrado, co-construcción de valores).
- Bucle de reflexión (Celery beat): insights, reescribe narrativa, deriva traits/baseline clampeado + bound + diff versionado.
- UI: tarjeta de identidad (radar Big-Five, narrativa Markdown), timeline de evolución.

### Fase 4 — Curiosidad y pensamiento de fondo (ADR 0078)

- Bucle de curiosidad (drives bajos → metas desde entities → WebSearch→digest→memoria→satisfacción).
- Inicia temas en el siguiente encuentro.
- Budget caps + circuit-breaker + (opcional) owner-approval gate; métricas OTEL.
- UI: "lo que está aprendiendo".

### Fase 5 — Voz/avatar afectivo + olvido (ADR 0073/0077)

- WS `/ws/owner/cortex/voice` (gate `require_system_owner`, reutiliza `VoiceSession`).
- Frame `{type:'affect'}`; avatar modulado por PAD; voz Kokoro por arousal.
- Routing de effort para voz + audio de relleno.
- Bucle de mantenimiento: olvido por soft-delete/consolidación (protección de identity/owner_model); ADR de decay aprobado.

---

## ADRs necesarios (a crear como `proposed`)

- **ADR 0074** — Rol `system_owner` y Córtex: identidad global singleton, tablas tenant-less sobre BYPASSRLS y **excepción consciente al Principio 1 (RLS)**.
- **ADR 0075** — Modelo afectivo computacional (PAD + appraisal OCC + drives homeostáticos), dinámica determinista, appraisal asíncrono, filosofía de honestidad.
- **ADR 0076** — Razonamiento profundo sobre `claude_sdk` agéntico + egress confiable del api-server (WebSearch/WebFetch del SDK), eludiendo el ADR 0067.
- **ADR 0077** — Política de olvido y consolidación de la memoria del córtex (reversible, protección de identity/owner_model).
- **ADR 0078** — Bucles cognitivos de fondo (reflexión, curiosidad autónoma, gobierno de coste/egress, kill-switch).

---

## Crítica de restricciones y seguridad

> La crítica adversarial automática del workflow no llegó a ejecutarse (límite de sesión). Esta es mi pasada manual contra las restricciones del proyecto.

- **ADR 0021 (catálogo cerrado):** ✅ respetado — el razonamiento profundo sale de `claude_sdk`/`reasoning_effort`, sin 5º proveedor. El distilador afectivo usa **Ollama local** (ya en el catálogo). Riesgo: si `claude_sdk` no está disponible (imagen sin `WITH_CLAUDE`, ADR 0064), el córtex degrada a loop clásico — verificar, no asumir.
- **Principio 1 (RLS):** ⚠️ **excepción consciente** — las tablas del córtex son tenant-less (singleton del owner) sobre BYPASSRLS con aislamiento por `owner_user_id` explícito. **Exige ADR 0074 + test cross-owner.** Es el punto que más escrutinio merece.
- **Principio 2 (aislamiento/egress):** ✅ el egress del córtex es el del api-server confiable, no abre egress en runtimes; WebSearch del SDK evita SSRF. El camino degradado (web propia) queda **gated** por su ADR con anti-SSRF obligatorio.
- **Secretos solo en Vault:** ✅ claves del SDK/Ollama/web vía `build_llm_provider`/Vault. **Atención** al hallazgo de la auditoría: `ClaudeAgentProvider` escribe credenciales en `os.environ` global — **debe arreglarse antes** de que el córtex use claude_sdk intensivamente en el api-server.
- **Ética / honestidad:** la simulación de emociones **no debe engañar**. Copy honesto obligatorio ("modelo computacional de afecto, no sentimientos reales"). Riesgo de apego del usuario y de datos sensibles del owner en memoria → el owner es el único con acceso; aplicar la misma postura de no-filtrado de secretos a la memoria del córtex.
- **Sobre-ingeniería:** el plan por fases entrega valor en F1 (chat con memoria + razonamiento) antes de la maquinaria afectiva; los drives/curiosidad/olvido llegan después. Aceptable.

## Sinergia con la auditoría

- El córtex **resuelve** el hallazgo "asistente sin persistencia de conversación" desde F1.
- **Depende** de arreglar el hallazgo "credencial en `os.environ` global" (LLM providers) antes de usar claude_sdk en el api-server.
- Reutiliza el modo voz (ADR 0073) y su deuda conocida (avatar SVG, validación de voz, formato de audio) — conviene arreglar esos hallazgos de la zona voz antes de F5.

## Anexo — Grafo de memoria (decisión transversal, ver conversación 2026-06-22)

Sobre la propuesta de **grafo de conocimiento / relaciones** (a raíz de la pregunta sobre Obsidian): **NO** adoptar Obsidian como infraestructura (app de escritorio, sin multi-tenancy/RLS). **SÍ** una **capa de grafo nativa en PostgreSQL** (`entities` + `memory_edges` tipadas, tenant-scoped + RLS) sobre el entity-linking existente (ADR 0059), con visualización en el frontend. **Córtex-first** (owner único = bajo riesgo, alto valor; encaja con el Panel de Mente y la memoria episódica/autobiográfica), y **generalizar a la memoria multi-tenant de agentes después, con su propio ADR** (coste de migración + recall + rendimiento). Export Obsidian-compatible (markdown `[[wikilinks]]`) como extra **opt-in** para el owner. → merece **ADR propio** cuando se priorice.
