---
name: adr-0130-app-preview-on-demand
description: "ADR 0130 — botón de app-preview on-demand (proyecto rama-default + plan rama-plan), 24h sin veredicto, reutiliza review-runtime; COMPLETO y DESPLEGADO"
metadata:
  node_type: memory
  type: project
  originSessionId: ed356da1-3ffb-49dc-a846-642abace2f05
  modified: 2026-07-24T19:48:13.433Z
---

ADR 0130 (`docs/05-architecture-decisions/0130-app-preview-on-demand.md`, accepted): botón para **levantar la app en preview a demanda**, reutilizando toda la maquinaria de review-runtime (proxy firmado `/api/review/{sid}/app/` + reapers + expiry + WS logs) **sin veredicto**, 24h. Dos ámbitos (el operador pidió ambos): **proyecto** (rama por defecto `git_config.default_branch`, fallback `main`) y **plan** (rama del plan, para re-inspeccionar un plan cuya validación de 48h caducó).

Esquema (migración **0118**, reversible, DESPLEGADA en dev): `review_sessions.plan_id` → **NULLABLE** + discriminador **`kind` ∈ {plan,preview}** (default plan) + 2 CHECKs (`ck_review_sessions_kind`, `ck_review_sessions_plan_or_preview`: plan_id NULL ⇒ preview). Las consultas de validación humana **excluyen previews** (`kind='plan'`): idempotencia del autostart (`review_autostart` — un preview no impide arrancar la validación real), `list_review_sessions_for_plan` (panel), barrido de hermanas al emitir veredicto. `submit_verdict` (review.py) + `_block_plan_for_expired_session` (review_runtimes.py) toleran `plan_id` NULL. Preview de plan usa key de worktree distinta (`preview-{id8}` vs `review-{id8}`) para no compartir bind-mount RW.

Backend: `_compose_review_runtime` acepta `kind`/`plan_id` opcional/`preview_ref`; `_resolve_preview_worktree_host_path` (rama por defecto, ensure_repo+align_default_branch+add+sync); NO notifica si kind='preview'. `create_review_session(plan_id opt, kind)` + `list_active_preview_sessions(project_id|plan_id)` (proyecto matchea `spec->>'project_id'`). `celery_client.enqueue_compose_review_runtime`. Builder puro `preview_launch.build_preview_request` (409 si no hay `review_image`). Endpoints (require_tenant_member): `POST/GET /projects/{id}/preview[-session]` + `/plans/{id}/preview[-session]`, idempotentes por objetivo. Hereda servicios ADR 0129. UI: `components/projects/preview-launcher.tsx` (POST → polla GET la URL firmada → abre en pestaña nueva) montado en hub de proyecto + página de plan.

**DESPLEGADO Y VERIFICADO VIVO 2026-07-24**: 4 imágenes rebuild + 6 servicios recreate (6/6 healthy), migración 0117→0118 aplicada. Smoke: las 4 rutas preview devuelven **401** por el gateway localhost:8080 (existen, requieren auth — NO 404); worker tiene el cableado; bundle admin-panel contiene «Levantar preview». **OJO gotcha**: `python -c "from api_server.main import app"` en el contenedor da un app PARCIAL (77 rutas, sin projects/plans) — NO es fiable para verificar rutas; usar **curl al gateway** como ground truth. TDD: `tests/unit/test_preview_launch.py` (builder), `tests/integration/test_preview_sessions_db.py` (migración + queries + kind-filter). Commit `fd68069c` en `plan/runs-visor-trabajo`, **EMPUJADO a origin** 2026-07-24 (junto con ADR 0129 fase 2 y el fix de review-task). Apilado sobre [[adr-0129-servicios-runtime-por-proyecto]].

REVIEW_VERDICT_TIMEOUT_S real = **48h** (no 24h; las 24h eran el idle-suspend). Relacionado: [[adr-0129-servicios-runtime-por-proyecto]], review-runtime ADR 0062/0063.
