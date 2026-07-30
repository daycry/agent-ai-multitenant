---
title: "Córtex F5 — Voz y avatar afectivo del system_owner + olvido de memoria"
status: pending_human_validation
blocking_plan:
  - "cortex-system-owner.md F1 (córtex conversacional con memoria persistente) — IMPLEMENTADO"
  - "cortex-system-owner.md F2 (modelo afectivo PAD + Panel de Mente) — IMPLEMENTADO"
  - "cortex-system-owner.md F4 (curiosidad + kill-switch/budget) — IMPLEMENTADO"
  - "ADR 0073 (modo voz STT/TTS/avatar) — proposed"
  - "ADR 0075 (modelo afectivo computacional) — proposed"
  - "ADR 0077 (política de olvido) — debe pasar de proposed a accepted antes de F5.7"
started_at: 2026-06-24
completed_at: null
related_adrs: ["0073", "0075", "0077", "0021", "0070", "0074"]
docs_language: es
---

# Córtex F5 — Voz/avatar afectivo + olvido

> **Auditoría 2026-07-27 — las casillas de este plan se verificaron una a una
> contra el código.** Las marcadas `[x]` lo están con evidencia `file:line` y una
> segunda pasada adversarial; las que siguen sin marcar tienen su hueco concreto
> descrito en
> [`gaps-cortex-2026-07-27.md`](gaps-cortex-2026-07-27.md) (informe:
> [`auditoria-cortex-2026-07-27.md`](auditoria-cortex-2026-07-27.md)).
> Antes de implementar una casilla sin marcar, **abre el fichero**: la pasada
> adversarial dio al menos un falso positivo comprobado.

> **✅ IMPLEMENTADO Y DESPLEGADO** (verificado 2026-07-06 — auditoría de estado del roadmap). La
> frase "hoy SIN código: no existe `apps/api-server/src/api_server/cortex/`" es falsa desde el
> primer commit de F1 (2026-06-24): el directorio existe con 18+ ficheros. Código real de F5:
> `cortex/voice_affect.py`, `voice_turn.py`, router `cortex_voice.py` (WS `/ws/owner/cortex/voice`),
> `cortex/forgetting.py` (olvido, docstring propio "Córtex F4/F5") + worker `cortex_maintenance.py`
> (entrada `sched["cortex-maintenance"]`), con `test_cortex_voice_ws.py`/`test_cortex_voice_turn.py`/
> `test_cortex_forgetting.py` en verde, y frontend `cortex-voice-call.tsx`/`cortex-avatar.tsx`. Ver
> [cortex-identidad-real.md](cortex-identidad-real.md) para `recall_frequency` real (este plan lo
> dejaba a 1.0 hardcodeado). Checkboxes de tareas NO re-verificados línea a línea; el status
> refleja el veredicto agregado, no un cierre formal con changelog propio.

## Objetivo

Dar al córtex del `system_owner` una **videollamada con avatar afectivo**: un WS `/ws/owner/cortex/voice` (gate `require_system_owner`) que reutiliza `VoiceSession`/STT/TTS pero invoca el cerebro del córtex (F1) en vez del asistente, modula la voz Kokoro por `arousal` y emite un frame `{type:'affect'}` (de F2) que pinta color/expresión/sway del avatar; y cerrar el ciclo de memoria con un **bucle de olvido** reversible (ADR 0077).

## Arquitectura

El transporte y la orquestación por turno son **idénticos** al asistente (`routers/assistant_voice.py` + `assistant/voice_session.py`): se clona el patrón cambiando el gate (`require_system_owner` DB-authoritative en lugar de `require_assistant_access`) y el callable `respond` (cerebro del córtex de F1 en lugar de `run_assistant_turn`). La novedad afectiva vive en dos costuras puras y testeables: (a) `arousal → speed` modula la síntesis Kokoro vía el parámetro `speed` ya soportado por `/v1/audio/speech` (hoy `HttpTextToSpeech.synthesize` no lo expone — se añade aditivamente); (b) tras cada turno el WS lee el estado afectivo de Redis (`read_affect(owner)`, contrato de F2) y empuja un frame `{type:'affect', valence, arousal, dominance, mood_label, drives}` que el avatar mapea a color/expresión/parpadeo/sway. El bucle de olvido es un Celery beat de mantenimiento (puro `compute_retention` + aplicación SOFT-DELETE) con protección dura de `metadata_.kind ∈ {identity, owner_model}`, gobernado por el kill-switch de F4. **Sin 5º provider LLM (ADR 0021)**: STT/TTS son medios; el cerebro es `cortex.default_model`.

