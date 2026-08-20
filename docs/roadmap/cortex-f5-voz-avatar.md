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

~~**Migración `0092`**: dos columnas de soporte al olvido (`last_recalled_at`, `recall_count`) más un índice parcial sobre `memory_entries`.~~

> **Reescrito el 2026-08-19 — ver la tarea D3.** Las columnas **no se escribieron**: el diseño pivotó a JSONB (`metadata_.recall_count` / `metadata_.last_recalled_at`) y ese pivote está cableado de punta a punta —productor `cortex.memory._bump_recall_counters`, consumidores en `cortex.forgetting`—, así que duplicarlo en columnas era abrir dos fuentes de verdad para el mismo dato sobre una tabla de 321 filas.
>
> Lo que sí se entregó es lo único del enunciado que seguía faltando y seguía siendo buena idea: el **índice parcial**, en la migración **`0142`** (`20260819_0142_cortex_forget_sweep_index.py`, `down_revision="0141_kb_embedding_canonical"`), con la forma que de verdad usa el barrido:
>
> `ix_memory_entries_cortex_sweep ON memory_entries (user_id, created_at) WHERE deleted_at IS NULL AND scope='private' AND type='episodic' AND (metadata->>'cortex')='true'`
>
> Ordena por `created_at`, no por `last_recalled_at`, porque es por `created_at` por lo que ordena el sweep. `down()`: `drop_index` (reversible; no hay columnas que quitar).

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

- [x] **A2. Mapeo puro `arousal → speed` (afecto → prosodia)**
  - Ficheros: `apps/api-server/src/api_server/cortex/voice_affect.py` (NUEVO — función pura `arousal_to_speed(arousal: float) -> float`, clamp determinista, p.ej. `speed = clamp(0.85 + 0.5*arousal, 0.85, 1.25)`); `tests/unit/test_cortex_voice_affect.py` (NUEVO).
  - TDD:
    1. Test: `arousal=0 → 0.85`, `arousal=1 → 1.25`, `arousal=0.5 → 1.10`, y clamp ante valores fuera de `[0,1]`. Falla (módulo no existe).
    2. Implementa la función pura (sin I/O, sin LLM — dinámica determinista y auditable, ADR 0075).
    3. Verde + commit `feat(cortex-f5): pure arousal→speed prosody mapping`.
  - Aceptación: función pura cubierta al 100%, monótona y clampeada; reutilizable por el WS.

### Fase B — WS de voz del córtex (GATED por F1 brain + F2 affect)

> Precondición de arranque: existe el cerebro reactivo del córtex de F1 (contrato esperado: una corrutina `respond(owner_principal, user_text) -> str` análoga a `_respond` del asistente, sobre `cortex.default_model`) y el lector de afecto de F2 (`read_affect(owner_user_id) -> AffectState` con `valence/arousal/dominance/mood_label/drives`). Si las firmas reales difieren, adaptar el adaptador (B2), nunca el protocolo del WS.

- [x] **B2. Adaptador de turno del córtex para voz (`respond` + lectura de afecto)**
  - Ficheros: `apps/api-server/src/api_server/cortex/voice_turn.py` (NUEVO — `cortex_respond(principal, user_text)` que invoca el cerebro F1, persiste el turno en `cortex_turns` igual que el chat de F1; `affect_frame(owner_user_id) -> dict` que lee `read_affect` de F2 y devuelve el payload `{type:'affect', ...}`); `tests/unit/test_cortex_voice_turn.py` (NUEVO).
  - TDD:
    1. Test con dobles: un cerebro scripted (estilo `ScriptedAssistantModel`) y un `read_affect` fake → `cortex_respond` devuelve el texto y `affect_frame` construye el dict con las 5 claves PAD+drives. Falla (módulo no existe).
    2. Implementa reutilizando el patrón de `_respond` de `routers/assistant_voice.py` pero contra el cerebro del córtex y `get_admin_sessionmaker` (tablas `cortex_*` tenant-less, **filtrando `owner_user_id` explícito en todo SQL**).
    3. Verde + commit.
  - Aceptación: `cortex_respond` persiste exactamente un turno por llamada (sin duplicados); `affect_frame` nunca lanza si Redis está vacío (fail-open: devuelve baseline neutro).

