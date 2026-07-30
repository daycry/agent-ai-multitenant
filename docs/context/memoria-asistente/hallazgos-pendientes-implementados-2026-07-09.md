---
name: hallazgos-pendientes-implementados-2026-07-09
description: "Tanda 2026-07-09: implementados los 9 hallazgos-pendientes-2026-07-07 (#2/#6/#7/#8/#10a/#10c/#10e + luego «lo diferido»: #8 e2e ciclo real + #9 detalle-plan 1703→161)"
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

Tanda «analiza e implementa los hallazgos» (rama `plan/runs-visor-trabajo`, 2026-07-09,
Opus tras límite Fable). Precedido de un **workflow de mapeo** (8 lectores paralelos →
mapa file:line+riesgos por hallazgo; las 4 verificaciones adversariales fallaron por
límite de Fable, verificadas luego vía TDD en Opus). Todo TDD + commit atómico:

- **#2** (`c55597a`) — planes `blocked` varados: 3 vías huérfanas (`delete_task`,
  `update_task` deps-only, `create_free_task`) ahora llaman `reactivate_plan_if_unstuck`
  (no-op si no está blocked) + RED del reconciler `_reconcile_unblocked_plans` (espejo de
  `_reconcile_complete_plans` con `transition_from_blocked` + guard atómico
  `WHERE status='blocked'`, corre ANTES de completed). Sin ping-pong (negación exacta). El
  fix de 50f4e5d ya cubría human-action + PUT Kanban; esto cierra los huecos + la red async.
- **#10e** (`944085a`) — schema-gap córtex: `LLMAssistantModel.decide` usaba SIEMPRE
  `tool_schemas` del asistente → toda `complete()` del córtex iba con `tools=None`. Fix:
  campo `schema_fn` inyectable (default `tool_schemas`, preservado por `dataclasses.replace`);
  `build_cortex_model` pasa `cortex_tool_schemas`. Verificado en vivo: web_search recibe
  `{query,limit}` (firma real) en vez de `{topn,source}` descartados.
- **#10c** (`5fd17cc1`) — F32/truncado protege también a claude_sdk: nuevo
  `CompletionResponse.stop_reason` (shared-llm/types) cosechado del último AssistantMessage
  (`_harvest_stop_reason` en claude_agent); `_completion_signals` mapea `max_tokens`/`length`
  → truncated; guard extendido a la rama prose-FINISH de `_decision_from` (turno truncado →
  noop ACT reintento, fail-closed). TDD en shared-llm + agent-runtime.
- **#6** (`9f95a9fc`) — PASO 0: los tests del agent-runtime (viven fuera de tests/unit) ahora
  corren en CI (nuevo step en test-unit + meta-test en tests/docs/test_ci_workflows). PASO 1:
  `written_files` tipada en `ReviewState(AgentState, total=False)`; el test-contrato deriva las
  claves inyectadas de la jerarquía de TypedDicts. Cascada de firmas a ReviewState = diferido.
- **#8** (`e3954baa`) — PARCIAL: unit puro de `detect_outliers` (medido 31.2%→31.6%), ratchet
  `--cov-fail-under` 30→31 + meta-test baseline 30→31. **Falta el e2e Docker-real del ciclo**
  (diseño en el mapa: `tests/e2e/test_autonomous_cycle.py`, seed→dispatch→run scripted→review→
  done, marker `@pytest.mark.e2e`).
