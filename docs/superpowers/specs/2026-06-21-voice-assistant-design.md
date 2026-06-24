---
title: Modo Voz (Videoconferencia) del Asistente — Diseño
date: 2026-06-21
status: draft
author: Claude (workflow voice-assistant-design, verificado contra el repo)
---

> Diseño generado por un workflow de investigación (STT/TTS/avatar/arquitectura/integración) y verificado contra el código real. Requisitos firmes del operador: UX estilo Meet/Teams; avatar con movimiento de cabeza + lip-sync; selector de voz M/F; el LLM es el provider YA asignado al asistente (provider-agnóstico, sin 5º provider — ADR 0021).

Tengo confirmación completa del enrutado Caddy (single-origin: `handle_path /api/*` retira el prefijo → `api-server:8000`, con WS upgrade transparente). Tengo todo lo necesario. Produzco el documento de diseño.

# Diseño del Modo Voz ("Videoconferencia") del Asistente

> Verificado contra el código real del repo. Donde el JSON de investigación afirmaba algo que el codebase confirma, lo doy por bueno; donde había imprecisión, lo corrijo. Marco con **[INCERTIDUMBRE]** lo que no he podido verificar y debe validarse con una prueba real antes de comprometerlo.

---

## 1. STACK ELEGIDO

### Decisión por defecto (local-first, CPU-capaz, GPU opt-in)

| Capa               | Elección por defecto                                                                                                    | Imagen / librería concreta                                                                                | Licencia                       |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ------------------------------ |
| **STT**            | **WhisperLive** (backend faster-whisper / CTranslate2) como servicio Docker interno, con streaming WS y VAD server-side | `ghcr.io/collabora/whisperlive-cpu` (y `-gpu` para CUDA/TensorRT). **Pin de tag exacto**, no `:latest`    | MIT (código + modelos Whisper) |
| **TTS**            | **Kokoro-82M vía Kokoro-FastAPI** como servicio Docker interno, API OpenAI-compatible + streaming por chunks            | `ghcr.io/remsky/kokoro-fastapi-cpu` (y `-gpu` cu12x). **Pin de tag**                                      | Apache-2.0 (modelo Y wrapper)  |
| **Avatar**         | **TalkingHead.js** (browser-side, three.js) sobre un GLB de licencia comercial-amigable                                 | npm/`<script>` `met4citizen/talkinghead` + `three` ~0.180; avatar **VRoid/MPFB/CC0** (NO Ready Player Me) | MIT (lib); avatar según asset  |
| **VAD / barge-in** | **Silero VAD en el navegador** vía `@ricky0123/vad-web`                                                                 | npm `@ricky0123/vad-web` (ONNX Runtime Web + AudioWorklet)                                                | MIT                            |
| **Captura micro**  | **AudioWorklet → PCM16 16 kHz mono** (NO MediaRecorder)                                                                 | Web Audio API nativa                                                                                      | —                              |
| **Transporte**     | **WebSocket binario full-duplex** `/api/assistant/voice` en el api-server                                               | FastAPI/Starlette (ya en el stack)                                                                        | MIT                            |

### Justificación

- **STT — WhisperLive sobre faster-whisper puro.** `faster-whisper` no hace streaming nativo (transcribe ficheros/chunks); reimplementar el ventaneo + estabilización de hipótesis (LocalAgreement) es esfuerzo y bugs. WhisperLive ya da parciales por WS + VAD + ES/EN multilingüe, en CPU (`int8`, modelo `small`/`base`) o GPU (`float16`, `medium`/`large`). Encaja con el principio de **aislar runtimes**: no engordamos la imagen del api-server con CTranslate2 + modelos. El plan B (faster-whisper embebido) queda documentado pero descartado por defecto.
- **TTS — Kokoro-FastAPI.** Único candidato con **licencia limpia para producto comercial** (Apache-2.0, modelo incluido), ES+EN nativos, ligero (82M, corre en CPU), streaming por chunks y API OpenAI-style trivial de integrar. Descartamos **XTTS v2** (modelo bajo CPML **no-comercial** → bloqueante) y dejamos **Piper** como alternativa CPU-only documentada (es GPL-3.0 en su repo activo `OHF-Voice/piper1-gpl` — aislado en su propio contenedor el GPL es asumible, pero la política del proyecto debe confirmarlo).
- **Avatar — browser-side.** No requiere GPU de servidor, no añade contenedores, y desacopla el avatar del cerebro LLM. Las vías server-side de vídeo (MuseTalk/SadTalker/Audio2Face) exigen GPU potente y/o transporte WebRTC de vídeo, y se reservan como modo "realista" opcional. **Wav2Lip queda descartado por licencia** (no-comercial).

