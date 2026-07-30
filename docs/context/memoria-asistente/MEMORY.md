# Memory index

> **Esta memoria está espejada en el repo** para sobrevivir a un cambio de ordenador:
> [Memoria persistida en el repo](memoria-persistida-en-el-repo.md) — punto de entrada
> curado en `docs/context/memoria-del-asistente.md`, archivo verbatim en
> `docs/context/memoria-asistente/`. **Al añadir algo durable aquí, vuelca también allí.**

## Preferencias y órdenes permanentes

- [Idioma: castellano](language-preference-spanish.md) — responder siempre en español en este proyecto.
- [Entregables en docs/roadmap](deliverables-en-docs-roadmap.md) — auditorías/planes/diseños en docs/roadmap/, NO docs/plans; ADR en 05-architecture-decisions.
- [Prioridad: código limpio y mantenible](prioridad-codigo-limpio-mantenible.md) — TDD, módulos enfocados, refactor oportunista, sin big-bang; gated→ADR primero.
- [UX asignación de tools amigable](ux-tool-assignment-friendly.md) — el operador prioriza que categorización y asignación de tools/comandos sean intuitivas.
- [Textareas con preview Markdown](textareas-markdown-preview.md) — todos los textareas previsualizan markdown; falta persona-section + e2e.
- [Supervisión runs: autofix de plataforma](supervision-runs-autofix-plataforma.md) — fallo de run → si es de plataforma lo arreglo yo (TDD+deploy+relanzar); escalar solo decisiones humanas.
- [NO desbloquear sin verificación](no-desbloquear-sin-verificacion.md) — no relanzar/desbloquear tareas hasta que el operador dé por verificado el sistema.
- [ADR pendientes: implementar autónomo](adr-pendientes-implementar-autonomo.md) — tras cerrar las fases prod-01, analizar los ADR proposed e implementarlos eligiendo la mejor opción.
- [Córtex: implementación autónoma](cortex-implementacion-autonoma.md) — luz verde para F1→F5 en secuencia, autónomo (anula el gating por-fase).

## Trabajo reciente (2026-07)

- [Alcance real de los planes pendientes 2026-07-30](alcance-real-planes-pendientes-2026-07-30.md) — los 14 `pending_approval` son **195 días-persona** de verdad: 121 de 163 casillas son `GAP`, no casillas rancias.
- [Cierre de planes bloqueado por el PR sin mergear](bloqueo-cierre-planes-pr-sin-mergear.md) — regla dura de CLAUDE.md: sin PR #66 mergeado ningún plan puede pasar a `completed`.

- [Backlog fuera de la remediación 2026-07-26](backlog-fuera-de-remediacion-2026-07-26.md) — 3 planes `in_progress` cerrados + 3 ADR aceptados; **cero fases in_progress**. Queda el ADR 0117 y ~16 planes en `pending_approval`, que son tuyos.

- [Remediación workflow proyectos COMPLETA 2026-07-25](remediacion-workflow-proyectos-en-curso.md) — las **56 tareas cerradas** y empujadas (`3523b257`), `pending_human_validation`, **sin desplegar**; falta lo humano (tests, deploy, siembra del dataset).

