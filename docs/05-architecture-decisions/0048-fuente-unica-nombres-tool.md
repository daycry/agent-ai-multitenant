---
adr_id: "0048"
title: "Fuente única de nombres canónicos de tool (catálogo ↔ chat-mode ↔ runtime ↔ approval) + punto único de intersección"
status: accepted
date: 2026-06-03
authors: [system_architect]
plan_referenced: 06.18-tools-overhaul
docs_language: es
---

# ADR 0048 — Fuente única de nombres canónicos de tool + punto único de intersección

> **Estado: `accepted`** (aprobado por el operador 2026-06-03, Fase 0 del Plan 06.18).
> Implementado por `task_06_18_03` y `task_06_18_05`.

## Contexto

El mismo nombre lógico de cada acción de tool vive **triplicado en tres espacios de nombres
incompatibles**, y la intersección de enforcement se hace por string crudo:

- **Catálogo** (`seeds/builtin_tools.py:70,81,109,285,310,383`): `read_file` / `write_file` /
  `list_files` / `http_get` + `http_post` / `send_notification`.
- **Chat-mode** (`chat/modes.py:111-152`): `file_read` / `file_write` / `file_list` / `http_request`
  / `notify_user`.
- **Runtime ejecutable** (`agent_runtime/file_tools.py:41,55,69`, `orchestration_tools.py:99-102`):
  `file_read` / `file_write` / `file_list` / `notify_user`.

**Tabla de mapeo de los tres namespaces** (misma acción lógica, tres nombres incompatibles). El
nombre **canónico** elegido (ver Decisión) es el del catálogo, y el alias bidireccional reconcilia
los otros dos:

| Canónico (catálogo) | Chat-mode (`chat/modes.py`) | Runtime ejecutable (`agent_runtime`) | Alias                             |
| ------------------- | --------------------------- | ------------------------------------ | --------------------------------- |
| `read_file`         | `file_read`                 | `file_read`                          | `file_read ↔ read_file`           |
| `write_file`        | `file_write`                | `file_write`                         | `file_write ↔ write_file`         |
| `list_files`        | `file_list`                 | `file_list`                          | `file_list ↔ list_files`          |
| `http_get`          | `http_request`              | (familia red)                        | `http_request ↔ {http_get,…}`     |
| `http_post`         | `http_request`              | (familia red)                        | `http_request ↔ {…,http_post}`    |
| `send_notification` | `notify_user`               | `notify_user`                        | `notify_user ↔ send_notification` |

`combine_tool_allowlists` (`agent_tools_enforcement.py:75-109`) intersecta por `Tool.name`; su
docstring (`:33-35`) afirma **falsamente** un "single namespace … `read_file`". Resultado: **el
nombre del catálogo que el operador ve y asigna (`read_file`) nunca existe como función ejecutable
(`file_read`)**, y la intersección agente∩modo puede quedar vacía sin aviso. Dos problemas asociados:

1. **La intersección nunca se ejecuta con un modo real.** El único call-site productivo
   (`dispatch.py:360`) pasa `mode=None`; el chat-path (`conversations.py:186-237`) no resuelve
   `resolve_mode_config`. La capa de chat-mode es hoy **inerte end-to-end**.
2. **Bypass latente de aprobación.** `approval.DEFAULT_TOOL_CATEGORIES` (`approval.py:22-27`) clasifica
   lo sensible por nombres de chat-mode (`file_write`/`http_request`), no por los del catálogo
   (`write_file`/`http_get`/`http_post`). Hoy no es explotable solo porque esas tools no se registran;
   cualquier reconciliación ingenua hacia el nombre del catálogo abriría el agujero.

Este ADR fija **cómo** se unifican los nombres sin romper agentes existentes ni el gate de aprobación.

## Opciones consideradas

- **A. Adoptar el namespace del catálogo** (`read_file`/`write_file`/…) y migrar chat-mode, runtime y
  approval a él. ✅ El nombre que el operador ve es el real. ❌ Renombra funciones del runtime y
  `allowed_tools` de chat-modes existentes (rename duro).
- **B. Adoptar el namespace del runtime/chat-mode** (`file_read`/…) y migrar el catálogo + la UI. ✅
  Menos cambios en el runtime. ❌ Cambia los `Tool.name` que el operador ya conoce y que están en
  `agent_tools` existentes.
- **C. Módulo neutro `CANONICAL_TOOL_NAMES` en `shared-domain`** importado por las cuatro capas, con
  **capa de alias bidireccional** y test de contrato en CI. ✅ Una sola fuente de verdad; ✅ no rename
  duro (los alias preservan datos existentes); ✅ el test impide que vuelvan a divergir. ❌ Mantiene
  temporalmente los alias.