### Alternativa cloud / GPU (opt-in documentado, NO por defecto)

- **GPU local (recomendado para sensación "Meet" fluida):** mismos contenedores WhisperLive-gpu + Kokoro-FastAPI-gpu, activados vía `docker/docker-compose.gpu.yml` (mismo patrón que `ollama`, ADR 0056). Con GPU el first-chunk de Kokoro baja a ~300 ms y STT a ~200-500 ms.
- **TTS Azure Speech con visemas/blendshapes ARKit** como vía de **máxima calidad de lip-sync con mínimo código** para tenants que ya usan `azure_foundry`. **Aviso clave:** Azure _Speech_ es un recurso distinto del LLM `azure_foundry`; es **otra credencial en Vault**, NO toca el catálogo LLM cerrado. Cloud, de pago, no local-first.
- **OpenAI Realtime / speech-to-speech: RECHAZADO** como modo de voz extremo-a-extremo, porque sustituye el "cerebro" y **no usa el provider ya asignado al asistente** → violaría ADR 0021 y la reutilización del grafo/memoria/tools. Solo sería admisible si se limitara a STT+TTS (y ahí no aporta sobre lo dedicado).

---

## 2. ARQUITECTURA END-TO-END

### Diagrama (texto)

```
┌──────────────────────── NAVEGADOR (apps/admin-panel, Next.js) ────────────────────────┐
│  app/admin/assistant  (nuevo modo "videollamada")                                       │
│                                                                                          │
│  getUserMedia(echoCancellation,noiseSuppression)                                         │
│        │                                                                                 │
│        ├─► AudioWorklet → PCM16 16kHz mono (frames ~20-40ms) ─┐                          │
│        │                                                       │ (binario)               │
│        ├─► @ricky0123/vad-web (Silero VAD)                     │                          │
│        │     onSpeechStart → frame control {type:"interrupt"}  │ (barge-in)               │
│        │     onSpeechEnd   → frame control {type:"eot"}        │                          │
│        │                                                       ▼                          │
│        │                                          ┌──────────────────────┐               │
│        │   audio TTS (bin) + eventos JSON  ◄───────┤  WS /api/assistant/   │              │
│        │   (partial/final STT, word/viseme,        │       voice           │              │
│        │    turn state)                             └──────────┬───────────┘              │
│        ▼                                                       │                          │
│  TalkingHead.js (three.js)  ◄── audio + (visemas|amplitud) ────┘                          │
│  (cabeza/ojos/parpadeo procedural + lip-sync)                                            │
└──────────────────────────────────────────────────────────────────────────────────────┘
                              │ HTTPS/WSS single-origin  (NEXT_PUBLIC_API_URL=/api)
                              ▼
┌──────────────────────────────── CADDY (reverse-proxy) ─────────────────────────────────┐
│  handle /api/v1 /api/v1/*  → api-server:8000   (API pública, SIN strip)                  │
│  handle_path /api/*        → api-server:8000   (retira /api; upgrade WS transparente)    │
│       └── incluye la nueva ruta /api/assistant/voice                                     │
│  handle /*                 → admin-panel:3000                                            │
└──────────────────────────────────────────────────────────────────────────────────────┘
                              │  (agentic-net, red interna)
                              ▼
┌──────────────────────────── api-server (FastAPI/Uvicorn) ──────────────────────────────┐
│  routers/assistant_voice.py  (NUEVO)                                                     │
│   ├─ auth: ?token= + sesión Redis viva + RLS  (patrón EXACTO de routers/ws.py)           │
│   ├─ gate: require_assistant_access (Tenant Admin + personal_assistant_enabled)          │
│   ├─ VoiceSession (en memoria, por WS): buffer audio, hipótesis STT, handle TTS, estado  │
│   │                                                                                       │
│   │   audio PCM ──► [proxy WS]──► STT (WhisperLive)  ──► parciales/finales                │
│   │   texto final usuario ──► recall_user_memories + augment_system_prompt (memory.py)   │
│   │                       ──► run_assistant_turn / run_assistant_turn_streamed (graph.py) │
│   │                            └─ get_assistant_model → resolve_assistant_model           │
│   │                               → build_llm_provider  (provider-AGNÓSTICO, ADR 0021)    │
│   │   texto respuesta (troceado por frases) ──► [HTTP]──► TTS (Kokoro-FastAPI)            │
│   │   chunks audio TTS + word-timings ──► WS ──► navegador                                │
│   └─ barge-in: cancela el stream TTS en curso + poda el turno                            │
└──────────────────────────────────────────────────────────────────────────────────────┘
        │  agentic-net (interno, SIN puertos al host, NO expuesto por Caddy)
        ├───────────────────────────► stt   (WhisperLive)        stt:9090
        └───────────────────────────► tts   (Kokoro-FastAPI)     tts:8880
```

