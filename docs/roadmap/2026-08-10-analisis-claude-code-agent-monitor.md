---
title: "Qué ideas tiene Claude-Code-Agent-Monitor que nosotros no tengamos"
date: 2026-08-10
status: informe
docs_language: es
author: claude-code (contraste de tres inventarios contra el código real del repo)
subject: https://github.com/hoangsonww/Claude-Code-Agent-Monitor
---

# Análisis: `hoangsonww/Claude-Code-Agent-Monitor` frente a nuestra plataforma

## Conclusión

**Seis ideas sobreviven al filtro. Ninguna es una tecnología; todas son una forma
de modelar el dato.** Y la mayor parte del repo externo se descarta: o ya lo
tenemos —a menudo mejor—, o existe únicamente para compensar un problema que
nosotros no tenemos (ellos observan a un CLI ajeno; nosotros ejecutamos el
agente).

Las seis, por valor, con el esfuerzo honesto:

| #   | Idea                                                                                 | Pregunta que hoy no podemos responder                          | Esfuerzo    |
| --- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------- | ----------- |
| 1   | **El paso es un intervalo, no un instante** (dos sellos reales)                      | ¿Dónde se fue el tiempo de este run?                           | **1-2 d**   |
| 2   | **El run vivo cuenta la verdad en la BD**, no solo en Redis                          | ¿Cuánto lleva gastado esto que lleva 25 minutos corriendo?     | 2-3 d       |
| 3   | **El paso es una fila consultable con facetas**, no un blob JSONB                    | ¿Qué porcentaje de mis `stack_exec` falla, y en qué proyectos? | 5-8 d       |
| 4   | **La conversación del run es un artefacto navegable** (prompt y respuesta por turno) | ¿Qué le dijimos exactamente al modelo cuando se equivocó?      | 5-8 d + ADR |
| 5   | **Alertar por la AUSENCIA de eventos**, no solo por su presencia                     | ¿Hay algún run vivo que lleve 40 minutos mudo?                 | 0,5-1 d     |
| 6   | **La relación de invocación es una columna**, no una inferencia temporal             | ¿Qué run revisó a cuál, y cuánto costó el ciclo entero?        | 1-2 d       |

Si solo se puede hacer una cosa: **la 1**. Es un día de trabajo, desbloquea la 3
entera, y hoy tenemos una UI que lleva desde el primer día pintando `0 ms` en
todos los pasos de todos los runs —un número inventado— porque
`agent_runtime/steps.py:34-45` sella `started_at` y `ended_at` con el mismo
`datetime.now(UTC)`.

**Lo que NO hay que sacar de ahí**, y conviene dejarlo escrito para no discutirlo
dentro de seis meses: la app Electron, el SQLite local, la captura por hooks del
CLI, el árbol de subagentes con reparentado heurístico, el multi-máquina por SSH,
la mascota Tabby y el modelo de precios recalculable. Las razones, en §3.

---

## 1. Lo que descartamos porque ya lo tenemos (a menudo mejor)

Esta sección es la mitad del valor del informe. Trece capacidades del repo
externo que ya están cubiertas:

