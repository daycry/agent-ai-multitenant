---
title: Auditoría de verificación post-remediación AUD16 + nuevos hallazgos (2026-07-17)
version: 1.0
audit_date: 2026-07-17
last_updated: 2026-07-17
status: published
created_by: claude-fable-5-audit-2026-07-17
docs_language: es
baseline_branch: plan/runs-visor-trabajo
baseline_commit: 2317fea9
scope: verificacion-aud16, nuevos-hallazgos
---

# Auditoría de verificación (2026-07-17) — AUD16 verificada + nuevos hallazgos N-01…N-17

Pasada final ordenada por el operador tras implementar y desplegar la
remediación AUD16 completa. Dos frentes read-only sobre el stack vivo recién
recreado: verificación fix a fix de lo desplegado, y caza de hallazgos NUEVOS
(sin repetir nada con dueño: AUD14 → plan 07-14, prod-08/09/14, gated AUD16).

## Veredicto ejecutivo

1. **La remediación AUD16 está VERIFICADA funcionando en vivo, sin ninguna
   regresión.** Las 6 imágenes nuevas corren; el envelope, los prompts, el
   safeguard `stack_exec_unavailable`, el inbox de plataforma (endpoints +
   RLS `tenant IS NULL` + bundle del panel), las colas con exchange propio,
   el alias de precios resolviendo `claude_sdk→anthropic/claude-opus-4-8`
   contra la BD real, el default de memorización con fracasos, 0 duplicados
   exactos vivos tras la consolidación, las 4 reglas nuevas de Prometheus
   cargadas con heartbeat fresco y 4 colectores `up=1`, el drain de
   `task_comment` y el anuncio sin tools fantasma, el evento
   `provider_credential_invalid` registrado, y el audit de login presente.
   `CadvisorDegraded` se armó sola tras el arranque (comportamiento diseñado
   en Docker Desktop). Tres puntos quedan «no observables» hasta el primer
   uso real (contenido de un envío nuevo, snapshot de un run nuevo, primera
   fila de audit_log) — son exactamente los tests humanos del plan.

2. **La caza de hallazgos nuevos confirma un sistema mucho más maduro, con
   una cola corta y concreta de mejoras (N-01…N-17)**: un HIGH (el
   device-flow de Copilot repite el patrón del ReadTimeout ya pagado dos
   veces), operabilidad (healthchecks de los proxies de egress que no pueden
   fallar, logging de workers sin niveles), higiene de datos (sin retención
   en tablas append-only, streams `exec:*` sin TTL, 5 índices FK calientes
   ausentes) y mantenibilidad (4 ficheros backend >1600 líneas creciendo).
   Seguridad práctica en verde: 0 secretos en logs, 0 endpoints sin guard,
   CORS estricto, contratos cross-package sincronizados (salvo los
   comentarios de N-05, que ahora mienten a propósito de AUD16-02).

## Frente 1 — Verificación de la remediación AUD16

| Área                                    | Veredicto | Evidencia clave                                                                                                                                                                                                                                     |
| --------------------------------------- | --------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Imágenes desplegadas                    | ✅        | 6 imágenes creadas 2026-07-16 21:5x UTC; los 4 contenedores workers sobre el mismo imageID nuevo; el worker lanza `agent-runtime:v1` nuevo                                                                                                          |
| A1/A2/D2 (envelope, prompts, safeguard) | ✅        | En la imagen del runtime: ambas tools `type=function`; `search_code` fuera de ambos prompts; `SafeguardCode.STACK_EXEC_UNAVAILABLE` presente                                                                                                        |
| B1/B2/F1 (notifs)                       | ✅        | `subject/body` + receipt nullable en BD (head 0113) + policy `notification_logs_platform_read`; rutas `/notifications/platform/logs*` en OpenAPI vivo; cola priority con exchange/rk propios en el banner del dispatcher; página inbox en el bundle |
| C1 (precios)                            | ✅        | `lookup_current_price_for_call(claude_sdk, claude-opus-4-8)` → fila anthropic vigente ($5/$25, source litellm) contra la BD real                                                                                                                    |
| C2/C3/C4 (memoria)                      | ✅        | default con fracasos en la imagen viva; `_resolve_inherited_model_config` importable; 0 grupos duplicados exactos (91 soft-deleted tras consolidar)                                                                                                 |
| E1/E2/E3/E4 (monitorización)            | ✅        | 4 reglas nuevas cargadas; heartbeat 17 s de frescura; `collector_up` 4×1; node-exporter healthy; `CadvisorDegraded` pending→firing por diseño                                                                                                       |
| D1/D3/D5/F6                             | ✅        | drain importable; `SYSTEM_TOOL_NAMES` sin las 3 tools sin consumidor; `provider_credential_invalid` en el registry vivo; `_audit_login` con sus 3 call sites                                                                                        |
| Arranque                                | ✅        | RestartCount=0, 0 ERROR/Traceback en los 8 servicios de app, todos healthy                                                                                                                                                                          |

Anotaciones sin acción obligatoria: tag residual `agentic-platform/agent-runtime:v1`
(07-12, sin uso — limpieza cosmética); `HostSwapActive` firing (condición del
host WSL2, prueba de que la cadena funciona).

## Frente 2 — Hallazgos nuevos (N-01…N-17)

### Resiliencia y errores