### Servicios nuevos en `docker/docker-compose.yml`

Dos servicios, ambos en `agentic-net`, **sin `ports:` al host**, reusando las anclas existentes verificadas (`*default-restart`, `*default-logging`, `*default-seccomp` → `no-new-privileges` + `apparmor=agentic-default` + `cap_drop:[ALL]`, `*default-limits`):

```yaml
  stt:
    image: ${IMAGE_STT:-ghcr.io/collabora/whisperlive-cpu:<PIN>}
    environment: { ... modelo small/base, language auto|es|en ... }
    volumes: [ whisper_models:/root/.cache/whisper ]   # modelos en volumen
    healthcheck: { ... }
    networks: [agentic-net]
    <<: [*default-restart, *default-logging, *default-seccomp, *default-limits]

  tts:
    image: ${IMAGE_TTS:-ghcr.io/remsky/kokoro-fastapi-cpu:<PIN>}
    healthcheck: { test: ["CMD","curl","-fsS","http://localhost:8880/health"] }
    networks: [agentic-net]
    <<: [*default-restart, *default-logging, *default-seccomp, *default-limits]
```

Overlay GPU en `docker/docker-compose.gpu.yml` (mismo patrón que `ollama`): `deploy.resources.reservations.devices` NVIDIA sobre `stt` y `tts`, e imágenes `-gpu`. **[INCERTIDUMBRE]** El healthcheck de Kokoro-FastAPI usa `curl`: confirmar que la imagen lo trae o usar `python -c`/`wget` según corresponda.

### Ruteo Caddy

**Cero cambios estructurales.** La regla `handle_path /api/*` → `api-server:8000` ya proxya con upgrade WS transparente (verificado en `apps/installer/backend/src/installer_backend/proxy_generator.py`). El navegador conecta a `wss://<host>/api/assistant/voice?token=...` y Caddy lo entrega a `/assistant/voice` en el api-server. **`stt` y `tts` NO se exponen por Caddy**: viven en `agentic-net` y solo los consume el api-server (preserva auth/tenant/secretos).

### Endpoints nuevos (api-server)

1. `WS /assistant/voice` — full-duplex binario+JSON. Único endpoint de tiempo real del modo voz.
2. (Opcional F1) `GET /assistant/voice/health` — estado de disponibilidad de `stt`/`tts` para que la UI muestre "modo voz no disponible" en vez de fallar al conectar.

El protocolo de frames del WS (a fijar en el ADR): subida = binario PCM16 + frames JSON de control (`interrupt`, `eot`, `lang`); bajada = binario audio TTS + JSON (`stt_partial`, `stt_final`, `assistant_delta`, `word_timings`/`visemes`, `turn_state`, `error`). Multiplexado por prefijo de tipo de frame (1 byte) sobre el binario, o canal JSON separado por `ws.send_json` (como ya hace `_pump`).

