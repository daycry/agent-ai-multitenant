---
adr_id: "0073"
title: "Modo voz (videoconferencia) del Asistente: STT/TTS/avatar como add-ons de medios, provider-agnósticos y dockerizables (sin 5º provider LLM)"
status: accepted
date: 2026-06-21
authors: [system_architect]
plan_referenced: voice-assistant
docs_language: es
extends: ["0021", "0033", "0065"]
---

# ADR 0073 — Modo voz del Asistente: STT/TTS/avatar provider-agnósticos (sin 5º provider LLM)

> **Estado: `proposed`.** Diseño verificado contra el código por el workflow
> `voice-assistant-design`. El diseño completo (stack, arquitectura, avatar,
> plan por fases F1–F4, riesgos) vive en
> [`docs/superpowers/specs/2026-06-21-voice-assistant-design.md`](../superpowers/specs/2026-06-21-voice-assistant-design.md).
> Requisitos firmes del operador: UX estilo **Google Meet/Teams**; **avatar** que
> mueve la cabeza + lip-sync; **selector de voz masculina/femenina**; el LLM es el
> **provider ya asignado al asistente** → ha de funcionar con CUALQUIERA de los
> configurados.

## Contexto

- El Asistente personal (ADR 0033/0054) responde por chat de texto reutilizando el
  grafo LangGraph (`assistant/graph.py`) y la resolución **provider-agnóstica**
  (`get_assistant_model` → `resolve_assistant_model` → `build_llm_provider`;
  ADR 0021/0065/0070). El tool-calling ya es uniforme entre los 4 providers tras
  el fix de `claude_sdk.complete()` (commit `0bc524b`).
- Se quiere un modo de voz estilo Meet/Teams: micro → STT → **el mismo LLM ya
  asignado** → TTS → avatar con lip-sync.
- Restricciones duras: catálogo LLM **cerrado** (ADR 0021, sin 5º provider);
  local-first y dockerizable en single-machine (no k8s); **ES + EN**; aislamiento
  por contenedor + secretos en Vault; single-origin Caddy `/api`; reutilizar
  auth/RLS/sesión del patrón `routers/ws.py`.

## Decisión

1. **STT = WhisperLive** (backend faster-whisper/CTranslate2) y **TTS =
   Kokoro-FastAPI** (modelo Kokoro-82M, **Apache-2.0**, ES+EN, voces M/F,
   streaming, API OpenAI-compatible) como **dos servicios Docker internos** en
   `agentic-net`, **sin puertos al host**, con hardening por las anclas existentes
   y overlay GPU opcional (`docker-compose.gpu.yml`, patrón `ollama`/ADR 0056).
   Tags **pineados** (no `:latest`).
2. **Transporte = WebSocket binario full-duplex** `/api/assistant/voice` en el
   api-server, reutilizando auth `?token=` + sesión Redis + RLS +
   `require_assistant_access` (molde: `routers/ws.py`). Caddy no necesita cambios
   (ya proxya `/api/*` con upgrade WS).
3. **El cerebro NO cambia**: el modo voz invoca el grafo vía `get_assistant_model`
   (provider-agnóstico para los 4 kinds y un OpenAI futuro). Se añade un **camino
   de streaming** (`run_assistant_turn_streamed` + `decide_stream`) que streamea
   solo la ronda final (`provider.stream()`), con las rondas de tools en
   `complete()` (caveat `claude_sdk`: `stream()` no soporta tools → patrón
   complete-tools / stream-final). Memoria (recall/augment/remember) intacta.
4. **Avatar = TalkingHead.js** browser-side (three.js) con GLB **CC0/VRoid**;
   movimiento de cabeza/parpadeo procedural; lip-sync por amplitud en el MVP y por
   visemas/word-timings de Kokoro en la mejora. **Sin GPU de servidor.**
5. **Selector de voz M/F**: expuesto en la UI; mapea a las voces de Kokoro
   (`af_*`/`am_*`, `bf_*`/`bm_*`, voces ES). VAD/barge-in con `@ricky0123/vad-web`
   (Silero) en el navegador; captura `AudioWorklet` → PCM16 16 kHz mono.
