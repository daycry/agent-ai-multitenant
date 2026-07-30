---
name: tanda-features-2026-07-19
description: "Mandato «adelante con todo» (2026-07-19): 5 features aprobadas — MFA TOTP+QR, La Oficina v1 (miniverse con telemetría real), Replay de runs, Standup PM, Leaderboard; ADRs primero, orden MFA→Oficina+Replay→Standup→Leaderboard"
metadata:
  node_type: memory
  type: project
  originSessionId: 50eee157-5b9f-4f4f-85b3-9a5c1e232a6e
---

2026-07-19 — el operador aprobó en bloque («adelante con todo») la tanda de features que propuse (inspiración: github.com/ianscott313/miniverse, «infinitamente mejorado»):

1. **MFA TOTP con QR** — DESCUBRIMIENTO (2026-07-19): el BACKEND YA ESTÁ COMPLETO (Plan 08 task_08_09): `api_server/auth/mfa/` (totp, webauthn, challenge_store, secrets, store) + routers `mfa.py` (GET/POST /totp, /totp/enroll, /totp/confirm, DELETE /totp, /totp/verify → LoginResponse, webauthn completo) + login en `auth.py` que devuelve `mfa_required` con challenge token si hay factor confirmado. **El hueco es SOLO UI del admin-panel** (cero rastro de mfa/totp/qr en apps/admin-panel/src): (a) paso de challenge TOTP en el login tras `mfa_required`, (b) pantalla de enrolamiento en ajustes de usuario con QR (renderizar otpauth:// URI como QR en el cliente), (c) gestión (estado/desactivar/webauthn opcional). SIN ADR nuevo — la arquitectura ya está decidida en Plan 08; es cerrar el gap de UI + e2e.
2. **La Oficina v1** — mundo 2D del tenant en el admin-panel donde TODO mapea a eventos reales (nada de teatro): agentes=personajes, proyectos=mesas, dispatch=sentarse, burbuja=summary del step en vivo (WS de runs ya existente), review=acercarse a la mesa, ask_human/needs_human_review=puerta del humano, bucles=dar vueltas, córtex=personaje con afecto REAL (valence persistido). Doble clic → ficha real. v2: interacciones review/escalada; v3: córtex/asistente.
3. **Replay de runs** — scrubber de timeline que reproduce un run paso a paso desde steps_log (ya contiene todo); comparte el mapeo evento→animación con la Oficina.
4. **Standup diario del PM agente** — parte matinal del tenant (hecho/bloqueado/esperando-humano/coste) por inbox/WhatsApp; composición de cron+notificaciones+agentes existentes.
5. **Leaderboard de configuraciones** — ranking modelo×persona×skills por convergencia/coste con datos reales de runs+evals.

**Protocolo**: ADR primero para cada una (05-architecture-decisions; el operador ya aprobó la dirección — status accepted con nota). Entregables/planes en docs/roadmap. Orden de implementación: MFA → Oficina v1 + Replay → Standup → Leaderboard. TDD siempre.

**PROGRESO (2026-07-19, todo pusheado a plan/runs-visor-trabajo):**

1. ✅ MFA UI COMPLETA (765c0aed): MfaChallenge en login (canje mfa_token→sesión), pantalla /admin/settings/security (enrol 3 pasos: QR qrcode.react + secret + recovery codes + confirm; desactivar), entrada «Seguridad» personal en el menú. 7 tests vitest.
2. ✅ ADRs 0118-0121 accepted (49c090c3).
3. ✅ Oficina v1 (f19d2bf8): /admin/office — mesas=planes con runs (GET /runs verdict=running, 5s), personajes con estado del mapping, puerta del humano (needs_human_review), banco idle (GET /agents), click→run real; lib/office/mapping.ts PURO (agentVisualState/stepBubble/stepVisual, 11 tests) compartido con Replay; entrada «La Oficina» en menú. Panel 247/247.
4. ✅ Replay (07b5a374): ReplayBar (play/pausa/scrubber/velocidad, TDD timers falsos) + integración en /admin/executions/[id] (los steps aparecen hasta el índice). Panel 249/249, tsc limpio.
5. ✅ Standup PM (27930756): workers/standup.py — beat horario workers.daily_standup (crontab :05), gate por hora (platform_settings standup.enabled default True / standup.hour default 8 UTC), collector SQL, LLM redacta con \_default_llm_factory del memorizer (fail-open a estructurado), CeleryStandupNotifier → evento daily_standup (EventSpec in_app+telegram + plantillas es/en + catálogo de preferencias). 4 tests unit con fakes; candados dispatcher verdes.
6. ✅ Leaderboard (a2ecbd07): GET /runs/leaderboard (member, RLS; agregación modelo×agente con LATERAL sobre steps_log para el modelo; min_runs≥5; orden éxito/coste) + vista /admin/leaderboard «Rendimiento» con nota de atribución. Panel 251/251, tsc ok.

**TANDA CERRADA Y VERIFICADA (2026-07-19, commit final 7d290192):**

- Suite de confirmación completa: **3430 passed / 9 failed / 0 errors** (de 57→9; los 9 = bloque orden-dependiente conocido, pasan aislados; el GC-hang final se flusheó matando el PID — 3ª vez, patrón estable). El «atasco» del 81% era un tramo lento legítimo (~50 min), no un cuelgue.
- Integración: test_runs_leaderboard 1/1 (fix seed: faltaba steps_log en el INSERT; assert valida extracción del modelo vía LATERAL), test_standup_collector 2/2.
- **DEPLOY dev HECHO**: 4 imágenes reconstruidas (api-server:manuals WITH_CLAUDE, workers:ci sobre esa base, notification-dispatcher:manuals, admin-panel:manuals contexto-app-dir) + 6 contenedores recreados, stack healthy, leaderboard sirviendo DATOS REALES (fila 1: CI4 Frontend Dev × claude-opus-4-8, 8/8 done, $0.32/run).
- e2e MFA 1/1 contra el panel desplegado (gotcha: selector 'Sign in' exact — convive con botones SSO multi-provider).
- Pendiente del OPERADOR: QA visual de las 4 pantallas nuevas (Seguridad/Oficina/Replay/Rendimiento), decidir si regenerar el manual PDF con ellas, y el standup llegará solo a las 08 UTC (o probar con PUT platform_settings standup.hour).

**Restricción activa**: la suite de confirmación (tests/integration completa, task b3a5g2bk7, log scratchpad/suite-integration-… «suite-confirmacion-final.log») corre hasta ~6h — mientras corra NO tocar el stack ni la DB de test (contaminaría como el run del 18-jul); mientras tanto: ADRs, código con tests unit, frontend (vitest). Al acabar: migraciones+tests integración+deploy y reportar veredicto de la suite al operador.

Relacionadas: [[prueba-mcp-tools-skills-2026-07-18]] (fixes y recetario que el manual ya documenta).
