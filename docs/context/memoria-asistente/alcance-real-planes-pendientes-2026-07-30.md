---
name: alcance-real-planes-pendientes-2026-07-30
description: "Los 14 planes pending_approval son 195 días-persona reales (121 de 163 casillas son GAP verdadero), no casillas mal marcadas"
metadata:
  node_type: memory
  type: project
  originSessionId: 5d8f55fb-8d51-43ab-8655-49099d7db010
  modified: 2026-07-30T12:19:20.886Z
---

Medido el 2026-07-30 con un recon de un auditor por plan + pasada adversarial
sobre los veredictos `DONE` (16 planes, 163 casillas abiertas):

| veredicto                                 | casillas |
| ----------------------------------------- | -------: |
| `GAP` (no está implementado)              |  **121** |
| `PARTIAL` (falta el test o un tramo)      |       25 |
| `HUMAN` (no es código)                    |       10 |
| `DONE` (casilla rancia)                   |        6 |
| `REJECTED` (lo invalidó un ADR posterior) |        1 |

Suma de `estimated_effort_person_days` de sus frontmatter: **195 días-persona**.

**Por qué importa:** el patrón que documenta
[[verificar-antes-de-implementar]] («la mayoría de las casillas pendientes ya
están hechas») vale para los planes **entregados** que quedaron sin marcar, y
NO para estos: los 14 `pending_approval` están de verdad sin empezar, y su
estimación es realista. No prometas cerrarlos en una sesión.

**Cómo aplicarlo:** al recibir «termina todos los planes», medir primero y dar
la cifra antes de empezar. El orden que funcionó: (1) acreditar con tests los
planes en `pending_human_validation` — barato y cierra planes; (2) los huecos
del córtex, que estaban ya especificados uno a uno; (3) los prod-XX, que son el
grueso. Ver [[bloqueo-cierre-planes-pr-sin-mergear]].

Los recon quedaron destilados en el scratchpad de la sesión
(`recon-prod-digest.json`, `validacion-digest.json`, `cortex-digest.json`).