| Capacidad suya                                                                    | Qué tenemos nosotros                                                                                                                                                                                                                                                                               | Veredicto                                                                                                                     |
| --------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Registro de cada invocación de tool con sus argumentos                            | `tool_call_step` guarda **args y resultado verbatim, sin truncar** (`steps.py:91-104`), y lo hace igual en vivo y persistido                                                                                                                                                                       | **Mejor nosotros.** Ellos guardan el payload crudo del hook para el agente principal, y truncan a 50 000 chars en el diferido |
| Bandeja «esto me está esperando» con taxonomía de motivos (`awaiting_reason`)     | `/admin/human-queue` (4 tipos, ordenada por antigüedad), `/admin/approvals`, `/admin/inbox`, la tool `ask_human` (ADR 0114), las 13 categorías de política de aprobación y `workers.escalate_human_assignments`                                                                                    | **Mejor nosotros.** Su `awaiting_reason` es una inferencia sobre hooks; el nuestro es estado de dominio                       |
| «El observador nunca daña a lo observado» (hooks fire-and-forget)                 | El bus de eventos ya es best-effort explícito en sus ocho publicadores: `events.py:112`, `:188`, `:227`, `:340`… con el comentario literal `never fail the caller`                                                                                                                                 | **Igual.** Misma decisión, tomada por su cuenta                                                                               |
| Reconciliación del estado tras perder eventos (liveness probe, sesiones colgadas) | `workers.sweep_stale_executions` (7 h + **huérfanos sin contenedor a los 5 min**), `reap_orphans`, `reconcile_pipeline_state`, `provider_watchdog`, y el servicio `apps/watchdog` con backoff exponencial                                                                                          | **Mejor nosotros.** Comprobamos el contenedor por etiqueta; ellos buscan un proceso por `cwd` y lo desactivan en Windows      |
| Alertas + webhooks con reintentos + push                                          | `notification-dispatcher` (8 canales), preferencias por scope con quiet hours, `guardrail_alert_rules` (umbral + ventana + debounce), y 11 reglas de Alertmanager → `/internal/alerts/ingest`                                                                                                      | **Muy por delante nosotros.** Solo sobrevive una señal concreta que nos falta → idea 5                                        |
| Enmascarar secretos y URLs en toda respuesta de API                               | El secreto de canal **nunca se devuelve**: solo `has_secret` + `secret_source`, y el escritor rechaza un `config` que traiga `secret`/`token`/`password` en claro (`schemas/notifications.py`)                                                                                                     | **Ya cubierto**                                                                                                               |
| Reproducir un run paso a paso                                                     | `components/executions/replay-bar.tsx` (ADR 0119)                                                                                                                                                                                                                                                  | **Igual** — y ambos sin ritmo real. El nuestro deja de ser un juguete en cuanto exista la idea 1                              |
| Lanzar y reanudar runs desde el panel (`dashboard_runs`)                          | Dispatch desde el Kanban/plan, **Cancelar** y **Redirigir** (guía de un solo uso inyectada en la siguiente iteración)                                                                                                                                                                              | **Mejor nosotros.** Ellos relanzan; nosotros además intervenimos en caliente                                                  |
| Progreso de tareas reconstruido de `TodoWrite`, con un campo `confidence`         | Nosotros **somos** la fuente: tareas con estados de dominio, DAG con dependencias, doble Kanban, `retry_count`, `task_audit_events`                                                                                                                                                                | **Mejor nosotros.** No tenemos que reconstruir con confianza parcial lo que ya es dato primario                               |
| Avisador ambiental (Tabby, 8 humores)                                             | La Oficina (ADR 0118): 7 estados visuales sobre telemetría real, burbuja por WS con el último step de cada run, HUD de 4 contadores. Y encima un córtex afectivo que **ya lee el pulso de la plataforma** cada 15 min (`cortex/platform_affect.py`) y **escribe primero** (`cortex/initiative.py`) | **Mejor nosotros**, con diferencia                                                                                            |
| Precios con dimensión temporal y recálculo histórico                              | `price_snapshot` congelado por llamada (`execution_repo.py:96-140`) + `workers.sync_model_prices`                                                                                                                                                                                                  | **Decisión opuesta y deliberada.** Cambiar una tarifa NO debe reescribir lo ya facturado. No es un hueco                      |
| Estadísticas de efectividad por tipo de agente                                    | Leaderboard modelo×agente (n≥5, 90 días), shadow-evals al 5 %, plan 14                                                                                                                                                                                                                             | **Igual o mejor**                                                                                                             |
| Explorador de configuración con backup atómico antes de escribir                  | Nuestra configuración vive en BD con `audit_log` y RBAC, no en ficheros del usuario                                                                                                                                                                                                                | **No comparable; cubierto**                                                                                                   |

---

## 2. Lo que descartamos porque no aplica

