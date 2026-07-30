---
name: voz-cortex-git-fixes-2026-07-09
description: "Tanda voz+córtex+git 2026-07-09: keepalive, respuestas vacías del córtex, búsqueda web, resiliencia de tools, UI de sync git — todo desplegado+verificado"
metadata:
  node_type: memory
  type: project
  originSessionId: 46819ab5-f853-4ca2-aea8-a56ed20f06f1
---

Tanda 2026-07-09 (rama `plan/runs-visor-trabajo`), TDD + commit atómico, TODO desplegado en dev:

**VOZ (el «Llamada finalizada: keepalive ping timeout»)** — `41417d7`: causa = un turno
STT→LLM→TTS son 40-90s de silencio y el keepalive por defecto de uvicorn/websockets (ping
20s/timeout 20s) cerraba con 1011 si el pong del navegador se retrasaba (pestaña 2º plano /
proxy de Docker Desktop). Fix: Dockerfile del api-server con `--ws-ping-interval 60
--ws-ping-timeout 120`; `VoiceSession.handle_turn(on_transcript=...)` que emite `transcript`+
`thinking` TRAS el STT y ANTES del cerebro (feedback inmediato + tráfico intermedio); el
front pinta «Pensando» con `thinking`.

**CÓRTEX «no responde / no tiene internet»** — 5 causas encadenadas (`0d678b5`, `08b5294`):
(1) answer VACÍO: `build_cortex_model` no fijaba max_tokens → default 1024; gpt-oss:120b
(ollama-cloud, effort high) gasta el presupuesto en el canal `reasoning` → `content` vacío.
Fix: `CORTEX_MAX_TOKENS=16384`. (2) el guard SSRF bloqueaba su propio searxng (IP privada
docker) → flag `allow_internal` en `assert_safe_url` para backends de confianza. (3) searxng
se enrutaba por el egress-proxy (para internet) → `_build_direct_client` (searxng es interno).
(4) una tool que fallaba (web_fetch, egress deny-by-default 403) TUMBABA el turno →
`_node_run_tools` (grafo compartido asistente+córtex) captura y devuelve el error al modelo.
(5) gpt-oss a veces alucinaba «no tengo permiso» sin buscar → prompt imperativo (voz+chat):
«SÍ tienes acceso, NUNCA digas que no, LLAMA a web_search antes de responder». Además:
searxng estaba PARADO (se levantó; estaba en el compose sin profile) + compose manuals con
`API_SERVER_CORTEX_EGRESS_PROXY_URL/_SEARXNG_URL` (el default del setting es localhost,
inservible en el contenedor). `cortex.web_enabled` YA estaba en True.
VERIFICADO e2e: turno de voz del córtex responde «según Meteored, Barcelona soleado ~28°C
(Fuente: Meteored)» desde web_search, con audio, sin caerse (103s).
**ANSWER VACÍO EN PREGUNTAS AMPLIAS — RESUELTO** (`ffd38f4`+`3c1094c`): «¿últimas noticias de
tecnología hoy?» devolvía answer="". Root-cause (instrumentado): gpt-oss:120b NUNCA
comprometía content — tras el web_search seguía emitiendo tool_calls NATIVAS de su tool
`browser` de harmony (`web_fetch {cursor,id}` = «abrir» un resultado) con content vacío ronda
tras ronda. Bajar reasoning_effort NO ayuda; formatear resultados NO ayuda; prohibir «llamar
a herramientas» a secas NO basta (el modelo quiere NAVEGAR, no llamar a nuestras tools). FIX:
el nodo `_node_finish` (grafo compartido) re-pregunta SIN tools con `FINISH_NUDGE` inyectado
como instrucción final (nuevo campo `AssistantState.final_instruction`, renderizado por
`LLMAssistantModel._build_messages` tras los tool_results, por recencia) que PROHÍBE
explícitamente abrir páginas/navegar/scroll/más búsquedas → gpt-oss redacta desde los snippets
(verificado e2e 3/3, en español). NO fija idioma (lo hace el system prompt). SECUNDARIO
anotado en hallazgos-pendientes-2026-07-07 §10 (no corregido): `LLMAssistantModel.decide` usa
`tool_schemas` del asistente → las tools del córtex nunca van como schema (`tools=None` en
todas las complete(); web_search cuela por nativa).