---

## 3. AVATAR

### Enfoque recomendado: browser-side con TalkingHead.js

- **Render y movimiento de cabeza:** TalkingHead renderiza un avatar GLB de medio cuerpo con three.js y genera de forma **procedural** el movimiento de cabeza, parpadeo, micro-saccades y contacto visual e idle. Esto da el "vibe Meet/Teams" sin coste de servidor y sin GPU. **Requiere un GLB con blendshapes ARKit (52) u Oculus/OVR visemes (15).**
- **Lip-sync — dos caminos, en este orden:**
  1. **MVP (F3): por amplitud/energía de audio.** Robusto, **idioma-agnóstico** (cubre ES+EN sin riesgo), trivial: se calcula el envelope RMS del audio TTS en el cliente y se mapea a apertura de mandíbula.
     **[INCERTIDUMBRE CRÍTICA QUE HAY QUE RESOLVER ANTES DE F3]** El JSON de investigación se contradice: una parte afirma que TalkingHead trae un módulo `HeadAudio` de lip-sync **solo por amplitud**, y otra afirma que `streamAudio()` **exige** visemas o word-timings y **no** hace amplitud. **Hay que verificar en la versión pineada de la librería qué API existe realmente** (`speakAudio` vs `streamAudio`/`streamStart`/`streamNotifyEnd`, y si acepta lip-sync por energía). Si NO acepta amplitud, el MVP de lip-sync pasa obligatoriamente a usar word-timings (ver punto 2) y ES deja de ser "gratis".
  2. **Mejora (F4): por visemas/word-timings.** TalkingHead consume `{audio, words, wtimes, visemes, vtimes}`. Kokoro-FastAPI expone **word-level timestamps** en `/dev/captioned_speech` y fonemas en `/dev/phonemize` — pero son **endpoints `/dev/*`, no parte del API OpenAI estable**: tratar como contrato propio y **pinear la versión de imagen**. El lip-sync nativo por-texto de TalkingHead cubre EN/DE/FR/FI/LT pero **NO ES**: para español hay que conducirlo con los timings/fonemas del TTS o un mapeo fonema→viseme propio. Es el punto de mayor esfuerzo del área.

### Integración en la página Next.js del asistente

- Ubicación: `apps/admin-panel/app/admin/assistant/` (hoy `page.tsx` hace `apiFetch` POST a `/assistant/chat`, sin streaming). Se añade un **nuevo componente cliente "modo videollamada"** que:
  - Abre el WS a `/api/assistant/voice?token=...` reutilizando el `apiFetch`/token de la página actual.
  - Monta `AudioWorklet` + `@ricky0123/vad-web` + `TalkingHead`.
  - Recibe audio + (amplitud|visemas) por el WS y llama a la API de habla de TalkingHead.
- **Assets como estáticos** servidos por Next.js (`/public`): el GLB del avatar + módulos de three.js. **Ningún contenedor nuevo para el avatar.**
- **Licencia del avatar:** usar **VRoid Studio / MPFB / CC0**. **Bloquear Ready Player Me** (CC BY-NC 4.0, no-comercial) y Live2D Cubism Core (licencia comercial de pago para empresas grandes) para uso de producto.

---

## 4. PROVIDER-AGNÓSTICO

### Qué se reutiliza TAL CUAL (verificado en el código)