6. **STT/TTS/avatar NO son `LLMProvider`** → **no añaden un 5º provider** ni tocan
   ADR 0021. Lo que esta ADR fija es la **feature de producto** (medios), no el
   catálogo LLM.
7. Entrega **incremental F1→F4** (ver spec): F1 voz por-turno sin avatar; F2
   streaming + barge-in; F3 avatar con lip-sync; F4 visemas precisos + robustez.

## Alternativas rechazadas

- **OpenAI Realtime / speech-to-speech extremo-a-extremo**: sustituye el cerebro,
  NO usa el provider asignado → viola ADR 0021 (y cloud, no local-first).
- **WebRTC + SFU por defecto**: sobreingeniería para single-machine; rompe el
  single-origin (UDP no proxyable por Caddy). Documentado para red adversa/cliente
  externo.
- **SSE para subir audio**: unidireccional, impide barge-in.
- **faster-whisper embebido en el api-server**: reimplementar streaming + engordar
  la imagen (contra el principio de aislar runtimes).
- **XTTS v2**: modelo CPML **no-comercial** → bloqueante. **Piper** queda como
  alternativa CPU-only (GPL-3.0, aislada en su contenedor; confirmar política).
- **Ready Player Me / Live2D Cubism Core**: licencias no aptas para producto.
  **Wav2Lip**: no-comercial. **MuseTalk/SadTalker/Audio2Face**: GPU/ops elevadas →
  modo "realista" opcional opt-in.

## Consecuencias

- **+** Voz estilo Meet/Teams reutilizando el cerebro y la memoria del asistente,
  con cualquier provider del catálogo; sin GPU obligatoria (degradada en CPU).
- **+** Sin nuevo provider LLM: el catálogo cerrado (ADR 0021) intacto.
- **−** Dos contenedores nuevos a mantener (pin de tags + healthcheck, como
  docling-serve); calidad/latencia "fluida" dependen de GPU; lip-sync fonético en
  ES exige visemas/word-timings (F4) si la amplitud no basta.
- **−** Nuevo camino de streaming en el grafo a cubrir con tests en los 4 providers.

## Incertidumbres a cerrar con prueba real (antes de comprometer fases)

1. **API de lip-sync de TalkingHead** (¿soporta amplitud?) — bloquea F3.
2. Latencia real de Kokoro/WhisperLive en la CPU del host objetivo.
3. Calidad de visemas en ES.
4. Healthcheck correcto de la imagen Kokoro-FastAPI.

## Tests / criterios de cierre

Por fase (ver spec §5): F1 = ciclo voz por-turno funcionando con ≥2 providers +
auth/RLS del WS (rechazo cross-tenant 1008) verificados; F2 = primera frase del LLM
hablada + barge-in <300 ms en los 4 providers; F3 = avatar con cabeza + boca
sincronizada en ES+EN (QA visual humano); F4 = visemas fonéticos + degradación con
gracia bajo carga + runbook/changelog.

## Estado de implementación (2026-07-12)

F1 IMPLEMENTADA Y DESPLEGADA (verificado 2026-07-12) para el asistente de tenants Y el cortex sobre la misma infra: WS `/ws/assistant/voice` (`routers/assistant_voice.py`), `VoiceSession` (transcribe->respond->synthesize) con clientes STT/TTS (`assistant/voice_clients.py`), servicios `stt` (faster-whisper) y `tts` (Kokoro-82M) en docker-compose.yml, shell UI compartida `components/voice/voice-call-shell.tsx` + avatar con lip-sync por amplitud (`realistic-avatar.tsx`, SVG procedural ~= F3 MVP; no el TalkingHead/GLB propuesto). PENDIENTE: F2 (streaming token-a-token + barge-in/VAD; hoy push-to-talk y `/assistant/chat/stream` emite progreso por rondas, no deltas) y F4 (visemas). El token-a-token exige `stream()` en los 4 providers de shared-llm.