## Tablas nuevas

**Ninguna tabla nueva.** F5 reutiliza:

- `memory_entries` (de F1) — el olvido escribe `deleted_at` (soft-delete) y opcionalmente `metadata_.retention_score` / `metadata_.consolidated_into`.
- `cortex_affect_snapshots` (de F2) — leído para el frame afectivo; no se altera su esquema.

**Migración `0092`** (`apps/api-server/migrations/versions/20260623_0092_cortex_forgetting_columns.py`, encadena `down_revision="0091_system_owner_f0"`): añade, SI Y SÓLO SI F1/F2 no las crearon ya, columnas de soporte al olvido sobre `memory_entries` **de forma aditiva y reversible**:

- `last_recalled_at TIMESTAMPTZ NULL` (recencia de acceso para el retention score).
- `recall_count INTEGER NOT NULL server_default '0'` (frecuencia de acceso).
- Índice parcial `ix_memory_entries_cortex_active ON memory_entries (user_id, last_recalled_at) WHERE deleted_at IS NULL AND (metadata_->>'cortex') = 'true'` para el barrido del bucle.
- `down()`: `drop_index` + `drop_column` de ambas (reversible).

> Nota: si F1/F2 ya hubieran añadido estas columnas, `0092` queda como no-op documentado o se fusiona en su migración; **verificar el esquema real de `memory_entries` antes de escribir `0092`**.

## Endpoints / WS

- **WS `/ws/owner/cortex/voice`** (nuevo, `apps/api-server/src/api_server/routers/cortex_voice.py`, gate `require_system_owner` DB-authoritative). Clona el protocolo de `/ws/assistant/voice`:
  - cliente → frames binarios de audio + control JSON `{type:'config', voice}`, `{type:'reset'}`, `{type:'eot'}`.
  - servidor → `{type:'ready', voice}`, `{type:'transcript', text}`, `{type:'answer', text}`, `<binario audio>`, `{type:'affect', valence, arousal, dominance, mood_label, drives}`, `{type:'turn_end'}`.
- **Sin endpoints REST nuevos.** El Panel de Mente (`/owner/cortex/mind`, `/affect/timeseries`, WS telemetry) es de F2; F5 sólo añade el frame afectivo _dentro del WS de voz_.

---

## FASES → TAREAS

> Orden estricto. Cada tarea es TDD: test primero (rojo) → implementar (verde) → commit. Comandos de test: `pytest tests/unit/<archivo> -q` y `pytest -m integration tests/integration/<archivo> -q`; frontend `pnpm --filter admin-panel test` (vitest) y `pnpm --filter admin-panel exec playwright test` (e2e).

### Fase A — Modulación de voz por arousal (sustrato medios, NO gated por F1)

- [x] **A1. `HttpTextToSpeech.synthesize(speed=...)` — parámetro de velocidad Kokoro**
  - Ficheros: `apps/api-server/src/api_server/assistant/voice_clients.py` (modificar: añadir `speed: float = 1.0` a `synthesize` y al `Protocol TextToSpeech`, incluyéndolo en el payload `/v1/audio/speech` sólo si `!= 1.0`).
  - TDD:
    1. Escribe en `tests/unit/test_voice_session.py` un test nuevo `test_http_tts_forwards_speed_param`: monta `httpx.MockTransport`, llama `synthesize("hola", voice="ef_dora", speed=1.4)`, asserta que `body["speed"] == 1.4`. Falla (no existe el kwarg).
    2. Implementa el parámetro aditivo (default 1.0 → payload idéntico al actual cuando no se pasa, preservando los 2 tests existentes de TTS).
    3. `pytest tests/unit/test_voice_session.py -q` en verde.
    4. Commit `feat(cortex-f5): TTS speed param for arousal modulation`.
  - Aceptación: el test de regresión existente (`test_http_tts_posts_voice_and_returns_audio`) sigue verde y `speed` viaja sólo cuando difiere de 1.0.