- **N-01 · HIGH · S** — El device-flow de Copilot repite el bug del transporte
  crudo: `start_device_flow` (`copilot.py:173-179`) y `poll_device_flow_once`
  (`:237-246`, con `resp.json()` sin comprobar status → `JSONDecodeError` ante
  un HTML de rate-limit) no usan `typed_transport_errors`, y sus endpoints
  (`routers/copilot_device_flow.py:144-150,187-193`) solo capturan `AuthError`
  → 500 crudo. Mismo patrón ya corregido dos veces (córtex, F4).
- **N-02 · MEDIUM · S** — Guardrails: un check que crashea es fail-OPEN por
  defecto (`shared_guardrails/pipeline.py:104-120`, `on_error="warn"`). Para
  la categoría security el default correcto es block.
- **N-03 · MEDIUM-LOW · S** — El contador de gasto autónomo del córtex traga
  el fallo de persistencia (`cortex/autonomy.py:139-141`) → infra-contabiliza.
- **N-04 · LOW · S** — El cliente MCP clasifica auth vs transporte por
  string-matching (`shared_mcp/client.py:194-197`).

### Contratos

- **N-05 · MEDIUM · S** — Los comentarios «MUST stay in sync» de
  `SYSTEM_TOOL_NAMES` (runtime `builtin_families.py:213` vs worker
  `agent_tool_schemas.py`) ahora MIENTEN: la divergencia es intencional
  (AUD16-02, anunciado ⊂ registrado) pero quien «corrija la deriva»
  reintroduce el éxito falso. Reescribir ambos + test de contrato.

### Operabilidad

- **N-06 · MEDIUM · S** — Workers sin configuración de logging: TODO sale
  como WARNING vía Celery (imposible alertar por nivel; debug en prod).
  Agrava: el beat no-op `idle_sweep_pools` late 2.880 veces/día.
- **N-07 · MEDIUM · S** — Healthchecks de egress-proxy y registry-proxy
  terminan en `|| true` (`docker-compose.yml:390,420`): un tinyproxy muerto
  queda healthy y los `depends_on: service_healthy` no protegen nada.
- **N-08 · LOW · S** — Healthchecks menores: clamav no dialoga con el daemon;
  admin-panel acepta 404; docker-socket-proxy y vault-unsealer sin check.
- **N-09 · LOW · S** — `Duplicate Operation ID` en cada arranque
  (`routers/review.py:388-391`, api_route con 7 métodos) — OpenAPI
  contaminado; `include_in_schema=False`.

### Seguridad práctica (resto en verde)

- **N-10 · LOW-MEDIUM · S** — Sin `Content-Security-Policy` ni
  `Permissions-Policy` en el proxy de prod (`proxy_generator.py:88-98`).
- **N-11 · LOW · S** — Los WS hacen `accept()` antes de validar el token
  (inconsistente con `review.py`, que valida antes); añadir test CI de
  marcador de auth por ruta.

### BD y Redis

- **N-12 · MEDIUM · M** — Cero retención en tablas append-only
  (`notification_logs`, `task_audit_events` —crecerá más con D3—,
  `llm_usage_events` —1 fila/turno—, `incoming_webhook_events`,
  `price_sync_audit`, turnos de córtex/asistente). Task `prune_retention`
  con ventanas por tabla + beat diario; rollup mensual del usage.
- **N-13 · MEDIUM · S** — Streams Redis `exec:{id}` sin TTL ni borrado: 207
  acumulados desde junio (existe `delete_*` para conv/doc, no para exec).
  `EXPIRE` al finalizar + barrido en `reap_orphans`.
- **N-14 · MEDIUM-LOW · S** — 5 FKs calientes sin índice:
  `task_dependencies.depends_on_task_id` (consultado en CADA task done),
  `tasks.plan_id`, `memory_entries.source_execution_id`,
  `executions.agent_id`, `notification_log_reads.log_id`. Una migración.
- **N-15 · LOW · S** — Llaves huérfanas en Redis DB3 (pre-fix F5); limpiar y
  anotar en runbook.

### Mantenibilidad

- **N-16 · MEDIUM · M-L** — Cuatro gigantes backend creciendo con cada fix:
  `providers.py` 1637 (+551 desde 07-02), `graph.py` 1670 (+401),
  `dispatch.py` 1634 (+305), `execution.py` 1691 (+233). Split por
  caracterización (método del tramo #9 del frontend), empezando por
  providers.py.
- **N-17 · LOW · S** — Docstring stale en `routers/marketplace.py:39`.

## Top-5 recomendado (impacto/esfuerzo)

1. **N-01** — transporte tipado en el device-flow de Copilot (~2 líneas por
   método + captura en los 2 endpoints).
2. **N-07** — quitar `|| true` de los healthchecks de los proxies de egress.
3. **N-13** — TTL/limpieza de los streams `exec:{id}`.
4. **N-06** — logging con niveles en workers (+ retirar el beat no-op).
5. **N-14** — migración con los 5 índices FK calientes.

Siguientes: N-12 (retención — el único M imprescindible antes de operación
continua), N-02 (fail-closed en checks de seguridad), N-05 (contrato del
anuncio de tools).

Esta auditoría no cambia estados del roadmap. La conversión de N-01…N-17 en
plan de remediación queda a decisión del operador.
