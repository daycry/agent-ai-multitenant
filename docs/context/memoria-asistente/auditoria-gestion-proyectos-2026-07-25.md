---
name: auditoria-gestion-proyectos-2026-07-25
description: Auditoría del workflow completo de gestión de proyectos (38 hallazgos verificados) + plan de remediación en 6 olas — pendiente de aprobación del operador
metadata:
  node_type: memory
  type: project
  originSessionId: c24b547e-58f5-4ecf-a67d-6507fb095bad
  modified: 2026-07-25T11:12:15.093Z
---

Auditoría integral del **workflow de gestión de proyectos** (2026-07-25, baseline
`a17ed99f` en `plan/runs-visor-trabajo`). Entregables: informe
`docs/roadmap/auditoria-gestion-proyectos-2026-07-25.md` (38 hallazgos) + plan
`docs/roadmap/remediacion-gestion-proyectos-2026-07-25.md` (6 olas, 24 d,
`pending_approval`). Ambos indexados en `docs/roadmap/README.md`.
**Nada implementado**: el operador eligió «informe + plan priorizado».

**Hallazgo de tesis**: el problema dominante NO es de diseño ni de calidad de código,
es de **cableado del último tramo** — funcionalidad construida, correcta y testeada que
nunca se conecta al consumidor final. El progreso del plan se calcula y nadie lo pide;
el PR se abre y nadie lo enseña; las tools MCP se permiten y no se le anuncian al modelo;
el OAuth se implementa y el runtime no lo pasa; los perfiles seccomp se escriben y no se
pinean; los prompts tienen columna de versión y nadie la rellena. Casi todos los arreglos
son pequeños.

**Los 3 críticos**: (A-01/A-02) el chat de planning carga y promptea los **50 mensajes
más antiguos** (`order_by ASC limit 50`, UI sin `after`) → a los ~6 turnos el feed se
congela y «Generar Plan» desaparece; (B-01) las tools MCP del proyecto (ADR 0128) entran
en el allowlist pero su esquema nunca llega a `build_model_tool_schemas` porque
`tool_specs` es **por agente** → permitidas e invisibles.

**Altos**: `summary` str vs dict (rompe `PUT`), `estimated_hours` nunca emitido (Gantt y
coste ficticios), «Generar Plan» duplica planes, sin replanificación (0 ocurrencias de
`replan*`), spec de solo lectura, agente sin grants no ve `read_file`, OAuth MCP sin
caller de producción, `send_notification` anunciada y siempre `not wired`, solo 2 de 4
hooks de guardrails cableados, **`HOME=/workspace` en el test-runtime** (las cachés caen
en el worktree y `git add -A` las commitea), test-runtime sin `pids_limit`/seccomp,
progreso y PR del plan invisibles.

**Gotcha metodológico**: la re-verificación individual **refutó 4 candidatos** de los
agentes exploradores — `allowed_domains` SÍ tiene UI (`projects/[id]/commands/page.tsx:344`),
`apps/web-app/` solo tiene `.gitkeep` (no hay duplicación con admin-panel: eso cierra de
hecho la resolución (c) del ADR 0117), `workers/secrets.py` lo usan los backups, y el
test-runtime SÍ tiene el seccomp por defecto de Docker (falta el endurecido). No publicar
hallazgos de subagentes sin abrir el fichero.

**Corrección del operador (misma sesión)**: la v1 del plan proponía, para 5 hallazgos, la
corrección mínima en vez de la mejora real. Preguntó «¿lo de los últimos 50 mensajes lo ves
bien?» y tenía razón: invertir el orden arregla el bug y deja el mal diseño (truncar por
**número de mensajes** en vez de por tokens, sin condensar lo evictado). En un chat de
planning el principio importa más que el final — los requisitos están en el mensaje 1 — así
que **ningún extremo es la respuesta**. El patrón correcto YA existe en el repo, en
`agent_runtime/providers.py:318-374` (ventana verbatim + `_condense_evicted` + sticky fuera
de la ventana); el chat de planning no usa nada de eso (`history_from_messages` mapea
verbatim). Añadidas §7b al informe y 3 tareas nuevas + 1 fusión (+2,25 d).

**Regla que sale de aquí**: al proponer remediación, distinguir siempre «corrección mínima»
de «mejora real» y dejar la decisión explícita — sobre todo cuando el encargo dice
«sobretodo mejora del workflow». Y antes de inventar un fix, buscar si el mismo problema ya
está resuelto en otra capa de este repo.

Relacionado: [[auditoria-proyecto-integral-2026-07-17]] (auditoría anterior del dominio,
ya remediada), [[adr-0129-servicios-runtime-por-proyecto]],
[[deliverables-en-docs-roadmap]], [[prioridad-codigo-limpio-mantenible]].
