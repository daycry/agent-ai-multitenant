---
title: "Córtex del system_owner — índice de planes por fase"
status: pending_human_validation
started_at: 2026-06-23
completed_at: null
docs_language: es
related:
  ["cortex-system-owner", "cortex-identidad-real", "0074", "0075", "0076", "0077", "0078", "0073"]
---

# Córtex del `system_owner` — planes de implementación por fase

> **✅ F0-F5 IMPLEMENTADAS Y DESPLEGADAS** (verificado 2026-07-06 — auditoría de estado del
> roadmap). Este índice quedó en `in_progress` desde el 2026-06-23 y nunca se actualizó mientras
> las 5 fases se completaban (21 commits entre 2026-06-24 y 2026-07-06). Los 5 planes de fase
> (`cortex-f1`…`cortex-f5`) seguían con banner "GATED — NO IMPLEMENTAR"; se corrigieron hoy junto
> con este índice. Encima de F1-F5 se implementó además una capa de "identidad real" (self-model
> unificado) — ver [cortex-identidad-real.md](cortex-identidad-real.md), que es el único doc del
> track que se mantuvo al día por sí mismo.

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

## Gating — histórico y estado actual

**El gate por fase ya no existe.** Comprobado ADR por ADR el 2026-07-30: **0073, 0075, 0076,
0077, 0078 y 0080 están `accepted`** en su frontmatter, y **0074 está `accepted-f0`** — el único
valor así del repo, conservado a propósito porque ese ADR se aprobó en dos tiempos (cimiento F0
primero, excepción a RLS después); su banner, que hasta el 2026-07-30 seguía declarando
«F1-F5 `proposed` (gated)» con el código desplegado, ya está corregido.

> Esta sección decía «Todas las fases F1-F5 siguen `proposed` en sus ADR». Era cierto el
> 2026-06-23 y dejó de serlo con las promociones del 2026-06-24 y el 2026-07-27. Se corrige el
> 2026-07-30 — el mismo patrón que documenta
> [`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md) §1.

También decayó el doble gate de la **búsqueda web** de F1: el ADR 0076 está `accepted` y la web
del córtex salió por el **camino degradado** de su punto 4 (tool propia provider-agnóstica con
anti-SSRF obligatorio, ADR 0067), no por las WebSearch/WebFetch nativas de `claude_sdk` del punto
3, porque el owner del stack de desarrollo usa Ollama. La divergencia está registrada en el propio
0076 y en el changelog de F1.

**Lo que sí sigue gated es la autonomía, y no por fase sino por interruptor**: el kill-switch
`cortex.autonomy_enabled` arranca **OFF** (ADR 0078) y nadie lo ha encendido, así que las tres
entradas del beat tickean y salen no-op. Encenderlo es una decisión explícita del operador, y
antes de tomarla conviene leer el estado de F4: salió sin owner-approval gate ni tope de gasto en
USD cableados al bucle (inventario en
[gaps-cortex-2026-07-27.md](gaps-cortex-2026-07-27.md)).

## Estado real por fase (no confundir «implementada» con «cerrada»)

Las seis fases están implementadas y desplegadas; **ninguna está cerrada**. F2, F3, F4 y F5
conservan casillas abiertas con hueco identificado. La autoridad sobre el estado de cada casilla
son el plan de la fase y sus tests, no esta tabla:

| Fase   | Implementada  | Cerrada                          | Changelog                                                                     |
| ------ | ------------- | -------------------------------- | ----------------------------------------------------------------------------- |
| **F0** | ✅ 2026-06-23 | pendiente de validación humana   | [mejoras-2026-06…](../07-changelog/mejoras-2026-06-chat-coste-cortex.md)      |
| **F1** | ✅            | pendiente de validación humana   | [cortex-f1-memoria-cognitiva](../07-changelog/cortex-f1-memoria-cognitiva.md) |
| **F2** | ✅            | ❌ casillas abiertas             | [cortex-f2-afectivo](../07-changelog/cortex-f2-afectivo.md)                   |
| **F3** | ✅            | ❌ casillas abiertas             | [cortex-f3-identidad](../07-changelog/cortex-f3-identidad.md)                 |
| **F4** | ✅            | ❌ casillas abiertas (seguridad) | [cortex-f4-autonomia](../07-changelog/cortex-f4-autonomia.md)                 |
| **F5** | ✅            | ❌ casillas abiertas             | [cortex-f5-voz-avatar](../07-changelog/cortex-f5-voz-avatar.md)               |