- [Auditoría workflow gestión de proyectos 2026-07-25](auditoria-gestion-proyectos-2026-07-25.md) — 38 hallazgos verificados + plan de 6 olas (24 d), `pending_approval`, NADA implementado; tesis: falla el **cableado del último tramo**, no el diseño; gotcha: re-verificar refutó 4 candidatos de subagentes.
- [La Oficina = miniverso 2D en canvas 2026-07-24](oficina-miniverso-canvas-real.md) — piso 2D en `<canvas>` sobre telemetría real; desplegado+push (e749c5b6); v2 pendiente (burbujas por WS, córtex).
- [Fix: visor diff daba 500 2026-07-24](fix-code-diff-500-delegar-worker.md) — la api-server no monta agent-data; fix = delegar al worker. **Gotcha: git/data ops SIEMPRE en el worker.**
- [Fix: review no bloquea por hallazgos 2026-07-24](fix-review-task-no-bloquea-por-hallazgos.md) — `has_produced` no cuenta prosa; fix has_deliverable + SELF_REVIEW_STALEMATE. **Gotcha: el grafo vive en la imagen BASE.**
- [ADR 0130 app-preview on-demand 2026-07-24](adr-0130-app-preview-on-demand.md) — COMPLETO y DESPLEGADO; migración 0118. Gotcha: verificar rutas con curl al gateway.
- [ADR 0129 servicios+imagen runtime por proyecto 2026-07-24](adr-0129-servicios-runtime-por-proyecto.md) — fases 1 y 2 COMPLETAS y DESPLEGADAS (stack_exec/tests + review/preview con bridge per-sesión aislado + UI).
- [Sesión WS+draft+stack_exec+MCP 2026-07-23](sesion-fixes-ws-draft-stackexec-2026-07-23.md) — ADR 0128 (tools MCP por proyecto) y ADR 0127 (OAuth «Conectar») COMPLETOS + 4 skills atlassian + fix git «no history in common»; desplegado; falta que el operador pruebe OAuth en navegador.
- [Tanda 2 mejoras 2026-07-19](tanda2-mejoras-2026-07-19.md) — vigía credenciales, bandeja humano, retro planes, restore-drill, Loki; ADRs 0122-0126.
- [Tanda features 2026-07-19](tanda-features-2026-07-19.md) — MFA TOTP, Oficina v1, Replay, Standup PM, Leaderboard.
- [Prueba MCP+tools+skills 2026-07-18](prueba-mcp-tools-skills-2026-07-18.md) — e2e validado con runs reales; 3 fixes; pendiente manual PDF + push.
- [Auditoría dominio Proyecto 2026-07-17](auditoria-proyecto-integral-2026-07-17.md) — 42 hallazgos → remediación ENTERA (migración 0114, 5 imágenes, stack healthy); `pending_human_validation`; pendiente tests humanos + ADR 0117.
- [Auditoría AUD16 2026-07-16](auditoria-dirigida-2026-07-16.md) — 27 hallazgos → remediación ENTERA desplegada (migración 0113); pendiente tests humanos + gated.
- [Tanda inteligencia 2026-07-11](tanda-inteligencia-2026-07-11.md) — P0 agentes 7/7 hecho (persona, auto-RAG, planning, fracasos, brief); pendiente NOTIF, P1, CÓRTEX-1, ASISTENTE-1.
- [Investigación inteligencia de agentes 2026-07-11](investigacion-inteligencia-agentes.md) — informe entregado; plan P0/P1/P2 pendiente de aprobación.
- [Hallazgos pendientes implementados 2026-07-09](hallazgos-pendientes-implementados-2026-07-09.md) — los 9 hallazgos + auditoría 2026-07-10 remediada entera (1 crítico + 7 importantes + 7 menores) + tramo #9 de modularización + deploy ×2. **100% implementada**; único abierto: decisión A/B/C del ADR 0108.
- [Voz+córtex+git fixes 2026-07-09](voz-cortex-git-fixes-2026-07-09.md) — keepalive WS, córtex busca en web, reasoning-leak e idioma por voz resueltos, git reconciliado + PR #1.
- [ADR 0107 correcciones 2026-07-09](adr-0107-correcciones-y-tanda-2026-07-09.md) — rechazo→correcciones en el MISMO plan, entregado + e2e vivo; prod-12 → pending_human_validation.
- [Tanda hallazgos+prod12 2026-07-08](tanda-hallazgos-prod12-2026-07-08.md) — #1-#6 + prod-12 A/B/docker/reaper/av hechos y desplegados; head migr=0106.
- [Implementación auditoría 2026-07-04](implementacion-auditoria-2026-07-04.md) — P1/P2 git, ADR 0098-0101, g6 gate P0; c1 revertido; pendiente c6/c8/c9/c3/g4/g5.
- [Auditoría runs 2026-07-02](auditoria-runs-2026-07-02-remediacion.md) — F0-F3 (infra /data, contratos review, memoria, budgets) desplegada.
- [Plan guardas-research-por-novedad 2026-07-03](plan-guardas-research-novedad.md) — guardas por novedad + digests en PROGRESS + workers-aux, desplegado; F2/F3 pendientes; ADR 0097 proposed.
- [Data-root en volumen durable 2026-07-03](data-root-volumen-durable.md) — agent-data en named volume externo; backup diario reparado. **Gotcha: build workers sobre api-server:manuals, NO :ci.**
- [Revisión memorias 2026-07-03](revision-memorias-2026-07-03.md) — scopes sanos; D1 recall + D2 clamp hechos; private=humano.
- [Refactorización por partes](refactorizacion-por-partes.md) — COMPLETADA (P1-P7 + H1-H6); P8 no-abordar.
- [Remediación auditoría prod-implementados](remediacion-auditoria-prod-implementados.md) — 2 olas TDD; head migr=0104; SIN desplegar.
- [Córtex identidad real](cortex-identidad-real-entregado.md) — self-model unificado implementado+desplegado; QA operador pendiente; autonomía OFF.

## Trabajo previo (2026-06)

