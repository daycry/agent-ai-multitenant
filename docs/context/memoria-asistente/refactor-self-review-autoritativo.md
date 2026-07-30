---
name: refactor-self-review-autoritativo
description: "ADR 0087 — self-review autoritativo de 3 estados + finish estructurado submit_result, IMPLEMENTADO y DESPLEGADO en dev (rama plan/runs-visor-trabajo)."
metadata:
  node_type: memory
  type: project
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

2026-06-27: refactor limpio del pipeline ejecución+self-review del agent-runtime
(spec en docs/roadmap/refactor-pipeline-ejecucion-review.md). Tres decisiones del
operador: self-review **autoritativo** (fail-closed), **finish estructurado**
`submit_result(status,summary)` + UI, veredicto **inconcluso → escalar a humano**.

**Implementado y DESPLEGADO en el stack dev** (rama `plan/runs-visor-trabajo`, NO
mergeado a master; sin PR abierto). Commits f6b1a94 (A+A2) · 5e4752c (B) · bcdd9cb
(C0–C3) · 77f37fe (persistencia+UI) · 9e5501b (ADRs 0086+0087).

Claves del diseño (ver [[adr-0082-provider-id-unificacion]] para el contexto de providers):

- Verdict 3-estados (pass/fail/inconcluso), orden canónico tool-call>JSON>prosa-acotada;
  el prose-sniffing es ÚNICO último recurso (red invariante). Polaridad fail-closed.
- Escalado: runtime emite `execution.status = needs_human_review` → worker lleva la
  tarea a `blocked` (reusa la bandeja humana). `pending_human_validation` es de PLAN,
  NO de tarea (ppio 7) — por eso NO se añadió estado de tarea nuevo.
- `submit_result` advertido solo en HTTP; en claude_sdk NO se fuerza (su content=""
  al disparar tool tiraría el output) → finaliza en prosa + wrap. Routing por nombre
  de tool en `_decision_from`.
- Migración 0100 (executions.finish_status varchar(16)) APLICADA a la BD dev (head=0100).
- Descartado por over-engineering: sección de prosa "Resultado final" (recreaba la
  colisión de dominio de [[memoria-tool-calling-fix]]/markers), response_schema genérico.

Deploy dev: imágenes reconstruidas con tags propios — api-server `:manuals` y `:ci`
(WITH_CLAUDE=1), workers `:ci` (FROM api-server:ci), agent-runtime `:v1` (WITH_CLAUDE=1,
**contexto = raíz del repo**, no la carpeta), admin-panel `:manuals` (NEXT_PUBLIC_API_URL=/api).
Builds con `--build-arg` SIEMPRE vía PowerShell y `Set-Location` a la raíz (el cwd de
PowerShell no es la raíz por defecto). Recreate con base+dev+manuals (project name
`agentic-platform` fijado en el base). Pendiente del operador: merge a master.