- [ ] **A2. Mapeo puro `arousal → speed` (afecto → prosodia)**
  - Ficheros: `apps/api-server/src/api_server/cortex/voice_affect.py` (NUEVO — función pura `arousal_to_speed(arousal: float) -> float`, clamp determinista, p.ej. `speed = clamp(0.85 + 0.5*arousal, 0.85, 1.25)`); `tests/unit/test_cortex_voice_affect.py` (NUEVO).
  - TDD:
    1. Test: `arousal=0 → 0.85`, `arousal=1 → 1.25`, `arousal=0.5 → 1.10`, y clamp ante valores fuera de `[0,1]`. Falla (módulo no existe).
    2. Implementa la función pura (sin I/O, sin LLM — dinámica determinista y auditable, ADR 0075).
    3. Verde + commit `feat(cortex-f5): pure arousal→speed prosody mapping`.
  - Aceptación: función pura cubierta al 100%, monótona y clampeada; reutilizable por el WS.

### Fase B — WS de voz del córtex (GATED por F1 brain + F2 affect)

> Precondición de arranque: existe el cerebro reactivo del córtex de F1 (contrato esperado: una corrutina `respond(owner_principal, user_text) -> str` análoga a `_respond` del asistente, sobre `cortex.default_model`) y el lector de afecto de F2 (`read_affect(owner_user_id) -> AffectState` con `valence/arousal/dominance/mood_label/drives`). Si las firmas reales difieren, adaptar el adaptador (B2), nunca el protocolo del WS.

- [ ] **B2. Adaptador de turno del córtex para voz (`respond` + lectura de afecto)**
  - Ficheros: `apps/api-server/src/api_server/cortex/voice_turn.py` (NUEVO — `cortex_respond(principal, user_text)` que invoca el cerebro F1, persiste el turno en `cortex_turns` igual que el chat de F1; `affect_frame(owner_user_id) -> dict` que lee `read_affect` de F2 y devuelve el payload `{type:'affect', ...}`); `tests/unit/test_cortex_voice_turn.py` (NUEVO).
  - TDD:
    1. Test con dobles: un cerebro scripted (estilo `ScriptedAssistantModel`) y un `read_affect` fake → `cortex_respond` devuelve el texto y `affect_frame` construye el dict con las 5 claves PAD+drives. Falla (módulo no existe).
    2. Implementa reutilizando el patrón de `_respond` de `routers/assistant_voice.py` pero contra el cerebro del córtex y `get_admin_sessionmaker` (tablas `cortex_*` tenant-less, **filtrando `owner_user_id` explícito en todo SQL**).
    3. Verde + commit.
  - Aceptación: `cortex_respond` persiste exactamente un turno por llamada (sin duplicados); `affect_frame` nunca lanza si Redis está vacío (fail-open: devuelve baseline neutro).

