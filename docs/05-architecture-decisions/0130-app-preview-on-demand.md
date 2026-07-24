---
title: "ADR 0130: App-preview on-demand (proyecto y plan)"
status: accepted
date: 2026-07-24
deciders: [operador]
relates_to: [0062, 0063, 0129, 0107]
---

# ADR 0130: App-preview on-demand (proyecto y plan)

## Contexto

El review-runtime (ADR 0062/0063) sirve la app construida del proyecto SOLO
mientras un plan está en `pending_human_validation`: el orquestador arranca un
contenedor con la imagen `repository_config.review_image`, lo expone tras un
proxy firmado (`/api/review/{session}/app/…`) y lo derriba al emitir veredicto o
al caducar (48h). No existía forma de **levantar esa misma app-preview a
demanda** — para probar el estado actual del repo, o re-inspeccionar el
resultado de un plan cuya validación ya expiró.

Toda la fontanería (proxy firmado, WS de logs, expiry/idle-suspend, reapers de
contenedores + bridges) se apoya en la fila `review_sessions` + la firma HMAC,
**no** en el estado del plan. Reutilizarla para un preview on-demand es barato;
el único acoplamiento al plan es el veredicto (que un preview no necesita) y la
columna `plan_id NOT NULL`.

## Decisión

Añadir un **app-preview on-demand** que reutiliza el review-runtime, con dos
ámbitos y **sin veredicto** (es solo la app en vivo, 24h):

1. **Preview de proyecto** — levanta la **rama por defecto** del proyecto
   (`git_config.default_branch`, `main` por defecto). Botón en el hub de
   proyecto.
2. **Preview de plan** — levanta la **rama del plan** (`plan/{id8}-{slug}`).
   Botón en la página del plan. Útil para re-ver un plan cuya validación (48h)
   caducó.

### Esquema

`review_sessions.plan_id` pasa a **NULLABLE** (un preview de proyecto no cuelga
de un plan) y se añade un discriminador **`kind` ∈ {`plan`, `preview`}** (default
`plan`). Dos CHECK: `kind IN ('plan','preview')` y `plan_id IS NOT NULL OR
kind='preview'` (invariante: sin plan ⇒ preview). Migración `0118`, reversible.

### Aislamiento de los flujos de plan

Las consultas de validación humana **excluyen** los previews (`kind='plan'`):
la idempotencia del autostart (para que un preview de un plan no impida arrancar
su validación real), el lookup `/plans/{id}/review-session` del panel y el
barrido de sesiones hermanas al emitir veredicto. El veredicto y el barrido de
expiración toleran `plan_id NULL` (no hay plan al que escribir). Un preview de
plan usa una **key de worktree distinta** (`preview-{id8}` vs `review-{id8}`)
para no compartir bind-mount RW con la validación del mismo plan.

### Flujo

`POST /projects/{id}/preview` (o `/plans/{id}/preview`) resuelve la imagen
(`review_image`; 409 si no hay), y **encola** `workers.compose_review_runtime`
con `kind='preview'`, `plan_id` opcional, `preview_ref` (rama) y
`expires_in_seconds=86400`. El worker crea la fila, provisiona el worktree (rama
por defecto para proyecto; rama del plan para plan) y lanza el contenedor
endurecido + (ADR 0129) los servicios declarados del proyecto. El frontend hace
**polling** de `GET …/preview-session` hasta recibir la `app_url` firmada y la
abre en pestaña nueva. Idempotente: si ya hay un preview vivo para el objetivo,
se devuelve ese en vez de lanzar otro.

## Opciones consideradas

1. **Reutilizar `review_sessions` con `kind` (elegida).** Máxima reutilización
   (proxy/reapers/expiry/logs intactos); coste = una migración pequeña + filtrar
   `kind` en 3 consultas de plan.
2. **Tabla `preview_sessions` separada.** Duplicaría proxy, firma, reapers y
   expiry — rechazada por duplicación.
3. **Atar el preview a un plan "representante".** Semántica falsa para un preview
   de proyecto y ensucia los flujos de plan — rechazada.

## Consecuencias

- **A favor:** el operador prueba la app cuando quiere (proyecto o plan) sin
  esperar a validación humana; cero duplicación de infraestructura; 24h con
  auto-teardown por los reapers existentes; hereda los servicios del proyecto
  (ADR 0129), así que una app con BD se previsualiza.
- **Riesgos / a validar:** (a) los previews comparten el **cap por-tenant** de
  review-runtimes — un exceso de previews podría bloquear una validación humana
  (aceptable v1; futura separación de cap si molesta); (b) el preview corre la
  imagen de app aportada por el proyecto (misma postura de procedencia que
  `review_image`, ADR 0063); (c) el `env` del proyecto (ADR 0129) no es Vault.
- **Relación:** extiende ADR 0062/0063 (review-runtime + `review_image`) y 0129
  (servicios por proyecto en el preview). No abre el catálogo cerrado.

## Estado de implementación

- **HECHO (2026-07-24), TDD:** migración `0118` (plan_id nullable + `kind` +
  CHECKs); modelo `ReviewSession`; repo (`create_review_session(kind, plan_id
opt)`, `list_review_sessions_for_plan` filtra `kind='plan'`,
  `list_active_preview_sessions`); worker `_compose_review_runtime`
  (`kind`/`plan_id` opcional/`preview_ref`) + `_resolve_preview_worktree_host_path`
  (rama por defecto) + key de worktree por `kind`; guards (autostart idempotencia
  `kind='plan'`, `submit_verdict`/expiry con `plan_id` NULL); `celery_client.
  enqueue_compose_review_runtime`; endpoints `POST/GET /projects/{id}/preview` y
  `/plans/{id}/preview` (+ builder puro `preview_launch.build_preview_request`);
  UI `PreviewLauncher` (hub de proyecto + página de plan). Tests
  `tests/unit/test_preview_launch.py`, `tests/integration/test_preview_sessions_db.py`.
