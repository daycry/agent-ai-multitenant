---
title: Guardrails declarativos — Referencia del motor, tipos, acciones y observabilidad
audience: backend-dev, ai-engineer, architect, security
phase: 11-guardrails-precios
updated: 2026-08-01
---

# Guardrails declarativos — Referencia

Esta página documenta el motor de guardrails del Plan 11: los cuatro puntos de
hook, la configuración declarativa en capas con campos bloqueables, las seis
acciones, los doce tipos built-in con su configuración, y la observabilidad
(`guardrail_events` + dashboard del tenant). Para el catálogo de precios ver
[`pricing.md`](./pricing.md); para la matriz de roles general ver
[`rbac.md`](./rbac.md); para los ADRs de fondo ver
[ADR 0035](../05-architecture-decisions/0035-guardrails-declarativos-en-capas-catalogo-precios-usd-snapshot.md)
(guardrails + precios) y
[ADR 0001](../05-architecture-decisions/0001-postgres-rls-from-day-one.md)
(RLS).

## El motor en una frase

`packages/shared-guardrails` expone una `GuardrailPipeline` **pura** (sin DB,
sin I/O) que, dada una config declarativa y un `GuardrailContext`, ejecuta los
guardrails configurados para un hook y devuelve un `PipelineDecision`. El host
(api-server / workers) resuelve la config por capas, corre el pipeline y aplica
la acción / persiste el evento.

## Los cuatro puntos de hook

| Hook        | Cuándo corre                              | Campo del contexto que inspecciona |
| ----------- | ----------------------------------------- | ---------------------------------- |
| `pre_llm`   | antes de enviar el prompt al modelo       | `prompt`                           |
| `post_llm`  | después de la respuesta del modelo        | `response`                         |
| `pre_tool`  | antes de ejecutar una tool solicitada     | `tool_name` + `tool_args`          |
| `post_tool` | después de que una tool produce resultado | `tool_name` + `tool_result`        |

`GuardrailContext.metadata` lleva contexto libre (tenant_id, project_id, agent,
model, allowed_tools, coste acumulado, …) que los guardrails leen sin ampliar el
dataclass; el motor lo trata como opaco.

## Configuración declarativa en capas (plataforma → tenant → proyecto)

La config se autoría como YAML/dict: por hook, una lista ordenada de guardrails
`{type, action?, config?, id?}`. Se compone en **tres capas** least- a
most-specific (`layers.py`):

| Capa       | Quién la configura | Regla                                                               |
| ---------- | ------------------ | ------------------------------------------------------------------- |
| `platform` | System Admin       | baseline que todo tenant hereda; puede marcar un guardrail _locked_ |
| `tenant`   | Tenant Admin       | overrides sobre el baseline de plataforma                           |
| `project`  | (proyecto)         | overrides sobre la capa del tenant                                  |

- Dentro de un hook, un guardrail se direcciona por su **key** (`id` si existe,
  si no `type`). Una capa más específica reemplaza el guardrail de la misma key
  de una capa menos específica.
- **Campos bloqueables (`locked`)**: si la plataforma marca una key _locked_,
  una capa inferior **no puede** debilitarla; el override se ignora (modo
  default) o se rechaza con `LockedFieldOverrideError` (modo `strict`), y en
  ambos casos se registra en `rejected_overrides` con su provenance.
- Un guardrail _locked_ es además **obligatorio**: una capa inferior no puede
  removerlo. Los baselines `pii` / `secret_leakage` / `prompt_injection` viven
  bloqueados en la plataforma.
- Remover un guardrail _no bloqueado_ de una capa superior se hace
  redeclarándolo con `remove: true` en la capa que sobreescribe.

### Dónde vive cada capa (prod-03 task_prod03_07/08)

Hasta el 2026-08-01 las capas no tenían tabla: la de plataforma vivía en
`platform_settings.guardrails_config` y la de proyecto en
`projects.guardrails_config` (ADR 0102 D3, migración 0110), y **la capa tenant
no existía**. La migración **0132** creó `guardrail_configs`, una fila por capa:

