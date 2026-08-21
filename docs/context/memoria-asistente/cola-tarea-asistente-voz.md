---
name: cola-tarea-asistente-voz
description: "Tarea ENCOLADA — modo voz/videoconferencia (STT+TTS, +avatar opcional) en el chat del Asistente."
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

El operador pidió (2026-06-20) añadir al **Asistente** un modo tipo
videoconferencia: charla interactiva por **voz** en el chat — **STT** (hablar) +
**TTS** (responder con voz), y opcionalmente un **avatar** que mueve los labios.

**Estado: ENCOLADA**, a abordar DESPUÉS de cerrar el fix de memoria
([[memoria-tool-calling-fix]]). Debe hacerse con **spec + plan + ADR** (feature
nueva → ADR obligatorio por CLAUDE.md) y actualizando docs. Robusto y profesional.

**Requisitos del operador (firmes):**

- UX estilo **Google Meet/Teams** (videoconferencia en vivo).
- **Avatar** que mueve la cabeza + lip-sync (labios). Preferir browser-side sin GPU
  (TalkingHead.js / Live2D / RPM+three.js); SadTalker/MuseTalk solo opción GPU.
- **Selector de voz masculina/femenina** (voces gratuitas y bonitas: Kokoro/Piper/XTTS).
- El LLM = **provider ya asignado al asistente** → provider-agnóstico (los 4 + OpenAI futuro).

**Pistas del operador (no vinculantes, a evaluar en el ADR):**

- STT local: **Faster-Whisper** (p.ej. imagen `fedirz/faster-whisper-server`).
- TTS local: **Kokoro** (alt: Piper, Coqui).
- Alternativa cloud todo-en-uno: **OpenAI Realtime API** (WS, baja latencia) —
  PERO el catálogo LLM está CERRADO (ADR 0021): meter OpenAI Realtime pide ADR
  explícito y choca con el catálogo actual; evaluar como add-on de voz, no como
  5º provider LLM.
- Avatar: Live2D / TalkingHead (ligero) o SadTalker / MuseTalk (realista).
- Todo dockerizable y local (encaja con el stack Compose single-machine).

**Encaje arquitectónico a respetar:** stack Docker Compose single-machine;
frontend Next.js (admin-panel, página del asistente); backend FastAPI + el grafo
del asistente (`api_server/assistant`); WebSocket/SSE ya en el stack. El LLM sigue
siendo el catálogo cerrado; voz = servicios STT/TTS nuevos delante/detrás del chat.
