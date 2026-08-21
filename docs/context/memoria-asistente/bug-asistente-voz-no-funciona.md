---
name: bug-asistente-voz-no-funciona
description: 'El "hablar por voz" del asistente de tenants no funciona — investigar al hacer F5 (voz del córtex).'
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

2026-06-24: el operador reporta que en el **asistente de los tenants** el **hablar por voz NO funciona** (modo voz STT/TTS, ADR 0073). Investigar y arreglar **al implementar F5 del córtex** (voz/avatar), porque reutiliza la misma infra de voz (router `assistant_voice`, STT, TTS Kokoro, avatar, WS de voz) — si el fallo está en el pipeline compartido, el fix beneficia a ambos.

**Pistas a revisar:** captura de micrófono/permiso en el front; el WS/endpoint de voz del asistente (`routers/assistant_voice.py`); STT (qué motor, si está cableado/levantado en el stack — servicios `stt`/`tts` en compose); allowlist de voz server-side (hubo un fix previo de validar el id de voz). Reproducir desde `/admin/assistant` con el modo voz. Relacionado: [[cortex-implementacion-autonoma]] (F5), [[cola-tarea-asistente-voz]].
