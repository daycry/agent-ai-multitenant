---
name: deliverables-en-docs-roadmap
description: "Auditorías, planes y diseños se guardan en docs/roadmap/, NO en docs/plans ni docs/superpowers."
metadata:
  node_type: memory
  type: feedback
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

Los documentos de auditoría, planes y diseños van en **`docs/roadmap/`** (junto a `auditoria-produccion-2026-06.md`, `prod-01..16`, fases `00..16`). NO crear `docs/plans/` ni usar `docs/superpowers/plans`.

**Why:** el operador mantiene TODOS los planes/auditorías consolidados en `docs/roadmap/` ("ahí están todos", 2026-06-22). Crear otra carpeta fragmenta el roadmap.

**How to apply:** al generar un informe de auditoría, plan de feature o diseño, escribirlo en `docs/roadmap/<nombre>.md`. Los ADR siguen en `docs/05-architecture-decisions/`. Relacionado con el protocolo de roadmap de [[estado-trabajo-en-curso]].