- **Resolución del provider/modelo:** `routers/assistant.py::get_assistant_model` → `assistant/model_config.py::resolve_assistant_model` (herencia tenant override → platform default, ADR 0053/0065) → `to_provider_model_name` → `reasoning_call_kwargs` (ADR 0070) → `llm_providers/factory.py::build_llm_provider`. Devuelve un `LLMAssistantModel`. **El modo voz debe depender de este MISMO `get_assistant_model`** → queda provider-agnóstico gratis y los tests pueden override-arlo con `ScriptedAssistantModel`.
- **El grafo:** `assistant/graph.py::run_assistant_turn` nunca importa un provider; habla con el seam `AssistantModelClient`, cuyo adaptador real `assistant/llm.py::LLMAssistantModel.decide()` usa `provider.complete()`. **No se toca.**
- **Memoria:** `recall_user_memories` + `augment_system_prompt` + tool `remember_about_me` (`memory.py`/`tools.py`). Si el modo voz **reutiliza el mismo flujo** que `assistant_chat`, recall/remember funcionan idénticos; si se crea endpoint nuevo, hay que **replicar esas 3 llamadas** (recall + augment + `identity.effective_tools()`) o se produce drift.
- **Auth/RLS/gate:** patrón de `routers/ws.py` (`_resolve_principal` con sesión Redis viva, `_owns_resource`, `?token=`, `asyncio.wait` sobre `ws.receive()`) + `require_assistant_access`.

### Compatibilidad con los 4 providers (y un OpenAI futuro)

El contrato `LLMProvider` (`packages/shared-llm/src/shared_llm/base.py`) define **`complete()` y `stream()`** y los 4 providers implementan ambos (verificado: `ollama.py:129`, `azure_foundry.py:112`, `copilot.py:335`, `claude_agent.py:359`). Un quinto provider OpenAI futuro, si algún día se aprueba por ADR, solo tendría que cumplir el mismo Protocol. El modo voz **no añade un 5º provider**: STT/TTS son medios, no LLM.

### Qué FALTA para streaming incremental al TTS

Hoy el grafo **solo usa `complete()`** (`llm.py` nunca llama a `stream()`): no se emite texto incremental, el TTS tendría que esperar la respuesta completa. Para "hablar antes de terminar" hay que añadir un **camino de streaming** (entregable de F2):

1. **`AssistantModelClient.decide_stream()` / `run_assistant_turn_streamed()`**: las rondas de tool-calling siguen con `complete()` (necesitan la respuesta estructurada con `tool_calls`); **solo la ronda final** (cuando `_route_after_decide` → `finish`) usa `provider.stream()`, reenviando `StreamChunk.delta`.
2. **En `llm.py`**: implementar el stream delegando en `self.provider.stream(messages, model=self.model, **self.extra_call_kwargs)` → mantiene la agnosticidad.
3. **Troceo por frases** (segmentación por puntuación) de los deltas para empujarlos al TTS clausula a clausula.
4. **CAVEAT `claude_sdk` (verificado, `claude_agent.py:366` `tools` con `noqa: ARG002`):** `ClaudeAgentProvider.stream()` **ignora `tools` y no emite tool_calls**. Patrón obligatorio y **uniforme** para los 4: rondas de tools con `complete()`, respuesta final con `stream()`. Así `claude_sdk` no pierde el tool-calling y el camino es idéntico para todos.
5. **Mismo event loop:** `decide()` debe await-earse en el loop de la request (el comentario en `llm.py` documenta el "Event loop is closed" en Windows al bridgear loops). El WS de voz ya corre en ese loop.

---

## 5. PLAN POR FASES

Cada fase es entregable y testeable de forma independiente.

### F1 — Voz sin avatar (STT + TTS + WS, por-turno)

- **Alcance:** ciclo completo de voz **no incremental**: micro → STT (por-utterance, disparado por VAD del cliente) → texto entra al **mismo flujo que `/assistant/chat`** (`complete()`, sin cambios en el grafo) → respuesta completa → TTS → audio al navegador. Sin avatar (solo audio + texto/subtítulos).
- **Servicios/ficheros:**
  - `docker/docker-compose.yml`: servicios `stt`, `tts` (+ `whisper_models` volumen) con anclas de hardening.
  - `docker/docker-compose.gpu.yml`: overlay GPU para `stt`/`tts`.
  - api-server: `routers/assistant_voice.py` (WS, auth/RLS de `ws.py`, gate `require_assistant_access`, `VoiceSession`), cliente fino HTTP a `stt`/`tts`.
  - admin-panel: modo voz básico (AudioWorklet PCM16 + vad-web + reproducción de audio).
