---
title: Guardrails declarativos — Referencia del motor, tipos, acciones y observabilidad
audience: backend-dev, ai-engineer, architect, security
phase: 11-guardrails-precios
updated: 2026-05-30
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
| 11_22       | ✅ guardrails del chat de planning (commiteado)                                                           |

Detalle completo y huecos de alcance en el changelog del plan:
[`docs/07-changelog/11-guardrails-precios.md`](../07-changelog/11-guardrails-precios.md).
