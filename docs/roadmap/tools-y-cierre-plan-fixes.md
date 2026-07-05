---
plan_id: tools-y-cierre-plan-fixes
title: Tools, guardrails de runtime y cierre de plan — paridad catálogo↔executor, gate humano que no falle-abierto y changelog automático
status: pending_approval
blocking_plan: []
started_at: null
completed_at: null
estimated_duration_calendar: 4-5 días
estimated_effort_person_days: 4
estimated_cost_human_eur: 1.600 € – 2.400 €
estimated_cost_ai_eur: 25 € – 45 €
created_by: auditoría-plataforma-2026-07-03
spec_sections_referenced: []
docs_language: es
---

# Plan tools-y-cierre-plan-fixes — que el catálogo no mienta, que el gate humano enforce y que el plan cierre completo

> **Origen:** auditoría de plataforma 2026-07-03, causas raíz **D (guardrails/gate sin callers en runtime)**,
> **E (el catálogo declara lo que el runtime no tiene)** y **G (cierre de plan incompleto)**. Hallazgos
> g1-g6 + c4 verificados adversarialmente en Opus 4.8.
>
> **Coordinación obligatoria con `prod-03-guardrails-validacion-humana.md`** (`pending_approval`): ese plan ya
> aborda el re-mapeo de categorías del gate y el cableado de guardrails. Este plan **no lo duplica**: donde
> se solapa, remite a prod-03 y añade solo lo que falta (paridad catálogo↔executor, docling-mcp, changelog).

## Cabecera

| Campo           | Valor                                                                   |
| --------------- | ----------------------------------------------------------------------- |
| **ID del Plan** | `tools-y-cierre-plan-fixes`                                             |
| **Rama git**    | `plan/runs-visor-trabajo` (rama en curso)                               |
| **Causa raíz**  | D (guardrails sin callers) + E (catálogo≠runtime) + G (cierre incompl.) |

## Problema (con evidencia verificada)

| Id     | Veredicto                     | Defecto                                                                                                                                                                                                                                                                                                                                                              | Reencuadre tras verificación                                                                                                                                                                                                                                                                                                            |
| ------ | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **g1** | confirmado (**P0 seguridad**) | Los guardrails `pre_tool`/`post_tool` no se evalúan en NINGÚN camino de ejecución de agentes: `GuardrailPipeline` solo se instancia en `planning.py` y solo cablea pre_llm/post_llm; `apps/workers` tiene **0 refs** a guardrail; el agent-runtime lo reconoce en docstrings («lands in Plan 11»). Principio rector 10 incumplido; prod-03 lo reconoce textualmente. | ADR 0035 `accepted` diseña el motor **puro** («el host lo cabla después») y el host **nunca lo cabló** para hooks de tool. El agent-runtime ejecuta http/MCP/RAG con egress (ADR 0094/0067) y las salidas **reentran crudas** → inyección indirecta sin defensa. `tool_wiring.py:23` confirma que tampoco se enforcea `security_level`. |
| **g2** | matizado                      | Las tools MCP declaradas-pero-no-importadas son invisibles al LLM (`agent_tool_schemas.py:197` no hace discovery MCP).                                                                                                                                                                                                                                               | **Es el diseño de ADR 0052** (importación manual P-A; auto-import P-B rechazado). No es defecto; el «import-para-ver» es intencional. Residuo: solo UX (badge «requiere import»).                                                                                                                                                       |
| **g3** | confirmado (deuda aceptada)   | El marketplace no materializa: instalar solo crea `MarketplaceInstallation`+auditoría, sin `Tool/Skill`; la tabla `tools` no tiene columna de provenance; `_run_security_gates` no corre en install fresco (solo en update).                                                                                                                                         | **Deuda aceptada y transparente** (ADR 0081, H4+M1 diferidos a Fase B/C, bloqueado por infra). El docstring de `ENABLED` **desmiente** la usabilidad («NOT a live capability»). No hay promesa rota; en dev 0 instalaciones.                                                                                                            |
| **g4** | confirmado (peor)             | `apply_patch`/`search_code`/`summarize_text` están en el catálogo asignable pero **sin executor** (`RUNTIME_WIRED_TOOL_NAMES` los excluye); la UI no los filtra.                                                                                                                                                                                                     | **Peor que el claim**: los **seeds de rol/equipo asignan estos tools** esquivando la guarda 422 del PUT; `dispatch` no filtra por wired. Evidencia viva: `search_code` murió como «unknown tool» en run 019f27ff (idx 36); el agente QA CI4 lo tiene asignado hoy.                                                                      |
| **g5** | confirmado                    | `DOCLING_MCP` se oferta en el picker pero requiere un binario `docling-mcp` que upstream no publica; el servicio está comentado en compose.                                                                                                                                                                                                                          | Correcto en todo. La única vía Docling operativa es `docling-serve` HTTP (ingestión KB), un code-path distinto. `command="docling-mcp"` ni coincide con el ejecutable real.                                                                                                                                                             |
| **g6** | confirmado (**P0 seguridad**) | El gate de validación humana emite 4 categorías (`code_execution`/`file_write`/`network_access`/`agent_delegation`) que **NO intersectan** las 13 canónicas de las plantillas → intersección vacía → `requires_human()` siempre `auto` → **fail-open**. Las tools MCP nunca se gatean.                                                                               | **Principio 11 incumplido de facto**: ni el preset «Cliente Externo» (13/13 human_required) detiene una sola tool sensible. Ortogonal a ADR 0048 (que unificó NOMBRES, no categorías). Los tests solo pinean claves, no valores → CI no lo detecta.                                                                                     |
| **c4** | confirmado                    | El Technical Writer al cierre es aspiracional: `generate_plan_docs`/`render_changelog` solo se invocan desde tests; `_on_task_done` solo transiciona a `pending_human_validation` y arranca el review-runtime.                                                                                                                                                       | Criterio de cierre 4 de CLAUDE.md (entrada en `docs/07-changelog/{plan_id}.md`) **no lo cumple ningún path automático**. El agente Technical Writer existe sembrado pero nadie le crea/despacha la tarea.                                                                                                                               |

