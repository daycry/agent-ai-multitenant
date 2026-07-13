export const meta = {
  name: "auditoria-runs-sistema",
  description:
    "Auditoría read-only de ejecuciones, memorias, workers y review pipeline sobre la BD viva",
  phases: [
    { title: "Analizar", detail: "6 analistas forenses en paralelo" },
    { title: "Verificar", detail: "verificación adversarial de hallazgos critical/high/medium" },
    { title: "Criticar", detail: "crítico de completitud" },
  ],
};

const CTX = `CONTEXTO GENERAL
Plataforma de IA agéntica multi-tenant (Python/FastAPI/Celery/LangGraph + Postgres + Redis + MinIO), repo en C:/laragon/python/agent-ai-multitenant, rama plan/runs-visor-trabajo. Entre el 26 y el 30 de junio se desplegaron muchos fixes al pipeline de ejecución de agentes (ADR 0087 review autoritativo + submit_result/finish_status, ADR 0089-D4 backstop read-churn, ADR 0090 convergencia runs, ADR 0091 asignación por rol, ADR 0092 allowlist tools SDK, ADR 0093 stack_exec, ADR 0094 registry-proxy/egress de runtime-templates, ADR 0095 reviewer con worktree read-only + safeguards is_review). El stack Docker Compose está VIVO (contenedores agentic-platform-*) y AHORA MISMO hay 2 ejecuciones en estado running y 2 contenedores agent-runtime activos (goofy_merkle, eloquent_kare).

ACCESO A LA BD (solo lectura), usa la herramienta Bash:
docker exec agentic-platform-postgres-1 psql -U postgres -d agentic_platform -c "SELECT ..."
Tablas clave: executions, tasks, plans, task_dependencies, task_audit_events, review_sessions, memory_entries, agents, teams, plan_comments, guardrail_events, llm_providers, audit_log.
executions tiene: status (done/needs_human_review/aborted/failed/running), abort_code, finish_status, iterations, total_tokens, tool_call_count, model_call_count, memorize_skip_reason, celery_task_id, cancel_requested_at, steps_log (jsonb array — PUEDE SER GRANDE: usa jsonb_array_length, extrae elementos concretos con steps_log->N o jsonb_array_elements(...) LIMIT, no lo vuelques entero).

RESTRICCIONES DURAS (sistema vivo + modo plan del operador):
- SOLO LECTURA: SELECT en psql, docker logs, docker inspect, leer ficheros del repo (Read/Grep/Glob), redis-cli solo comandos de lectura (LLEN/LRANGE/KEYS/SCAN/HGETALL/GET/TYPE/XLEN/XRANGE).
- PROHIBIDO: INSERT/UPDATE/DELETE/DDL, docker restart/stop/kill/rm/exec que escriba, escribir o editar CUALQUIER fichero, git checkout/switch/commit, comandos de escritura en Redis.
- No interfieras con las 2 ejecuciones en curso.

INVENTARIO YA CONOCIDO (51 executions, 2026-06-29 a 2026-07-02, tenant Demo, proyecto CI4): 27 done, 11 needs_human_review, 8 aborted, 3 failed, 2 running. abort_codes vistos: max_iterations_exceeded x6 (todos <= 06-30 11:14, siempre iterations=50), repetitive_loop_detected x5, research_exhausted x4, max_review_retries_exhausted x3, commit_failed x2 (¡con status=done!), runtime_stuck_no_progress x1, superseded x2. memory_entries: 74 (17 team_shared, 57 project_shared). review_sessions: 0 filas. Ningún execution tiene finish_status poblado. Tasks: 8 done, 2 in_progress, 4 backlog; 1 plan in_progress.

FORMATO DE SALIDA: hallazgos concretos con evidencia verificable (IDs de execution completos, SQL exacto usado, líneas de log literales, referencias fichero:línea del código). Cada hallazgo lleva status: fixed_verified (fix desplegado y comprobado funcionando con evidencia), fix_regressed (fix desplegado pero la evidencia muestra que NO funciona), broken (roto sin fix conocido), risk (riesgo latente que puede dar problemas), unclear. Tu texto final NO lo ve el usuario: es dato para síntesis — sé denso y factual.

`;