| Suyo                                                                                           | Por qué no encaja                                                                                                                                                                                                                                                                                                                                          |
| ---------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **App Electron con tray, SQLite local, statusline en la terminal, extensión de VS Code**       | Somos un servidor multi-tenant: el dato vive en PostgreSQL con RLS y `tenant_id` en cada tabla (principio 1). Un SQLite por máquina de desarrollador rompe eso de raíz. **El fondo de la idea —llevar el dato adonde el usuario ya mira— sí lo tenemos**, y por ocho canales (Telegram, Slack, Teams, Discord, email, SMS, WhatsApp, in-app)               |
| **Captura por hooks del CLI + relectura incremental del transcript JSONL**                     | Toda su arquitectura (8 hooks, caché por `(path, mtime, size)`, watchdog de 15 s, sonda de proceso, autocuración de errores de API) existe **porque no controlan al observado**. Nosotros lanzamos el contenedor y el bucle emite el paso: la telemetría es de primera mano. Copiar su mecanismo sería importar una compensación sin el problema           |
| **Árbol de subagentes: reparentado diferido, dedup por ventana de ±30 s, detección de ciclos** | No tenemos subagentes por diseño: `agent_invoke` devuelve `_not_wired` y el SDK de Claude lleva `Task`/`Agent` en `disallowed_tools` (`claude_agent.py:62-79`). Su heurística de identidad «mismo tipo, ±30 s» es exactamente la ambigüedad que nuestro diseño evita. **Lo único que sobrevive es que la relación de invocación sea una columna** → idea 6 |
| **Multi-proveedor `claude`/`codex` y multi-máquina por SSH con cursores de ingesta**           | Nuestro multi-proveedor es otra cosa (ADR 0021, `packages/shared-llm`) y el multi-máquina está explícitamente fuera de alcance: Docker Compose en una sola máquina                                                                                                                                                                                         |
| **Bind a `127.0.0.1`, `hostGuard` anti-DNS-rebinding, token separado para `/api/hooks/*`**     | Modelo de amenaza de herramienta local de un desarrollador. El nuestro es JWT + RBAC + RLS + Vault + ingress, y es más exigente                                                                                                                                                                                                                            |
| **Tabby «Ask» sin LLM: pattern matching local contra el estado en memoria, con handoff**       | Idea elegante y barata, pero nuestro asistente ya responde con tool-calling sobre datos reales: el riesgo de invención está cubierto, y el ahorro de tokens de cinco preguntas factuales es ruido frente al gasto de un solo run. **Descartada por valor, no por dificultad**                                                                              |
| **El monitor expuesto como servidor MCP para que el agente consulte su propia telemetría**     | Sin verificar en su código (el inventario A lo marca como afirmación del README). Y en un servidor multi-tenant es una superficie de fuga cross-tenant y un incentivo raro: un agente que puede leer su propia métrica puede optimizarla. Si algún día se hace, el consumidor natural es **el córtex del operador**, no el agente de la tarea              |

---

## 3. Las ideas que quedan

### Idea 1 — El paso es un intervalo, no un instante

**Esfuerzo: 1-2 días. Valor: el más alto del informe.**

**La pregunta que hoy no podemos responder:** «este run tardó 40 minutos, ¿en
qué?». Hoy el único dato temporal real de un run son sus dos extremos
(`executions.started_at` y `completed_at`). Un run de 300 pasos es una caja negra
con una lista ordenada dentro. Cuando salta `max_wall_clock_exceeded` no sabemos
si se lo comió una llamada al LLM o un `pytest` de veinte minutos.

**La idea, separada de su implementación:** ellos emiten **dos filas** por
invocación (`PreToolUse` y `PostToolUse`), cada una con su `created_at`. Nosotros
emitimos **una**, construida _después_ de que la tool ya corrió, con
`started_at == ended_at`. Ojo con la atribución honesta: **ellos tampoco calculan
la duración** —su propio inventario lo admite— pero su forma de dato la hace
derivable y la nuestra la imposibilita. La idea aprovechable es la forma, no su
uso.

**Qué habría que construir:** que `graph.py` selle el instante _antes_ de ejecutar
(la tool, el batch read-only del ADR 0111, la llamada al modelo) y lo pase a
`steps.py`, que hoy lo inventa en `_base`. Son ~6 puntos de emisión. No hace falta
tabla nueva: el campo ya está en el esquema del step y la UI ya llama a
`fmtDuration`; simplemente empezaría a decir la verdad. Requiere rebuild y
redespliegue de la imagen `agent-runtime`.

**El detalle que no hay que olvidar:** los runs antiguos seguirán con
`started_at == ended_at` para siempre. La UI debe pintar **«sin dato»**, no
«0 ms» — ver §4.

**Lo que desbloquea:** la barra de replay deja de avanzar a 800 ms/paso inventados
y pasa a reproducir el ritmo real; el timeline se puede ordenar por duración; y
la idea 3 pasa de ser un índice a ser una herramienta.

---

### Idea 2 — El run vivo cuenta la verdad en la BD, no solo en Redis

**Esfuerzo: 2-3 días. Valor: alto.**