| Columna      | Nota                                                                  |
| ------------ | --------------------------------------------------------------------- |
| `scope`      | `platform` \| `tenant` \| `project` (CHECK)                           |
| `tenant_id`  | NULL **solo** en `platform`; lo exige un segundo CHECK                |
| `project_id` | NOT NULL solo en `project`                                            |
| `config`     | JSONB `{guardrails: {hook: [...]}}`, validado contra `PipelineConfig` |
| `version`    | contador de escrituras — invalidación de caché sin releer el JSONB    |

Tres índices únicos parciales garantizan **una fila efectiva por ámbito**: una
de plataforma, una por tenant, una por proyecto.

**RLS asimétrica, y es a propósito**: `USING (tenant_id IS NULL OR tenant_id =
app.tenant_id)` deja LEER el baseline de plataforma desde cualquier tenant —es
la capa que todos heredan y no contiene dato de nadie—, mientras que el
`WITH CHECK` **no** tiene la rama NULL: desde una sesión de tenant no se puede
crear ni modificar la fila de plataforma. Lo impide PostgreSQL, no la app.

**Compatibilidad**: la resolución mira primero la tabla nueva y, si esa capa no
tiene fila, cae a la columna vieja. Mientras nadie escriba en
`guardrail_configs`, la plataforma se comporta exactamente igual que antes.

### El baseline de plataforma sembrado

`seeds/guardrail_baseline.py` siembra tres guardrails `locked: true`:

| key                         | tipo               | hook        | acción |
| --------------------------- | ------------------ | ----------- | ------ |
| `platform_prompt_injection` | `prompt_injection` | `post_tool` | `warn` |
| `platform_secret_leakage`   | `secret_leakage`   | `post_llm`  | `warn` |
| `platform_pii`              | `pii`              | `post_llm`  | `warn` |

`prompt_injection` va en `post_tool` porque ahí es donde reentra al contexto lo
que devuelve una tool (RAG / HTTP / MCP): es el hook que cierra la inyección
**indirecta**.

Los tres arrancan en `warn` y no en `block` a propósito (mitigación nº1 de
riesgos de prod-03: calibrar con datos antes de bloquear). **El candado protege
la EXISTENCIA del check, no su dureza**: un tenant no puede quitarlos ni
relajarlos, pero la plataforma puede subirlos a `block` sin tocar código. El
seed es idempotente y **no pisa**: si el operador los subió, un re-arranque no
se lo revierte.

### CRUD de capas

| Método   | Ruta                                         | Quién        |
| -------- | -------------------------------------------- | ------------ |
| `GET`    | `/guardrails/config[?project_id=]`           | miembro      |
| `GET`    | `/guardrails/config/layers/{scope}`          | miembro      |
| `PUT`    | `/guardrails/config/layers/tenant`           | tenant_admin |
| `PUT`    | `/guardrails/config/layers/project/{id}`     | tenant_admin |
| `DELETE` | `/guardrails/config/layers/{tenant,project}` | tenant_admin |

El `GET` de la config efectiva devuelve además el **recibo de procedencia**: qué
capa ganó cada check y cuáles están bloqueados, para que la UI pueda explicar
por qué algo no se puede tocar.

Cada escritura resuelve con `resolve_config(..., strict=True)`, así que un
intento de sobrescribir, degradar o **eliminar** (`remove: true`) un guardrail
`locked` responde **422** con `{error: "locked_guardrail_override", hook, key,
layer, message}`. Antes de prod-03 ese intento se ignoraba en silencio y el
tenant se quedaba creyendo que había apagado el check.

## Qué pasa cuando un check NO emite veredicto (`on_error`)