- **Hecho cuando:** un Tenant Admin (toggle ON) habla por micro, ve la transcripción, y oye la respuesta del asistente sintetizada; funciona con **al menos 2 de los 4 providers** verificados manualmente (p.ej. ollama local + uno cloud); tests de integración con `ScriptedAssistantModel` cubren auth/RLS del WS (rechazo cross-tenant 1008) y el contrato de frames; `stt`/`tts` no exponen puertos al host.

### F2 — Streaming + barge-in

- **Alcance:** `run_assistant_turn_streamed()` + `LLMAssistantModel.decide_stream()` (ronda final con `provider.stream()`, troceo por frases, patrón complete-tools/stream-final con caveat `claude_sdk`); TTS por clausulas; **barge-in** real (VAD cliente → frame `interrupt` → cancela stream TTS + poda turno).
- **Servicios/ficheros:** `assistant/graph.py`, `assistant/llm.py`, `routers/assistant_voice.py` (cancelación de TTS en curso, estado de turno `user_speaking|thinking|assistant_speaking`); admin-panel (corte de reproducción al hablar, eco/feedback con `echoCancellation`).
- **Hecho cuando:** el asistente empieza a hablar tras la **primera frase** del LLM (no tras la respuesta completa); el usuario puede interrumpir y el audio se corta <300 ms; verificado que `stream()` rinde incremental en los **4 providers**; tests de los nuevos caminos del grafo (rondas tools con `complete()`, final con `stream()`).

### F3 — Avatar con lip-sync básico

- **Alcance:** TalkingHead.js en la página del asistente con GLB CC0/VRoid; movimiento de cabeza/parpadeo procedural; **lip-sync por amplitud** (idioma-agnóstico) — **sujeto a resolver la [INCERTIDUMBRE CRÍTICA] de la API de TalkingHead** (ver §3); si la lib no soporta amplitud, F3 usa directamente word-timings de Kokoro.
- **Servicios/ficheros:** admin-panel (componente avatar, assets GLB en `/public`); ningún contenedor nuevo.
- **Hecho cuando:** el avatar mueve cabeza/ojos y la boca se mueve sincronizada con el audio en **ES y EN**; QA visual humano aprueba la sensación "videollamada"; sin regresión de latencia respecto a F2.

### F4 — Pulido (visemas precisos + robustez)

- **Alcance:** lip-sync por **visemas/word-timings** de Kokoro (`/dev/captioned_speech`) con mapeo fonema→viseme para ES; audio de relleno ("déjame mirar…") para cubrir la latencia del **bucle de tools** antes de la primera palabra; semáforo de **sesiones de voz concurrentes**; healthcheck/disponibilidad en la UI; documentación del modo CPU ("funcional pero no fluido") y de las vías opt-in (GPU, Azure visemas).
- **Servicios/ficheros:** api-server (límite de concurrencia, relleno), admin-panel (mapeo viseme), `docs/`.
- **Hecho cuando:** lip-sync fonético en ES+EN aceptable; el sistema degrada con gracia bajo carga (rechaza/encola sesiones extra sin tumbar el stack); runbook + changelog + ADR cerrados.

---

## 6. RIESGOS + DECISIONES QUE REQUIEREN ADR

### Riesgos técnicos