- [x] **B3. WS `/ws/owner/cortex/voice` (gate owner DB-authoritative + frame afectivo)**
  - Ficheros: `apps/api-server/src/api_server/routers/cortex_voice.py` (NUEVO, clona `assistant_voice.py`); registrar el router en `apps/api-server/src/api_server/main.py` (modificar: `include_router`); reutiliza `_resolve_principal` de `routers/ws.py`, `VoiceSession`/`VoiceTurn`, `HttpSpeechToText/HttpTextToSpeech`, settings `assistant_stt_url`/`assistant_tts_url`/`assistant_tts_default_voice`, el allowlist `_SUPPORTED_VOICES`, `_resolve_voice`, `_MAX_UTTERANCE_BYTES` y `_VoiceLoopState`.
  - Diferencias clave frente al asistente: (a) gate = `_is_db_system_owner(principal.user_id)` (clonar la verificación de `auth/deps.require_system_owner` en el accept del WS; un no-owner → close 1008); (b) `respond` = `cortex_respond`; (c) `synthesize` con `speed = arousal_to_speed(arousal_actual)` (lee el arousal vía `affect_frame` ANTES de sintetizar, o aplica el frame del turno previo — decisión: usar el afecto vigente en Redis al inicio de la síntesis); (d) tras `{type:'answer'}` y antes del binario, enviar `{type:'affect', ...}`.
  - TDD (integración, patrón de `test_cortex_f0_ownership.py` + WS de `routers/ws.py`):
    1. `tests/integration/test_cortex_voice_ws.py` (NUEVO): owner conecta → recibe `{type:'ready'}`; envía audio + `eot` (STT/TTS/brain fakeados vía override de dependencias) → recibe `transcript`, `answer`, `affect` (con las 5 claves), binario, `turn_end`. Falla (router no existe).
    2. **TEST CROSS-OWNER OBLIGATORIO** (regla dura BYPASSRLS): un usuario NO-owner (incluso con claim `own` forjado) → el WS cierra con 1008 y NO ejecuta turno. Asserta el close code.
    3. Test: voz no soportada en `{type:'config'}` se ignora (cae al allowlist), igual que el asistente.
    4. Implementa el router; verde + commit `feat(cortex-f5): owner cortex voice WS with affect frame`.
  - Aceptación: owner completa un turno de voz con frame afectivo; no-owner rechazado (1008) sin tocar el cerebro; el `speed` enviado a Kokoro coincide con `arousal_to_speed(arousal_de_Redis)`.

### Fase C — Avatar afectivo (frontend)

- [x] **C1. `affectToVisual` puro — PAD → color/expresión**
  - Ficheros: `apps/admin-panel/lib/cortex-affect.ts` (NUEVO — `affectToVisual({valence,arousal,dominance,mood_label})` → `{hue, swaySpeed, blinkRate, mouthBias, label}`, puro y determinista); `apps/admin-panel/lib/__tests__/cortex-affect.test.ts` (NUEVO, vitest).
  - TDD:
    1. Tests: valence alto → hue cálido/verde; valence bajo → azul/frío; arousal alto → sway más rápido y blink más frecuente; clamps. Falla.
    2. Implementa la función pura.
    3. `pnpm --filter admin-panel test` verde + commit.
  - Aceptación: mapeo puro cubierto; sin dependencias de React.