## Alcance

**Entra:** cerrar el fail-open del gate humano (g6, coordinado con prod-03), cablear un slice mínimo de
guardrails en el loop (g1, coordinado con prod-03), paridad catálogo↔executor con test de CI (g4), docling-mcp
imagen-o-retirar (g5), badge de import (g2-UX), y el cableado del changelog/docs al cierre de plan (c4). g3 no
requiere acción (deuda ADR 0081 ya planificada); se documenta como consciente.

**Queda fuera (GATED → ADR):**

- **Guardrails persistidos por proyecto + motor completo en runtime** → **ADR candidato 0102** (este plan solo
  cablea el slice mínimo default-log; la persistencia por proyecto y las 4 fases completas van al ADR).
- **Materialización del marketplace** (g3) → ya cubierto por ADR 0081 Fase B/C; **ADR candidato 0100** si se
  quiere adelantar. No se toca aquí.
- **Discovery MCP en runtime** (auto-visibilidad de tools) → **ADR candidato 0101** (contradice ADR 0052 si se
  hace mal; decisión de producto).

## Decisiones clave

- **g6 es la prioridad P0**: un gate que cree estar activo y deje pasar todo es peor que no tenerlo. Se
  re-mapea el vocabulario de categorías a las 13 canónicas **en coordinación con prod-03** (no duplicar) y se
  añade un test de contrato que falle si los dos vocabularios divergen.
- **Paridad catálogo↔executor como invariante de CI** (principio de ADR 0049, que ya retiró la familia git por
  esto): ninguna tool asignable sin executor; los seeds tampoco pueden asignarla.
- **El slice de guardrails nace en modo `log`** (no bloqueante) para medir antes de enforcar (misma filosofía
  que la instrumentación B1 de guardas).

## Tareas

### Fase A — Gate humano y guardrails de runtime (g6 + g1) — coordinar con prod-03

- [x] **T1 — Re-mapeo de categorías del gate (g6, P0)**: `DEFAULT_TOOL_CATEGORIES` del runtime
      (`approval.py:25-32`) emite las **13 categorías canónicas** de las plantillas de política
      (`builtin_approval_policies.py`), no las 4 actuales. Mapear cada tool builtin a su categoría canónica
      (p.ej. `shell_exec→code_changes`, `http_post→external_http_post`, `write_file→code_changes`). **Coordinar
      con prod-03 task_prod03_01.** **Test de contrato:** el conjunto de categorías que emite el gate ⊆ las 13
      canónicas; un preset `customer-external` (13/13 human_required) **detiene** `shell_exec`/`write_file`/
      `http_post`/`agent_invoke` (hoy pasan). Falla si los vocabularios divergen.
- [ ] **T2 — Tools MCP gateables (g6)**: forwardear un campo `category` en el `ToolSpec` de las tools MCP/custom
      para que `<server>.<tool>` sea gateable (hoy solo 6 builtins están en el mapa). **Coordinar con prod-03
      task_prod03_02.** **Test:** una tool MCP marcada sensible se aparca para validación humana bajo el preset
      adecuado.
