---
name: tanda2-mejoras-2026-07-19
description: "Mandato «implementa todo» (2ª tanda, 2026-07-19): vigía credenciales LLM, bandeja unificada del humano, retro de planes→memoria, propuestas de config del leaderboard, restore-drill, 9 tests orden-dependientes, Loki"
metadata:
  node_type: memory
  type: project
  originSessionId: 50eee157-5b9f-4f4f-85b3-9a5c1e232a6e
---

2026-07-19 (tras cerrar [[tanda-features-2026-07-19]]) — el operador aprobó en bloque («implementa todo») la 2ª tanda:

1. **Vigía de credenciales LLM** — beat ~30min que hace ping barato a cada proveedor ACTIVO (llm_provider_configs) y notifica ANTES de que un run muera por credencial caducada (dolor real: claude_sdk caducó 2 veces, runs blocked en silencio). Patrón standup: beat + núcleo inyectable + evento al pipeline notificaciones (nuevo event_type `provider_credential_expiring`/`provider_unhealthy` — OJO: ya existe "provider_credential_invalid" emitido por el worker en runs; revisar y REUSAR ese event_type si está en el registry). Files: apps/workers/src/workers/provider_watchdog.py (nuevo), beat_schedule, event_mapping+templates+catálogo si hace falta evento nuevo.
2. **Bandeja unificada del humano** — endpoint agregado GET /human-queue (planes pending_human_validation + approvals pendientes + runs needs_human_review + ask_human abiertos, con edad) + página /admin/human-queue con acciones inline (aprobar/rechazar/abrir). Es el cuello de botella nº1.
3. **Retro automática de planes** — al cerrar/cancelar un plan, el PM genera retro (atascos, reintentos, escalados, coste, lección) → memoria project_shared vía memorizer. Hook: donde el plan transiciona a completed/cancelled (reconciler/plan close) o beat que barre planes recién cerrados sin retro (marker memorize de plan). Patrón fail-open.
4. **Propuestas de configuración del leaderboard** — beat semanal(?) o botón: si una config domina (n≥5, mejor éxito Y coste), crear PROPUESTA con gate humano (¿tabla approvals existente? revisar approval_repo/pending approvals para reusar) — nunca auto-aplicar.
5. **Restore-drill automático** — beat mensual: restaurar último backup a DB efímera (scripts/restore existentes, workers/restore_per_tenant.py como referencia) + verificar conteos básicos + notificar resultado (ok/fail SIEMPRE notifica).
6. **9 tests orden-dependientes** — aislar estado global entre ficheros de la suite (rbac_resources bloque de 6 + ~3 más: tracing, run_tools_by_stack, seed_skills). Causa típica: get_settings.cache_clear/reset_engine_cache/monkeypatch env que un fichero previo deja sucio. Meta: suite completa = 0 failed.
7. **Loki desplegado** — gap de monitorización detectado (promtail/loki en docker-compose.monitoring\*.yml; cablear datasource Grafana).

**Protocolo**: ADR breve por feature nueva (0122-0126; numeración tras 0121), TDD, commits atómicos con doble-pasada anti-hook, push frecuente a plan/runs-visor-trabajo. NO tocar: decisiones del operador (ADR 0108/0117, gemma4 memoria). El deploy de imágenes al final de la tanda (mismas recetas que [[tanda-features-2026-07-19]]). Stack dev libre (sin suites corriendo).

**PROGRESO — LOS 7 BLOQUES IMPLEMENTADOS Y PUSHEADOS (2026-07-19):**