1. **API de lip-sync de TalkingHead [CRÍTICO, sin verificar]:** contradicción en la investigación sobre si soporta lip-sync por amplitud. Verificar en la versión pineada **antes de comprometer F3**; condiciona si ES es "gratis" o exige word-timings.
2. **Latencia en CPU:** Kokoro first-chunk ~3.5 s y STT con modelos medianos no son tiempo-real. Para sensación "Meet" se necesita **GPU** (overlay) o aceptar modelos pequeños (whisper `small`/`base`, Piper) con menor calidad. Documentar CPU como "funcional, no fluido".
3. **Barge-in + eco/feedback:** el VAD del cliente puede auto-dispararse con el audio del propio asistente. Mitigar con `echoCancellation`/`noiseSuppression` en `getUserMedia` y/o pausar VAD durante TTS.
4. **`claude_sdk.stream()` ignora tools (verificado):** patrón obligatorio complete-tools / stream-final; un stream ingenuo con `claude_sdk` pierde el tool-calling.
5. **WS sobre TCP:** head-of-line blocking en redes con pérdida. Aceptable para operadores internos/LAN; **WebRTC** queda como alternativa documentada solo para red adversa/cliente externo (rompería el single-origin limpio de Caddy: UDP no se proxya).
6. **Dependencia de proyectos comunitarios** (WhisperLive, Kokoro-FastAPI) y endpoints `/dev/*` no estables: **pinear tags** (no `:latest`), cliente fino + healthcheck, igual que se hizo con docling-serve.
7. **Recursos en single-machine:** cada sesión de voz consume CPU/GPU de STT+TTS; **semáforo de concurrencia** + respetar `*default-limits`.
8. **Audio del navegador:** PCM16 16 kHz mono evita transcodificar en servidor; si en algún punto se usa MediaRecorder (WebM/Opus) habría que `ffmpeg` en el servicio STT.
9. **Drift de memoria/seguridad** si se crea endpoint de voz aparte: replicar recall/augment/`effective_tools` y la auth/RLS del WS o se pierden.

### Licencias (decisión de producto)

- **Avatar:** prohibir Ready Player Me (CC BY-NC) y Live2D Cubism Core (comercial de pago); usar VRoid/MPFB/CC0.
- **TTS:** Kokoro Apache-2.0 (limpio). XTTS v2 **bloqueado** (CPML no-comercial). Piper GPL-3.0 (aislado en contenedor; confirmar política).
- **Vídeo server-side:** Wav2Lip **bloqueado** (no-comercial); MuseTalk (MIT) y SadTalker (Apache-2.0) admisibles pero GPU.

### Por qué NO requiere ADR de provider LLM

STT/TTS/avatar son **servicios de medios separados**, no `LLMProvider`. **No añaden un 5º provider** y **no tocan el catálogo cerrado ADR 0021**. El LLM sigue resolviéndose por `get_assistant_model`. Lo que **sí** requiere ADR es la **feature de producto** (decisiones abajo).

### Decisiones que el ADR debe fijar

- Pieza STT y TTS elegidas + tags pineados.
- Transporte (WS binario vs WebRTC) y protocolo de frames del WS.
- Fuente de lip-sync (amplitud vs word-timings/fonema vs Azure visemas) y librería/licencia del avatar.
- Reutilizar el flujo de `assistant_chat` vs endpoint nuevo (y, si nuevo, obligación de replicar recall/augment/tools).
- Política de credenciales: STT/TTS locales no usan keys; la alternativa cloud (Azure Speech) va por **Vault** y no contamina el catálogo LLM.

---

## 7. ESBOZO DEL ADR

> A escribir en `docs/05-architecture-decisions/0073-modo-voz-asistente-stt-tts-avatar.md` (siguiente número libre tras 0072). Frontmatter YAML + Mermaid según convención.

**Título:** ADR 0073 — Modo voz ("videoconferencia") del Asistente: STT/TTS/avatar como add-ons de medios, provider-agnósticos y dockerizables (sin 5º provider LLM)

**Estado:** `proposed`

**Contexto:**

- El Asistente personal (ADR 0033) responde por chat de texto reutilizando el grafo LangGraph (`assistant/graph.py`) y la resolución provider-agnóstica (`get_assistant_model` → `resolve_assistant_model` → `build_llm_provider`, ADR 0021/0053/0065/0070).
- Se quiere un modo de voz estilo Meet/Teams: micro → STT → **el mismo LLM ya asignado** → TTS → avatar con lip-sync.
- Restricciones duras: catálogo LLM **cerrado** (ADR 0021, sin 5º provider); local-first y dockerizable en single-machine (no k8s); ES+EN; aislamiento por contenedor + secretos en Vault; single-origin Caddy `/api`; reutilizar auth/RLS/sesión del patrón `ws.py`.

**Decisión:**

