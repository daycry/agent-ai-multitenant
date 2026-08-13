---
title: Auditoría integral de implementación, lógica y rendimiento (2026-07-14)
version: 1.0
audit_date: 2026-07-14
last_updated: 2026-07-14
status: published
created_by: github-copilot-audit-2026-07-14
docs_language: es
baseline_branch: plan/runs-visor-trabajo
baseline_commit: ebee96806b8eb5e5ce2a9b2f3916a628a0eccf39
---

# Auditoría integral de implementación, lógica y rendimiento (2026-07-14)

Auditoría read-only del árbol actual, contrastada con `CLAUDE.md`, las convenciones,
los planes correctivos de producción y las remediaciones del 7-12 de julio. Se
distingue expresamente entre defecto vigente, deuda ya planificada, contradicción
documental y candidato refutado.

## Veredicto ejecutivo

El núcleo de aplicación muestra buena disciplina: ruff y mypy strict pasan, 2.267
tests unitarios están verdes, el frontend compila y sus 218 tests pasan, el import
MCP conserva schemas, el planning usa retrieval híbrido con scope de agente y los
flujos de ejecución recientes tienen cobertura dirigida.

La plataforma, sin embargo, **no tiene hoy todos sus gates en verde**. La suite
`tests/security` falla en cuatro invariantes, la cobertura unitaria global es
32,96% frente al objetivo escrito de 70%/80%, hay cuatro documentos simultáneos
con `status: in_progress`, y 39 esperan validación humana. Los riesgos principales
son contradicciones entre la arquitectura declarada y la ejecutable, no errores
de sintaxis o tipado.

## Alcance y método

- API, auth/RBAC, RLS, ORM y migraciones.
- Orquestador, workers, ciclo de vida, runtime, review, worktrees y guardrails.
- LLM providers, MCP, RAG/KB, memoria, costes y marketplace.
- Async, pools, Redis/Celery, WebSocket, Docker, observabilidad y salud.
- Admin panel, tests, CI y gobernanza de `docs/roadmap`.
- Cinco exploraciones especializadas en paralelo, seguidas de verificación directa
  de cada hallazgo aceptado.
- No se levantó el stack Docker completo ni se ejecutaron pruebas humanas. Los
  hallazgos que requieren PostgreSQL/Vault/egress reales quedan como validación de
  plan, no como hechos observados en producción.

## Hallazgos vigentes nuevos o ampliados

### AUD14-01 — El gate de seguridad está rojo por el overlay de monitorización

**Severidad**: alta · **estado**: confirmado · **prioridad**: P0 de CI

`pytest tests/docs tests/security -q` produce cuatro fallos. Tres convergen en
`docker/docker-compose.monitoring.yml`: `textfile-init` no declara
`no-new-privileges`, `cap_drop: [ALL]` ni AppArmor; además, los tests inspeccionan
el servicio `workers` del overlay como si fuera un servicio autónomo, perdiendo el
hardening que hereda de `docker-compose.yml` cuando ambos ficheros se renderizan
juntos.

Impacto: CI de seguridad en rojo y posibilidad de dos correcciones erróneas:
duplicar configuración en el overlay o eximir servicios sin justificar. El init es
one-shot y `network_mode: none`, por lo que el riesgo de explotación es menor que
el impacto del gate, pero el baseline escrito exige hardening uniforme.

Acción: endurecer `textfile-init` y hacer que los meta-tests evalúen la composición
base+overlay real, diferenciando servicios heredados, one-shot y host agents.

### AUD14-02 — `cortex_conversations.tenant_id` contradice el invariante RLS

**Severidad**: alta · **estado**: confirmado como contradicción de diseño

La migración `20260623_0092_cortex_threads.py` declara `tenant_id NOT NULL` sin
ENABLE/FORCE RLS. Su cabecera afirma que la tabla es tenant-less y que el campo es
solo un discriminante físico; el principio 1 de `CLAUDE.md` y
`test_every_tenant_owned_table_has_rls_enabled` consideran tenant-scoped a toda
tabla con esa columna. El aislamiento cross-owner sí tiene tests explícitos, pero
depende de filtros de aplicación sobre una sesión BYPASSRLS.