**2ª tanda misma sesión (commits `9c6bea2` + `9a518e0`, desplegados+verificados e2e con
modelo real):** (A) REASONING-LEAK — el córtex «respondía» «We need to use web_search» (en
inglés): el `content` de un turno que PIDE tool es preámbulo de gpt-oss, no respuesta; si el
turno final salía vacío se colaba como answer. Fix en `assistant/graph.py::_node_decide`:
solo guarda `last_content` de un turno que NO pide tools; answer de turno-tool vacío = ""
(mejor vacío que el pensamiento crudo). Grafo compartido → arregla chat Y voz. (B) IDIOMA —
con voz española contestaba en inglés (gpt-oss razona en inglés y arrastraba el idioma). Fix:
el idioma de la RESPUESTA se ata a la VOZ Kokoro elegida por la 1ª letra del prefijo (a/b=EN
US/UK, e=ES; default ES). `voice_language`/`voice_language_instruction` en
`routers/assistant_voice.py` (instrucción imperativa «responde SIEMPRE en español, NUNCA en
inglés»); `_cortex_voice_base_prompt` acepta `language_instruction`; se threadea desde
`cortex_voice._respond(state.voice)` y `assistant_voice._respond(voice)`. VERIFICADO con el
modelo real: voz `ef_dora` + «Hello how are you» → «¡Hola! Todo bien, gracias»; voz
`am_michael` + pregunta en español → inglés; turno completo con web_search real → «Hoy en
Barcelona hace sol… Fuente: Meteored España» (español, con fuente, sin leak).

**GIT** — base local no nacía del remoto (`b9cd821`) + UI (`0d678b5`): (a)
`BareRepoManager.align_default_branch` tras el clone/sync (crea/ff la rama default local
desde origin/<default>; remoto vacío o divergencia se REPORTAN, no se pisan); (b)
`PlanGitWorkflow.base_branch` = guard de ancestro antes del PR (merge-base contra la rama del
plan → skip con motivo accionable en vez del 422 crudo de GitHub); (c) `repo_clone` persiste
`repository_config.last_git_sync` {at,status,default_branch_alignment} (worker BYPASSRLS); (d)
panel: botón «Sincronizar» (faltaba) + tarjeta de última sync con AVISO de alineación.
VERIFICADO: api-ci muestra `alignment=diverged` → la UI explica su PR fallido. El repo api-ci
(daycry/test-mailchimp-agent-ai) tenía master GitHub 736e9d8b con raíz distinta al b93c005e
local sembrado → PR imposible sin reconciliar (config del proyecto demo, no bug).
**RECONCILIADO 2026-07-09 (autorizado por el operador, opción «forzar semilla como master»):**
force-push `git push --force origin refs/heads/master` en el bare
`.../projects/demo/api-ci/repos/api-ci.git` (worker DooD, path
`/var/lib/docker/volumes/agentic-platform-agent-data/_data`) → GitHub master pasa 736e9d8b→
b93c005e (dry-run primero). Auth = `build_git_auth_env("pat", provider="github", token de
Vault via `\_vault_store`+`project_git_secret_path(PID)`); PID api-ci = 019f1384-311d-7dcd-b0c7-
adbb449079fd. Tras el push, `merge-base origin/master..plan/019f1397`= b93c005e (¡comparten
historia!) → el PR del plan (c69eec84, 12+ commits, ya empujado a origin/plan) abrirá limpio.
GOTCHA: el sync manual por`docker exec`(root) falla con «dubious ownership» y persiste un`last_git_sync=error`FALSO; correcto =`clone_project_repo.delay(PID)`para que lo corra el
worker real (usuario`app`, dueño del repo) → dejó `repository_config.last_git_sync
={status:ok, default_branch_alignment:up_to_date}`. La columna es `Project.repository_config`(JSON), NO`git_config`.
**PR DEL PLAN CI4 ABIERTO (cierre del ciclo):** el plan 019f1397-afaf está `completed`(18
tareas done = 14 orig + 4 correcciones ADR 0107 aceptadas; verdict approved vía review.py),
pero`pr_url`era None y`pr_error`= el 422 «no history in common with master» (el PR se
intentó antes de reconciliar). Tras el force-push, re-encolé`open_plan_pr.delay(PID, PLAN,
title, body)` (mismo title/body que review.py:518: «Plan: <título>» / «PR automático tras la
validación humana…») → **PR #1** https://github.com/daycry/test-mailchimp-agent-ai/pull/1
(open, head=plan/019f1397→base=master, 25 commits, mergeable=True). Merge = decisión humana
(push_policy human_required). Ciclo demo validado punta a punta: crear→ejecutar→rechazo→
correcciones→aprobar→PR.

**Why:** el operador reportó el keepalive, el córtex sin internet, y dudas del clone git en
sesión interactiva; pidió arreglarlos («todo autónomo»). Ver [[adr-0107-correcciones-y-tanda-2026-07-09]]
y [[bug-asistente-voz-no-funciona]] (esta tanda lo resuelve para el córtex; el avatar+shell
de videollamada son de la tanda anterior). RECETA CRÍTICA reforzada: api-server:manuals
SIEMPRE `--build-arg WITH_CLAUDE=1` (ver [[gotcha api-server-manuals-needs-with-claude]]).