const FINDINGS = {
  type: "object",
  required: ["summary", "findings"],
  properties: {
    summary: {
      type: "string",
      description: "Resumen denso de 3-6 frases del estado de tu dimensión",
    },
    findings: {
      type: "array",
      items: {
        type: "object",
        required: ["title", "severity", "status", "evidence", "detail"],
        properties: {
          title: { type: "string" },
          severity: { type: "string", enum: ["critical", "high", "medium", "low", "info"] },
          status: {
            type: "string",
            enum: ["fixed_verified", "fix_regressed", "broken", "risk", "unclear"],
          },
          evidence: {
            type: "string",
            description: "SQL/logs/refs exactos que sustentan el hallazgo",
          },
          detail: { type: "string" },
          suggested_action: { type: "string" },
        },
      },
    },
  },
};

const VERDICT = {
  type: "object",
  required: ["isReal", "confidence", "note"],
  properties: {
    isReal: {
      type: "boolean",
      description: "true si el hallazgo se sostiene tras intentar refutarlo",
    },
    confidence: { type: "string", enum: ["high", "medium", "low"] },
    note: { type: "string", description: "qué comprobaste, con SQL/refs" },
    corrected: {
      type: "string",
      description: "si se sostiene solo parcialmente, la versión corregida del hallazgo",
    },
  },
};

const CRITIC = {
  type: "object",
  required: ["missing", "overall"],
  properties: {
    missing: {
      type: "array",
      items: {
        type: "object",
        required: ["area", "why"],
        properties: {
          area: { type: "string" },
          why: { type: "string" },
          quick_check: { type: "string" },
        },
      },
    },
    overall: { type: "string" },
  },
};