1. STT = **WhisperLive** (faster-whisper) y TTS = **Kokoro-FastAPI** (Apache-2.0) como **dos servicios Docker internos** en `agentic-net`, sin puertos al host, con hardening por anclas existentes y overlay GPU opcional (`docker-compose.gpu.yml`, patrón ollama).
2. Transporte = **WebSocket binario full-duplex** `/api/assistant/voice` en el api-server, reutilizando auth `?token=` + sesión Redis + RLS + `require_assistant_access`. Caddy no necesita cambios (ya proxya `/api/*` con upgrade WS).
3. El **cerebro no cambia**: el modo voz invoca el grafo vía `get_assistant_model` (provider-agnóstico para los 4 kinds y un OpenAI futuro). Se añade un **camino de streaming** (`run_assistant_turn_streamed` + `decide_stream`) que streamea solo la ronda final (`provider.stream()`), con rondas de tools en `complete()` (caveat `claude_sdk`).
4. Avatar = **TalkingHead.js** browser-side con GLB CC0/VRoid; lip-sync por amplitud en MVP y por visemas/word-timings de Kokoro en la mejora.
5. **STT/TTS/avatar NO son `LLMProvider`** → no modifican ADR 0021.
6. Entrega incremental F1→F4.

**Alternativas rechazadas:**

- **OpenAI Realtime / speech-to-speech extremo-a-extremo:** sustituye el cerebro, no usa el provider asignado → viola ADR 0021. (Cloud, no local-first.)
- **WebRTC + SFU (LiveKit/aiortc) por defecto:** sobreingeniería para single-machine/operadores internos; rompe single-origin (UDP no proxyable por Caddy). Queda documentado para red adversa.
- **SSE para subida de audio:** unidireccional, no permite barge-in.
- **faster-whisper embebido en api-server:** reimplementar streaming + engordar la imagen del api-server (contra el principio de aislar runtimes).
- **XTTS v2:** modelo CPML **no-comercial** → bloqueante. **Piper** queda como alternativa CPU-only (GPL-3.0, aislada).
- **Ready Player Me / Live2D Cubism:** licencias no aptas para producto. **Wav2Lip:** no-comercial. **MuseTalk/SadTalker/Audio2Face:** GPU/ops elevadas → modo "realista" opcional.

**Consecuencias:** dos contenedores nuevos a mantener (pin de tags); calidad/latencia dependen de GPU; lip-sync ES exige trabajo extra (visemas) si la amplitud no basta; nuevo camino de streaming en el grafo a cubrir con tests en los 4 providers.

---

### Ficheros clave (rutas absolutas)

- Grafo y seam: `C:\laragon\python\agent-ai-multitenant\apps\api-server\src\api_server\assistant\graph.py`, `...\assistant\llm.py`
- Resolución provider + endpoints actuales: `...\api_server\routers\assistant.py`
- Patrón WS (molde del nuevo endpoint): `...\api_server\routers\ws.py`
- Contrato LLM (`stream()`/`StreamChunk`): `...\packages\shared-llm\src\shared_llm\base.py`, `...\shared_llm\types.py`
- Providers `stream()` (caveat claude_sdk en `claude_agent.py:366`): `...\shared_llm\providers\{ollama,azure_foundry,copilot,claude_agent}.py`
- Memoria: `...\api_server\assistant\memory.py`, `...\assistant\tools.py`
- Compose + anclas + GPU: `...\docker\docker-compose.yml`, `...\docker\docker-compose.gpu.yml`
- Ruteo Caddy (generado): `...\apps\installer\backend\src\installer_backend\proxy_generator.py`
- Página asistente (frontend): `...\apps\admin-panel\app\admin\assistant\page.tsx`
- ADR a crear: `...\docs\05-architecture-decisions\0073-modo-voz-asistente-stt-tts-avatar.md`

**Incertidumbres a cerrar con prueba real antes de comprometer fases:** (a) API de lip-sync de TalkingHead (¿amplitud soportada?) — bloquea F3; (b) latencia real de Kokoro/WhisperLive en CPU del host objetivo; (c) calidad de visemas en ES; (d) healthcheck correcto de la imagen Kokoro-FastAPI.
