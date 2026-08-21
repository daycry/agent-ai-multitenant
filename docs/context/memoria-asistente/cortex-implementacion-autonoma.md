---
name: cortex-implementacion-autonoma
description: "Luz verde del operador para implementar el córtex F1-F5 en secuencia, autónomo, hasta acabar."
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

2026-06-23: el operador autorizó implementar el **Córtex completo (F1→F5) una detrás de otra, hasta acabar**, de forma autónoma, SIN pedir aprobación rutinaria por fase ("uno detrás de otro hasta acabar"). Esto **anula el gating por-fase** de [[estado-trabajo-en-curso]] para el córtex; solo interrumpir ante un bloqueo de decisión real (no rutinario).

**Estado de partida:** F0 HECHO y desplegado (rol `system_owner`: `users.is_system_owner` singleton, migración 0091 HEAD, claim JWT `own`, `require_system_owner`/`require_admin_or_owner` DB-authoritative, bootstrap, `/me`). `demo@example.com` promovido a owner. Planes F1-F5 generados en `docs/roadmap/cortex-f*.md` (workflow wp1mg6x8n).

**Orden:** F1 memoria cognitiva → F2 afectivo → F3 identidad → F4 autonomía → F5 voz/avatar.

**PROGRESO (2026-06-24):**

- **F0** rol system_owner — DESPLEGADO (migración 0091).
- **F1** memoria cognitiva — DESPLEGADO (migración 0092; chat persistente + recall híbrido + deliberación + endpoints owner-only + página /admin/cortex + selector de modelo en platform-defaults). `cortex.default_model` lo configura el owner por UI.
- **Web provider-agnóstica** (ADR 0067 accepted) — commiteado + api-server desplegado, **OFF por defecto** (`cortex.web_enabled`). web_search (SearXNG/Brave) + web_fetch + anti-SSRF por el egress-proxy, host tools válidas para los 4 providers. SearXNG declarativo en compose (sin arrancar); Brave key por env. ADR 0080 (Playwright) proposed.
- **F2** afectivo — DESPLEGADO (migración 0093 cortex_affect_snapshots; motor PAD determinista + distilador Celery/Ollama fail-open + Redis cortex:affect + endpoints /owner/cortex/{mind,affect/timeseries,episodes} + WS /ws/owner/cortex/telemetry + **dashboard "Panel de Mente"** /admin/cortex/mind).
- **F3** identidad — DESPLEGADO (migración 0094: cortex_identity singleton + cortex_identity_history versionado; identity NUNCA se borra ADR 0077; onboarding /admin/cortex/identity + tarea de reflexión Celery con clamps fail-open + identidad en el prompt).
- **F4** autonomía — DESPLEGADO (migración 0095; kill-switch cortex.autonomy_enabled OFF + budgets + circuit-breaker; workers curiosity/maintenance/forgetting; beat_schedule; endpoint /owner/cortex/autonomy). Activación: falta un proceso `celery beat` + flip del kill-switch (no urge con autonomy OFF).
- **F5** voz/avatar — DESPLEGADO (WS /ws/owner/cortex/voice + TTS Kokoro modulado por arousal + frame afecto→avatar SVG; commits 3f0af4c backend, 10a4646 frontend). Reutiliza stt/tts.
- **BUG VOZ ASISTENTE ([[bug-asistente-voz-no-funciona]]) — ARREGLADO**: stt/tts no estaban levantados; arrancados + healthcheck cambiado a python (las imágenes no traen wget) → healthy; content_type real (no audio/wav hardcodeado). Commit 602aa07.

**CÓRTEX COMPLETO F0→F5 + web, todo desplegado.** dev DB head = **0095**. Follow-ups TODOS HECHOS (2026-06-24): (1) instalador de prod genera stt/tts + voz (commit 08cf82b, VoiceMode default cpu); (2) servicio `cortex-beat` + schedule F4 (curiosidad/reflexión/mantenimiento) desplegado (commit 52665c2); (3) e2e Playwright del córtex — 8 pasan en navegador real. Para usar: configurar cortex.default_model (UI); opcional web_enabled + SearXNG/Brave; opcional autonomy_enabled (UI owner) → el beat ya tickea. Bug de voz del asistente RESUELTO (stt/tts levantados+healthy). NADA pendiente del córtex salvo lo que el operador quiera ampliar.

Commits córtex: cf8f7cd (planes) → F1 (afbcc45/046f6b3/9841406/bc41814) → eff794f (ADRs) → ff37675 (config modelo) → a224bd0 (web) → F2 (ccaa970/b114290/eb4ead9) → F3 (bb7f1b5/1d71738). Todo en `feat/builtin-customization` (PR #53). dev DB head = **0094**. Build deploy: api-server:manuals (WITH_CLAUDE=1), workers:ci (--build-arg BASE_IMAGE=agentic-platform/api-server:manuals), admin-panel:manuals (NEXT_PUBLIC_API_URL=/api); recreate los 3 con los 3 overlays compose. Migración a dev: alembic upgrade head con DATABASE_URL migrations_user@localhost:15432.

**Cómo:** TDD por tarea; migraciones encadenadas desde 0092 (reversibles); tablas `cortex_*` tenant-less con `owner_user_id` + **test cross-owner obligatorio** (excepción Principio 1 RLS); copy honesto sobre simulación afectiva; budget caps + kill-switch en bucles autónomos (F4); catálogo LLM cerrado ADR 0021. Commit + deploy incremental por hito. ADRs 0074-0078 pasan a `accepted` por fase al implementarlas. Prioridad de [[prioridad-codigo-limpio-mantenible]].