const DIMENSIONS = [
  {
    key: "runs-forensics",
    prompt: `TU MISIÓN: forense de ejecuciones anómalas. Analiza steps_log y campos de estas ejecuciones y determina causa raíz de cada patrón:
1) status=done PERO abort_code=commit_failed: 019f1853-97c5-7302-8611-b533fb597ad1 y 019f1856-7c4d-72a1-be43-f52ccf3de0eb — ¿el trabajo se comiteó o se perdió? ¿estado contradictorio? Localiza en apps/workers el código que setea commit_failed y decide si done+commit_failed es coherente.
2) total_tokens=0 con 20+ iteraciones y 20+ tool_calls: 019f1872-42b6-7907-8f18-7e495c1c1628, 019f18c8-911f-704e-8330-ae1493d5f336, 019f1dc4-c372-7f7f-b69c-8874ff21bbc4, 019f1dcf-922e-7701-9c22-b19026161b82 — ¿contabilidad de tokens/usage rota para algún provider? Mira llm_providers (kind, model) y qué provider usó cada run (steps_log o agents). ¿Afecta a coste (total_cost_usd) y presupuestos?
3) runtime_stuck_no_progress: 019f1d60-3104-79cf-95c2-1e38da0ca7d6 — 0 iteraciones pero duró de 11:11 a 12:59 (1h48m). ¿Qué pasó y por qué el backstop tardó tanto? Busca el watchdog/umbral en apps/workers.
4) superseded: 019f1dfa-4e6c-7b2f-a9e6-103648352a1d y 019f1dfa-4ebc-73de-98c8-332333d6a3a4 — creadas 07-01 13:59, completadas 07-02 07:33 (toda la noche en running). ¿Por qué colgadas? ¿El supersede ocurre al reiniciar el stack (busca el código de recovery/startup en workers u orchestrator)? ¿Se relanzaron correctamente (las 2 running de hoy)?
5) El primer failed sin abort_code con 0 iter: 019f1399-373b-7310-993c-994744030154 (06-29 13:37) — ¿qué falló?
6) finish_status NUNCA poblado en 51 executions pese al ADR 0087 (submit_result estructurado). Grep en el repo dónde se escribe executions.finish_status y por qué no llega a la BD. OJO: coordina con otro analista que mira lo mismo desde el lado review — tú céntrate en el data-flow worker→BD.
7) Barrido SQL libre de otras anomalías transversales: duraciones (completed_at-started_at) absurdas, steps_log vacíos en runs done, total_cost_usd=0 con tokens>0 o viceversa, cancel_requested_at poblados, celery_task_id NULL, started_at NULL.`,
  },
  {
    key: "tareas-no-convergen",
    prompt: `TU MISIÓN: las dos tareas que siguen sin converger tras todos los fixes.
'Auditar dependencias y fijar versiones' acumula: repetitive_loop_detected (019f1872-42b6, 019f18a6-e56d, 019f18c8-911f), research_exhausted (019f1dc4-c372, 019f1dcf-922e), runtime_stuck (019f1d60-3104), superseded (019f1dfa-4ebc), y una running AHORA (019f21be-e5c0) — pese a que hubo un done (019f1870-bd22) el 06-30 12:11.
'Aplicar cabeceras de seguridad y restricciones' similar: research_exhausted (019f1dcf-0b30), superseded (019f1dfa-4e6c), running ahora (019f21be-e4ec).
Preguntas:
a) Mira \\d tasks y lee las filas completas de esas 2 tasks (title, description, acceptance_criteria, status, assigned role/agent, human_validation_required, etc.). ¿Los acceptance_criteria son ambiguos, imposibles de verificar dentro del sandbox, o exigen red/tooling que el runtime no tiene?
b) Extrae de steps_log de 2-3 de esas ejecuciones la secuencia de tool calls (nombre de tool + resumen args + resultado truncado): ¿en qué se atascan exactamente? ¿falla composer/stack_exec (ADR 0093)? ¿egress denegado (ADR 0094)? ¿el mismo tool repetido en bucle?
c) ¿Por qué tras el done de 019f1870-bd22 la MISMA task siguió generando ejecuciones? ¿Son runs de review (join executions.agent_id -> agents para ver role/nombre)? Distingue en la línea temporal completa de la task cuáles son work-runs y cuáles review-runs.
d) Reconstruye la cronología completa de la task 'Auditar dependencias' (todas sus executions + task_audit_events de esa task_id + plan_comments si los hay) y di EXACTAMENTE qué la mantiene en in_progress y si el sistema va a poder cerrarla solo o necesita intervención (y cuál).`,
  },
  {
    key: "memoria",
    prompt: `TU MISIÓN: el sistema de memoria (memorizer + memory_entries). Hay 74 memory_entries (17 team_shared, 57 project_shared, 0 private/global).
a) Mira \\d memory_entries. ¿Los runs done tienen memoria indexada (join executions done vs memory_entries.source_execution_id)? ¿Qué % de dones se memorizó?
b) Muestrea ~10 entradas (contenido truncado a 300 chars): ¿el contenido es útil (lecciones, decisiones) o ruido/duplicados? ¿Hay dedup o crece sin control (busca contenidos casi idénticos con similitud básica: mismos primeros 80 chars)?
c) ¿Embeddings generados (columna embedding IS NOT NULL, dimensión)? Si hay NULL, ¿por qué?
d) memorize_skip_reason: valores y cuentas. 'not_done' en aborted/failed es esperado. Pero 019f1dcd-ac42-7058-b570-711da0bfc183 acabó done con memorize_skip_reason='llm_empty': ¿el LLM del memorizer devolvió vacío? Localiza el código del memorizer (apps/memorizer o dentro de apps/workers) y los valores posibles de skip_reason; mira docker logs del contenedor que corre el memorizer (probablemente workers-1; filtra con --since y grep memoriz) buscando errores.
e) Scopes: CLAUDE.md define privada(agente)/team_shared/project_shared/global(org). ¿Que no haya NINGUNA privada ni global es diseño o bug? Mira el código que decide el scope al memorizar.
f) ¿Las memorias se INYECTAN en prompts de runs posteriores (busca en apps/workers el retrieval: recall/search de memorias al montar el prompt)? ¿Hay evidencia en steps_log de algún run reciente de que recibió memorias?`,
  },
  {
    key: "workers-infra",
    prompt: `TU MISIÓN: workers e infraestructura de ejecución EN VIVO (solo lectura, no interfieras).
1) Las 2 ejecuciones running AHORA: 019f21be-e4ec-788c-a267-5567d41a96ce ('Aplicar cabeceras') y 019f21be-e5c0-7a45-85ea-29a2e6ce3772 ('Auditar dependencias'), creadas 07:33, runtimes goofy_merkle y eloquent_kare. ¿Progresan? (docker logs de ambos runtimes; vuelve a consultar iterations/tool_call_count en BD un par de veces separadas unos minutos usando tus otras consultas entre medias). ¿O repiten el patrón de sus predecesoras?
2) docker logs agentic-platform-workers-1 --since 24h (acota con --tail si es enorme): ERROR/Traceback/retry recurrentes. Clasifícalos.
3) docker logs agentic-platform-orchestrator-1 --since 24h: ¿dispatch sano? ¿errores de asignación por rol (ADR 0091)?
4) Redis/Celery: docker exec agentic-platform-redis-1 redis-cli con comandos SOLO lectura (KEYS celery*, LLEN celery, LLEN de colas default/heavy/test/review si existen, KEYS unacked*, HLEN unacked). ¿Backlog o mensajes zombis?
5) La noche 07-01→07-02: 2 runs quedaron running toda la noche y fueron superseded a las 07:33 cuando el stack arrancó (contenedores llevan ~5-10 min up). docker inspect -f '{{.State.StartedAt}}' de workers-1, postgres-1, orchestrator-1: ¿cuándo arrancó el stack hoy y cuánto estuvo caído? ¿Existe watchdog que debería haber abortado esos runs ANTES del apagado (busca en apps/workers: watchdog, stuck, heartbeat, supersede, runtime_stuck_no_progress) y cuáles son sus umbrales? ¿El apagado del host (es un PC de desarrollo Windows) deja siempre runs zombis, y el recovery al arrancar es correcto (re-encola) o pierde trabajo?
6) egress-proxy (agentic-egress-proxy) y registry-proxy (agentic-registry-proxy): docker logs buscando denies/errores relevantes de los últimos runs. ¿stack_exec/composer funcionó en runs recientes?
7) Recursos: docker stats --no-stream una vez: ¿algo saturado (memoria/CPU) que explique cuelgues?`,
  },
  {
    key: "review-pipeline",
    prompt: `TU MISIÓN: pipeline de review (ADR 0087 review autoritativo 3-estados + submit_result; ADR 0095 reviewer con worktree read-only + safeguards is_review).
1) review_sessions tiene 0 filas. Mira \\d review_sessions y grep 'review_sessions' en el repo: ¿qué código escribe ahí y por qué está vacía pese a que ha habido ciclos work→review? ¿Tabla muerta de una feature anterior (¿el review autoritativo ADR 0087 la sustituyó sin borrarla?) o fix_regressed?
2) ¿Cómo se materializan los runs de review? (¿executions con agent_id de agente con role reviewer? join executions->agents y clasifica las 51: cuántas son reviews). ¿El run de review 019f184f-77ff (done, 13 iter, 06-30 11:34) fue el e2e del ADR 0095?
3) max_review_retries_exhausted (019f13bb-d74e, 019f1862-b0c9, 019f1866-1bcb): reconstruye el ciclo work→review→retry de una de ellas: ¿el reviewer rechaza con feedback accionable? ¿ese feedback llega al prompt del siguiente intento (busca en apps/workers cómo se inyecta)? ¿cuántos retries permite, dónde se cuenta, y qué pasa al agotarse (needs_human_review)?
4) Las 11 executions needs_human_review: ¿se reflejan en la task y son visibles/accionables en el admin-panel o web-app (busca endpoints/páginas que listen needs_human_review en apps/api-server y apps/admin-panel o web-app)? ¿El humano puede aprobar/rechazar/relanzar desde ahí, o quedan en limbo? ¿Las tasks asociadas quedan bloqueadas?
5) finish_status (columna de executions, migración 0100, ADR 0087): grep dónde se escribe (runtime emite submit_result → worker persiste finish_status). Está NULL en las 51 executions. ¿El código del worker nunca lo persiste, el runtime nunca lo emite, o solo aplica a un camino que no se ha dado? Da el fichero:línea del bug si lo encuentras.
6) ¿El fix del reviewer-ciego (ADR 0095) se observa funcionando en reviews recientes (steps_log con read_file exitosos sobre el worktree)? Compara un review de antes (06-29) y uno de después (07-01).`,
  },
  {
    key: "code-xref",
    prompt: `TU MISIÓN: inventario de fixes recientes y su estado, y lista de lo que queda pendiente. NO consultes la BD salvo comprobaciones puntuales; tu fuente es el repo.
1) Lee docs/05-architecture-decisions/ — localiza los ADR 0087 a 0095 (y cualquier ADR posterior o en estado proposed) y resume: qué problema ataca cada uno, estado (accepted/proposed/implemented).
2) Lee docs/roadmap/ — localiza la auditoría del pipeline de ejecución (clusters C1..C8, plan P0→P3) y cualquier plan de remediación: qué items se marcaron hechos y cuáles quedaron pendientes o GATED (p.ej. D1/D2 gated, dag_03/ADR 0063 diferido, B0.2/ADR 0067).
3) git log --oneline -80 en C:/laragon/python/agent-ai-multitenant: mapea commits recientes a los ADR/fixes.
4) Construye la tabla: fix → problema → comportamiento observable esperado en BD/logs → correlación con la evidencia conocida (te la doy abajo) → veredicto (fixed_verified / fix_regressed / no observable aún).
EVIDENCIA OBSERVADA CONOCIDA para correlar: max_iterations_exceeded (iterations=50) desapareció tras 06-30 11:14; research_exhausted apareció 06-30 y sigue el 07-01 (¿backstop nuevo funcionando o síntoma de no-convergencia?); repetitive_loop_detected sigue apareciendo el 07-01 11:06; el 07-01 hubo varios done rápidos (2-13 iter); commit_failed x2 el 06-30 con status done; finish_status jamás poblado; review_sessions vacía; 2 runs pasaron la noche running y fueron superseded al arrancar el stack hoy 07:33.
5) Lista explícita de 'qué queda por corregir' según los propios docs del repo (pendientes declarados, ADR proposed sin implementar, TODOs/FIXMEs en apps/workers relacionados con convergencia/review/memoria si los hay: grep TODO|FIXME|XXX en apps/workers y apps/orchestrator, solo los relevantes).`,
  },
];