- [Auditoría runs + plan remediación](auditoria-runs-remediacion.md) — 41 confirmados, 8 clusters de causa raíz (C1 reviewer a ciegas); plan aprobado por fases.
- [Runs no convergen: causas estructurales](runs-no-convergen-causas-estructurales.md) — R1/R5/R6 (ADR 0090) + 5 tracks implementados y desplegados (ADR 0091 asignación por rol, ADR 0092 allowlist SDK).
- [Reviewer ciego (ADR 0095)](reviewer-ciego-convergencia-fix.md) — el reviewer no montaba worktree; fix = worktree read-only + safeguards reviewer-aware; verificado e2e.
- [Refactor self-review autoritativo (ADR 0087)](refactor-self-review-autoritativo.md) — review 3-estados + submit_result; migración 0100. Recipe de build/tags (agent-runtime contexto=raíz).
- [Hardening convergencia agent-runtime](agent-runtime-convergencia-hardening.md) — 8 fixes para que los runs claude_sdk completen (HOME, timeouts por-provider, max_iter 50).
- [stack_exec (ADR 0093)](stack-exec-feature.md) — el agente pide al worker correr su toolchain; verificado e2e. **RECETA: build con WITH_CLAUDE=1.**
- [registry-proxy / egress (ADR 0094)](registry-egress-feature.md) — egress allowlisted a registries + git vía 2º tinyproxy en el bridge per-task.
- [4 features acceptance/detalle/comentarios/git](features-acceptance-detalle-comentarios-git.md) — planner genera acceptance_criteria, detalle de tarea, comentarios→prompt; QA visual pendiente.
- [ADR 0082: modelo por provider_id](adr-0082-provider-id-unificacion.md) — unificación de selección/resolución por provider_id, desplegada. Gotcha: build admin-panel desde PowerShell.
- [Fix ingesta KB stack manuales](fix-ingesta-kb-manuals-stack.md) — rota en 3 capas (env del worker + `/v1/convert` retirado → `/v1/chunk/hybrid/file`); arreglado y verificado.
- [prod-06 entregado + PR #55](prod-06-entregado.md) — fases A-E entregadas; PR #55 abierto y mergeable; falta merge humano.
- [Auditoría memoria/tools/marketplace](auditoria-memoria-tools-marketplace.md) — H1-H4 confirmados; sin fuga cross-tenant.
- [Planning chat sin cablear](planning-chat-sin-cablear.md) — RESUELTO: el chat de planning ya está cableado (ADR 0091).
- [Estado del trabajo en curso](estado-trabajo-en-curso.md) — rama `feat/builtin-customization` entregada (ADR 0065/0066); merge = decisión del operador.
- [CodeIgniter 4 built-in](codeigniter4-builtin-team.md) — equipo CI4 mergeado a master (PR #33).
- [Modelo LLM por agente: heredable](model-per-agent-inheritance.md) — plataforma→proyecto→agente + override (ADR 0021).
- [Memoria: fix tool-calling provider-agnóstico](memoria-tool-calling-fix.md) — claude_sdk.complete() ya emite tool_calls; agentes #2/#3 pendientes.
- [Cola: Asistente modo voz](cola-tarea-asistente-voz.md) — ENCOLADA: STT+TTS (+avatar) en el chat.
- [Bug: voz del asistente no funciona](bug-asistente-voz-no-funciona.md) — investigar al hacer F5 (voz del córtex, ADR 0073).

## Gotchas transversales

> **Persistidos en el repo el 2026-07-26.** La fuente de verdad de las trampas
> del toolchain es `docs/03-guides/gotchas/` (66 entradas), y la de las
> prácticas de trabajo `docs/03-guides/verificar-antes-de-implementar.md`.
> Las notas de abajo quedan como punteros. **Al resolver una trampa nueva,
> documéntala allí** — CLAUDE.md lo exige — y aquí solo el puntero.

- [Resolución de provider: dos vías](provider-resolution-two-paths.md) — por provider_id (sync/asistente/test) vs por kind (dispatch); no confundirlas.
- [Gotchas: setpriv HOME + visibility timeout](gotcha-setpriv-home-y-visibility-timeout.md) — entrypoint root→setpriv hereda HOME=/root (asyncpg EACCES silencioso); re-entrega Celery ~7h por diseño.
- [Gotcha: caplog y orden de tests](gotcha-caplog-orden-tests.md) — afirmar sobre logs con caplog es frágil (la app hace logging.disable); usar logger fake.
- [Revisión paralela contamina la fuente](workflow-review-paralelo-contamina-fuente.md) — lentes de review con `parallel()` que mutan ficheros se contaminan; re-correr en serie antes de creer un "flaky".