- **D. Mantener namespaces separados con una tabla de alias/mapeo** resuelta antes de intersectar. ✅
  Mínimo cambio. ❌ No hay fuente única; la tabla se desincroniza igual que hoy.

**Sub-decisión — punto único de intersección agente∩modo:** (i) cablear en chat-path resolviendo
`resolve_mode_config`; (ii) cablear en task-path pasando el modo real; (iii) retirar la capa de
chat-mode si el modo no debe limitar tools; (iv) mantener ambas capas pero **emitir un warning de
configuración** cuando la intersección sea vacía por desajuste de nombres (no silencio).

## Decisión

**Opción C** + **sub-decisión (iv)**:

1. **`packages/shared-domain/.../tool_names.py`** define los **nombres canónicos** = los del
   **catálogo** (`read_file`, `write_file`, `list_files`, `http_get`, `http_post`,
   `send_notification`, …) — el nombre que el operador ya ve y asigna es el canónico.
2. **Capa de alias bidireccional retrocompatible**: `file_read↔read_file`, `file_write↔write_file`,
   `file_list↔list_files`, `http_request↔{http_get,http_post}`, `notify_user↔send_notification`.
   `chat/modes.py`, `agent_runtime` (registro) y `approval.py` importan el módulo y resuelven a
   canónico **antes** de comparar. **No hay rename duro**: las filas `agent_tools` y los
   `allowed_tools` de chat-modes existentes siguen funcionando vía alias.
3. **`combine_tool_allowlists` resuelve a canónico** antes de intersectar; si la intersección queda
   vacía **por desajuste de nombres** (no por un `discussion` con allowlist vacío intencional), emite
   un **warning de configuración estructurado** (no un `ToolResult` silencioso).
4. **`approval.DEFAULT_TOOL_CATEGORIES` se alinea a nombres canónicos** — **prerequisito** de cablear
   `write_file`/`http_*` en el runtime (`task_06_18_05`), para no abrir el bypass de aprobación.
5. **Test de contrato en CI** (`task_06_18_14`): todo `Tool.name` del catálogo y todo nombre en
   `allowed_tools` de cualquier chat-mode resuelven a un canónico que existe en el conjunto registrable
   del runtime; falla si divergen.

`http_request` (chat-mode) mapea a la familia `{http_get, http_post}`: el alias lo expande, y la
categoría de aprobación de red cubre ambos canónicos.

## Consecuencias

**Mejora:** una sola fuente de verdad de nombres; fin de las intersecciones vacías silenciosas; el
gate de aprobación deja de poder ser eludido por divergencia de nombres; el catálogo que se asigna
empieza a corresponder con lo ejecutable (junto con `task_06_18_05`).

**Complejidad añadida:** una capa de alias que hay que mantener mientras existan los nombres legacy;
el test de contrato es nuevo. Mitigación: el alias es un diccionario pequeño y estable; el test lo
fija.

**Trade-offs:** no se hace rename duro (preserva datos) a cambio de arrastrar alias; se acepta porque
romper `agent_tools`/chat-modes existentes sería un cambio masivo y silencioso (mismo criterio
backward-compat del ADR 0044).

## Riesgos

| Riesgo                                                    | Prob. | Impacto | Mitigación                                                          |
| --------------------------------------------------------- | ----- | ------- | ------------------------------------------------------------------- |
| Alias incompleto deja una tool sin resolver               | Media | Medio   | Test de contrato CI cubre todo nombre del catálogo y de chat-modes  |
| Alinear approval mal abre el bypass al cablear write/http | Baja  | Alto    | approval se alinea ANTES de cablear esas familias (orden en 06.18)  |
| Warning de intersección vacía se ignora                   | Media | Bajo    | Se expone también en `effective-tools` (`task_06_18_07`) y en la UI |

## Alternativas rechazadas

A y B (rename duro) por romper datos existentes; D por no crear fuente única. (iii) retirar la capa de
chat-mode se descarta: el modo sí debe poder limitar tools (p. ej. `discussion`); se conserva con (iv).

## Trazabilidad

- Roadmap: `docs/roadmap/06.18-tools-overhaul.md` (`task_06_18_03`, `task_06_18_05`, `task_06_18_14`).
- Módulo: `packages/shared-domain/src/shared_domain/tool_names.py` (nuevo).
- Enforcement: `apps/api-server/src/api_server/agent_tools_enforcement.py`; `chat/modes.py`.
- Runtime/approval: `docker/agent-runtimes/agent-runtime/agent_runtime/approval.py`, registro de tools.
- ADRs relacionados: 0044 (taxonomía derivada), 0025 (MCP+ejecutores), 0014 (tools built-in).
