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

| Plan                                    | Tests humanos                     | Guía                                                                           |
| --------------------------------------- | --------------------------------- | ------------------------------------------------------------------------------ |
| Plan 00 — Fundaciones                   | 5 (`human_00_01..05`)             | [`00-fundaciones.md`](./00-fundaciones.md)                                     |
| Plan 01 — Dominio mínimo                | 4 (`human_01_01..04`)             | [`01-dominio-minimo.md`](./01-dominio-minimo.md)                               |
| Plan 02 — Ejecución de agentes          | 5 (`human_02_01..05`)             | [`02-ejecucion-agentes.md`](./02-ejecucion-agentes.md)                         |
| Plan 03 — Chat, planning y aprobación   | 5 (`human_03_01..05`)             | [`03-chat-planning-aprobacion.md`](./03-chat-planning-aprobacion.md)           |
| Plan 04 — Memoria, RAG, KBs             | 5 (`human_04_01..05`)             | [`04-memoria-rag-kbs.md`](./04-memoria-rag-kbs.md)                             |
| Plan 04.5 — Agent-runtime integration   | 2 demos                           | [`04.5-agent-runtime-integration.md`](./04.5-agent-runtime-integration.md)     |
| Plan 05 — MCP y Tools avanzadas         | 3 (`human_05_01..03`)             | [`05-mcp-tools-avanzadas.md`](./05-mcp-tools-avanzadas.md)                     |
| Plan 06 — Testing + Git ciclo           | 12 (`human_06_01..12`)            | [`06-testing-revision-git.md`](./06-testing-revision-git.md)                   |
| Plan 06.5 — Orchestrator wiring         | 0 propios (reusa los del Plan 06) | — (ver nota abajo)                                                             |
| Plan 06.6 — Admin UI gaps               | 2 (`human_06_6_01..02`)           | [`06.6-admin-ui-gaps.md`](./06.6-admin-ui-gaps.md)                             |
| Plan 06.7 — Memory dedup                | 2 (`human_06_7_01..02`)           | [`06.7-memory-dedup.md`](./06.7-memory-dedup.md)                               |
| Plan 06.8 — RBAC enforcement            | 4 (`human_06_8_01..04`)           | [`06.8-rbac-enforcement.md`](./06.8-rbac-enforcement.md)                       |
| Plan 06.9 — Agent-scoped KBs            | 4 (`human_06_9_01..04`)           | [`06.9-agent-scoped-kbs.md`](./06.9-agent-scoped-kbs.md)                       |
| Plan 06.10 — KB categories              | 4 (`human_06_10_01..04`)          | [`06.10-kb-categories.md`](./06.10-kb-categories.md)                           |
| Plan 06.11 — KB ingestion fixes         | 4 (`human_06_11_01..04`)          | [`06.11-kb-ingestion-fixes.md`](./06.11-kb-ingestion-fixes.md)                 |
| Plan 06.12 — Global catalog consistency | 3 (`human_06_12_01..03`)          | [`06.12-global-catalog-consistency.md`](./06.12-global-catalog-consistency.md) |
| Plan 06.13 — KB catalog content         | 2 (`human_06_13_01..02`)          | [`06.13-kb-catalog-content.md`](./06.13-kb-catalog-content.md)                 |
| Plan 06.14 — Hardening + auditoría      | 16 (`human_06_14_01..16`)         | [`06.14-hardening-auditoria.md`](./06.14-hardening-auditoria.md)               |
| Plan 06.15 — Agent tools assignment UI  | 2 (`human_06_15_01..02`)          | [`06.15-agent-tools-assignment-ui.md`](./06.15-agent-tools-assignment-ui.md)   |
| Plan 06.16 — Polyglot tool catalog      | 1 (`human_06_16_01`)              | [`06.16-polyglot-tool-catalog.md`](./06.16-polyglot-tool-catalog.md)           |
| Plan 07 — Documentación + visor         | 4 (`human_07_01..04`)             | [`07-documentacion-visor.md`](./07-documentacion-visor.md)                     |
| Plan 08 — SSO empresarial               | 3 (`human_08_01..03`)             | [`08-sso-empresarial.md`](./08-sso-empresarial.md)                             |
| Plan 09 — Marketplace                   | 4 (`human_09_01..04`)             | [`09-marketplace.md`](./09-marketplace.md)                                     |
| Plan 09.1 — Marketplace seed + publish  | 1 (`human_09_1_01`)               | [`09.1-marketplace-seed-publish.md`](./09.1-marketplace-seed-publish.md)       |
| Plan 10 — Asistente personal            | 4 (`human_10_01..04`)             | [`10-asistente-personal.md`](./10-asistente-personal.md)                       |
| Plan 11 — Guardrails + precios          | 4 (`human_11_01..04`)             | [`11-guardrails-precios.md`](./11-guardrails-precios.md)                       |
| Plan 11.2 — LLM provider admin UI       | 3 (`human_11_2_01..03`)           | [`11.2-llm-provider-admin-ui.md`](./11.2-llm-provider-admin-ui.md)             |
| Plan 12 — Backup + restore              | 4 (`human_12_01..04`)             | [`12-backup-restore.md`](./12-backup-restore.md)                               |
| Plan 13 — API pública + webhooks        | 4 (`human_13_01..04`)             | [`13-api-publica-webhooks.md`](./13-api-publica-webhooks.md)                   |
| Plan 14 — Evals + estadísticas          | 4 (`human_14_01..04`)             | [`14-evals-estadisticas.md`](./14-evals-estadisticas.md)                       |
| Plan 15 — Instalador + producción       | 5 (`human_15_01..05`)             | [`15-instalador-produccion.md`](./15-instalador-produccion.md)                 |
| Plan 16 — Human agents                  | 6 (`human_16_01..06`)             | [`16-human-agents.md`](./16-human-agents.md)                                   |
| Marketplace v2 — despliegue             | 3 (`human_mkt2_01..03`)           | [`marketplace-v2-despliegue.md`](./marketplace-v2-despliegue.md)               |
| Córtex F2 — modelo afectivo             | 1 (sin `human_*` declarado)       | [`cortex-f2-afectivo.md`](./cortex-f2-afectivo.md)                             |
| Córtex F5 — voz y avatar                | 1 (sin `human_*` declarado)       | [`cortex-f5-voz-avatar.md`](./cortex-f5-voz-avatar.md)                         |

> **Las dos guías del córtex no declaran ids `human_*`** porque sus planes
> tampoco los declaran: F2 y F5 escribieron sus criterios humanos dentro de la
> casilla de cierre, en prosa. Se documentan igual porque el problema era el
> contrario al habitual — las casillas decían «bloqueada por un humano» cuando
> **la mitad de lo que las bloqueaba era ejecutable**, y eso hace creer al
> operador que le toca a él más trabajo del que le toca. Cada guía separa, con
> números medidos, lo que ya acredita una máquina de lo que necesita ojos.

> El antiguo `docs/03-guides/run-demo-human-tests.md` acumulaba los
> Plans 02 / 04 / 04.5 en un único documento. La migración a la
> estructura per-plan ya está hecha — esa página queda como
> redirección + troubleshooting compartido (los problemas
> transversales del stack dev: JWT secret mismatch, asyncpg,
> docling-serve down, etc.).

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