- [x] **T3 — Slice mínimo de guardrails en el loop (g1, P0)**: cablear `pre_tool`/`post_tool` del motor
      `shared-guardrails` en el loop del agent-runtime (o worker) en modo `log` (no bloqueante), de modo que las
      salidas MCP/HTTP/RAG pasen por al menos un check de inyección (`prompt_injection`, `secret_leakage`)
      registrado — hoy el motor existe y está testeado pero **nunca corre** en ejecución de agentes. Enforcar
      `security_level` de la Tool (hoy `tool_wiring.py:23` no lo hace). **Coordinar con prod-03.** **Test:** una
      salida MCP/HTTP con patrón de inyección genera un evento de guardrail en `steps_log`; el ADR 0102 (motor
      completo + persistencia por proyecto + enforce) queda listado, no redactado.

### Fase B — Paridad catálogo↔executor (g4 + g5 + g2)

- [ ] **T4 — Test de CI de paridad catálogo↔executor (g4, P1)**: test que falla si un nombre de
      `_CATALOG_TOOL_NAMES` builtin **no** está en `RUNTIME_WIRED_TOOL_NAMES` **y** es asignable. **Test:**
      hoy falla nombrando `apply_patch`/`search_code`/`summarize_text`.
- [ ] **T5 — Cablear-o-retirar los tools sin executor (g4)**: para cada uno, decidir: implementar el executor
      (p.ej. `search_code` como grep del worktree) **o** retirarlo del catálogo asignable Y de
      `ROLE_DEFAULT_TOOLS`/`ci4_team._BASE_TOOLS` (los seeds que hoy los asignan esquivando el 422). El
      `dispatch` (`combine_tool_allowlists`) filtra por `is_runtime_wired` antes de anunciar al LLM. **Test:**
      ningún seed asigna un tool no cableado; el LLM nunca recibe un tool sin executor; regresión del run 019f27ff
      (search_code) imposible.
- [ ] **T6 — Badge «requiere import» / no cableado en la UI (g4 + g2-UX)**: la UI de asignación
      (`agent-tools-section.tsx`) marca visualmente los tools no cableados (deshabilitados o con aviso) y las
      tools MCP declaradas-no-importadas con un badge «requiere import» (comportamiento ADR 0052 preservado, solo
      señalizado). **Test:** un tool no cableado no es asignable desde la UI; una tool MCP no importada muestra el
      badge.
- [ ] **T7 — docling-mcp: imagen-o-retirar (g5)**: retirar `DOCLING_MCP` del picker del catálogo MCP (o filtrarlo
      como «no disponible») hasta que exista una imagen; documentar que la vía operativa es `docling-serve` HTTP.
      **Test:** el picker no ofrece un template que no puede arrancar (o lo marca no-disponible con motivo).

### Fase C — Cierre de plan completo (c4)

- [ ] **T8 — Changelog + docs automáticos al cierre (c4, G)**: cablear `generate_plan_docs`/`render_changelog`
      (o el despacho del agente Technical Writer) en el path real de cierre de plan (`_on_task_done` /
      `maintenance._reconcile_complete_plans`), generando la entrada `docs/07-changelog/{plan_id}.md`. **Test:**
      al completar un plan (con o sin PR), existe la entrada de changelog; el criterio de cierre 4 de CLAUDE.md
      se cumple por un path automático.

### Fase D — g3 (documentar, no tocar)

- [x] **T9 — Registrar g3 como deuda consciente**: dejar constancia (en este plan y en el informe) de que la no
      materialización del marketplace es diferimiento aceptado (ADR 0081 Fase B/C), no un defecto oculto; no se
      implementa aquí. Si se quiere adelantar → **ADR 0100**. **Test:** n/a (documental).
      **REGISTRADO (2026-07-06):** g3 (el marketplace de tools/MCP no materializa installs frescos ni los gatea) es
      **diferimiento consciente** heredado de ADR 0081 (Fase B/C del marketplace sin implementar), NO un fallo oculto:
      el catálogo builtin y la asignación por proyecto sí funcionan; lo que falta es el flujo de instalación desde un
      marketplace externo. Adelantarlo requiere ADR 0100 (materialización del marketplace). Sin impacto en runs
      actuales (todos usan el catálogo builtin).

## Criterios de cierre

1. Checkboxes en `[x]` con test automático en verde.
2. **g6 cerrado**: el preset «Cliente Externo» detiene toda tool sensible (test de contrato verde); vocabularios
   de categorías reconciliados con prod-03.
3. Test de CI de paridad catálogo↔executor activo y verde (T4); ningún seed asigna tools sin executor (T5).
4. Al cerrar un plan se genera su entrada de changelog automáticamente (T8).
5. ADRs 0100/0101/0102 listados como candidatos en el informe; no redactados aquí.
