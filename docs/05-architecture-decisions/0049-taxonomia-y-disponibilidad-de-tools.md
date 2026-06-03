---
adr_id: "0049"
title: "Taxonomía de tools en tres facetas (Función/Seguridad/Origen) + disponibilidad real (is_runtime_wired)"
status: accepted
date: 2026-06-03
authors: [system_architect]
plan_referenced: 06.18-tools-overhaul
docs_language: es
---

# ADR 0049 — Taxonomía de tools en tres facetas + disponibilidad real

> **Estado: `accepted`** (aprobado por el operador 2026-06-03, Fase 0 del Plan 06.18).
> Implementado por `task_06_18_04`, `task_06_18_06`, `task_06_18_10`. Complementa el ADR 0044.

## Contexto

La taxonomía de tools no tiene una fuente de verdad coherente y la UI muestra capacidad que el motor
no entrega:

- `Tool.category`, `Tool.security_level` e `Tool.implementation_type` son **String libres sin CHECK**
  en BD (`domain.py:495,506-508`; única CHECK en `migración 0002` es `ck_tools_timeout_positive`). Un
  `POST /tools` con `category='GIT'` o un `security_level` inventado se persiste y cae a un fallback.
- `implementation_type` **mezcla dos ejes**: el mecanismo de ejecución (`builtin`/`python_function`/
  `http_endpoint`/`docker_command`) y el **origen** (`mcp_tool`). ADR 0044 ya usa `mcp_tool` como
  marcador de origen para el scope.
- Las facetas se renderizan **divergentes** entre pantallas: el diagnóstico
  (`agent-tools-diagnostic/page.tsx:76-80`) usa `SECURITY_BADGE={safe,sensitive,privileged}` —
  `sensitive` **no existe** (el enum real es `safe/sandboxed/privileged`, `domain.py:175-178`) y falta
  `sandboxed`, que cae al gris; e `IMPL_BADGE` pinta `docker_command='danger'` (`:73`) mientras la
  asignación lo pinta `'info'`; ambos renderizan el enum crudo en inglés.
- Hay **categorías fantasma**: `git` (4 tools, `builtin_tools.py:226-281`) tiene UI dedicada pero **no
  hay `git_tools.py` ni `register_git_tools`**; `semantic_search` (knowledge) ≠ `rag_search` (runtime).
  Al ejecutarse caen en `unknown tool`.
- El tier básica/avanzada del ADR 0044 se deriva sin constante compartida y el conteo está desfasado
  (ADR 0044 dice 18 tools; el seed tiene 19).

## Opciones consideradas

**Taxonomía:**

- **T-A. Tres facetas con Origen DERIVADO** (`is_builtin` + `implementation_type==mcp_tool`) sin nuevo
  campo. ✅ Coherente con la filosofía "derivar, no persistir" del ADR 0044; cero migración de datos.
  ❌ Origen sigue acoplado a dos columnas.
- **T-B. Campo `origin` de primera clase** en `Tool`. ✅ Explícito. ❌ Migración + backfill + otra
  fuente de verdad que sincronizar (mismo anti-patrón que el ADR 0044 rechazó para `tier`).
- **T-C. Status quo documentado** (mantener `implementation_type` como faceta mixta). ❌ No resuelve la
  ambigüedad ni la divergencia de badges.

**Disponibilidad (categorías sin motor):**

- **D-A. Campo DERIVADO `is_runtime_wired`** (Tool × conjunto registrable del runtime) en `ToolResponse`
  - badge "No disponible aún" + `422`/aviso en `PUT /agents/{id}/tools` si no ejecutable.
- **D-B. Retirar del seed** las categorías sin implementación (`git`) hasta que existan; cablear el resto.
- **D-C. Endpoint de capacidades del runtime** consultado en vivo por la UI.

## Decisión

**Taxonomía T-A + Disponibilidad (D-A ∧ D-B):**

1. **Tres facetas ortogonales**, siempre con las mismas etiquetas/colores en toda la app:
   - **Función** (qué hace): Archivos · Git · Runtime/Tests · Red · Conocimiento · Notificación ·
     Comandos shell · MCP · Orquestación. Eje principal de agrupación → `ToolCategory` **StrEnum +
     CHECK** en BD.
   - **Seguridad** (riesgo): Segura (`safe`) · Aislada (`sandboxed`) · Privilegiada (`privileged`),
     con `CHECK`. Nunca el enum crudo, **nunca `sensitive`**.
   - **Origen** (de dónde viene): Plataforma (`is_builtin`) · Tenant (custom) · MCP
     (`implementation_type==mcp_tool`, con prefijo `<server>.` visible). **Derivado**, no nuevo campo.
2. **`CHECK`/enum** también sobre `security_level` e `implementation_type`; las etiquetas (label ES+EN,
   variant, help) viven en **un módulo compartido del admin-panel** importado por asignación **y**
   diagnóstico (`task_06_18_10`), y/o servidas por el backend, para que nunca diverjan.
3. **`is_runtime_wired`**: campo derivado (Tool.name ∈ conjunto registrable del runtime) expuesto en
   `ToolResponse`; la UI marca "No disponible aún" + checkbox deshabilitado; `PUT /agents/{id}/tools`
   avisa/`422` ante nombre no ejecutable (`task_06_18_06`).
4. **Categorías sin motor**: se **retira del seed** la categoría `git` hasta que exista
   `register_git_tools`, y se reconcilia `semantic_search`↔`rag_search` en la fuente única (ADR 0048).
5. Se **actualiza el ADR 0044** al conteo real (19 tools) y se documenta que el tier se deriva del
   backend (campo derivado en `ToolResponse`), no re-derivado en cliente.

## Consecuencias

**Mejora:** una taxonomía con fuente de verdad (enums + CHECK), consistente en todas las pantallas; el
operador deja de ver como asignable lo que terminará en `unknown tool`; los badges no engañan.

**Complejidad:** migración reversible para los enums/CHECK; saneo de filas existentes fuera de enum (no
debería haber ninguna en los built-ins, pero la migración valida).

**Trade-offs:** Origen derivado (no campo nuevo) a cambio de seguir leyendo dos columnas — aceptado por
el mismo criterio del ADR 0044; si un día Origen deja de ser función de esas columnas, será su ADR.

## Riesgos

| Riesgo                                                        | Prob. | Impacto | Mitigación                                                       |
| ------------------------------------------------------------- | ----- | ------- | ---------------------------------------------------------------- |
| Una fila existente tiene `category`/`security_level` inválido | Baja  | Medio   | La migración detecta y la tarea sanea antes de aplicar el CHECK  |
| Retirar `git` del seed confunde a quien lo esperaba           | Media | Bajo    | Se marca "No disponible aún" + se documenta; vuelve al cablearse |
| El módulo de etiquetas vuelve a duplicarse en el frontend     | Baja  | Bajo    | Un único módulo importado por ambas pantallas + test de render   |

## Alternativas rechazadas

T-B (campo `origin`) y D-C (endpoint en vivo por render) por coste/redundancia frente a la derivación;
T-C por no resolver la divergencia.

## Trazabilidad

- Roadmap: `docs/roadmap/06.18-tools-overhaul.md` (`task_06_18_04/06/10`, `task_06_18_14`).
- Modelo/migración: `apps/api-server/src/api_server/db/domain.py`, `migrations/versions/`.
- UI: `apps/admin-panel/lib/tools/taxonomy.ts` (nuevo), `agent-tools-section.tsx`, `agent-tools-diagnostic/page.tsx`.
- ADRs relacionados: 0044 (tier derivado — se actualiza el conteo), 0048 (nombres canónicos), 0014/0025.