**La pregunta:** «llevo 25 minutos con este run abierto, ¿cuánto lleva gastado y
cuántas iteraciones lleva?». Hoy: cero. La fila nace con `steps_log=[]` y todos
los contadores a cero, y no se escribe nada hasta `finalize_execution`. El panel
hace polling cada 5 s y lee ceros; la lista de runs pinta `—` en duración, tokens
y coste mientras corre. Si el contenedor se pierde y nadie finaliza, el
`steps_log` queda vacío para siempre aunque Redis tenga los pasos.

**La idea:** en su sistema, **el coste y los tokens se recalculan en cada
extracción**, así que un run vivo tiene números vivos —y por eso pueden ponerlos
en la statusline. La idea es que _el estado agregado de un run en curso es un dato
de primera clase_, no un subproducto del cierre.

**Qué habría que construir.** Dos caminos, y el barato es el bueno:

- **(a) Sin escrituras en BD:** cuando `status == "running"`, `GET /executions/{id}`
  lee la cola del stream `exec:{id}` y agrega ahí mismo tokens, coste, iteraciones
  y `last_step_at`. Cero write amplification sobre una tabla que desde el ADR 0151
  está particionada. Es lo que recomiendo.
- **(b) `UPDATE` incremental por paso:** más simple de consultar, pero mete una
  escritura por paso en la tabla más caliente del sistema.

El subproducto de (a) es `last_step_at`, que es exactamente lo que la idea 5
necesita.

---

### Idea 3 — El paso es una fila consultable con facetas, no un blob JSONB

**Esfuerzo: 5-8 días. Valor: alto, con retorno más lento.**

**Las preguntas:** «¿qué porcentaje de mis `stack_exec` falla, y en qué
proyectos?», «¿qué tool consume más reloj?», «¿cuánto de este run se fue en
self-review y cuánto en trabajo?», «¿qué modelo se atasca más en el nodo `act`?».
Ninguna se puede responder hoy: la contabilidad agregada solo cuenta **cuántas**
tools se llamaron (`executions.tool_call_count`), no cuáles; para saber la tool
hay que escanear el JSONB.

**La idea:** su tabla `events` con `GET /api/events` filtrable por
`event_type`/`tool_name`/`agent_id`/`session_id` y un `GET /api/events/facets` que
devuelve los valores distintos. Nada sofisticado —su búsqueda es un `LIKE` sobre
la columna `data`— pero el _encuadre_ es correcto: **la unidad de análisis es el
paso, y vive donde se puede agrupar**.

**Qué habría que construir:** una tabla `execution_steps` particionada por rango
(el patrón que ya fija el ADR 0151) con `tenant_id`, `execution_id`, `index`,
`kind`, `node`, `tool`, `status`, `started_at`, `ended_at`, `duration_ms`,
`cost_usd`, `tokens_*`; los `args`/`result` pueden quedarse en `steps_log`, que ya
está medido (9,5 KiB de media por run, 64 KiB el máximo). Más un endpoint de
facetas y filtros reales en `/admin/runs`, que hoy no expone ni los que el backend
ya acepta (`agent_id`, `role`, `plan_id`, `model`, `min_cost`, `window_days`).

**El coste escondido, dicho antes de que sorprenda:** `executions` es particionada
con PK compuesta desde prod-13, así que la FK desde la tabla nueva cae de lleno en
el problema que documenta el **ADR 0154**. No es un `CREATE TABLE`.

**Dependencia:** sin la idea 1 esta tabla nace sin la columna que la haría útil.
Hacerlas en este orden.

---

### Idea 4 — La conversación del run es un artefacto navegable

**Esfuerzo: 5-8 días + un ADR. Valor: alto para la convergencia; caro.**

**La pregunta:** «¿por qué el agente insistió en leer el mismo fichero?», «¿qué
contexto tenía cuando decidió esto?». Hoy `model_call_step` guarda modelo, tokens
y coste; **no guarda el prompt ni la respuesta**, el preámbulo de sistema se
ensambla dentro del contenedor y no se persiste, y no hay visor de logs del
contenedor en ninguna pantalla. Se deduce del `summary` (que es
`decision.rationale`) y de los `args` de las tools. Este repo lleva media docena
de auditorías sobre «runs que no convergen»; se han hecho todas a ciegas sobre
este punto.

**La idea:** su `GET /api/sessions/:id/transcript` con cuatro modos de paginación
—incremental para seguir en vivo, histórico hacia atrás, offset y ventana— y
bloques tipados (`text`, `thinking`, `tool_use`, `tool_result` con `is_error`).
Es decir: **la conversación completa es navegable, paginada y tipada**, no un log
que se vuelca entero.