1. ✅ Vigía (c1b15685): workers/provider_watchdog.py — beat 30min, probe liveness reutilizado, transición ok→fail (provider_credential_invalid reutilizado) + recordatorio 6h + recovery (provider_recovered nuevo), estado Redis TTL 7d. Vigila TODAS las filas activas de llm_providers (cualquier consumidor puede cambiar de provider — aclaración del operador). 4 tests unit. Gotcha: get_provider_vault_store vive en routers/llm_providers, no en llm_providers/vault.
2. ✅ Bandeja (7b98cb36): GET /human-queue (4 fuentes: plans pending_human_validation, approval_requests pending, executions needs_human_review/awaiting_human_approval; RLS; orden por edad) + /admin/human-queue «Esperan tu decisión» (rojo >24h, click→pantalla real, refetch 30s) + menú. Integración 1/1 + vitest 2/2.
3. ✅ Retro (def7a850): workers/plan_retro.py — beat 15min, planes completed/cancelled 48h, marker Redis retro:plan:<id> TTL 30d, stats SQL, \_redact del standup (fail-open), INSERT memory_entries scope=project_shared type=semantic tag plan_retro embedding NULL (back-fill lo indexa). 4 tests unit.
4. ✅ Asesor (2dd5da45): workers/config_advisor.py — beat lunes 07:00, misma agregación LATERAL del leaderboard 30d; propone SOLO si actual n≥5 éxito≤60% y otra combinación n≥5 éxito≥actual+25pts; evento config_proposal (in_app) con evidencia; JAMÁS auto-aplica. 3 tests unit.
5. ✅ Drill (de1dca97 + evento en 04ddba89): workers/restore*drill.py — beat día 2 04:30, último bundle → BackupVerifier existente → restore real a DB efímera drill*<ts> (pg_restore, DROP en finally) → conteos organizations/plans/executions (0=fallo) → restore_drill_result SIEMPRE (in_app+telegram). 4 tests unit. Gotcha: report.failures es property; CheckResult.check (no .name).
6. ✅ Blindaje tests orden (8b56921c): autouse \_fresh_global_state_shield (cache_clear+reset_engine+reset_redis al ENTRAR) en rbac_resources/tracing/run_tools_by_stack/seed_skills — no se pudo reproducir el orden culpable barato; verificación definitiva = suite completa post-deploy.
7. ✅ Loki (e3b2e243): loki 3.1 + promtail (json-logs daemon) + datasource provisionado; verificado vivo (query devuelve streams). Servicios en monitoring.yml con hardening estándar.

**ENCARGO EXTRA (2026-07-19, pusheado d8b98eb0)**: manual 14 `docs/manuals/specs/14-tutorial-proyecto-ci4.manual.ts` — tutorial completo proyecto CI4 + GitHub + equipo built-in + MCP Context7/Atlassian/recomendados (16 pasos, gotchas e2e incorporados, validado con playwright --list). **PENDIENTE: generar sus PDF** cuando la suite acabe — TAMBIÉN el manual 15 Laravel (09c126b0: equipo Backend/API adoptado+especializado con persona/skills Laravel, php-phpunit|php-pest, plan de ejemplo API Sanctum, troubleshooting APP_KEY/sqlite/storage): `./scripts/dev/generate-manuals.ps1 -SkipBuild -SkipSeed -Grep "1[45]"` (+ regenerar manual-completo si se quiere el índice actualizado: sin -Grep).

**TANDA 2 CERRADA (2026-07-19, último commit 269edb9d, árbol limpio y pusheado):**

- Suite final: 3434 passed / 9 failed / 0 errors — los 9 CON NOMBRE (el GC-hang llegó antes del summary pero los tracebacks de FAILURES estaban en el log: extraer con regex `^_{5,} (nombre) _{5,}`). Los blindajes de ayer eran a ficheros equivocados; causas reales ARREGLADAS (01d1ddf2): test_revocation seed sin manifest (6), test_tech_writer candado obsoleto vs ROLE_DEFAULT_SKILLS intencional (derivar exclusivas del mapa, no pinnear), test_tracing sin llamar \_reset_for_tests de api_server.telemetry.setup (existe en línea 104), test_slug_columns upgrade(\_PREV) no-op con DB en head (downgrade explícito). Todos verdes por fichero.
- PDFs generados: 14-tutorial-ci4 (24 pp), 15-tutorial-laravel (18 pp), manual-completo recombinado **266 pp / 16 manuales** (node lib/combine-pdfs.mjs recombina sin re-capturar; el runner con -Grep NO recombina el completo).
- Gotcha PowerShell runner: -Grep "1[45]-tutorial" funciona como regex de playwright -g.

**CIERRE EN CURSO (histórico)**: builds backend (bkxdxb098: api→workers→dispatcher) + panel (b39kwo3s0) en background → redeploy 6 contenedores (sin runs en vuelo: verificar) → SUITE COMPLETA FINAL en máquina quieta (meta 0 failed; log con tee a scratchpad; el GC-hang final se resuelve matando el PID y contando desde el transcript) → memoria+informe. Los 3 event_types nuevos exigen redeploy del dispatcher ANTES de que un beat los emita (si no, event_unknown).
