---
name: prioridad-codigo-limpio-mantenible
description: "El operador prioriza código limpio, bien estructurado y refactor oportunista por mantenibilidad al implementar."
metadata:
  node_type: memory
  type: feedback
  originSessionId: cc6008fc-23fa-4218-be2b-123a3f5cd8cc
---

Al implementar planes/fixes, el operador quiere **código limpio, perfectamente estructurado y refactorización oportunista** de las partes mejorables — explícitamente "por mantenibilidad" (2026-06-22).

**Why:** valora la mantenibilidad a largo plazo por encima de soluciones rápidas; espera que, al tocar una zona, deje el código mejor de lo que estaba.

**How to apply:** TDD; módulos pequeños y enfocados (un fichero = una responsabilidad); seguir los patrones existentes del repo; refactor oportunista donde mejore legibilidad/estructura SIN reescrituras big-bang ni scope creep; pre-commit en verde (nunca `--no-verify`). Respetar las reglas del repo: features nuevas o que toquen aislamiento/egress/arquitectura → ADR primero y luz verde del operador (egress = ADR 0067 GATED). Relacionado con [[deliverables-en-docs-roadmap]] y [[adr-pendientes-implementar-autonomo]].