- [x] **C2. `CortexAvatarFace` — avatar modulado por afecto**
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
  - ⏳ **Pendiente (2026-07-30):** sólo falta el QA visual humano en navegador (ES+EN) que exige su aceptación; el test vitest `components/cortex/cortex-voice-call.test.tsx` (frame `affect` → estado + copy honesto, binario → audio) ya está escrito y en verde.
  - ↑ **Confirmado y acotado el 2026-08-20.** Esta nota era la única de las tres casillas «bloqueadas por un humano» que ya decía la verdad; se deja con el número real y con el procedimiento escrito, que es lo que faltaba.
    - **Ejecutado, no inferido:** `npx vitest run components/cortex lib/cortex-affect.test.ts app/admin/cortex` → **99 passed / 13 ficheros** en 90 s, con `cortex-voice-call.test.tsx` dentro (4 tests: el aviso honesto visible **antes** de cualquier frame y que no se va, el frame `affect` de valencia negativa actualizando el estado y rotulando el mood como simulado, un frame de telemetría anidado que **no** contamina el afecto de la llamada, y el binario del TTS reproduciéndose).
    - **Una mitad del «ES+EN» ya no es del humano**: el aviso honesto de la videollamada es bilingüe en el código (`SUBTITLE` en `components/cortex/cortex-voice-call.tsx`: «Afecto simulado (modelo computacional) — no son sentimientos reales» / «Simulated affect (computational model) — these are not real feelings») y la píldora de mood lleva su sufijo `simulado`/`simulated`. Lo que queda de mirar en pantalla es que **se vea** traducido, no descubrir si alguien lo tradujo.
    - **⏳ Lo que sigue siendo de un humano, y son cinco minutos:** que la **boca vaya sincronizada con el audio**, que la **cabeza se balancee**, que dos frases de valencia opuesta den **colores distintos** (el mapeo es `avatarStyleFromAffect`: valencia → tono 0-130 rojo→verde, activación → saturación 45-90 % y balanceo de 3,4 s a 1,8 s) y que el aviso honesto esté visible en **todos** los estados de la llamada — en ES y en EN. Procedimiento exacto, con URL, voces de cada idioma, criterio de pass/fail y los comandos de diagnóstico si falla: [`docs/03-guides/human-tests/cortex-f5-voz-avatar.md`](../03-guides/human-tests/cortex-f5-voz-avatar.md).
    - **NO se marca `[x]`**: la aceptación de esta casilla es literalmente un QA visual, y eso no lo puede acreditar un agente.

- [x] **C4. Página `app/admin/cortex` integra la videollamada (gated isSystemOwner)**
  - Ficheros: `apps/admin-panel/app/admin/cortex/page.tsx` (si F1 ya la creó, MODIFICAR para añadir el botón/tab "Videollamada" que monta `CortexVoiceCall`; si no existe aún por F1, crear el esqueleto gated). Gating con `useCurrentUser().isSystemOwner` (ya expuesto por F0); el grupo NAV "Córtex" `systemOwnerOnly` es de F1.
  - TDD:
    1. e2e Playwright `apps/admin-panel/e2e/cortex-voice.spec.ts` (NUEVO): un owner ve el control de videollamada; un no-owner no ve la superficie (count 0), espejando el patrón de `assistant-input` count 0 del asistente.
    2. Implementa el wiring.
    3. Verde + commit.
  - Aceptación: sólo el owner accede a la videollamada del córtex; no-owner sin superficie.
  - ✅ **Cerrada (2026-08-19): la e2e se ha EJECUTADO y está en verde.** `npx playwright test
e2e/cortex-voice.spec.ts` → **2 passed**, dos pasadas seguidas (una con compilación en frío de
    `/admin/cortex` bajo `next dev`, otra con el servidor caliente). Cubre las dos mitades de la
    aceptación: el owner ve `cortex-voice-toggle`, al pulsarlo aparece `cortex-voice-call` con su
    botón de conectar; un `tenant_admin` que NO es owner ve `cortex-no-access` y **count 0** de las
    dos superficies de voz.
  - **Por qué estaba abierta, y qué se ha aprendido:** la spec llevaba escrita desde F5 con la
    cabecera «WRITTEN, NOT run … PENDING HUMAN VERIFICATION». Una spec que nunca ha corrido no es
    cobertura sino una intención, y de hecho ya había fallado antes en silencio: la auditoría del
    2026-07-27 descubrió que apuntaba a un testid (`cortex-voice-card`) **que sólo existía en la
    propia spec** — no podía pasar nunca, y el rojo se lo iba a encontrar el operador. Esa nota se
    ha retirado del fichero y sustituido por el resultado real.
  - **No necesitó credenciales**: la spec siembra la sesión con `e2e/helpers/session.ts` (cookie
    `agentic_session` + CSRF de doble envío) e intercepta `GET /me` y
    `GET /owner/cortex/conversations` con `page.route`. Corre 100 % offline contra el panel; no
    toca la BD ni el api-server.
  - ⏳ **Lo que sigue siendo de un humano** (y no lo cierra esta casilla, sino la C3): el QA visual
    del avatar en navegador —cabeza y boca sincronizadas, color siguiendo al afecto, en ES y EN, con
    el disclaimer siempre visible— y la latencia real de Kokoro.