Un check puede **reventar** (modelo caído, regex que se atraganta) o declararse
**indisponible** (`content_safety` sin clasificador, que es su estado por
defecto). Son el mismo caso: no hay veredicto. La política la fija `on_error`
por check (ADR 0102 D5, opción c):

| `on_error` | Efecto                                                       |
| ---------- | ------------------------------------------------------------ |
| `block`    | fail-closed: cuenta como disparo con acción `block`          |
| `warn`     | fail-open: dispara con acción `warn` — advisory, **no muda** |

**El default depende de `locked`**: `block` para los guardrails que la
plataforma bloqueó, `warn` para el resto. Lo que el operador escriba gana
siempre (un `locked` con `on_error: warn` se queda en warn, que es la vía para
observar antes de bloquear). Un candado que se abre solo cuando el check
revienta no es un candado.

Nótese la asimetría que esto crea en el baseline sembrado, y es deliberada: los
tres `locked` corren en `warn`, así que **un hallazgo solo avisa, pero un check
ROTO bloquea**.

`warn` no significa silencio: el outcome **dispara**, y como
`record_pipeline_decision` solo persiste los outcomes disparados, es la
diferencia entre que el dashboard enseñe «content_safety lleva una semana sin
clasificador» y que no lo enseñe nunca.

## Coste y concurrencia

El motor es síncrono y CPU-bound (regex, entropía, `ast.parse`; el detector
genérico de `secret_leakage` es lineal-cuadrático en el peor caso). Por eso:

- los hosts async lo ejecutan **fuera del event loop**, vía `asyncio.to_thread`
  (`api_server/guardrails/planning.py::_run_off_loop`). El motor es puro, así
  que sacarlo a un hilo es seguro por construcción;
- el texto escaneado está **acotado** a 50 000 caracteres a los dos lados de la
  plataforma (`MAX_SCANNED_CHARS` en el api-server, `_HOOK_INPUT_MAX` en el
  runtime), y el recorte se **anota** (`metadata.truncated`), que viaja al
  evento: un escaneo parcial presentado como completo es peor que no escanear,
  porque su «no se encontró nada» se lee como una garantía que no se dio.

## Las seis acciones

Cuando un guardrail dispara, su acción (la del config gana sobre el default del
guardrail) decide el efecto:

| Acción                | Efecto                                        |
| --------------------- | --------------------------------------------- |
| `block`               | detiene la llamada / descarta el payload      |
| `redact`              | enmascara el/los span(s) ofensivos y continúa |
| `warn`                | loguea + surfacea un aviso y continúa         |
| `retry_with_feedback` | re-ejecuta el LLM con feedback correctivo     |
| `escalate_to_human`   | pausa para validación humana                  |
| `transform`           | reescribe el payload vía un `Transformer`     |

Cuando varios guardrails disparan en un mismo hook, la acción decisiva se elige
por **precedencia**: `block > escalate_to_human > retry_with_feedback >
transform > redact > warn`. `PipelineDecision.allowed` es `False` para `block` y
`escalate_to_human` (ambos gatean el flujo).

## Los doce tipos built-in