- [ ] **B3. WS `/ws/owner/cortex/voice` (gate owner DB-authoritative + frame afectivo)**
  - Ficheros: `apps/api-server/src/api_server/routers/cortex_voice.py` (NUEVO, clona `assistant_voice.py`); registrar el router en `apps/api-server/src/api_server/main.py` (modificar: `include_router`); reutiliza `_resolve_principal` de `routers/ws.py`, `VoiceSession`/`VoiceTurn`, `HttpSpeechToText/HttpTextToSpeech`, settings `assistant_stt_url`/`assistant_tts_url`/`assistant_tts_default_voice`, el allowlist `_SUPPORTED_VOICES`, `_resolve_voice`, `_MAX_UTTERANCE_BYTES` y `_VoiceLoopState`.
  - Diferencias clave frente al asistente: (a) gate = `_is_db_system_owner(principal.user_id)` (clonar la verificación de `auth/deps.require_system_owner` en el accept del WS; un no-owner → close 1008); (b) `respond` = `cortex_respond`; (c) `synthesize` con `speed = arousal_to_speed(arousal_actual)` (lee el arousal vía `affect_frame` ANTES de sintetizar, o aplica el frame del turno previo — decisión: usar el afecto vigente en Redis al inicio de la síntesis); (d) tras `{type:'answer'}` y antes del binario, enviar `{type:'affect', ...}`.
  - TDD (integración, patrón de `test_cortex_f0_ownership.py` + WS de `routers/ws.py`):
    1. `tests/integration/test_cortex_voice_ws.py` (NUEVO): owner conecta → recibe `{type:'ready'}`; envía audio + `eot` (STT/TTS/brain fakeados vía override de dependencias) → recibe `transcript`, `answer`, `affect` (con las 5 claves), binario, `turn_end`. Falla (router no existe).
    2. **TEST CROSS-OWNER OBLIGATORIO** (regla dura BYPASSRLS): un usuario NO-owner (incluso con claim `own` forjado) → el WS cierra con 1008 y NO ejecuta turno. Asserta el close code.
    3. Test: voz no soportada en `{type:'config'}` se ignora (cae al allowlist), igual que el asistente.
    4. Implementa el router; verde + commit `feat(cortex-f5): owner cortex voice WS with affect frame`.
  - Aceptación: owner completa un turno de voz con frame afectivo; no-owner rechazado (1008) sin tocar el cerebro; el `speed` enviado a Kokoro coincide con `arousal_to_speed(arousal_de_Redis)`.

### Fase C — Avatar afectivo (frontend)

- [ ] **C1. `affectToVisual` puro — PAD → color/expresión**
  - Ficheros: `apps/admin-panel/lib/cortex-affect.ts` (NUEVO — `affectToVisual({valence,arousal,dominance,mood_label})` → `{hue, swaySpeed, blinkRate, mouthBias, label}`, puro y determinista); `apps/admin-panel/lib/__tests__/cortex-affect.test.ts` (NUEVO, vitest).
  - TDD:
    1. Tests: valence alto → hue cálido/verde; valence bajo → azul/frío; arousal alto → sway más rápido y blink más frecuente; clamps. Falla.
    2. Implementa la función pura.
    3. `pnpm --filter admin-panel test` verde + commit.
  - Aceptación: mapeo puro cubierto; sin dependencias de React.

- [ ] **C2. `CortexAvatarFace` — avatar modulado por afecto**
  - Ficheros: `apps/admin-panel/components/cortex/cortex-avatar-face.tsx` (NUEVO — extiende `avatar-face.tsx` aceptando además `affect?: {valence,arousal,dominance,mood_label}`; aplica `affectToVisual` a color del rostro, velocidad de `sway`, frecuencia de parpadeo y curvatura de la boca). Reutiliza la estructura SVG existente; **sin** motor 3D (mantener la deuda conocida de avatar SVG del ADR 0073).
  - TDD:
    1. Test de render (vitest + testing-library) en `apps/admin-panel/components/cortex/__tests__/cortex-avatar-face.test.tsx`: dado `affect` con valence negativo, el `style`/`fill` reflejan el hue frío; con `affect` ausente cae a neutro. Falla.
    2. Implementa el componente controlado.
    3. Verde + commit.
  - Aceptación: el avatar cambia color/sway observablemente con el afecto; degrada a neutro sin frame.

