---
name: prueba-mcp-tools-skills-2026-07-18
description: "Prueba crucial MCP Atlassian + custom tool + skill VALIDADAS e2e con runs reales; 3 fixes de plataforma (cancel-scope runner, step mcp_wire, NO_PROXY internos); pendiente manual PDF regenerado"
metadata:
  node_type: memory
  type: project
  originSessionId: 50eee157-5b9f-4f4f-85b3-9a5c1e232a6e
---

2026-07-18 — mandato del operador: "prueba crucial" de MCP Atlassian + custom tools + custom skills, con documentación de ejemplos.

**VALIDADO con runs reales (proyecto c3afa43e, tenant demo):**

- **MCP**: server de prueba estilo Atlassian (FastMCP streamable_http en `agentic-agents`) → test-connection → import-tools → asignación → run del plan v4 (019f771e): el agente ESCRIBIÓ docs/BIENVENIDA.md y llamó `atlassian.confluence_create_page` (space DEMO, título "Bienvenida E2E") y `atlassian.jira_transition_issue` (DEMO-123→Done con la URL de la página encadenada). Payloads verificados en calls.jsonl del server.
- **Custom tool** (`python_function`): `changelog_stamp` creada por API, run real la invocó y escribió su output en docs/CHANGELOG-PRUEBA.md; reviewer aprobó (plan 019f7709 done).
- **Skill** (`prompt_fragment`): "Estilo de changelog corporativo" — la tarea NO nombraba la tool; el fragment indujo su uso + sello CHANGELOG-OK en el output. Mecanismo ADR 0050 confirmado.

**3 fixes de plataforma (commits en plan/runs-visor-trabajo):**

1. `807431dc` MCPToolRunner: task dedicada dueña del context manager completo por servidor (anyio cancel-scope violaba en transportes HTTP; solo cubierto stdio antes). Tests streamable_http reales con logger espiado.
2. `6884c78f` step `mcp_wire` (ok/error) en steps_log — antes el wiring MCP era invisible en el visor.
3. NO_PROXY en `_build_runtime_env` (workers): hostnames INTERNOS (sin punto) de los mcp_servers declarados exentos del egress-proxy — el 403 Filtered de tinyproxy era la causa raíz del fallo del run (no el cancel-scope). MCP externos (FQDN) siguen por el proxy + allowlist.

**Aprendizajes clave:**

- El abort inicial (25 iters, 0 MCP calls) fue por TAREA NO AUTOCONTENIDA (pedía publicar un fichero inexistente) — no por el stack MCP.
- El run intentó `atlassian.confluence_create_page` con nombre exacto aun sin la tool registrada → el prompt/catálogo funcionan; "unknown tool" = wiring, no modelo.
- El worker usa el tag `agent-runtime:v1` (SIN prefijo agentic-platform/) — retaggear tras rebuild.
- Docs: [[deliverables-en-docs-roadmap]] guía nueva `docs/03-guides/recetas-mcp-tools-skills.md` (Atlassian, Context7, GitHub, MCP propio, python_function, http_endpoint, skills) + sección "que los agentes USEN las tools" en configurar-mcp-server.md.

**CIERRE 2026-07-18 (todo verificado):** 4º fix — digest de tool calls en `_review_messages` (e9a7e593): la self-review no veía las invocaciones MCP («no evidence of calls») y escalaba trabajo hecho; con el digest el run v5 (plan 019f7728) cerró el ciclo ENTERO: mcp_wire ok → write_file → confluence_create_page → jira_transition_issue (payloads en calls.jsonl) → self-review PASS → AI reviewer → **task=done sin humano**. PUSH hecho (7d779ac3..e9a7e593, 6 commits). CLEANUP hecho: contenedor+volumen borrados, mcp_servers del proyecto vaciado, tools atlassian.\* quitadas de agente y catálogo; changelog_stamp + skill "Estilo de changelog corporativo" CONSERVADAS en tenant demo como ejemplos vivos del recetario. Gotcha nuevo: pre-commit con black/prettier que reformatean ficheros staged + cambios unstaged en el mismo fichero → 'stashed changes conflicted', el commit NO entra silenciosamente — correr black/prettier a mano y re-add antes de commitear.

**MANUAL PDF HECHO (2026-07-19):** specs 03/08/11/13 ampliados (subagente, tsc --strict OK; 03: 17→20 pasos con changelog_stamp/skills/flujo MCP; 08: SSO multi-provider + aviso MFA sin UI; 11: paso instalación producción; 13: mcp_wire) + REGENERADO entero con `./scripts/dev/generate-manuals.ps1 -SkipBuild` — 14/14 passed (31.8 min), manual-completo.pdf = 224 pp, capturas de la UI real. Commit e8d1b1f5 + push. Receta: -SkipBuild vale cuando las imágenes :manuals corrientes ya son las verificadas; el runner siembra demo idempotente y corre Playwright 1 worker.

**SUITE INTEGRATION CERRADA (2026-07-19):** run limpio = 3378 passed / 57 failed / 4 errors (el run intermedio 97+342 quedó invalidado: contaminado por mi trabajo concurrente sobre la misma DB). TODOS los 57 triados y arreglados en commit e647fdec (pusheado): eran candados pinneando contratos ANTERIORES a las remediaciones (SSO 0115, marketplace materializa+manifests, guard SSRF con DNS real+URL pinneada, AUD16-02 honesto, memorizer AUD16-17 fracasos+routing ADR 0071, providers per-call client, PROY2 approve gated 403, guard ancestro auto-PR, ck 0101 'done', self-dependency 422, fixture redis córtex) — CERO regresiones de producto. ~10 restantes = orden-dependientes (pasan aislados: rbac, run_tools_by_stack, tracing, seed_skills) — deuda conocida de aislamiento de la suite, sin arreglar (estado global entre ficheros). Gotcha pre-commit reafirmado: black del hook reformatea → el commit NO entra silenciosamente; patrón robusto = commit (falla+autofix) → git add → commit de nuevo. Los tests humanos de los planes de prueba (pending_human_validation) son del operador.