Impacto: un gate de seguridad permanentemente incompatible con una excepción
arquitectónica y defensa en profundidad inferior al resto del dominio. No se debe
resolver añadiendo una allowlist silenciosa: hace falta decidir si el córtex es
owner-global, tenant-scoped o owner-scoped con una política estructural propia.

Acción: ADR propuesto y migración/test según la opción ratificada.

### AUD14-03 — `embedding_model_id` por KB es configuración no ejecutable

**Severidad**: alta · **estado**: confirmado · bug funcional/de contrato

La API crea y protege `knowledge_bases.embedding_model_id`, pero el worker construye
`OllamaEmbedder()` antes de cargar documento y KB, por lo que usa siempre el default
global. El retrieval recibe un único vector de consulta y no agrupa resultados por
modelo. Los tests actuales verifican CRUD, inmutabilidad y forma del cliente, no que
el modelo almacenado gobierne ingesta y búsqueda.

Impacto: la UI promete una selección que no cambia el comportamiento. Un modelo de
dimensión distinta falla; uno compatible puede mezclar vectores de espacios
semánticos diferentes y degradar recall sin error visible.

Acción: decidir entre un único modelo/dimensión de plataforma (recomendación para el
alcance actual) o un registro real por modelo con reindexado y query por grupos.

### AUD14-04 — Creación de engines Celery dispersa y scope obsoleto de prod-13

**Severidad**: media · **estado**: confirmado como deuda de rendimiento

Hay 36 llamadas a `create_async_engine(settings.database_url)` en 30 módulos de
workers. Todas las instancias revisadas ejecutan `dispose()`, por lo que no se
confirma una fuga activa, pero cada tarea crea un pool efímero con defaults. La
tarea `task_prod13_08` solo enumera tres consumidores antiguos y no evita que el
patrón vuelva a crecer.

Impacto: coste de conexión/pool por task, configuración divergente y riesgo futuro
de olvidar `dispose()` o el filtro de tenant en código BYPASSRLS.

Acción: factoría única de engine/session para Celery con `NullPool`, ownership claro
y meta-test que prohíba creaciones directas fuera del módulo autorizado.

### AUD14-05 — Escrituras WebSocket sin deadline

**Severidad**: media · **estado**: confirmado

`routers/ws.py::_pump` detecta cierres con un `receive()` concurrente y cancela
`xread`, pero `await ws.send_json(event)` no tiene timeout. Un cliente que deja de
leer puede retener la coroutine hasta que la red o el servidor rompan la escritura.
Los tests cubren autenticación, aislamiento, streaming y reconexión del cliente, no
backpressure ni cliente lento.

Impacto: acumulación de tareas y memoria bajo clientes lentos o redes partidas.

Acción: deadline configurable de envío, cierre controlado, cancelación de ambas
tasks y test con `send_json` bloqueante.

### AUD14-06 — Hay liveness, pero no readiness operativa

**Severidad**: media · **estado**: confirmado

`/healthz` devuelve siempre `{"status": "ok"}`. Es correcto como liveness, pero no
existe `/readyz` no autenticado y acotado para PostgreSQL/Redis. El endpoint admin
`/admin/system-health` prueba más dependencias, pero no sirve como gate del proxy o
del orquestador.

Impacto: una instancia viva pero incapaz de servir tráfico puede seguir recibiendo
requests. Meter todas las dependencias en `/healthz` sería igualmente incorrecto:
provocaría reinicios por fallos externos.

Acción: separar liveness y readiness, con timeouts duros y documentación de qué
dependencias son críticas.

### AUD14-07 — Cobertura y frontend pasan, pero con deuda medible

**Severidad**: media-baja · **estado**: confirmado, ya parcialmente planificado

- Cobertura unitaria: 32,96%, ratchet 31%; el objetivo sigue en 70% global y 80%
  para dominio crítico. Módulos sensibles como `routers/ws.py` (20,8%),
  `orchestrator/dispatch.py` (29,3%) y `workers/execution.py` (31,8%) están bajos.
- Next build y Vitest pasan, pero ESLint emite ocho warnings de dependencias
  inestables en hooks.