- [ ] **C3. `CortexVoiceCall` — videollamada del córtex con frame afectivo + copy honesto**
  - Ficheros: `apps/admin-panel/components/cortex/cortex-voice-call.tsx` (NUEVO, clona `components/assistant/voice-call.tsx`): conecta a `wsUrl("/ws/owner/cortex/voice")`, parsea el frame `{type:'affect'}` a estado React, lo pasa a `CortexAvatarFace`, mantiene el lip-sync por amplitud existente. Añade un **aviso honesto persistente** ("Modelo computacional de afecto — no son sentimientos reales ni consciencia") junto al avatar; voces M/F del allowlist ya existente.
  - TDD:
    1. Test vitest: al recibir un frame `{type:'affect', valence:-0.6, ...}` el componente actualiza el estado y renderiza el copy honesto; al recibir binario reproduce audio (mock). Falla.
    2. Implementa.
    3. Verde + commit.
  - Aceptación: e2e/manual en navegador (incertidumbre del ADR 0073, QA visual humano): cabeza + boca sincronizada + color que sigue al afecto, en ES y EN, con el disclaimer siempre visible.

- [ ] **C4. Página `app/admin/cortex` integra la videollamada (gated isSystemOwner)**
  - Ficheros: `apps/admin-panel/app/admin/cortex/page.tsx` (si F1 ya la creó, MODIFICAR para añadir el botón/tab "Videollamada" que monta `CortexVoiceCall`; si no existe aún por F1, crear el esqueleto gated). Gating con `useCurrentUser().isSystemOwner` (ya expuesto por F0); el grupo NAV "Córtex" `systemOwnerOnly` es de F1.
  - TDD:
    1. e2e Playwright `apps/admin-panel/e2e/cortex-voice.spec.ts` (NUEVO): un owner ve el control de videollamada; un no-owner no ve la superficie (count 0), espejando el patrón de `assistant-input` count 0 del asistente.
    2. Implementa el wiring.
    3. Verde + commit.
  - Aceptación: sólo el owner accede a la videollamada del córtex; no-owner sin superficie.

### Fase D — Bucle de olvido / mantenimiento (GATED por ADR 0077 accepted + kill-switch F4)

- [ ] **D1. `compute_retention` puro — score de retención**
  - Ficheros: `apps/api-server/src/api_server/cortex/forgetting.py` (NUEVO — `compute_retention(entry) -> float` a partir de recencia (`last_recalled_at`), frecuencia (`recall_count`), intensidad emocional (`metadata_.emotion.intensity`) y antigüedad; y `is_protected(entry) -> bool` que devuelve True para `metadata_.kind ∈ {identity, owner_model}`); `tests/unit/test_cortex_forgetting.py` (NUEVO).
  - TDD:
    1. Tests: una memoria `kind=identity` SIEMPRE `is_protected` (nunca se olvida); una episódica vieja, no recordada y de baja intensidad → score bajo; recordada hace poco → score alto. Falla.
    2. Implementa puro y determinista (sin LLM).
    3. Verde + commit.
  - Aceptación: protección dura de `identity`/`owner_model` verificada por test; score monótono respecto a recencia/frecuencia/intensidad.

- [ ] **D2. Tarea de mantenimiento (Celery beat) — soft-delete/consolidación reversible**
  - Ficheros: `apps/api-server/src/api_server/cortex/maintenance.py` (NUEVO — `run_forgetting_sweep(owner_user_id)` que: respeta el **kill-switch/budget de F4** (aborta si `cortex:budget:{owner}` agotado o circuit-breaker abierto); barre `memory_entries` del owner vía `get_admin_sessionmaker` filtrando `user_id=owner AND metadata_.cortex='true'` con SQL **explícito por owner**; aplica `deleted_at` (SOFT-delete, nunca DELETE físico) a las de score < umbral que NO estén protegidas; idempotente por `metadata_`); registrar el beat en la config Celery existente (mismo patrón que los workers actuales — localizar el `celery_client.py`/beat real antes de cablear).
  - TDD (integración):
    1. `tests/integration/test_cortex_forgetting_sweep.py` (NUEVO): inserta memorias del owner (una `identity`, una episódica olvidable) → corre el sweep → la episódica queda `deleted_at` no nulo, la `identity` intacta. **TEST CROSS-OWNER**: una memoria de OTRO usuario nunca es tocada por el sweep del owner. **TEST kill-switch**: con budget agotado, el sweep no borra nada.
    2. Implementa.
    3. Verde + commit `feat(cortex-f5): reversible forgetting maintenance loop (ADR 0077)`.
  - Aceptación: olvido reversible (sólo soft-delete), protección de identity/owner_model, aislamiento cross-owner, y respeto del kill-switch — los tres comprobados.