### Fase D — Bucle de olvido / mantenimiento (GATED por ADR 0077 accepted + kill-switch F4)

- [x] **D1. `compute_retention` puro — score de retención**
  - Ficheros: `apps/api-server/src/api_server/cortex/forgetting.py` (NUEVO — `compute_retention(entry) -> float` a partir de recencia (`last_recalled_at`), frecuencia (`recall_count`), intensidad emocional (`metadata_.emotion.intensity`) y antigüedad; y `is_protected(entry) -> bool` que devuelve True para `metadata_.kind ∈ {identity, owner_model}`); `tests/unit/test_cortex_forgetting.py` (NUEVO).
  - TDD:
    1. Tests: una memoria `kind=identity` SIEMPRE `is_protected` (nunca se olvida); una episódica vieja, no recordada y de baja intensidad → score bajo; recordada hace poco → score alto. Falla.
    2. Implementa puro y determinista (sin LLM).
    3. Verde + commit.
  - Aceptación: protección dura de `identity`/`owner_model` verificada por test; score monótono respecto a recencia/frecuencia/intensidad.

- [x] **D2. Tarea de mantenimiento (Celery beat) — soft-delete/consolidación reversible**
  - Ficheros: `apps/api-server/src/api_server/cortex/maintenance.py` (NUEVO — `run_forgetting_sweep(owner_user_id)` que: respeta el **kill-switch/budget de F4** (aborta si `cortex:budget:{owner}` agotado o circuit-breaker abierto); barre `memory_entries` del owner vía `get_admin_sessionmaker` filtrando `user_id=owner AND metadata_.cortex='true'` con SQL **explícito por owner**; aplica `deleted_at` (SOFT-delete, nunca DELETE físico) a las de score < umbral que NO estén protegidas; idempotente por `metadata_`); registrar el beat en la config Celery existente (mismo patrón que los workers actuales — localizar el `celery_client.py`/beat real antes de cablear).
  - TDD (integración):
    1. `tests/integration/test_cortex_forgetting_sweep.py` (NUEVO): inserta memorias del owner (una `identity`, una episódica olvidable) → corre el sweep → la episódica queda `deleted_at` no nulo, la `identity` intacta. **TEST CROSS-OWNER**: una memoria de OTRO usuario nunca es tocada por el sweep del owner. **TEST kill-switch**: con budget agotado, el sweep no borra nada.
    2. Implementa.
    3. Verde + commit `feat(cortex-f5): reversible forgetting maintenance loop (ADR 0077)`.
  - Aceptación: olvido reversible (sólo soft-delete), protección de identity/owner_model, aislamiento cross-owner, y respeto del kill-switch — los tres comprobados.