| Tipo                   | Hooks principales        | Acción por defecto    | Qué detecta / hace                                                                      |
| ---------------------- | ------------------------ | --------------------- | --------------------------------------------------------------------------------------- |
| `pii`                  | `pre_llm` / `post_llm`   | `block` / `redact`    | PII (Presidio opcional+lazy; fallback regex: email/tarjeta-Luhn/teléfono/IBAN/IPv4/SSN) |
| `secret_leakage`       | `post_llm` / `post_tool` | `redact`              | tokens (AWS/Google/GitHub/Slack/PEM/JWT/connection string) + alta entropía              |
| `prompt_injection`     | `pre_llm` / `pre_tool`   | `block`               | 6 categorías de inyección (es+en); en `pre_tool` escanea `tool_args`                    |
| `content_safety`       | `pre_llm` / `post_llm`   | `block`               | categorías de seguridad vía guard model (LlamaGuard/ShieldGemma) opcional+lazy          |
| `code_safety`          | `post_llm` / `post_tool` | `block`               | AST de Python + regex de shell (`eval`/`exec`/`rm -rf /`/`shell=True`/fork bomb…)       |
| `output_structure`     | `post_llm` / `post_tool` | `retry_with_feedback` | valida el output contra un JSON Schema (`jsonschema`)                                   |
| `allowed_domains`      | `post_llm` / `pre_tool`  | `block`               | bloquea URLs/hosts fuera del allowlist (suffix-match de subdominios)                    |
| `cost_ceiling`         | cualquiera               | `block`               | umbral de coste por llamada/acumulado leído de `metadata`                               |
| `factuality_citations` | `post_llm`               | `warn`                | afirmaciones numéricas/citadas sin cita de soporte (heurística, es+en)                  |
| `topic_restriction`    | `pre_llm` / `post_llm`   | `warn`                | adherencia a temas permitidos / lejanía de prohibidos (keyword, seam embeddings)        |
| `rate_per_agent`       | cualquiera               | `block`               | límite de llamadas por agente en ventana deslizante (`RateStore`/`clock`)               |
| `forbidden_actions`    | `pre_tool`               | `block`               | deny/allowlist de tools (enforcement de `allowed_tools`)                                |

### Dependencias opcionales (extras lazy)

| Extra                               | Backend                                | Sin instalar                                           |
| ----------------------------------- | -------------------------------------- | ------------------------------------------------------ |
| `shared-guardrails[pii]`            | Presidio (`presidio-analyzer` + spaCy) | fallback regex puro o resultado tipado _unavailable_   |
| `shared-guardrails[content-safety]` | guard model vía `shared-llm`           | resultado tipado _unavailable_ (NUNCA un "safe" falso) |

`jsonschema` (para `output_structure`) y `PyYAML` (config) son **deps base** —
el motor es importable en CI sin los extras pesados.

## Observabilidad: `guardrail_events` + dashboard

Cada guardrail que dispara se persiste como una fila en `guardrail_events`.

| Aspecto       | Detalle                                                                                                                                       |
| ------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Tenancy       | **Tenant-owned** (`tenant_id` NOT NULL + RLS `FOR ALL`): un tenant ve solo sus eventos                                                        |
| Inmutabilidad | Append-only: solo `created_at`, escrito una vez por el recorder, nunca actualizado/borrado                                                    |
| Enmascarado   | `detail` + `detail_payload` llevan SOLO un resumen enmascarado — el PII/secreto crudo NUNCA se persiste                                       |
| Columnas      | `guardrail_type`, `hook_point`, `severity`, `action`, refs (`project_id`/`agent_id`/`execution_id`/`agent_label`), `detail`, `detail_payload` |

El **recorder** (`api_server.guardrails.events`) garantiza el enmascarado con un
**allowlist** de claves seguras del payload (familias, conteos, offsets, error de
schema, hosts/tools bloqueados, umbrales) + un **denylist** que dropea cualquier
clave de contenido crudo (`redacted_text`, `matched_text`, `prompt`, `response`,
`secret`, `tool_result`, …) — defensa en profundidad por encima del enmascarado
que ya hacen `pii`/`secret_leakage`. `record_pipeline_decision(...)` es el único
punto de cableado: cualquier host que corra el pipeline lo llama con la decisión

- un `GuardrailEventContext`.

### Endpoints del dashboard

| Endpoint                | Método | Rol mínimo     | Notas                                                                                           |
| ----------------------- | ------ | -------------- | ----------------------------------------------------------------------------------------------- |
| `/guardrails/events`    | GET    | `tenant_admin` | lista paginada (`limit`/`offset`) + filtros `type`/`severity`/`hook_point`/`since`/`until`      |
| `/guardrails/dashboard` | GET    | `tenant_admin` | agregados `by_type` / `by_severity` / serie por día + recientes; ventana `window_days` (1..365) |

