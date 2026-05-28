# Tests humanos por plan

Cada plan del roadmap define en su frontmatter una lista de tests
"humanos" — escenarios end-to-end que un revisor humano valida antes
de pasar el plan de `pending_human_validation` a `completed`. Esta
carpeta documenta cómo ejecutarlos: comandos, output esperado, qué
mirar en la UI, criterios de pass/fail.

> **Diferencia con los tests automáticos**: los `auto_NN_NN_a` del
> roadmap corren en CI (`pytest`, `playwright`, etc.) y los lleva el
> CI. Los `human_NN_NN` necesitan ojos humanos — esta guía les pone
> contexto + un launcher cuando es viable.

## Índice

| Plan                                  | Tests humanos                     | Guía                                                                         |
| ------------------------------------- | --------------------------------- | ---------------------------------------------------------------------------- |
| Plan 02 — Ejecución de agentes        | 5 (`human_02_01..05`)             | [`../run-demo-human-tests.md`](../run-demo-human-tests.md) (pendiente split) |
| Plan 04 — Memoria, RAG, KBs           | 5 (`human_04_01..05`)             | [`../run-demo-human-tests.md`](../run-demo-human-tests.md) (pendiente split) |
| Plan 04.5 — Agent-runtime integration | 2 demos                           | [`../run-demo-human-tests.md`](../run-demo-human-tests.md) (pendiente split) |
| Plan 05 — MCP y Tools avanzadas       | 3 (`human_05_01..03`)             | [`05-mcp-tools-avanzadas.md`](./05-mcp-tools-avanzadas.md)                   |
| Plan 06 — Testing + Git ciclo         | 12 (`human_06_01..12`)            | [`06-testing-revision-git.md`](./06-testing-revision-git.md)                 |
| Plan 06.5 — Orchestrator wiring       | 0 propios (reusa los del Plan 06) | — (ver nota abajo)                                                           |
| Plan 06.6 — Admin UI gaps             | 2 (`human_06_6_01..02`)           | [`06.6-admin-ui-gaps.md`](./06.6-admin-ui-gaps.md)                           |
| Plan 06.7 — Memory dedup              | 2 (`human_06_7_01..02`)           | [`06.7-memory-dedup.md`](./06.7-memory-dedup.md)                             |
| **Plan 06.8 — RBAC enforcement**      | 4 (`human_06_8_01..04`)           | [`06.8-rbac-enforcement.md`](./06.8-rbac-enforcement.md)                     |

> El `run-demo-human-tests.md` de la raíz de guides acumula los Plans
> 02 / 04 / 04.5 históricamente en un único documento. La migración a
> esta estructura per-plan está pendiente; el de Plan 05 estrena la
> carpeta.

> **Plan 06.5 no tiene tests humanos propios**: su rol es cablear los
> módulos del Plan 06 en Celery + endpoints + beat. Los 12 tests del
> Plan 06 (`human_06_01..12`) que dependían del wiring (`06_01`,
> `06_03`, `06_04`, `06_05` end-to-end real, `06_07` con Prometheus,
> `06_09` con conflictos reales, `06_11` con submit del checkbox vía
> API) ahora son ejecutables contra infraestructura real gracias a
> 06.5. Usa la guía del Plan 06.

## Convención de nombres

`<plan_id>.md` — coincide con el `plan_id` del frontmatter del plan
(`docs/roadmap/<plan_id>.md`). Así un grep por plan_id encuentra el
plan + su guía humana + su entrada de changelog en
`docs/07-changelog/<plan_id>.md`.

## Estructura recomendada de cada guía

1. **Estado del plan + alcance** — qué se prueba y por qué.
2. **Pre-requisitos específicos** — además de `up.ps1`, lo propio del plan.
3. **Por cada `human_NN_NN`**:
   - "Qué prueba" (1-2 frases).
   - "Cómo ejecutarlo" — comandos exactos, copy-pasteables.
   - "Output esperado" — qué debe pasar.
   - "Checklist de pass/fail" — los items del roadmap.
   - "Pitfalls conocidos" — errores comunes y su workaround.
4. **Launcher PowerShell** si existe — `scripts/dev/run-human-tests-<plan>.ps1`.
5. **Troubleshooting** — solo lo específico del plan; los problemas
   transversales viven en `docs/03-guides/gotchas/`.