- [x] **D3. ~~Migración `0092` — columnas de soporte al olvido~~ → Migración `0142`: el índice parcial del barrido (las columnas NO se escriben)**
  - ✅ **Cerrada el 2026-08-19, con la decisión de producto tomada: opción (b).** El enunciado original queda **obsoleto** y se reescribe aquí; lo que se hizo en su lugar y por qué:
  - **Las dos columnas no se escriben.** El diseño pivotó a JSONB y el pivote está **entero y cableado**, no a medias: lo escribe `api_server.cortex.memory._bump_recall_counters` (`jsonb_set` anidado de `metadata_.recall_count` + `metadata_.last_recalled_at`, re-filtrado por `user_id`+`scope='private'`, cross-owner safe) y lo leen los dos consumidores de `api_server.cortex.forgetting` (`recency` desde `last_recalled_at`, `recall_frequency_factor` desde `recall_count`). Duplicarlo en columnas pedía backfill, reescribir productor y consumidores, y una ventana con **dos fuentes de verdad para el mismo dato** — sobre una tabla de **321 filas** (medidas contra la BD del stack el 2026-08-19: 230 vivas, 29 del córtex, 27 en el conjunto que barre el sweep, 3232 kB). Trabajo de simetría con el enunciado, cero de producto.
  - **El índice parcial sí, y no por completitud: porque hay un `Sort` que `_FORGET_SCAN_LIMIT = 500` no acota.** El plan real de `workers.cortex_maintenance._forget_low_retention` contra la BD viva era `Limit → Sort (created_at) → Index Scan using ix_memory_entries_user_id + Filter`. El `LIMIT` se aplica **después** de ordenar, así que cada pasada traía y ordenaba toda la memoria privada viva del owner —la del asistente incluida, porque el único índice aprovechable va por `user_id` a secas— para quedarse con 500 filas.
  - Ficheros: `apps/api-server/migrations/versions/20260819_0142_cortex_forget_sweep_index.py` (NUEVO, `down_revision="0141_kb_embedding_canonical"` — la cabeza REAL; el `0091` del enunciado tiene dos meses); índice también declarado en el modelo (`apps/api-server/src/api_server/db/memory.py`). Tests: `tests/integration/test_cortex_forget_sweep_index.py` (NUEVO) y `tests/unit/test_memory_models.py::test_cortex_forgetting_sweep_index_is_in_the_model_too` (NUEVO).
  - El índice: `ix_memory_entries_cortex_sweep ON memory_entries (user_id, created_at) WHERE deleted_at IS NULL AND scope='private' AND type='episodic' AND (metadata->>'cortex')='true'`. Las cuatro condiciones fijas van al predicado; `created_at` es la clave de orden. Sirve igual al otro barrido de la misma tarea (`_consolidate_similar`). Sin `CONCURRENTLY` a propósito: Alembic corre en transacción y con 3,2 MB la construcción es de milisegundos.
  - **Aceptación (verificada):** el test de integración afirma que el planificador **elige el índice**, que **desaparece el `Sort`** y que **no queda ninguna línea `Filter:`** (el predicado implica las cuatro condiciones); el round-trip está anclado a `0141_kb_embedding_canonical` **por su nombre**, nunca `downgrade("-1")` — ver `docs/03-guides/gotchas/alembic-round-trip-anclado-por-nombre.md`. El `downgrade` deja `memory_entries` exactamente como antes (el índice es lo único que la migración crea; ningún dato se toca).

### Fase E — Cierre

- [x] **E1. Documentación y honestidad**
  - Ficheros: `docs/05-architecture-decisions/0073-...md` (anotar que F5 reutiliza el modo voz), `docs/05-architecture-decisions/0077-...md` (mover a `accepted` tras aprobación), `CHANGELOG`/runbook si aplica. Verificar que el disclaimer "modelo computacional de afecto, no sentimientos reales" aparece en la UI de voz del córtex (C3) — criterio del CLAUDE.md.
  - Aceptación: ADR 0077 `accepted`; ADR 0073 enlaza F5; copy honesto presente; ES+EN cubiertos en voces y disclaimer.