- [ ] **D3. Migración `0092` — columnas de soporte al olvido**
  - Ficheros: `apps/api-server/migrations/versions/20260623_0092_cortex_forgetting_columns.py` (NUEVO, `down_revision="0091_system_owner_f0"`); test `tests/migrations/test_0092_cortex_forgetting.py` (NUEVO, patrón de los tests de migración existentes).
  - TDD:
    1. Test de migración: `upgrade()` crea `last_recalled_at`, `recall_count` y el índice parcial; `downgrade()` los elimina (round-trip limpio). Falla (migración no existe).
    2. Escribe la migración aditiva + reversible (ver sección "Tablas nuevas"). **Antes**: confirmar que F1/F2 no añadieron ya estas columnas (si sí, fusionar/no-op).
    3. `alembic upgrade head` + `alembic downgrade -1` limpios; verde + commit.
  - Aceptación: migración reversible; `down()` deja `memory_entries` exactamente como antes.

### Fase E — Cierre

- [ ] **E1. Documentación y honestidad**
  - Ficheros: `docs/05-architecture-decisions/0073-...md` (anotar que F5 reutiliza el modo voz), `docs/05-architecture-decisions/0077-...md` (mover a `accepted` tras aprobación), `CHANGELOG`/runbook si aplica. Verificar que el disclaimer "modelo computacional de afecto, no sentimientos reales" aparece en la UI de voz del córtex (C3) — criterio del CLAUDE.md.
  - Aceptación: ADR 0077 `accepted`; ADR 0073 enlaza F5; copy honesto presente; ES+EN cubiertos en voces y disclaimer.

- [ ] **E2. Suite completa verde + QA visual**
  - Aceptación observable: `pytest tests/unit tests/integration -q` (incluye todos los `test_cortex_voice_*`, `test_cortex_forgetting*`) en verde; `pnpm --filter admin-panel test` y la e2e `cortex-voice.spec.ts` en verde; QA visual humano del avatar en ES+EN (cabeza/boca/color) — incertidumbres del ADR 0073 cerradas con prueba real (latencia Kokoro y `speed` confirmados en la imagen pineada).

---

## Crítica de restricciones (pasada manual)

- **Principio 1 (RLS) / BYPASSRLS:** B2/D2 acceden a tablas `cortex_*` y a `memory_entries` del owner por `get_admin_sessionmaker`. Mitigación: filtro `owner_user_id` / `user_id=owner` explícito en todo SQL + tests cross-owner obligatorios en B3 y D2.
- **ADR 0021 (catálogo cerrado):** ✅ STT/TTS son medios, no providers; el cerebro es `cortex.default_model`. Sin 5º provider.
- **Egress:** ✅ el WS de voz no abre egress nuevo (STT/TTS internos en `agentic-net`); el bucle de olvido es local (DB+Redis), sin red externa.
- **Honestidad afectiva:** ✅ disclaimer obligatorio en C3; el frame `affect` se rotula como modelo computacional.
- **Kill-switch / coste:** ✅ D2 aborta bajo `cortex:budget:{owner}` agotado o circuit-breaker abierto (F4). El WS de voz es interactivo (owner presente), no autónomo.
- **Reversibilidad:** ✅ olvido = soft-delete (`deleted_at`), nunca DELETE físico; migración `0092` con `down()`.

### Critical Files for Implementation

- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/routers/assistant_voice.py
- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/assistant/voice_session.py
- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/assistant/voice_clients.py
- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/auth/deps.py
- c:/laragon/python/agent-ai-multitenant/apps/admin-panel/components/assistant/voice-call.tsx
