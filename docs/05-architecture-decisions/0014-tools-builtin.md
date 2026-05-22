---
adr: "0014"
title: Tools builtin del agente — allowlists y efectos
status: accepted
date: 2026-05-22
deciders: System Architect, Backend Dev, Security
phase: 02-ejecucion-agentes
---

# ADR 0014 — Tools builtin del agente: allowlists y efectos

## Contexto

Plan 02 Fase D da al agente sus herramientas: ejecutar comandos, tocar
ficheros, hacer peticiones HTTP y actuar sobre el Kanban. El principio
rector 2 (CLAUDE.md) es claro: **ningún tool builtin se ejecuta en el
worker** — corren dentro del contenedor `agent-runtime`. Hay que
decidir:

1. Dónde viven los tools y cómo se acotan.
2. Cómo actúan sobre la plataforma los tools que necesitan estado
   (Kanban, comentarios, notificaciones) si el contenedor del agente no
   tiene acceso a la plataforma.

## Decisión

### Los tools viven en `agent_runtime`, acotados por proyecto

Cada tool builtin es un módulo de `agent_runtime` y corre **dentro** del
contenedor. Sobre el sandbox de Fase B (ADR 0012) cada tool añade su
propia barrera, **configurada por proyecto**:

- **`shell_exec`** — el comando se parte con `shlex` y se ejecuta como
  vector argv, **nunca por shell** (cero superficie de inyección). El
  basename del primer token se valida contra un _allowlist_ de comandos
  del proyecto; un programa no permitido se rechaza antes de ejecutar.
  Timeout y captura de stdout/stderr/exit code.
- **`file_read` / `file_write` / `file_list`** — toda ruta se resuelve
  relativa a la raíz del workspace y debe quedar **dentro** de ella; una
  ruta absoluta o un `../` que se escape se rechaza antes de tocar el
  disco.
- **`http_request`** — único canal de red del agente. Tres raíles:
  _allowlist_ de dominios del proyecto, timeout y tamaño máximo de body
  (la respuesta se transmite en _streaming_ y se aborta al superar el
  tope).

Los _allowlists_ por proyecto son la unidad de control: el mismo tool
es más o menos capaz según la configuración del proyecto que lo usa.

### Tools de orquestación: emiten efectos, no llaman a la plataforma

`kanban_update`, `task_comment`, `notify_user` y `agent_invoke` actúan
sobre estado de plataforma (BD). Pero el contenedor del agente está en
una red **interna** sin acceso a Postgres/Redis (ADR 0012). No pueden
llamar a la plataforma.

Decisión: estos tools **no salen** — validan sus argumentos y registran
un **efecto** estructurado en un `OrchestrationSink`. El worker —que sí
tiene acceso a BD— drena el sink y aplica los efectos.

    agente → tool → OrchestrationSink (intención)  →  worker → BD (efecto)

Fase D entrega los tools, la validación y el sink. La aplicación de los
efectos por el worker aterriza con el Kanban en vivo (Fase E).

### Placeholders que devuelven 501

`memory_recall`, `memory_store` y `document_convert` están en el
catálogo pero sus backends llegan en Plan 04 (memoria + RAG; Docling).
Hasta entonces devuelven un `ToolResult` fallido con semántica HTTP 501
— un "todavía no" claro y estructurado, no un fallo ni un no-op
silencioso.

## Alternativas descartadas

1. **Ejecutar tools en el worker.** Viola el principio rector 2: el
   worker quedaría expuesto al código del agente. Los tools corren en
   el contenedor efímero, sin excepción.
2. **`shell_exec` con `shell=True`.** Cómodo, pero abre inyección de
   shell. Ejecutar el vector argv directamente la elimina; el coste es
   no soportar pipes/redirecciones — aceptable, el agente compone.
3. **Tools de orquestación llamando directamente al api-server.**
   Exigiría dar acceso de red del contenedor a la plataforma, anulando
   el aislamiento de Fase D. El patrón sink → worker mantiene el
   contenedor sin egress a servicios internos.
4. **`http_request` sin tope de body / sin streaming.** Una respuesta
   enorme agota la memoria del contenedor. El streaming con corte es
   imprescindible, no un extra.

## Consecuencias

Positivas:

- Cada tool añade una barrera configurable por proyecto sobre el
  sandbox del contenedor; el control es declarativo.
- Los tools de orquestación no necesitan acceso de red a la plataforma:
  el aislamiento de contenedor se mantiene intacto.
- El catálogo de tools está completo (placeholders incluidos) — un
  agente nunca se topa con un tool "que no existe".

Negativas / cuidados:

- Los efectos de orquestación **aún no se aplican**: Fase D los emite y
  valida; el worker que los drena llega en Fase E.
- `memory_*` y `document_convert` son 501 hasta Plan 04.
- El _allowlist_ de `shell_exec` casa por _basename_: dos binarios con
  el mismo nombre en distinta ruta son indistinguibles. Suficiente en
  el contenedor controlado de `agent-runtime`.

## Referencias

- `docs/roadmap/02-ejecucion-agentes.md` — Fase D, task_02_15..19.
- Tools: `docker/agent-runtimes/agent-runtime/agent_runtime/`
  (`shell_exec`, `file_tools`, `http_tool`, `orchestration_tools`,
  `placeholder_tools`).
- Tests: `tests/integration/test_tool_shell_exec.py`,
  `test_tool_file_ops.py`, `test_tool_http.py`,
  `test_tools_orchestration.py`, `test_placeholders.py`.
- ADR 0012 (aislamiento de contenedores) y ADR 0013 (agent loop).
- Documento maestro, sección 12 (ejecución y tools).