- [ ] **E2. Suite completa verde + QA visual**
  - Aceptación observable: `pytest tests/unit tests/integration -q` (incluye todos los `test_cortex_voice_*`, `test_cortex_forgetting*`) en verde; `pnpm --filter admin-panel test` y la e2e `cortex-voice.spec.ts` en verde; QA visual humano del avatar en ES+EN (cabeza/boca/color) — incertidumbres del ADR 0073 cerradas con prueba real (latencia Kokoro y `speed` confirmados en la imagen pineada).
  - ⏳ **Pendiente (2026-07-30):** los tests F5 dirigidos están en verde uno a uno, pero faltan las tres cosas que sólo puede dar un humano con el stack: la suite completa `tests/unit tests/integration` de una pasada, la e2e Playwright `cortex-voice.spec.ts` ejecutada, y el QA visual del avatar en ES+EN con latencia Kokoro y `speed` medidos de verdad.
  - ↑ **Reescrita el 2026-08-20: de las tres «cosas que sólo puede dar un humano», DOS no lo son.** Y una de ellas ya la había cerrado la casilla C4 de este mismo plan hace un día, así que esta nota llevaba 24 h diciendo que faltaba algo que constaba hecho doce líneas más arriba. Punto por punto:
    - **1) «La suite completa `tests/unit tests/integration` de una pasada» NO es un test humano: es un job de CI, y desde el 2026-08-19 no se corre de una pasada ni en CI.** Los números están en `.github/workflows/ci.yml`: la suite entera pide **~72 min** en un runner dedicado y el job murió al **45 %** contra su reloj de 45 min, así que se partió en **4 shards + un job `gate`** (cross-tenant + migraciones). Pedirle a un humano en un portátil lo que CI necesita cinco jobs para dar es garantizar que la casilla no se cierre nunca. Medido aquí el 2026-08-20 con la máquina compartida por cinco agentes (y con OTRA suite de integración corriendo en paralelo, que es la que se lleva el IO de PostgreSQL): se lanzó `pytest tests/unit tests/integration -q` de una pasada y llegó al **53 %** — o sea `tests/unit` **completo, sus 4774 tests, con CERO `F` y CERO `E` en el log** (1 skip) — y ahí `tests/integration` avanzaba a ~5 tests/min: **~14 h** para los 4240 restantes. Se paró a propósito y se sustituyó por lo que sí da señal, que es lo que la propia casilla nombra entre paréntesis: **`tests/unit/test_cortex_voice_affect.py test_cortex_voice_prompt.py test_cortex_voice_turn.py test_cortex_forgetting.py` → 51 passed en 14 s**, más `tests/unit` de los afectivos de F2 (**118 passed en 15,5 s**) y el frontend del córtex (**99 passed / 13 ficheros**). Del lado de integración se lanzaron los 11 ficheros del córtex que tocan F2 y F5 (79 tests: `affect_cache`, `affect_store`, `affect_task`, `mind_endpoints`, `telemetry_ws`, `voice_ws`, `maintenance_task`, `forget_sweep_index`, `threads_migration`, `owner_rls`, `curiosity_loop`) y **los 41 que llegaron a correr pasaron todos** — entre ellos `test_cortex_voice_ws.py` **completo, sus 8 tests**, que es el fichero de integración de esta fase: cierre 1008 para un no-owner incluso con claim forjado, turno de voz completo con su frame `affect`, el `speed` exacto del arousal vivo de Redis, y el `mood_label` siguiendo el idioma de la voz. La corrida se paró ahí a propósito: en esta máquina cada test que levanta la app pide minutos, y el resto de ficheros (`maintenance_task`, `owner_rls`, `curiosity_loop`) no son de F5. **Quien tiene que dar el verde de la suite entera es CI, con sus cinco jobs, no un humano con un portátil.**
    - **2) La e2e `cortex-voice.spec.ts` ya está ejecutada** — lo cerró la casilla **C4** el 2026-08-19 con 2 passed en dos pasadas. Re-ejecutada hoy **seis veces**: **cada uno de sus 2 tests pasa al menos una vez**, pero la pasada completa es **inestable en local bajo `next dev`** — el reparto de fallos fue el primero, el primero, el segundo, los dos, el segundo, los dos. **No es un defecto del producto y está demostrado**: la captura que Playwright guarda al agotar el tiempo muestra la página **entera y correcta**, con el botón «Modo voz» a la vista, y el snapshot ARIA del instante de la aserción dice «Cargando…». La causa está medida: `next dev` compila `/admin/cortex` en la primera petición y eso costó **27,8 s** en esta máquina, contra un presupuesto de 30 s por test. CI no lo sufre porque hace `npm run build` y sirve con `E2E_WEBSERVER_CMD="npm run start"`. Queda documentado en [`gotchas/playwright-next-dev-compila-la-ruta-y-agota-el-test.md`](../03-guides/gotchas/playwright-next-dev-compila-la-ruta-y-agota-el-test.md) para que nadie vuelva a perder una hora en esto — y **no se ha subido ningún timeout**: acomodar una máquina saturada tocando la config que comparte CI sería debilitar la guarda a cambio de nada.
      - **Hoy no se puede reproducir la receta de CI en local, y no por F5**: `npm run build` aborta en `app/admin/projects/[id]/plans/[planId]/plan-spec-editor-section.tsx:102` con `Type error: Expected 1 arguments, but got 2` (`localSpecProblems(drafts, lang)`), que es trabajo **sin comitear de otro carril** en plena migración i18n de las pantallas de planes. En cuanto ese carril cierre, la comprobación definitiva es un comando: `NEXT_PUBLIC_API_URL=http://localhost:8001 npm run build && E2E_WEBSERVER_CMD="npm run start" npx playwright test e2e/cortex-voice.spec.ts`.
    - **3) El QA visual del avatar SÍ es humano, y es lo único que queda.** Boca sincronizada con el audio, cabeza balanceándose, colores distintos para valencias opuestas, aviso honesto visible en todos los estados, en ES y en EN, y la latencia real de Kokoro anotada. Procedimiento exacto —URL, voces de cada idioma, criterio de pass/fail, comandos de diagnóstico— en [`docs/03-guides/human-tests/cortex-f5-voz-avatar.md`](../03-guides/human-tests/cortex-f5-voz-avatar.md). Dos avisos para que no sorprendan: el `speed` que se manda a Kokoro **ya está probado contra el arousal vivo de Redis** (`tests/integration/test_cortex_voice_ws.py::test_ws_speed_es_exactamente_el_del_arousal_vivo_de_redis` y `…_baja_cuando_el_cortex_esta_apagado`), así que del ADR 0073 sólo queda por medir la **latencia**; y el tooltip de la píldora de mood («Etiqueta derivada del afecto simulado (ADR 0075)») sigue siendo sólo castellano — está en la allowlist de `scripts/check-i18n.mjs`, se ve al pasar el ratón y no es motivo de rojo del QA.
    - **Corrección de los comandos del enunciado, que no se pueden ejecutar tal cual**: este plan dice `pnpm --filter admin-panel test` y `pnpm --filter admin-panel exec playwright test`. **Este repo no usa pnpm**: el frontend es npm (`apps/admin-panel/package-lock.json`, `npm ci` en CI) y no hay `pnpm-workspace.yaml`. Los comandos reales son `npx vitest run <rutas>` (o `npm test`) y `npx playwright test e2e/cortex-voice.spec.ts`. También el nombre `test_cortex_forgetting*` sólo casa con `tests/unit/test_cortex_forgetting.py`: el barrido de olvido que la fase D2 prometía en `tests/integration/test_cortex_forgetting_sweep.py` vive en realidad en `tests/integration/test_cortex_maintenance_task.py` (11 tests: kill-switch, protección de `identity`, idempotencia, breaker) y `tests/integration/test_cortex_forget_sweep_index.py`.
    - **Esta casilla NO se marca `[x]`**: su aceptación incluye el QA visual, y eso no lo puede acreditar un agente.