**Qué habría que construir:** persistir prompt y respuesta por turno con un
paginador equivalente y un visor. Y aquí está lo que exige ADR antes que código:

- **Volumen.** Hoy `steps_log` son 9,5 KiB de media por run. Guardar el hilo
  completo —que desde el ADR 0110 es acumulativo— lo multiplica por un orden de
  magnitud largo.
- **Contenido sensible.** Un prompt lleva código del cliente y puede llevar PII.
  Los guardrails ya corren en `pre_llm`/`post_llm`: la redacción debería reutilizar
  ese motor, no inventar otro.
- **Retención.** Es la séptima familia append-only. El ADR 0151 decidió particionar
  las seis que hay; esta habría que meterla en la misma conversación.

Recomendación: **hacerla después de 1, 2 y 5**, y con un interruptor por proyecto
(off por defecto en proyectos de cliente externo).

---

### Idea 5 — Alertar por la AUSENCIA de eventos

**Esfuerzo: 0,5-1 día la parte que importa. Valor: medio-alto por lo que cuesta.**

**La pregunta:** «¿hay algún run vivo que lleve 40 minutos sin emitir un paso?».
Hoy no se puede: nuestros 18 tipos de evento de notificación son **transiciones de
dominio discretas** (`execution_failed`, `task_blocked`, `plan_rejected`…). Todas
disparan porque _pasó_ algo. Ninguna dispara porque _dejó de pasar_.

**La idea:** sus reglas `inactivity` (N minutos sin eventos) y `status_duration`
(agente atascado en un estado), y sobre todo el humor `stuck` de Tabby —«hay
sesiones vivas pero llevan 10 minutos en silencio»—, que codifica exactamente el
fallo que un humano tarda horas en notar.

**Lo que ya tenemos y acota el hueco:** Alertmanager ya tiene reglas por ausencia
y por permanencia (`MetricsSamplerStale` con `time() - heartbeat > 300`,
`HumanApprovalsStale` con `oldest_age > 86400`, `TasksBlockedHigh` con `for: 30m`)
y la cadena de entrega hasta los canales del System Admin está montada. **La
maquinaria existe; falta la señal.**

**Qué habría que construir:**

- **La parte barata y valiosa (0,5-1 día):** un gauge
  `agentic_execution_silence_seconds` (máximo sobre las ejecuciones `running`,
  alimentado por el `last_step_at` que produce la idea 2) en
  `workers/queue_metrics.py`, más una regla en
  `docker/monitoring/prometheus/rules/app_alerts.yml`. Se acabó.
- **La parte cara y menos valiosa (3-5 días):** generalizar
  `guardrail_alert_rules` a reglas de alerta configurables por tenant sobre
  cualquier métrica («avísame si un run supera 5 $»). Útil, pero es producto, no
  observabilidad, y puede esperar.

---

### Idea 6 — La relación de invocación es una columna, no una inferencia

**Esfuerzo: 1-2 días. Valor: medio.**

**Las preguntas:** «¿qué run revisó a cuál?» y «¿cuánto costó el ciclo completo de
esta tarea, separando trabajo de revisión?». Hoy el reviewer IA **no referencia la
ejecución que revisa**: `_apply_review_verdict` aplica el veredicto a la _task_ y
deja rastro en `task_audit_events` con `actor="ai-reviewer"`. El vínculo
run-reviewer → run-revisado es inferencia por `task_id` y orden temporal.

**La idea:** su `agents.parent_agent_id` con FK autorreferencial. **Lo que hay que
quedarse es la columna, no su relleno**: ellos la rellenan con una heurística
temporal («mismo tipo, ±30 s») porque no controlan la invocación. Nosotros sí la
controlamos —el worker lanza el run de review a sabiendas— así que en nuestro caso
la columna se rellena con certeza, no con una ventana de tolerancia.

**Qué habría que construir:** `executions.review_of_execution_id` (y, si algún día
se cablea `agent_invoke`, `parent_execution_id`), cableada en el worker en el punto
donde ya se sabe la respuesta, expuesta en `ExecutionResponse` y pintada en la
ficha. **Mismo aviso que la idea 3:** la auto-FK sobre una tabla particionada con
PK compuesta es el caso del ADR 0154.