phase("Analizar");
const results = await pipeline(
  DIMENSIONS,
  (d) => agent(CTX + d.prompt, { label: "analiza:" + d.key, phase: "Analizar", schema: FINDINGS }),
  (res, d) => {
    if (!res) return null;
    const toVerify = res.findings.filter((f) =>
      ["critical", "high", "medium"].includes(f.severity),
    );
    const rest = res.findings
      .filter((f) => !["critical", "high", "medium"].includes(f.severity))
      .map((f) => ({ ...f, verdict: null }));
    return parallel(
      toVerify.map(
        (f) => () =>
          agent(
            CTX +
              "Eres un verificador ADVERSARIAL y ESCÉPTICO. Tu trabajo es intentar REFUTAR el hallazgo de abajo con evidencia primaria (re-ejecuta SQL contra la BD, relee logs, relee el código en las refs citadas). NO confíes en el texto del hallazgo: re-deriva su evidencia desde cero. Si la evidencia citada no se sostiene, o la causa raíz propuesta tiene una explicación alternativa mejor, decláralo isReal=false o da la versión corregida. Sé rápido y quirúrgico: verifica SOLO este hallazgo.\n\nHALLAZGO (dimensión " +
              d.key +
              "):\n" +
              JSON.stringify(f, null, 2),
            { label: "verifica:" + f.title.slice(0, 40), phase: "Verificar", schema: VERDICT },
          ).then((v) => ({ ...f, verdict: v })),
      ),
    ).then((vs) => ({
      key: d.key,
      summary: res.summary,
      findings: [...vs.filter(Boolean), ...rest],
    }));
  },
);

const ok = results.filter(Boolean);
log("Dimensiones completadas: " + ok.map((r) => r.key + "(" + r.findings.length + ")").join(", "));

phase("Criticar");
const digest = ok.map((r) => ({
  key: r.key,
  summary: r.summary,
  titles: r.findings.map((f) => "[" + f.severity + "/" + f.status + "] " + f.title),
}));
const critic = await agent(
  CTX +
    "Eres el CRÍTICO DE COMPLETITUD de una auditoría del sistema de ejecuciones. Abajo tienes el resumen de lo que 6 analistas ya cubrieron. Tu trabajo: (1) detectar qué área del sistema de runs/memoria/workers/review quedó SIN mirar o con evidencia floja (p.ej. guardrail_events, approval_requests, plan_comments→prompt, costes/presupuestos, notificaciones de needs_human_review, RLS/tenant en las tablas nuevas, backups de /data); (2) para cada hueco, si puedes, haz TÚ una comprobación rápida read-only (1-2 SQL o un grep) y reporta el resultado en quick_check. No repitas lo ya cubierto.\n\nCUBIERTO:\n" +
    JSON.stringify(digest, null, 2),
  { label: "critico-completitud", phase: "Criticar", schema: CRITIC },
);

return { dimensions: ok, critic };
