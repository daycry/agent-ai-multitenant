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

| Plan                                  | Tests humanos          | Guía                                                                         |
| ------------------------------------- | ---------------------- | ---------------------------------------------------------------------------- |
| Plan 02 — Ejecución de agentes        | 5 (`human_02_01..05`)  | [`../run-demo-human-tests.md`](../run-demo-human-tests.md) (pendiente split) |
| Plan 04 — Memoria, RAG, KBs           | 5 (`human_04_01..05`)  | [`../run-demo-human-tests.md`](../run-demo-human-tests.md) (pendiente split) |
| Plan 04.5 — Agent-runtime integration | 2 demos                | [`../run-demo-human-tests.md`](../run-demo-human-tests.md) (pendiente split) |
| Plan 05 — MCP y Tools avanzadas       | 3 (`human_05_01..03`)  | [`05-mcp-tools-avanzadas.md`](./05-mcp-tools-avanzadas.md)                   |
| **Plan 06 — Testing + Git ciclo**     | 12 (`human_06_01..12`) | [`06-testing-revision-git.md`](./06-testing-revision-git.md)                 |

> El `run-demo-human-tests.md` de la raíz de guides acumula los Plans
> 02 / 04 / 04.5 históricamente en un único documento. La migración a
> esta estructura per-plan está pendiente; el de Plan 05 estrena la
> carpeta.

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
