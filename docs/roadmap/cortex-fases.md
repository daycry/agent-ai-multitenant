---
title: "Córtex del system_owner — índice de planes por fase"
status: in_progress
started_at: 2026-06-23
docs_language: es
related: ["cortex-system-owner", "0074", "0075", "0076", "0077", "0078", "0073"]
---

# Córtex del `system_owner` — planes de implementación por fase

Diseño completo en [cortex-system-owner.md](cortex-system-owner.md). **F0 (rol `system_owner`)
YA está implementado y desplegado** (migración 0091, claim JWT `own`,
`require_system_owner`/`require_admin_or_owner` DB-authoritative, bootstrap, `/me`;
`demo@example.com` promovido a owner).

Orden de ejecución (cada fase apila sobre la anterior). El operador dio **luz verde para
implementar F1→F5 en secuencia, hasta acabar** (2026-06-23). Las migraciones se **encadenan
de verdad al implementar** (los planes proponen `0092` como placeholder; el orden real es
F1=0092, F2=0093, …).

| Fase   | Plan                                                             | Qué entrega                                                                                                                                                                  | Tablas nuevas                                |
| ------ | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| **F1** | [cortex-f1-memoria-cognitiva.md](cortex-f1-memoria-cognitiva.md) | Córtex conversacional con **hilo persistente** + **recall híbrido** (BM25+vector+entidad, RRF) + deliberación con `claude_sdk`/effort + endpoints `/owner/cortex/*` + página | `cortex_conversations`, `cortex_turns`       |
| **F2** | [cortex-f2-afectivo.md](cortex-f2-afectivo.md)                   | **Motor afectivo PAD** + distilador (Celery+Ollama) + **Panel de Mente** (dashboard: emoción, mood, drives, episodios) + WS telemetría                                       | `cortex_affect_snapshots`                    |
| **F3** | [cortex-f3-identidad.md](cortex-f3-identidad.md)                 | **Identidad** (nombre/valores/rasgos/narrativa) versionada + onboarding codiseñado + reflexión                                                                               | `cortex_identity`, `cortex_identity_history` |
| **F4** | [cortex-f4-autonomia.md](cortex-f4-autonomia.md)                 | **Bucles Celery** (reflexión/curiosidad/mantenimiento) + curiosidad con WebSearch (gated ADR 0076) + budget caps + kill-switch + olvido/consolidación                        | `cortex_curiosity_pursuits`                  |
| **F5** | [cortex-f5-voz-avatar.md](cortex-f5-voz-avatar.md)               | **Voz + avatar** modulados por el afecto (WS `/ws/owner/cortex/voice`, TTS Kokoro por arousal)                                                                               | —                                            |

## Dashboard de estado (Panel de Mente)

El **dashboard que muestra el estado general/emocional/sensaciones** del córtex es el
**Panel de Mente de la F2**: diales PAD (valence/arousal/dominance), etiqueta de mood,
barras de _drives_ (necesidades/"sensaciones"), espacio afectivo 2D con estela, mapa de
episodios e identidad. Necesita el **motor afectivo (F2)** para tener datos que mostrar, por
eso va tras F1 (memoria). Endpoints: `GET /owner/cortex/mind`, `/affect/timeseries`,
`/episodes`, y WS `/ws/owner/cortex/telemetry`. Copy honesto obligatorio (es simulación, no
se vende como conciencia).

## Gating

Todas las fases F1-F5 siguen `proposed` en sus ADR; el operador autorizó implementarlas en
cadena. Dentro de F1, la **búsqueda web** (egress vía `claude_sdk`, ADR 0076) queda
**doblemente gated** hasta aprobar el ADR 0076 — el resto de F1 (memoria + deliberación)
entrega valor sin ella.
