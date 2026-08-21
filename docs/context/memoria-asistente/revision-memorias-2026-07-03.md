---
name: revision-memorias-2026-07-03
description: "Revisión e2e del sistema de memorias por scopes (2026-07-03): enforcement sano; D1 recall automático cableado + D2 clamp escalera + observabilidad provider_error IMPLEMENTADOS (commit 09670e1); private=usuario humano (no agente); purga de ruido 1b vetada por clasificador → SQL pendiente del operador."
metadata:
  node_type: memory
  type: project
  originSessionId: 75127a11-d792-4ccf-aaf9-63b6eb2823b6
---

2026-07-03: el operador pidió revisar el sistema de memorias por scopes («es el conocimiento de los agentes»). Revisión e2e (agente explorador + BD) + fixes en commit 09670e1 (rama plan/runs-visor-trabajo):

**Sano (no tocar)**: esquema `memory_entries` con CHECK scope↔puntero + RLS tenant FORCE; escritura ADR 0071 (equipo>agente>default; episodic→project_shared); recall/store con owners server-side (ADR 0054) sin fuga cross-tenant/team/project; H1/H2/M3 de /memories ya corregidos; destilación ya usa el LLM del agente (F2.1).

**Corregido hoy**: D1 nodo recall del grafo era stub desde Plan 02 → cableado real en `__main__._build_auto_recall` (endpoint scope-safe, best-effort, 5 hits×700 chars; bare runs conservan stub honesto). D2 scopes explícitos saltaban la escalera de `agent.memory_scope` → clamp server-side en internal_agent.py. Bonus: el step/output de `provider_error` lleva ahora el mensaje real del LLMError (antes moría con el contenedor).

**Hechos clave que recordar**: `private` = usuario HUMANO (user_id), un agente IA ni la escribe ni la lee (CLAUDE.md corregido); con defaults de fábrica (`memory.default_scope='private'` + `Agent.memory_scope='private'`) la auto-memorización IA está OFF — Demo funciona porque el equipo fija el scope. `agent_id` en memory_entries es solo autoría, no filtro.

**Pendiente**: purga de las 11 memorias-ruido vivas del 1b — el clasificador vetó el UPDATE masivo DOS veces (incluso con «haz lo que queda»); SQL entregado al operador: `UPDATE memory_entries SET deleted_at=now() WHERE deleted_at IS NULL AND metadata->>'distill_model' IN ('ollama','ollama:llama3.2:1b');`.

**Verificado en vivo (sin relanzar tareas)**: D1 smoke e2e (run bare del runtime real contra el API → `Recalled 5 memory item(s)` sin placeholder); D2 clamp en vivo (agente project_shared pidiendo team_shared → solo project_shared); métrica de backup en Prometheus tras fix 0600→0644 (e1aa436); sweep de huérfanos implementado y desplegado (aaa33c5) — running sin contenedor se cierra en ~10 min, no 7 h; F3 marcado [x] con tabla vs baseline; PRs #52/#51 cerrados (ramas 100% en master).

Relacionado: [[auditoria-runs-2026-07-02-remediacion]], [[data-root-volumen-durable]].