**Un extra casi gratis por el camino:** `ExecutionResponse` tampoco devuelve
`agent_name`, `task_title`, `plan_id`, `model`, `prompt_version` ni
`runtime_image_digest` —los dos últimos se persisten y no llegan a ninguna
pantalla—, y por eso el timeline se titula «Timeline de ejecución» sin decir de
quién ni de qué. Eso no es idea de nadie: es un hueco nuestro que sale al tirar de
este hilo.

---

## 4. Una nota transversal que no es una feature

Su producto marca las tarjetas transitorias de Codex como no navegables y las
excluye de las APIs durables, y mete un campo `confidence` explícito en el
snapshot de tareas. Es decir: **prefieren decir «esto es provisional» a inventar
una fila**.

Nosotros hacemos lo contrario en dos sitios concretos, y ninguno es un bug de
código sino una decisión de UI:

- La ficha de ejecución pinta **`0 ms`** en todos los pasos de todos los runs
  desde el primer día. `0 ms` es un número plausible; «sin dato» es la verdad.
- El docstring de `/admin/runs` afirma «a running row shows live status + elapsed»
  y `elapsed` no se calcula en ninguna parte del panel.

La lección no cuesta nada y hay que aplicarla al ejecutar la idea 1: **cuando un
dato no está, la UI dice «—», no «0»**. Si no, el arreglo llega y nadie se entera
de que antes se mentía.

---

## 5. Dónde no hay nada aprovechable

Se dice explícitamente para que nadie vuelva a buscar ahí:

- **Seguridad: nada.** Su modelo de amenaza —herramienta local en `127.0.0.1`— es
  estrictamente más simple que el nuestro. Lo único que hacen bien (enmascarar
  secretos en las respuestas) ya lo hacemos.
- **Notificaciones y canales: nada**, salvo la señal de la idea 5. Nuestro
  `notification-dispatcher` con 8 canales, preferencias por scope, quiet hours y
  reintentos está a bastante distancia de sus `webhook_targets` + VAPID.
- **Modelo de precios: nada.** Su recálculo histórico es la decisión contraria a
  nuestro `price_snapshot`, y la nuestra es la correcta para auditar. Su
  `intro_until` (tarifas promocionales con caducidad) solo nos serviría para
  _estimar_ mejor a futuro, y es marginal.
- **Visualización: casi nada.** Sus 14 visualizaciones D3 son casi todas del árbol
  de subagentes que no tenemos (`ErrorPropagationMap`, `ConcurrencyTimeline`,
  `ModelDelegationFlow`, `SubagentEffectiveness`…). Lo único trasladable —colorear
  un DAG por estado de ejecución— ya está identificado como hueco propio en
  `lib/plan-dag.tsx`, que pinta la especificación del plan y no colorea por estado
  ni por progreso. Para verlo no hacía falta este repo.
- **Orquestación y ejecución: nada.** Ellos no ejecutan; observan.

---

## 6. Método

Contraste de los tres inventarios de partida contra el código real, no contra la
documentación. Verificado en esta sesión antes de descartar nada:

- `apps/notification-dispatcher/src/notification_dispatcher/event_mapping.py` (el
  `EVENT_REGISTRY` completo y su resolución de destinatarios).
- `apps/api-server/src/api_server/db/guardrail_alert_rule.py` (umbral, ventana,
  debounce).
- `docker/monitoring/prometheus/rules/app_alerts.yml` (las 11 reglas, incluidas
  las de ausencia y permanencia).
- `apps/workers/src/workers/maintenance/stale_sweeper.py` y
  `beat_schedule.py` (los 34 beats).
- `apps/api-server/src/api_server/events.py` (best-effort en los 8 publicadores).
- `apps/api-server/src/api_server/cortex/{initiative,platform_affect}.py`.
- `apps/api-server/src/api_server/schemas/notifications.py` (enmascarado).
- `docker/agent-runtimes/agent-runtime/agent_runtime/steps.py:34-45` (el `0 ms`).
- ADR 0110, 0151, 0154.

**Sesgo declarado:** el inventario del repo externo (A) advierte que su
documentación publicada describe un producto más granular que su código, con
cuatro contradicciones verificadas. Este informe se apoya solo en lo que ese
inventario marca como leído en fuente. Lo que ahí queda como «afirmación del
README sin verificar» —los 97 tools MCP— está descartado en §2 por otras razones,
pero conviene saber que además no consta.
