---
adr_id: "0033"
title: "Asistente personal en api-server reutilizando la infraestructura de chat (LangGraph + shared-llm), no en personal-assistant/"
status: accepted
date: 2026-05-30
authors: [ai-engineer]
plan_referenced: 10-asistente-personal
docs_language: es
---

# ADR 0033 — Asistente personal sobre la infraestructura de chat de api-server

## Contexto

El Plan 10 (task_10_14) pide un **asistente personal conversacional**
accesible **solo a Tenant Admins** que responda preguntas sobre el estado
global cross-proyecto del tenant. El scaffold `apps/personal-assistant/`
existe pero está vacío (`.gitkeep`), mientras que toda la fontanería de
chat ya vive en `apps/api-server/`:

- Modelos `Conversation` / `Message` (`db/conversation.py`, Plan 03).
- Sub-grafo LangGraph de planning (`chat/planning_graph.py`) con un
  **seam de modelo** (`PlanningModelClient`) que mantiene el LLM fuera de
  los tests vía un `ScriptedPlanningModel`.
- Capa `shared-llm` (ADR 0021) con los cuatro proveedores.
- Dependencias de auth/RBAC y sesión tenant-scoped con RLS
  (`auth/deps.py`: `require_tenant_admin`, `get_tenant_session`).
- Tabla genérica `tenant_settings` (Plan 06.7) para config por tenant.

La pregunta abierta: ¿construir el asistente en el scaffold vacío
`personal-assistant/` (nuevo proceso/servicio) o dentro de `api-server`
reutilizando lo anterior?

## Decisión

**El asistente se implementa dentro de `apps/api-server/`**, como un agente
conversacional especializado sobre el estado global del tenant, **no como
un nuevo stack ni un nuevo proceso**. Concretamente:

1. **Paquete `api_server.assistant`**: `config.py` (identidad por tenant),
   `tools.py` (tools de lectura cross-proyecto), `graph.py` (sub-grafo
   LangGraph de tool-use con el seam `AssistantModelClient`, hermano de
   `PlanningModelClient`) y `llm.py` (adaptador `LLMProvider` → seam).
2. **Router `routers/assistant.py`** con `POST /assistant/chat` y
   `GET/PUT /assistant/identity`, montado en el mismo FastAPI.
3. **Toggle** `Organization.personal_assistant_enabled` como **columna**
   booleana (migración 0047, default `false`) por estar en el hot path de
   cada request; la **identidad** (nombre/avatar/tono/idioma/system_prompt
   /tools habilitadas) se guarda como **un único blob JSONB en
   `tenant_settings`** (categoría `assistant`), sin migración y con forma
   evolutiva.
4. **Acceso doblemente verjado** con una dependencia
   `require_assistant_access` = `require_tenant_admin` (un `tenant_user`
   recibe 403) **+** comprobación del toggle (Tenant Admin de un tenant con
   el toggle en `false` recibe 403/disabled).
5. **Aislamiento por construcción**: las tools de lectura se ejecutan con
   la **sesión RLS-bound del request** (`get_tenant_session`), de modo que
   PostgreSQL filtra cada query al tenant del admin que pregunta — una tool
   nunca puede devolver datos de otro tenant, sea cual sea el argumento que
   el modelo invente.
6. **Tests sin LLM real**: el modelo se inyecta por dependencia
   (`get_assistant_model`), que en tests se sobreescribe con un
   `ScriptedAssistantModel` (mismo patrón que `ScriptedPlanningModel`).
7. `tenant_budget_status` devuelve un **marcador tipado "no disponible
   todavía"** (el motor de presupuesto es el Plan 11, §28.7), nunca cifras
   inventadas.

## Alternativas consideradas

- **Servicio independiente en `personal-assistant/`**: duplicaría la
  fontanería de chat, auth, RLS y `shared-llm`, y añadiría un proceso y un
  contrato de red sin beneficio — el asistente no tiene requisitos de
  aislamiento de runtime (no ejecuta código de usuario; solo lee estado).
  Descartado por coste y duplicación.
- **Columnas por campo de identidad en `organizations`**: rígido y con una
  migración por cambio. Descartado a favor del blob JSONB en
  `tenant_settings`.

## Consecuencias

- `apps/personal-assistant/` permanece como scaffold; si en el futuro el
  asistente necesitara aislamiento de proceso (p.ej. herramientas de
  escritura sensibles), se extrae entonces con el contrato ya definido por
  el seam `AssistantModelClient` y las tools.
- El asistente hereda gratis la RLS, el RBAC y la observabilidad del
  api-server.
- Añadir un proveedor LLM real al asistente es wiring de `get_assistant_model`;
  no toca el grafo ni las tools.