Ambos corren sobre `get_tenant_session` (RLS) → un tenant solo ve sus propios
eventos. El frontend lo espeja con `<RoleGuard min="tenant_admin">`.

## Guardrails del chat de planning (task_11_22)

El chat de planning de Plan 03 cablea el motor (`api_server.guardrails.planning`)
con tres guardrails que **reutilizan** built-ins (ningún check nuevo):

1. **topic adherence** — `topic_restriction` en `pre_llm` + `post_llm`
   (default `warn`).
2. **hallucination check sobre números** — `factuality_citations`
   (`require_document_citation=True`) en `post_llm` (default `warn`).
3. **gate estructural antes de "Generar Plan"** — `output_structure`
   (JSON-Schema `PLAN_DRAFT_SCHEMA`) en `post_llm` con `action=block`: un
   borrador estructuralmente inválido BLOQUEA la generación y devuelve feedback
   accionable (los errores de schema + su path JSON).

Cada disparo se persiste como `guardrail_events` tenant-scoped con `agent_label`
`planning_chat` / `plan_generation` (el chat dispara antes de que exista una
execution).

### Dónde se invocan de verdad (prod-03 task_prod03_14)

Durante dos meses estas funciones tuvieron **cero llamantes fuera de tests** y
el roadmap del Plan 11 daba `task_11_22` por cableada (hallazgo guardrails-9).
Los llamantes reales, desde el 2026-08-01, viven en
`api_server/guardrails/route_gates.py`:

| Gate                   | Ruta                                | Hook       |
| ---------------------- | ----------------------------------- | ---------- |
| `gate_planning_turn`   | `POST /conversations/{id}/messages` | `pre_llm`  |
| `gate_plan_generation` | `POST /projects/{id}/plans`         | `post_llm` |

Dos detalles que no son obvios:

- el turno se evalúa **antes** de persistir el mensaje y de programar la
  respuesta del equipo — bloquear después de haber llamado al LLM no bloquea
  nada;
- el gate de «Generar Plan» corre **solo sobre el borrador que produjo el
  chat**, no sobre un `specification` inline. El esquema exige `summary` no
  vacío y al menos una tarea; aplicarlo al contrato público de la API bloquearía
  flujos legítimos (incluido crear la carcasa vacía de un plan).

Cuando un gate bloquea, el evento se persiste **en su propia transacción**: el
422 hace rollback de la sesión de la request, y el único turno que la plataforma
llegó a DETENER sería justamente el que no aparecería nunca en el dashboard.

## Notas de seguridad / multi-tenancy

- `guardrail_events` es tenant-scoped + RLS; el aislamiento cross-tenant está
  cubierto por `@pytest.mark.cross_tenant`.
- El detalle persistido es **siempre enmascarado**: el secreto/PII crudo nunca
  llega a la BD (recorder + enmascarado de los propios guardrails).
- La capa de plataforma puede bloquear (`locked`) los baselines de seguridad de
  forma inviolable desde tenant/proyecto.

## Estado de implementación

| Tarea       | Estado                                                                                                    |
| ----------- | --------------------------------------------------------------------------------------------------------- |
| 11_01–11_09 | ✅ motor + 12 built-in (commiteado)                                                                       |
| 11_20       | implementado en el working tree (modelo/migración 0052/recorder/endpoints/dashboard); pendiente de commit |
| 11_21       | ⏳ **alertas configurables NO implementadas** (sin config/endpoint/evaluador de umbral)                   |
| 11_22       | ✅ guardrails del chat de planning (commiteado) — **cableados a las rutas en prod-03 task_prod03_14**     |
| prod-03     | ✅ capas persistidas (0132) + baseline locked + CRUD strict + `on_error` por `locked` + seam async        |

Detalle completo y huecos de alcance en el changelog del plan:
[`docs/07-changelog/11-guardrails-precios.md`](../07-changelog/11-guardrails-precios.md).
