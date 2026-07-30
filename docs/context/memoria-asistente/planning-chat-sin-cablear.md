---
name: planning-chat-sin-cablear
description: "RESUELTO 2026-06-29: el chat de planning de proyecto YA está cableado (Plan 04) — LLMPlanningModel + _stream_planning disparado desde post_message."
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

El **chat de proyecto en modo planning "no hace nada"** al enviar un mensaje
(reportado por el operador 2026-06-21). Causa raíz confirmada leyendo el código:

1. `routers/conversations.py:post_message` SOLO persiste el mensaje + lo emite por
   WS (`_publish_message_event`). No dispara ningún agente/orquestador.
2. El cerebro de planning EXISTE: `api_server/chat/planning_graph.py`
   (`run_planning_turn`, grafo multi-agente PM+especialistas+síntesis) +
   `planning_context.build_planning_context` + `guardrails/planning.py`. Pero
   `run_planning_turn` **no lo invoca NADIE** (solo el `def` + el export).
3. NO hay adaptador LLM: el único `PlanningModelClient` es `ScriptedPlanningModel`
   (test). El docstring del Protocol (planning_graph.py:110-117) dice que el real
   "plugs an adapter over shared_llm.LLMProvider (ADR 0021) behind the same
   surface" — **ese cableado de Plan 04 nunca se hizo**.

**Para que funcione (feature tamaño plan, provider-dependiente como el asistente):**

- Construir `LLMPlanningModel` (LLMProvider → PlanningModelClient): `pm_decide`→
  PMDirective, `specialist_speak`→SpecialistContribution, `pm_synthesise`→str, con
  salida estructurada. Provider-agnóstico (reusar resolución de provider, [[memoria-tool-calling-fix]]).
- Cablear `post_message` (USER + planning): tras guardar, `build_planning_context`
  - `run_planning_turn` (con roles del equipo + `run_planning_chat_guardrails`) en
    BACKGROUND → persistir la respuesta del PM como mensaje `agent` → la UI ya la
    recibe por `/ws/conversation/{id}`.
- Intención "crear plan": cuando el PM decide draft, crear el `Plan`
  (canonical-template) ligado a la conversación (routers/plans.py).
- Tests: ScriptedPlanningModel para el flujo del endpoint; verificación real con un
  provider configurado.

**Estado (actualizado 2026-06-29): YA IMPLEMENTADO.** Verificado en el código:
`routers/conversations.py:post_message` (~368-369, "Plan 04 wiring") SÍ dispara el
sub-grafo de planning para una conversación en modo planning; `chat/responder.py`
tiene `_stream_planning` (PM framing → especialistas → síntesis → `pm_plan_draft`
insertable) y `LLMPlanningModel` (el adaptador LLMProvider→PlanningModelClient que
faltaba). El diagnóstico de abajo es HISTÓRICO (2026-06-21). El plan resultante se
sincroniza al kanban vía [[deliverables-en-docs-roadmap]]/sync_to_kanban, que ahora
asigna por rol (ADR 0091).