- **#10a** (`0642211c`) — `worktree_coordinates(data_root,tenant,project,plan_id,plan_slug) →
(BareRepoLayout, plan_branch)` en plan_git.py: fuente única en 6 sitios (provisión, resolución,
  commit/push, review, sync explícito, back-fill). NO normaliza el path (sin resolve/realpath)
  → preserva identidad container==daemon-side DooD. Golden test que clava strings byte-a-byte.
- **#7** (`46655724`) — ADR 0108 `proposed`: 3 opciones de fusión de canales de veredicto
  (A todo-tool / B todo-tag / C status quo documentado). Recomienda C. **Decisión del operador.**
- **#9** (frontend por partes) — **DEFERIDO** a tramo dedicado (mecánico sobre código que
  funciona, riesgo big-bang). Plan del mapa en la cabecera de hallazgos-pendientes-2026-07-07:
  extraer `plan-spec-types.ts` + 8-9 `*-section.tsx` del detalle de plan (1703 lín) +
  model-prices/mcp-servers/knowledge-bases; caracterizar jsdom → extraer → verde. Ficheros
  extraídos NUNCA `page.tsx`/`layout.tsx`/`route.ts` dentro de app/\*\*, y con `'use client'`.

**2ª parte, tanda «implementa lo diferido» (2026-07-09):**

- **#8 e2e ciclo autónomo — HECHO** (`2a0d5496`): `tests/integration/test_autonomous_cycle.py`
  corre el ciclo COMPLETO sobre Docker real (implementador→in_review→reviewer approve→done, y
  reject→backlog) con modelos scripted (sin LLM). VERDE local (2 tests, ~32 s), NO skippeado.
  Extiende test_e2e_smoke (que llega solo al implementador). Va en integration/ (reutiliza su
  harness: PG efímero + agent-runtime:v1 + run_execution), NO en e2e/ (eso es el install e2e).
  GOTCHAs: la Task NO tiene `agent_id` (usa `assigned_agent_id`, autoritativo sobre la política);
  el reviewer scripted cierra con `<verdict>approve|reject</verdict>` (token de review_contract.py).
- **#9 refactor frontend — detalle de plan HECHO** (`415a2578`, `618e6844`): el peor hotspot
  (1703 líneas) modularizado en 3 ficheros colocados junto a la ruta — `plan-spec-types.ts`
  (interfaces + STATUS\_\* + formatCostRange), `plan-spec-sections.tsx` (8 secciones puras),
  `plan-interactive-sections.tsx` (7 secciones con hooks) — y `page.tsx` = **161 líneas** de
  composición. Verbatim, testids intactos; tsc 0 + vitest 201/201 + `next build` OK. RESTA como
  tramo: model-prices (1311) / mcp-servers (1105) / knowledge-bases (1042) — SIN red de tests, así
  que caracterización jsdom ANTES de extraer; patrón probado. Ficheros extraídos NUNCA
  page.tsx/layout.tsx/route.ts dentro de app/\*\*, y 'use client' si usan hooks/componentes cliente.

**AUDITORÍA 2026-07-10** (5 revisores + suites en local, informe en
`docs/roadmap/auditoria-hallazgos-implementados-2026-07-10.md`): implementación
sustancialmente correcta — TODAS las suites verificadas en verde (unit 2143, runtime+llm 371,
e2e ciclo 2/2 con api-server arriba, tsc+vitest 201/201+build), #9 verbatim 100% (85 testids).
Destapó 1 CRÍTICO + 7 importantes → **REMEDIADOS el mismo día** (TDD+commit atómico, §5 del
informe): C-1 ping-pong reconciler↔C8-F40 (`0456c09c`, red salta snapshots todo-terminal;
también arregló test_reconcile_pipeline_state roto por c55597a2), I-1 4ª vía
`create_task(plan_id)` (`0527bf3c`), I-2/I-3 remate #10a `worktree_layout`+golden literal+
contrato fuente-única (`f203d279`), I-4 verdict-PROSA truncado→inconcluso (`dcdf3e0f`),
I-5 reintento F32 dirigido — noop devuelve `reason` (`ffe4ebbc`), I-6 pins schema_fn+exclusión
mutua web nativa/host (`56c0a6b4`), I-7 e2e consume evento REAL de events:tasks (`0df2796b`).
Verificado post-remediación: unit 2149+ratchet 31, runtime/llm/docs 506, integración 13, e2e 2/2.
**MENORES también HECHOS (2ª tanda misma sesión, §5 del informe)**: M-2/M-3 PUT plan_id
re-evalúa ambos extremos+FOR UPDATE (582bbcb1), M-1 evento `plan_unblocked` registro+templates+
emisores router/reconciler (a820a3f1; OJO el stack dev NO despliega notification-dispatcher —
enqueue sin consumidor), M-4 stop_reason desde finish_reason en HTTP (909e672b), M-5
ReviewState anotado en firmas — mypy verifica (c38336db), M-6 `_pipeline_helpers.py` compartido
smoke/ciclo+asserts reject+timeout(300) (62ccc86d), M-7 export muerto (1ab58363). Abiertos
deliberados: partir plan-interactive-sections (tramo #9), test transporte claude_sdk córtex.
**Deploy HECHO 2026-07-10 ×2 tandas**: rebuild api-server:manuals+orchestrator:manuals+
workers:ci (BASE_IMAGE=manuals, WITH_CLAUDE=1)+agent-runtime:v1; stack
`docker compose -p agentic-platform -f yml+dev+manuals+monitoring(+dev)+windows up -d`,
todo healthy, símbolos verificados DENTRO de contenedores vivos, e2e 3/3 post-deploy.
admin-panel NO rebuildeado por M-7 (solo tipos).
OJO: e2e local exige api-server healthy (API interna), no solo PG+Redis; page.tsx=134 líneas
(no 161); vista UNA VEZ una flakiness de orden en installer/test_step_executor (verde aislado
y en re-runs).

**TRAMO #9 CERRADO (2026-07-10, tras la remediación)**: los 3 hotspots restantes
modularizados con el patrón probado (caracterizar jsdom → split MECÁNICO verbatim por rangos
de línea vía script python → tsc/vitest verde → commit; prettier reformatea al comitear → el
commit falla la 1ª vez y se re-añade+re-comitea, patrón conocido): model-prices 1311→517
(types+dialogs, 5 tests), mcp-servers 1105→180 (types+sections, 4 tests), knowledge-bases
1042→208 (types+sections, 4 tests). Testids 66/45/35 intactos (verificado vs git show),
vitest 214/214, next build OK. El split por rangos + exports calculados por uso (grep en los
otros bloques) + imports guiados por tsc es MÁS fiable que reescribir a mano. admin-panel
image pendiente de rebuild para servir esto (solo frontend).

**Why:** el operador pidió «analiza e implementa los hallazgos» y luego «implementa lo diferido». Ver [[voz-cortex-git-fixes-2026-07-09]]
(el schema-gap #10e nació ahí como follow-up). **Deploy**: rebuild api-server (WITH_CLAUDE=1)

- workers (BASE_IMAGE=api-server:manuals) + agent-runtime:v1 (context=raíz, WITH_CLAUDE=1;
  recoge shared-llm de #10c). El hook pre-commit hace rollback si `.claude/settings.local.json`
  está sin-stage (patrón conocido: `git reset HEAD` ese fichero antes de re-add) y reformatea
  con ruff/prettier (re-add tras el rollback). **TRAMOS RESTANTES**: #9 frontend, #8 e2e ciclo,
  #1 carrera lock (P1), #3 botón desbloquear, #4 app-preview UI, #5 pcov (varios ya de tandas
  previas — ver el doc).