- TypeScript instalado es 5.9.3 y el parser transitivo de Next 14.2.5 declara
  soporte `<5.5.0`; `package.json` permite la combinación incompatible.
- Mypy pasa sobre 591 ficheros y avisa de overrides sin uso.

Acción: mantener el ratchet de prod-02, priorizar cobertura por riesgo y alinear la
matriz Next/ESLint/TypeScript en vez de ignorar warnings.

### AUD14-08 — Documentación de diferidos contiene hechos ya obsoletos

**Severidad**: baja · **estado**: confirmado

`analisis-diferidos-2026-07-12.md` todavía afirma que el import MCP descarta
`input_schema`, mientras el código y `test_import_persists_discovered_schema` prueban
lo contrario. Este drift se suma a cuatro `in_progress`, 39
`pending_human_validation` y estados no canónicos usados por informes.

Acción: `prod-15` sigue siendo el dueño de la gobernanza; esta auditoría no cambia
estados ni reescribe historia.

## Riesgos vigentes ya cubiertos por planes existentes

| Riesgo                                     | Evidencia actual                                                   | Plan dueño           |
| ------------------------------------------ | ------------------------------------------------------------------ | -------------------- |
| Hardening incompleto de routers `/admin/*` | Solo parte de la superficie usa `require_hardened_system_admin`    | `prod-09`            |
| Junctions sin `tenant_id`/RLS              | `agent_skills`, `agent_tools`, `team_members`, `task_dependencies` | `prod-14`            |
| Guardrails incompletos por hooks/config    | El motor existe; el enforcement completo sigue diferido            | `prod-03` / ADR 0102 |
| Métricas de aplicación y alertas           | `/metrics` de API/workers sigue pendiente                          | `prod-08`            |
| Pool/transacciones/hot paths API           | Engine API con defaults y tareas de optimización pendientes        | `prod-13`            |
| Sesiones, cookies, tickets WS y headers    | Diseño y tests ya especificados                                    | `prod-09`            |
| i18n ES/EN y helpers frontend duplicados   | Implementación parcial                                             | `prod-16`            |
| Estados múltiples y cola humana            | 4 `in_progress`, 39 pendientes de validación                       | `prod-15`            |
| Fiabilidad provider/runtime y costes       | Trabajo restante ya detallado                                      | `prod-07`            |

## Candidatos refutados durante la auditoría

- El import MCP **sí** re-descubre y persiste `input_schema`; su test pasa.
- El planning **sí** pasa embedder y `agent_id` al contexto del proyecto.
- CI **sí** ejecuta tests de seguridad, documentación, cross-tenant y coverage.
- El cliente WebSocket **sí** reconecta con backoff exponencial acotado.
- `.next/` y `vault-init-output/` no tienen ficheros trackeados por git.
- LiteLLM no reapareció como provider de runtime; queda limitado al feed de precios.
- Los 36 engines Celery revisados tienen `dispose()`; se reporta consolidación y
  rendimiento, no una fuga demostrada.

## Validaciones ejecutadas

| Validación                                   | Resultado                                         |
| -------------------------------------------- | ------------------------------------------------- |
| `ruff check apps packages tests scripts`     | verde                                             |
| `python scripts/mypy_gate.py`                | verde, 591 ficheros; overrides sin uso            |
| `pytest tests/unit ... --cov-fail-under=31`  | 2.267 passed; 32,96%                              |
| tests dirigidos de embeddings/ingesta/MCP/WS | 41 passed                                         |
| `pytest tests/docs tests/security -q`        | **191 passed, 4 failed**                          |
| `npm run typecheck`                          | verde                                             |
| `npm run test`                               | 218 passed                                        |
| `npm run lint`                               | verde con 8 warnings + incompatibilidad TS/parser |
| `npm run build`                              | 57 páginas generadas; mismos 8 warnings           |

## Remediación propuesta

Los hallazgos delta se convierten en el plan
[`remediacion-auditoria-integral-2026-07-14`](./remediacion-auditoria-integral-2026-07-14.md),
creado en `pending_approval`. No se altera ningún plan activo ni se marca ninguna
tarea como completada sin su test automático.