---

## Crítica de restricciones (pasada manual)

- **Principio 1 (RLS) / BYPASSRLS:** B2/D2 acceden a tablas `cortex_*` y a `memory_entries` del owner por `get_admin_sessionmaker`. Mitigación: filtro `owner_user_id` / `user_id=owner` explícito en todo SQL + tests cross-owner obligatorios en B3 y D2.
- **ADR 0021 (catálogo cerrado):** ✅ STT/TTS son medios, no providers; el cerebro es `cortex.default_model`. Sin 5º provider.
- **Egress:** ✅ el WS de voz no abre egress nuevo (STT/TTS internos en `agentic-net`); el bucle de olvido es local (DB+Redis), sin red externa.
- **Honestidad afectiva:** ✅ disclaimer obligatorio en C3; el frame `affect` se rotula como modelo computacional.
- **Kill-switch / coste:** ✅ D2 aborta bajo `cortex:budget:{owner}` agotado o circuit-breaker abierto (F4). El WS de voz es interactivo (owner presente), no autónomo.
- **Reversibilidad:** ✅ olvido = soft-delete (`deleted_at`), nunca DELETE físico; migración `0142` con `downgrade()` que retira el índice (las columnas del enunciado `0092` no llegaron a existir — ver D3).

### Critical Files for Implementation

- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/routers/assistant_voice.py
- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/assistant/voice_session.py
- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/assistant/voice_clients.py
- c:/laragon/python/agent-ai-multitenant/apps/api-server/src/api_server/auth/deps.py
- c:/laragon/python/agent-ai-multitenant/apps/admin-panel/components/assistant/voice-call.tsx
