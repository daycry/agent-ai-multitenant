---
name: investigacion-inteligencia-agentes
description: '2026-07-11 — investigación "agentes lo más inteligentes posible" ENTREGADA (docs/roadmap/investigacion-inteligencia-agentes-2026-07-11.md, commit 7fc9b474); plan P0/P1/P2 pendiente de priorización del operador'
metadata:
  node_type: memory
  type: project
  originSessionId: 573f43d4-c6ef-46c2-8bc9-7c8109851461
---

2026-07-11: el operador pidió investigar cómo maximizar el rendimiento/inteligencia de los agentes de equipo e individuales (prompts, KB, memoria…). Investigación multi-agente (5 exploradores: prompts, KB/RAG, memoria, config, bucle) ENTREGADA en `docs/roadmap/investigacion-inteligencia-agentes-2026-07-11.md` (commit 7fc9b474, rama plan/runs-visor-trabajo). SOLO diagnóstico — nada implementado.

**Hallazgos centrales** (la plataforma tiene la información pero no la entrega al run):

- G1/P0-1 (la gran palanca): `agents.system_prompt` (persona, riquísima en seeds CI4) y el rol NUNCA llegan al prompt de ejecución — implementador y reviewer corren con `_DECIDE_SYSTEM` genérico + skill fragments. Punto de corte: `dispatch.py::_assemble_run_request` no la incluye.
- P0-2: la KB no se auto-inyecta en runs (solo tool PULL `rag_search`, que además NO está en `SYSTEM_FAMILY_TOOL_NAMES` → se cae de allowlists en silencio, P0-3). Las memorias sí se auto-inyectan (recall D1).
- P0-4: ruta BM25 de agentes/planning usa tokenizador `simple` (el preview usa `es_unaccent`) — peor recall en castellano.
- Memoria: solo aprende de runs `done` (policy default), sin dedup entre runs (solo el córtex la tiene), recall pre-run = título+descripción (5×700 chars), sin decay/priorización.
- Bucle: ventana de 8 items sin resumen de lo evictado, sin scratchpad persistente, implementador ciego al worktree acumulado (harvest ya existe para reviewer), stack_exec tail crudo 8000 chars, sin tool-calling paralelo (F36 descarta), reconstrucción stateless single-turn (claude_sdk max_turns=1 → sin caching conversacional).
- Config muerta: `temperature` validada pero no llega al runtime; `skill_match` es no-op; built-ins sin palanca de modelo/effort de equipo; reranker BGE OFF por flag.

**Plan**: Tanda 1 = P0 entero (persona+rol, auto-RAG, exención allowlist, es_unaccent, planning con embedder+agent_id, brief worktree, feedback de fracasos). P1 = aprendizaje de fracasos/review + dedup + recall rico, destilar stack_exec, resumen evictado, scratchpad, reviewer informado. P2 (ADR): threading/prompt-caching (ligado a ADR 0097), batch tool-calling, reflexión semántica, budgets ampliables, ask_human no-terminal, skill_match real.

**Why:** encargo explícito del operador; el informe es la base de la siguiente tanda de implementación cuando la apruebe.
**How to apply:** si el operador aprueba tandas, implementar TDD+commit atómico por ítem siguiendo los raíles existentes (payload→spec→preámbulo con fence UNTRUSTED ADR 0102) y medir con el e2e del ciclo autónomo. Relacionado: [[hallazgos-pendientes-implementados-2026-07-09]], [[prioridad-codigo-limpio-mantenible]].
